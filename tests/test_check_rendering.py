import pytest

from app.echo.models import (
    EchoClassification,
    EchoScoreComponents,
    HistoricalOutcome,
    HistoricalPullRequest,
    ScoredEcho,
)
from app.github.check_rendering import (
    ADVISORY_TEXT,
    CheckRunConclusion,
    render_analysis_check,
    render_large_pull_request_check,
)


def scored_echo(
    *,
    number: int = 91,
    title: str = "Replace authentication middleware",
    html_url: str | None = "https://github.com/example/repository/pull/91",
    score: float = 0.82,
    classification: EchoClassification = EchoClassification.STRONG_ECHO,
    outcome: HistoricalOutcome = HistoricalOutcome.CLOSED_WITHOUT_MERGE,
) -> ScoredEcho:
    return ScoredEcho(
        pull_request=HistoricalPullRequest(
            number=number,
            title=title,
            body="Historical description",
            state="closed",
            merged_at=None,
            closed_at="2026-08-01T10:00:00Z",
            html_url=html_url,
            file_paths=("app/auth.py", "app/middleware.py", "tests/test_auth.py"),
        ),
        score=score,
        classification=classification,
        outcome=outcome,
        overlapping_file_count=3,
        components=EchoScoreComponents(
            file_overlap=0.75,
            directory_overlap=1.0,
            title_similarity=0.6,
            body_similarity=0.0,
        ),
    )


def test_no_echo_renders_success_with_analysis_counts() -> None:
    result = render_analysis_check(
        changed_file_count=4,
        candidate_count=11,
        skipped_candidate_count=0,
        echoes=(),
    )

    assert result.conclusion is CheckRunConclusion.SUCCESS
    assert result.title == "No historical echo found"
    assert result.summary == "No meaningful historical echo found."
    assert result.text == (
        "Analyzed 4 changed files and 11 historical pull request candidates."
    )


def test_strong_echo_renders_neutral_linked_advisory_result() -> None:
    result = render_analysis_check(
        changed_file_count=4,
        candidate_count=11,
        skipped_candidate_count=0,
        echoes=(scored_echo(),),
    )

    assert result.conclusion is CheckRunConclusion.NEUTRAL
    assert result.title == "Historical echo detected"
    assert result.summary == (
        "Found 1 relevant historical pull request after analyzing 11 candidates "
        "across 4 changed files."
    )
    assert "## Strong Echo" in result.text
    assert (
        "[PR #91 - Replace authentication middleware]"
        "(https://github.com/example/repository/pull/91)" in result.text
    )
    assert "Echo Score: **82/100**" in result.text
    assert "Historical outcome: Closed without merge" in result.text
    assert "3 of 4 current changed files overlap" in result.text
    assert "directory paths overlap" in result.text
    assert "pull-request titles share normalized terms" in result.text
    assert "descriptions share" not in result.text
    assert result.text.endswith(ADVISORY_TEXT)


def test_multiple_echoes_render_each_classification_and_outcome() -> None:
    possible_echo = scored_echo(
        number=44,
        title="Reuse session handler",
        html_url=None,
        score=0.61,
        classification=EchoClassification.POSSIBLE_ECHO,
        outcome=HistoricalOutcome.MERGED,
    )

    result = render_analysis_check(
        changed_file_count=4,
        candidate_count=2,
        skipped_candidate_count=0,
        echoes=(scored_echo(), possible_echo),
    )

    assert result.title == "Historical echoes detected"
    assert result.text.count("## ") == 2
    assert "## Possible Echo" in result.text
    assert "PR #44 - Reuse session handler" in result.text
    assert "Historical outcome: Merged" in result.text


def test_skipped_candidates_make_no_echo_result_neutral_and_explicit() -> None:
    result = render_analysis_check(
        changed_file_count=2,
        candidate_count=3,
        skipped_candidate_count=1,
        echoes=(),
    )

    assert result.conclusion is CheckRunConclusion.NEUTRAL
    assert result.title == "Analysis completed with limitations"
    assert "among the candidates that could be analyzed" in result.summary
    assert "Analyzed 2 changed files and 2 historical pull request candidates." in (
        result.text
    )
    assert "Skipped 1 candidate safely" in result.text


def test_large_pull_request_renders_neutral_bounded_result() -> None:
    result = render_large_pull_request_check(max_files=100)

    assert result.conclusion is CheckRunConclusion.NEUTRAL
    assert result.title == "Analysis skipped safely"
    assert "limit of 100 changed files" in result.summary
    assert "does not block merging" in result.text


def test_link_text_is_normalized_and_unsafe_url_is_not_linked() -> None:
    echo = scored_echo(
        title="Add [unsafe]\n formatting",
        html_url="javascript:alert(1)",
    )

    result = render_analysis_check(
        changed_file_count=4,
        candidate_count=1,
        skipped_candidate_count=0,
        echoes=(echo,),
    )

    assert "PR #91 - Add \\[unsafe\\] formatting" in result.text
    assert "javascript:" not in result.text


def test_no_echo_classification_is_rejected_from_relevant_results() -> None:
    with pytest.raises(ValueError, match="possible echo threshold"):
        render_analysis_check(
            changed_file_count=4,
            candidate_count=1,
            skipped_candidate_count=0,
            echoes=(
                scored_echo(
                    score=0.2,
                    classification=EchoClassification.NO_ECHO,
                ),
            ),
        )


@pytest.mark.parametrize(
    ("changed_file_count", "candidate_count", "skipped_candidate_count"),
    [(-1, 0, 0), (0, -1, 0), (0, 0, -1), (0, 1, 2)],
)
def test_invalid_analysis_counts_are_rejected(
    changed_file_count: int,
    candidate_count: int,
    skipped_candidate_count: int,
) -> None:
    with pytest.raises(ValueError):
        render_analysis_check(
            changed_file_count=changed_file_count,
            candidate_count=candidate_count,
            skipped_candidate_count=skipped_candidate_count,
            echoes=(),
        )


def test_invalid_large_pull_request_limit_is_rejected() -> None:
    with pytest.raises(ValueError, match="greater than zero"):
        render_large_pull_request_check(max_files=0)
