from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

from pydantic import SecretStr

from app.echo.models import CurrentPullRequest, ScoredEcho
from app.echo.scoring import rank_echoes
from app.services.candidate_discovery import CandidateDiscoverer
from app.services.candidate_enrichment import CandidateEnricher


@dataclass(frozen=True, slots=True)
class HistoricalAnalysisResult:
    candidate_count: int
    skipped_candidate_count: int
    echoes: tuple[ScoredEcho, ...]


class HistoricalAnalyzer(Protocol):
    async def analyze(
        self,
        repository_full_name: str,
        current_pull_request_number: int,
        current_title: str,
        current_body: str | None,
        current_file_paths: Sequence[str],
        installation_token: SecretStr,
        max_commits_per_path: int,
        max_unique_candidates: int,
        max_results: int,
        possible_threshold: float,
        strong_threshold: float,
    ) -> HistoricalAnalysisResult: ...


class GitHubHistoricalAnalyzer:
    def __init__(
        self,
        candidate_discoverer: CandidateDiscoverer,
        candidate_enricher: CandidateEnricher,
    ) -> None:
        self._candidate_discoverer = candidate_discoverer
        self._candidate_enricher = candidate_enricher

    async def analyze(
        self,
        repository_full_name: str,
        current_pull_request_number: int,
        current_title: str,
        current_body: str | None,
        current_file_paths: Sequence[str],
        installation_token: SecretStr,
        max_commits_per_path: int,
        max_unique_candidates: int,
        max_results: int,
        possible_threshold: float,
        strong_threshold: float,
    ) -> HistoricalAnalysisResult:
        candidates = await self._candidate_discoverer.discover(
            repository_full_name=repository_full_name,
            current_pull_request_number=current_pull_request_number,
            current_file_paths=current_file_paths,
            installation_token=installation_token,
            max_commits_per_path=max_commits_per_path,
            max_unique_candidates=max_unique_candidates,
        )
        enrichment = await self._candidate_enricher.enrich(
            repository_full_name=repository_full_name,
            candidates=candidates,
            installation_token=installation_token,
        )
        echoes = rank_echoes(
            current=CurrentPullRequest(
                title=current_title,
                body=current_body,
                file_paths=tuple(current_file_paths),
            ),
            candidates=enrichment.pull_requests,
            max_results=max_results,
            possible_threshold=possible_threshold,
            strong_threshold=strong_threshold,
        )
        return HistoricalAnalysisResult(
            candidate_count=len(candidates),
            skipped_candidate_count=enrichment.skipped_count,
            echoes=echoes,
        )
