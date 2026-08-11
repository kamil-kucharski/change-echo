from dataclasses import dataclass
from typing import Protocol
from urllib.parse import quote

from pydantic import SecretStr, TypeAdapter, ValidationError

from app.github.client import GitHubClient, GitHubResponseError
from app.github.models import PullRequestFile

GITHUB_PULL_REQUEST_FILE_PAGE_LIMIT = 30
PULL_REQUEST_FILES_ADAPTER = TypeAdapter(list[PullRequestFile])


@dataclass(frozen=True, slots=True)
class CompletePullRequestInspection:
    files: tuple[PullRequestFile, ...]


@dataclass(frozen=True, slots=True)
class PullRequestTooLarge:
    max_files: int


type PullRequestInspectionResult = CompletePullRequestInspection | PullRequestTooLarge


class PullRequestInspector(Protocol):
    async def inspect(
        self,
        repository_full_name: str,
        pull_request_number: int,
        installation_token: SecretStr,
        max_files: int,
    ) -> PullRequestInspectionResult: ...


class GitHubPullRequestInspector:
    def __init__(self, client: GitHubClient) -> None:
        self._client = client

    async def inspect(
        self,
        repository_full_name: str,
        pull_request_number: int,
        installation_token: SecretStr,
        max_files: int,
    ) -> PullRequestInspectionResult:
        if max_files <= 0:
            raise ValueError("max_files must be greater than zero")

        owner, repository = self._repository_parts(repository_full_name)
        path = (
            f"/repos/{quote(owner, safe='')}/{quote(repository, safe='')}"
            f"/pulls/{pull_request_number}/files"
        )
        files: list[PullRequestFile] = []

        async for response in self._client.paginate(
            path,
            installation_token.get_secret_value(),
            params={"per_page": 100},
            max_pages=GITHUB_PULL_REQUEST_FILE_PAGE_LIMIT,
        ):
            try:
                page_files = PULL_REQUEST_FILES_ADAPTER.validate_python(response.json())
            except (ValueError, ValidationError) as error:
                raise GitHubResponseError(
                    "GitHub returned an invalid pull request files response",
                    status_code=response.status_code,
                ) from error

            files.extend(page_files)
            if len(files) > max_files:
                return PullRequestTooLarge(max_files=max_files)

        return CompletePullRequestInspection(files=tuple(files))

    @staticmethod
    def _repository_parts(repository_full_name: str) -> tuple[str, str]:
        parts = repository_full_name.split("/")
        if len(parts) != 2 or not all(parts):
            raise ValueError("repository_full_name must contain owner and repository")
        return parts[0], parts[1]
