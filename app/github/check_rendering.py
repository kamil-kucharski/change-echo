from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum
from urllib.parse import quote, urlsplit

from app.echo.models import EchoClassification, HistoricalOutcome, ScoredEcho

CHECK_RUN_NAME = "Change Echo"
ADVISORY_TEXT = (
    "Change Echo is advisory. Historical similarity does not imply that the "
    "current change is wrong."
)


class CheckRunConclusion(StrEnum):
    SUCCESS = "success"
    NEUTRAL = "neutral"


@dataclass(frozen=True, slots=True)
class RenderedCheckRun:
    conclusion: CheckRunConclusion
    title: str
    summary: str
    text: str


def render_analysis_check(
    *,
    changed_file_count: int,
    candidate_count: int,
    skipped_candidate_count: int,
    echoes: Sequence[ScoredEcho],
) -> RenderedCheckRun:
    _validate_counts(changed_file_count, candidate_count, skipped_candidate_count)

    if not echoes:
        return _render_no_echo(
            changed_file_count=changed_file_count,
            candidate_count=candidate_count,
            skipped_candidate_count=skipped_candidate_count,
        )

    sections = tuple(_render_echo(echo, changed_file_count) for echo in echoes)
    analyzed_candidate_count = candidate_count - skipped_candidate_count
    summary = (
        f"Found {_count(len(echoes), 'relevant historical pull request')} after "
        f"analyzing {_count(analyzed_candidate_count, 'candidate')} across "
        f"{_count(changed_file_count, 'changed file')}."
    )
    if skipped_candidate_count:
        summary += f" Skipped {_count(skipped_candidate_count, 'candidate')} safely."

    return RenderedCheckRun(
        conclusion=CheckRunConclusion.NEUTRAL,
        title=(
            "Historical echo detected"
            if len(echoes) == 1
            else "Historical echoes detected"
        ),
        summary=summary,
        text="\n\n".join((*sections, ADVISORY_TEXT)),
    )


def render_large_pull_request_check(max_files: int) -> RenderedCheckRun:
    if max_files <= 0:
        raise ValueError("max_files must be greater than zero")

    return RenderedCheckRun(
        conclusion=CheckRunConclusion.NEUTRAL,
        title="Analysis skipped safely",
        summary=(
            "Change Echo did not analyze this pull request because it exceeds "
            f"the configured limit of {_count(max_files, 'changed file')}."
        ),
        text=(
            "The limit keeps analysis time and GitHub API usage predictable. "
            "This result is advisory and does not block merging."
        ),
    )


def render_analysis_failure_check() -> RenderedCheckRun:
    return RenderedCheckRun(
        conclusion=CheckRunConclusion.NEUTRAL,
        title="Analysis could not be completed",
        summary="Change Echo could not complete this analysis safely.",
        text=(
            "No conclusion about historical similarity was produced. Retry the "
            "analysis or verify the GitHub App configuration and permissions. "
            "This result is advisory and does not block merging."
        ),
    )


def _render_no_echo(
    *,
    changed_file_count: int,
    candidate_count: int,
    skipped_candidate_count: int,
) -> RenderedCheckRun:
    analyzed_candidate_count = candidate_count - skipped_candidate_count
    counts = (
        f"Analyzed {_count(changed_file_count, 'changed file')} and "
        f"{_count(analyzed_candidate_count, 'historical pull request candidate')}."
    )
    if skipped_candidate_count:
        return RenderedCheckRun(
            conclusion=CheckRunConclusion.NEUTRAL,
            title="Analysis completed with limitations",
            summary=(
                "No meaningful historical echo was found among the candidates "
                "that could be analyzed."
            ),
            text=(
                f"{counts} Skipped {_count(skipped_candidate_count, 'candidate')} "
                "safely because complete data was unavailable."
            ),
        )

    return RenderedCheckRun(
        conclusion=CheckRunConclusion.SUCCESS,
        title="No historical echo found",
        summary="No meaningful historical echo found.",
        text=counts,
    )


def _render_echo(echo: ScoredEcho, changed_file_count: int) -> str:
    if echo.classification is EchoClassification.NO_ECHO:
        raise ValueError("echoes must meet the possible echo threshold")

    reference = _pull_request_reference(
        number=echo.pull_request.number,
        title=echo.pull_request.title,
        html_url=echo.pull_request.html_url,
    )
    reasons = _match_reasons(echo, changed_file_count)
    reason_list = "\n".join(f"- {reason}" for reason in reasons)
    return (
        f"## {_classification_label(echo.classification)}\n\n"
        f"{reference}\n\n"
        f"- Echo Score: **{echo.displayed_score}/100**\n"
        f"- Historical outcome: {_outcome_label(echo.outcome)}\n\n"
        f"Why it matched:\n{reason_list}"
    )


def _match_reasons(echo: ScoredEcho, changed_file_count: int) -> tuple[str, ...]:
    reasons: list[str] = []
    if echo.overlapping_file_count:
        reasons.append(
            f"{echo.overlapping_file_count} of {changed_file_count} current changed "
            "files overlap"
        )
    if echo.components.directory_overlap > 0.0:
        reasons.append("directory paths overlap")
    if echo.components.title_similarity > 0.0:
        reasons.append("pull-request titles share normalized terms")
    if echo.components.body_similarity > 0.0:
        reasons.append("pull-request descriptions share normalized terms")
    if not reasons:
        reasons.append("the weighted similarity score reached the configured threshold")
    return tuple(reasons)


def _pull_request_reference(number: int, title: str, html_url: str | None) -> str:
    normalized_title = " ".join(title.split()) or "Untitled pull request"
    label = f"PR #{number} - {_escape_link_label(normalized_title)}"
    if html_url is None:
        return label

    parsed_url = urlsplit(html_url)
    if parsed_url.scheme not in {"http", "https"} or not parsed_url.netloc:
        return label

    encoded_url = quote(
        html_url,
        safe=":/?#@!$&'*+,;=%-._~",
    )
    return f"[{label}]({encoded_url})"


def _escape_link_label(value: str) -> str:
    return value.replace("\\", "\\\\").replace("[", "\\[").replace("]", "\\]")


def _classification_label(classification: EchoClassification) -> str:
    labels = {
        EchoClassification.POSSIBLE_ECHO: "Possible Echo",
        EchoClassification.STRONG_ECHO: "Strong Echo",
    }
    return labels[classification]


def _outcome_label(outcome: HistoricalOutcome) -> str:
    labels = {
        HistoricalOutcome.MERGED: "Merged",
        HistoricalOutcome.CLOSED_WITHOUT_MERGE: "Closed without merge",
        HistoricalOutcome.OPEN: "Open",
        HistoricalOutcome.UNKNOWN: "Unknown",
    }
    return labels[outcome]


def _validate_counts(
    changed_file_count: int,
    candidate_count: int,
    skipped_candidate_count: int,
) -> None:
    if changed_file_count < 0 or candidate_count < 0 or skipped_candidate_count < 0:
        raise ValueError("analysis counts must not be negative")
    if skipped_candidate_count > candidate_count:
        raise ValueError("skipped_candidate_count must not exceed candidate_count")


def _count(value: int, singular: str) -> str:
    suffix = "" if value == 1 else "s"
    return f"{value} {singular}{suffix}"
