import pytest

from app.echo.models import (
    CurrentPullRequest,
    EchoClassification,
    HistoricalOutcome,
    HistoricalPullRequest,
)
from app.echo.scoring import (
    ROOT_DIRECTORY_TOKEN,
    classify_echo,
    classify_historical_outcome,
    directory_tokens,
    normalize_file_path,
    normalize_text_tokens,
    rank_echoes,
    score_pull_request,
)


def historical_pull_request(
    number: int,
    *,
    title: str = "Change API",
    body: str | None = None,
    state: str = "closed",
    merged_at: str | None = None,
    file_paths: tuple[str, ...] = (),
) -> HistoricalPullRequest:
    return HistoricalPullRequest(
        number=number,
        title=title,
        body=body,
        state=state,
        merged_at=merged_at,
        closed_at="2026-08-01T10:00:00Z",
        html_url=f"https://github.example/pulls/{number}",
        file_paths=file_paths,
    )


def test_path_and_directory_normalization_preserves_repository_hierarchy() -> None:
    assert normalize_file_path("./src\\auth//handler.py") == "src/auth/handler.py"
    assert directory_tokens(
        ("src/auth/handler.py", "src/auth/models/user.py", "README.md")
    ) == frozenset(
        {
            ROOT_DIRECTORY_TOKEN,
            "src",
            "src/auth",
            "src/auth/models",
        }
    )


def test_text_normalization_is_unicode_aware_and_removes_only_leading_labels() -> None:
    assert normalize_text_tokens("FEAT(auth)!: Straße, API—tokens!") == frozenset(
        {"strasse", "api", "tokens"}
    )
    assert "fix" in normalize_text_tokens("Fix authentication behavior")


def test_weighted_score_uses_all_specified_components() -> None:
    current = CurrentPullRequest(
        title="feat: Add login endpoint",
        body="Supports session tokens",
        file_paths=("src/auth/a.py", "src/auth/b.py"),
    )
    historical = historical_pull_request(
        7,
        title="fix(auth): add login flow",
        body="Supports session cookies",
        file_paths=("src/auth/a.py", "src/auth/c.py"),
    )

    result = score_pull_request(current, historical, 0.55, 0.72)

    assert result.components.file_overlap == pytest.approx(1 / 3)
    assert result.components.directory_overlap == 1.0
    assert result.components.title_similarity == 0.5
    assert result.components.body_similarity == 0.5
    assert result.score == pytest.approx(0.525)
    assert result.classification is EchoClassification.NO_ECHO
    assert result.outcome is HistoricalOutcome.CLOSED_WITHOUT_MERGE
    assert result.overlapping_file_count == 1


def test_missing_bodies_have_zero_similarity() -> None:
    current = CurrentPullRequest(
        title="Same title",
        body=None,
        file_paths=("app.py",),
    )
    historical = historical_pull_request(
        7,
        title="Same title",
        body=None,
        file_paths=("app.py",),
    )

    result = score_pull_request(current, historical, 0.55, 0.72)

    assert result.components.body_similarity == 0.0
    assert result.score == pytest.approx(0.9)
    assert result.classification is EchoClassification.STRONG_ECHO
    assert result.displayed_score == 90


@pytest.mark.parametrize(
    ("score", "expected"),
    [
        (0.0, EchoClassification.NO_ECHO),
        (0.549999, EchoClassification.NO_ECHO),
        (0.55, EchoClassification.POSSIBLE_ECHO),
        (0.719999, EchoClassification.POSSIBLE_ECHO),
        (0.72, EchoClassification.STRONG_ECHO),
        (1.0, EchoClassification.STRONG_ECHO),
    ],
)
def test_classification_threshold_boundaries(
    score: float,
    expected: EchoClassification,
) -> None:
    assert classify_echo(score, 0.55, 0.72) is expected


def test_invalid_threshold_order_is_rejected() -> None:
    with pytest.raises(ValueError, match="thresholds must satisfy"):
        classify_echo(0.7, 0.8, 0.6)


@pytest.mark.parametrize(
    ("state", "merged_at", "expected"),
    [
        ("closed", "2026-08-01T10:00:00Z", HistoricalOutcome.MERGED),
        ("closed", None, HistoricalOutcome.CLOSED_WITHOUT_MERGE),
        ("OPEN", None, HistoricalOutcome.OPEN),
        ("draft", None, HistoricalOutcome.UNKNOWN),
    ],
)
def test_historical_outcome_is_independent_from_similarity(
    state: str,
    merged_at: str | None,
    expected: HistoricalOutcome,
) -> None:
    assert classify_historical_outcome(state, merged_at) is expected


def test_ranking_uses_score_overlap_number_and_result_limit_deterministically() -> None:
    current = CurrentPullRequest(
        title="Change API",
        body=None,
        file_paths=("a.py", "b.py"),
    )
    candidates = (
        historical_pull_request(1, file_paths=("a.py",)),
        historical_pull_request(
            2,
            file_paths=("a.py", "b.py", "c.py", "d.py"),
        ),
        historical_pull_request(
            3,
            file_paths=("a.py", "b.py", "c.py", "d.py"),
        ),
        historical_pull_request(
            99,
            title="Unrelated database change",
            file_paths=("src/database.py",),
        ),
    )

    first_result = rank_echoes(current, candidates, 2, 0.55, 0.72)
    second_result = rank_echoes(current, candidates, 2, 0.55, 0.72)

    assert [echo.pull_request.number for echo in first_result] == [3, 2]
    assert first_result == second_result
    assert all(echo.score >= 0.55 for echo in first_result)
