from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

from pydantic import SecretStr

from app.echo.models import HistoricalPullRequest
from app.github.client import (
    GitHubNotFoundError,
    GitHubResponseError,
    GitHubValidationError,
)
from app.services.candidate_discovery import HistoricalPullRequestCandidate
from app.services.pull_request_inspection import (
    CompletePullRequestInspection,
    PullRequestInspector,
)

MAX_COMPLETE_CANDIDATE_FILES = 2999


@dataclass(frozen=True, slots=True)
class CandidateEnrichmentResult:
    pull_requests: tuple[HistoricalPullRequest, ...]
    skipped_count: int


class CandidateEnricher(Protocol):
    async def enrich(
        self,
        repository_full_name: str,
        candidates: Sequence[HistoricalPullRequestCandidate],
        installation_token: SecretStr,
    ) -> CandidateEnrichmentResult: ...


class GitHubCandidateEnricher:
    def __init__(self, pull_request_inspector: PullRequestInspector) -> None:
        self._pull_request_inspector = pull_request_inspector

    async def enrich(
        self,
        repository_full_name: str,
        candidates: Sequence[HistoricalPullRequestCandidate],
        installation_token: SecretStr,
    ) -> CandidateEnrichmentResult:
        pull_requests: list[HistoricalPullRequest] = []
        skipped_count = 0

        for candidate in candidates:
            try:
                inspection = await self._pull_request_inspector.inspect(
                    repository_full_name=repository_full_name,
                    pull_request_number=candidate.number,
                    installation_token=installation_token,
                    max_files=MAX_COMPLETE_CANDIDATE_FILES,
                )
            except (
                GitHubNotFoundError,
                GitHubResponseError,
                GitHubValidationError,
            ):
                skipped_count += 1
                continue

            if not isinstance(inspection, CompletePullRequestInspection):
                skipped_count += 1
                continue
            if not inspection.files:
                skipped_count += 1
                continue

            pull_requests.append(
                HistoricalPullRequest(
                    number=candidate.number,
                    title=candidate.title,
                    body=candidate.body,
                    state=candidate.state,
                    merged_at=candidate.merged_at,
                    closed_at=candidate.closed_at,
                    html_url=candidate.html_url,
                    file_paths=tuple(file.filename for file in inspection.files),
                )
            )

        return CandidateEnrichmentResult(
            pull_requests=tuple(pull_requests),
            skipped_count=skipped_count,
        )
