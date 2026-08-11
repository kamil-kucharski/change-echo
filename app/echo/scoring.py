import re
import unicodedata
from collections.abc import Iterable

from app.echo.models import (
    CurrentPullRequest,
    EchoClassification,
    EchoScoreComponents,
    HistoricalOutcome,
    HistoricalPullRequest,
    ScoredEcho,
)

FILE_OVERLAP_WEIGHT = 0.45
DIRECTORY_OVERLAP_WEIGHT = 0.20
TITLE_SIMILARITY_WEIGHT = 0.25
BODY_SIMILARITY_WEIGHT = 0.10
ROOT_DIRECTORY_TOKEN = "<root>"
STRUCTURAL_PREFIX_PATTERN = re.compile(
    r"^(?:feat|fix|refactor|chore|docs)(?:\([^)]*\))?!?\s*:\s*"
)


def normalize_file_path(file_path: str) -> str:
    segments = [
        segment
        for segment in file_path.replace("\\", "/").split("/")
        if segment not in {"", "."}
    ]
    return "/".join(segments)


def normalized_file_paths(file_paths: Iterable[str]) -> frozenset[str]:
    normalized = (normalize_file_path(path) for path in file_paths)
    return frozenset(path for path in normalized if path)


def directory_tokens(file_paths: Iterable[str]) -> frozenset[str]:
    tokens: set[str] = set()
    for file_path in normalized_file_paths(file_paths):
        segments = file_path.split("/")
        if len(segments) == 1:
            tokens.add(ROOT_DIRECTORY_TOKEN)
            continue
        tokens.update("/".join(segments[:depth]) for depth in range(1, len(segments)))
    return frozenset(tokens)


def normalize_text_tokens(text: str | None) -> frozenset[str]:
    if not text:
        return frozenset()

    normalized = STRUCTURAL_PREFIX_PATTERN.sub("", text.casefold().lstrip(), count=1)
    without_punctuation = "".join(
        " " if unicodedata.category(character).startswith("P") else character
        for character in normalized
    )
    return frozenset(without_punctuation.split())


def jaccard_similarity(left: frozenset[str], right: frozenset[str]) -> float:
    union = left | right
    if not union:
        return 0.0
    return len(left & right) / len(union)


def classify_echo(
    score: float,
    possible_threshold: float,
    strong_threshold: float,
) -> EchoClassification:
    _validate_thresholds(possible_threshold, strong_threshold)
    if score >= strong_threshold:
        return EchoClassification.STRONG_ECHO
    if score >= possible_threshold:
        return EchoClassification.POSSIBLE_ECHO
    return EchoClassification.NO_ECHO


def classify_historical_outcome(
    state: str,
    merged_at: str | None,
) -> HistoricalOutcome:
    if merged_at is not None:
        return HistoricalOutcome.MERGED

    normalized_state = state.strip().casefold()
    if normalized_state == "closed":
        return HistoricalOutcome.CLOSED_WITHOUT_MERGE
    if normalized_state == "open":
        return HistoricalOutcome.OPEN
    return HistoricalOutcome.UNKNOWN


def score_pull_request(
    current: CurrentPullRequest,
    historical: HistoricalPullRequest,
    possible_threshold: float,
    strong_threshold: float,
) -> ScoredEcho:
    current_files = normalized_file_paths(current.file_paths)
    historical_files = normalized_file_paths(historical.file_paths)
    components = EchoScoreComponents(
        file_overlap=jaccard_similarity(current_files, historical_files),
        directory_overlap=jaccard_similarity(
            directory_tokens(current_files),
            directory_tokens(historical_files),
        ),
        title_similarity=jaccard_similarity(
            normalize_text_tokens(current.title),
            normalize_text_tokens(historical.title),
        ),
        body_similarity=jaccard_similarity(
            normalize_text_tokens(current.body),
            normalize_text_tokens(historical.body),
        ),
    )
    score = min(
        1.0,
        max(
            0.0,
            FILE_OVERLAP_WEIGHT * components.file_overlap
            + DIRECTORY_OVERLAP_WEIGHT * components.directory_overlap
            + TITLE_SIMILARITY_WEIGHT * components.title_similarity
            + BODY_SIMILARITY_WEIGHT * components.body_similarity,
        ),
    )
    return ScoredEcho(
        pull_request=historical,
        score=score,
        classification=classify_echo(
            score,
            possible_threshold,
            strong_threshold,
        ),
        outcome=classify_historical_outcome(
            historical.state,
            historical.merged_at,
        ),
        overlapping_file_count=len(current_files & historical_files),
        components=components,
    )


def rank_echoes(
    current: CurrentPullRequest,
    candidates: Iterable[HistoricalPullRequest],
    max_results: int,
    possible_threshold: float,
    strong_threshold: float,
) -> tuple[ScoredEcho, ...]:
    if max_results <= 0:
        raise ValueError("max_results must be greater than zero")
    _validate_thresholds(possible_threshold, strong_threshold)

    scored_candidates = (
        score_pull_request(
            current,
            candidate,
            possible_threshold,
            strong_threshold,
        )
        for candidate in candidates
    )
    relevant_candidates = (
        candidate
        for candidate in scored_candidates
        if candidate.score >= possible_threshold
    )
    ranked = sorted(
        relevant_candidates,
        key=lambda candidate: (
            -candidate.score,
            -candidate.overlapping_file_count,
            -candidate.pull_request.number,
        ),
    )
    return tuple(ranked[:max_results])


def _validate_thresholds(
    possible_threshold: float,
    strong_threshold: float,
) -> None:
    if not 0.0 <= possible_threshold <= strong_threshold <= 1.0:
        raise ValueError(
            "thresholds must satisfy 0 <= possible_threshold <= strong_threshold <= 1"
        )
