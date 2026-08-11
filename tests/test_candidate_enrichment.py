from collections.abc import Mapping

import pytest
from pydantic import SecretStr

from app.github.client import (
    GitHubNotFoundError,
    GitHubPermissionError,
    GitHubResponseError,
    GitHubValidationError,
)
from app.github.models import PullRequestFile
from app.services.candidate_discovery import HistoricalPullRequestCandidate
from app.services.candidate_enrichment import (
    MAX_COMPLETE_CANDIDATE_FILES,
    GitHubCandidateEnricher,
)
from app.services.pull_request_inspection import (
    CompletePullRequestInspection,
    PullRequestInspectionResult,
    PullRequestTooLarge,
)

type InspectionOutcome = PullRequestInspectionResult | Exception


class StubPullRequestInspector:
    def __init__(self, outcomes: Mapping[int, InspectionOutcome]) -> None:
        self._outcomes = outcomes
        self.requests: list[tuple[str, int, int]] = []

    async def inspect(
        self,
        repository_full_name: str,
        pull_request_number: int,
        installation_token: SecretStr,
        max_files: int,
    ) -> PullRequestInspectionResult:
        assert installation_token.get_secret_value() == "installation-token"
        self.requests.append((repository_full_name, pull_request_number, max_files))
        outcome = self._outcomes[pull_request_number]
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def candidate(number: int) -> HistoricalPullRequestCandidate:
    return HistoricalPullRequestCandidate(
        number=number,
        title=f"Historical pull request {number}",
        body=None if number == 2 else "Historical body",
        state="closed",
        merged_at=None,
        closed_at="2026-08-01T10:00:00Z",
        html_url=f"https://github.example/pulls/{number}",
        matching_paths=("src/shared.py",),
    )


def pull_request_file(filename: str) -> PullRequestFile:
    return PullRequestFile(
        filename=filename,
        status="modified",
        additions=2,
        deletions=1,
        changes=3,
        patch=None,
    )


@pytest.mark.asyncio
async def test_enrichment_preserves_order_metadata_and_complete_file_paths() -> None:
    inspector = StubPullRequestInspector(
        {
            2: CompletePullRequestInspection(
                files=(
                    pull_request_file("src/two.py"),
                    pull_request_file("tests/test_two.py"),
                )
            ),
            1: CompletePullRequestInspection(files=(pull_request_file("src/one.py"),)),
        }
    )
    enricher = GitHubCandidateEnricher(inspector)

    result = await enricher.enrich(
        repository_full_name="octo-org/repository",
        candidates=(candidate(2), candidate(1)),
        installation_token=SecretStr("installation-token"),
    )

    assert [pull_request.number for pull_request in result.pull_requests] == [2, 1]
    assert result.pull_requests[0].body is None
    assert result.pull_requests[0].file_paths == (
        "src/two.py",
        "tests/test_two.py",
    )
    assert result.pull_requests[1].html_url == "https://github.example/pulls/1"
    assert result.skipped_count == 0
    assert inspector.requests == [
        ("octo-org/repository", 2, MAX_COMPLETE_CANDIDATE_FILES),
        ("octo-org/repository", 1, MAX_COMPLETE_CANDIDATE_FILES),
    ]


@pytest.mark.asyncio
async def test_enrichment_skips_isolated_unusable_candidates() -> None:
    inspector = StubPullRequestInspector(
        {
            1: GitHubNotFoundError("not found", status_code=404),
            2: GitHubValidationError("invalid", status_code=422),
            3: GitHubResponseError("malformed", status_code=200),
            4: PullRequestTooLarge(max_files=MAX_COMPLETE_CANDIDATE_FILES),
            5: CompletePullRequestInspection(files=()),
            6: CompletePullRequestInspection(
                files=(pull_request_file("src/available.py"),)
            ),
        }
    )
    enricher = GitHubCandidateEnricher(inspector)

    result = await enricher.enrich(
        repository_full_name="octo-org/repository",
        candidates=tuple(candidate(number) for number in range(1, 7)),
        installation_token=SecretStr("installation-token"),
    )

    assert len(result.pull_requests) == 1
    assert result.skipped_count == 5
    assert result.pull_requests[0].number == 6
    assert result.pull_requests[0].file_paths == ("src/available.py",)


@pytest.mark.asyncio
async def test_enrichment_propagates_permission_failures() -> None:
    inspector = StubPullRequestInspector(
        {
            1: GitHubPermissionError("permission denied", status_code=403),
            2: CompletePullRequestInspection(
                files=(pull_request_file("src/not-requested.py"),)
            ),
        }
    )
    enricher = GitHubCandidateEnricher(inspector)

    with pytest.raises(GitHubPermissionError):
        await enricher.enrich(
            repository_full_name="octo-org/repository",
            candidates=(candidate(1), candidate(2)),
            installation_token=SecretStr("installation-token"),
        )

    assert inspector.requests == [
        ("octo-org/repository", 1, MAX_COMPLETE_CANDIDATE_FILES)
    ]
