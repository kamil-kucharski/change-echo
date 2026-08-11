from dataclasses import dataclass
from enum import StrEnum


class EchoClassification(StrEnum):
    NO_ECHO = "no_echo"
    POSSIBLE_ECHO = "possible_echo"
    STRONG_ECHO = "strong_echo"


class HistoricalOutcome(StrEnum):
    MERGED = "merged"
    CLOSED_WITHOUT_MERGE = "closed_without_merge"
    OPEN = "open"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class CurrentPullRequest:
    title: str
    body: str | None
    file_paths: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class HistoricalPullRequest:
    number: int
    title: str
    body: str | None
    state: str
    merged_at: str | None
    closed_at: str | None
    html_url: str | None
    file_paths: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class EchoScoreComponents:
    file_overlap: float
    directory_overlap: float
    title_similarity: float
    body_similarity: float


@dataclass(frozen=True, slots=True)
class ScoredEcho:
    pull_request: HistoricalPullRequest
    score: float
    classification: EchoClassification
    outcome: HistoricalOutcome
    overlapping_file_count: int
    components: EchoScoreComponents

    @property
    def displayed_score(self) -> int:
        return min(100, max(0, int(self.score * 100 + 0.5)))
