from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass
from typing import Protocol
from urllib.parse import quote

import httpx
from pydantic import SecretStr, ValidationError

from app.github.client import (
    GitHubAPIError,
    GitHubClient,
    GitHubNotFoundError,
    GitHubResponseError,
)
from app.github.models import AssociatedPullRequest, RepositoryCommit

GITHUB_PAGE_SIZE = 100


@dataclass(frozen=True, slots=True)
class HistoricalPullRequestCandidate:
    number: int
    matching_paths: tuple[str, ...]


class CandidateDiscoverer(Protocol):
    async def discover(
        self,
        repository_full_name: str,
        current_pull_request_number: int,
        current_file_paths: Sequence[str],
        installation_token: SecretStr,
        max_commits_per_path: int,
        max_unique_candidates: int,
    ) -> tuple[HistoricalPullRequestCandidate, ...]: ...


class GitHubCandidateDiscoverer:
    def __init__(self, client: GitHubClient) -> None:
        self._client = client

    async def discover(
        self,
        repository_full_name: str,
        current_pull_request_number: int,
        current_file_paths: Sequence[str],
        installation_token: SecretStr,
        max_commits_per_path: int,
        max_unique_candidates: int,
    ) -> tuple[HistoricalPullRequestCandidate, ...]:
        if max_commits_per_path <= 0 or max_commits_per_path > GITHUB_PAGE_SIZE:
            raise ValueError("max_commits_per_path must be between 1 and 100")
        if max_unique_candidates <= 0:
            raise ValueError("max_unique_candidates must be greater than zero")

        owner, repository = self._repository_parts(repository_full_name)
        token = installation_token.get_secret_value()
        commit_paths: dict[str, set[str]] = {}
        first_seen: dict[str, int] = {}

        for file_path in sorted(set(current_file_paths)):
            commits = await self._commits_for_path(
                owner,
                repository,
                file_path,
                token,
                max_commits_per_path,
            )
            for commit in commits:
                if commit.sha not in first_seen:
                    first_seen[commit.sha] = len(first_seen)
                commit_paths.setdefault(commit.sha, set()).add(file_path)

        ordered_commits = sorted(
            commit_paths,
            key=lambda sha: (-len(commit_paths[sha]), first_seen[sha], sha),
        )
        candidate_paths: dict[int, set[str]] = {}

        for commit_sha in ordered_commits:
            if len(candidate_paths) >= max_unique_candidates:
                break

            try:
                async for pull_request_number in self._pull_requests_for_commit(
                    owner,
                    repository,
                    commit_sha,
                    token,
                    max_pages=max_unique_candidates + 1,
                ):
                    if pull_request_number == current_pull_request_number:
                        continue

                    existing_paths = candidate_paths.get(pull_request_number)
                    if existing_paths is not None:
                        existing_paths.update(commit_paths[commit_sha])
                        continue

                    candidate_paths[pull_request_number] = set(commit_paths[commit_sha])
                    if len(candidate_paths) >= max_unique_candidates:
                        break
            except GitHubNotFoundError:
                continue
            except GitHubAPIError as error:
                if error.status_code == 409:
                    continue
                raise

        candidates = (
            HistoricalPullRequestCandidate(
                number=number,
                matching_paths=tuple(sorted(paths)),
            )
            for number, paths in candidate_paths.items()
        )
        return tuple(
            sorted(
                candidates,
                key=lambda candidate: (
                    -len(candidate.matching_paths),
                    -candidate.number,
                ),
            )
        )

    async def _commits_for_path(
        self,
        owner: str,
        repository: str,
        file_path: str,
        token: str,
        max_commits: int,
    ) -> tuple[RepositoryCommit, ...]:
        path = f"/repos/{quote(owner, safe='')}/{quote(repository, safe='')}/commits"
        response = await self._client.request(
            "GET",
            path,
            token,
            params={"path": file_path, "per_page": max_commits},
        )
        payload = self._response_list(response)
        commits: list[RepositoryCommit] = []
        for item in payload:
            try:
                commits.append(RepositoryCommit.model_validate(item))
            except ValidationError:
                continue
        return tuple(commits)

    async def _pull_requests_for_commit(
        self,
        owner: str,
        repository: str,
        commit_sha: str,
        token: str,
        max_pages: int,
    ) -> AsyncIterator[int]:
        path = (
            f"/repos/{quote(owner, safe='')}/{quote(repository, safe='')}"
            f"/commits/{quote(commit_sha, safe='')}/pulls"
        )
        async for response in self._client.paginate(
            path,
            token,
            params={"per_page": GITHUB_PAGE_SIZE},
            max_pages=max_pages,
        ):
            payload = self._response_list(response)
            page_numbers: set[int] = set()
            for item in payload:
                try:
                    pull_request = AssociatedPullRequest.model_validate(item)
                except ValidationError:
                    continue
                page_numbers.add(pull_request.number)

            for number in sorted(page_numbers, reverse=True):
                yield number

    @staticmethod
    def _response_list(response: httpx.Response) -> list[object]:
        try:
            payload = response.json()
        except ValueError as error:
            raise GitHubResponseError(
                "GitHub returned an invalid candidate discovery response",
                status_code=response.status_code,
            ) from error
        if not isinstance(payload, list):
            raise GitHubResponseError(
                "GitHub returned an invalid candidate discovery response",
                status_code=response.status_code,
            )
        return payload

    @staticmethod
    def _repository_parts(repository_full_name: str) -> tuple[str, str]:
        parts = repository_full_name.split("/")
        if len(parts) != 2 or not all(parts):
            raise ValueError("repository_full_name must contain owner and repository")
        return parts[0], parts[1]
