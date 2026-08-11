from collections.abc import Sequence

import pytest
from pydantic import SecretStr

from app.echo.models import EchoClassification, HistoricalPullRequest
from app.services.candidate_discovery import HistoricalPullRequestCandidate
from app.services.candidate_enrichment import CandidateEnrichmentResult
from app.services.pull_request_analysis import GitHubHistoricalAnalyzer


class StubCandidateDiscoverer:
    def __init__(
        self,
        candidates: tuple[HistoricalPullRequestCandidate, ...],
    ) -> None:
        self._candidates = candidates
        self.requests: list[tuple[str, int, tuple[str, ...], int, int]] = []

    async def discover(
        self,
        repository_full_name: str,
        current_pull_request_number: int,
        current_file_paths: Sequence[str],
        installation_token: SecretStr,
        max_commits_per_path: int,
        max_unique_candidates: int,
    ) -> tuple[HistoricalPullRequestCandidate, ...]:
        assert installation_token.get_secret_value() == "installation-token"
        self.requests.append(
            (
                repository_full_name,
                current_pull_request_number,
                tuple(current_file_paths),
                max_commits_per_path,
                max_unique_candidates,
            )
        )
        return self._candidates


class StubCandidateEnricher:
    def __init__(self, result: CandidateEnrichmentResult) -> None:
        self._result = result
        self.requests: list[tuple[str, tuple[HistoricalPullRequestCandidate, ...]]] = []

    async def enrich(
        self,
        repository_full_name: str,
        candidates: Sequence[HistoricalPullRequestCandidate],
        installation_token: SecretStr,
    ) -> CandidateEnrichmentResult:
        assert installation_token.get_secret_value() == "installation-token"
        self.requests.append((repository_full_name, tuple(candidates)))
        return self._result


def candidate(number: int) -> HistoricalPullRequestCandidate:
    return HistoricalPullRequestCandidate(
        number=number,
        title="Add repository memory lookup",
        body=None,
        state="closed",
        merged_at=None,
        closed_at="2026-08-01T10:00:00Z",
        html_url=f"https://github.example/pulls/{number}",
        matching_paths=("src/memory.py",),
    )


@pytest.mark.asyncio
async def test_analysis_composes_discovery_enrichment_scoring_and_counts() -> None:
    candidates = (candidate(8), candidate(7))
    discoverer = StubCandidateDiscoverer(candidates)
    enricher = StubCandidateEnricher(
        CandidateEnrichmentResult(
            pull_requests=(
                HistoricalPullRequest(
                    number=8,
                    title="Add repository memory lookup",
                    body=None,
                    state="closed",
                    merged_at=None,
                    closed_at="2026-08-01T10:00:00Z",
                    html_url="https://github.example/pulls/8",
                    file_paths=("src/memory.py",),
                ),
            ),
            skipped_count=1,
        )
    )
    analyzer = GitHubHistoricalAnalyzer(discoverer, enricher)

    result = await analyzer.analyze(
        repository_full_name="octo-org/repository",
        current_pull_request_number=42,
        current_title="feat: Add repository memory lookup",
        current_body=None,
        current_file_paths=("src/memory.py",),
        installation_token=SecretStr("installation-token"),
        max_commits_per_path=20,
        max_unique_candidates=40,
        max_results=3,
        possible_threshold=0.55,
        strong_threshold=0.72,
    )

    assert result.candidate_count == 2
    assert result.skipped_candidate_count == 1
    assert len(result.echoes) == 1
    assert result.echoes[0].pull_request.number == 8
    assert result.echoes[0].classification is EchoClassification.STRONG_ECHO
    assert discoverer.requests == [
        ("octo-org/repository", 42, ("src/memory.py",), 20, 40)
    ]
    assert enricher.requests == [("octo-org/repository", candidates)]
