import json

import httpx
import pytest
from pydantic import SecretStr

from app.github.check_rendering import (
    CheckRunConclusion,
    RenderedCheckRun,
)
from app.github.client import GitHubClient, GitHubPermissionError
from app.services.check_run_reporting import GitHubCheckRunReporter


@pytest.mark.asyncio
async def test_reporter_creates_completed_check_run_on_head_commit() -> None:
    captured_request: httpx.Request | None = None

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal captured_request
        captured_request = request
        return httpx.Response(201, json={"id": 123}, request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        client = GitHubClient(
            http_client,
            base_url="https://api.github.test",
            api_version="2026-03-10",
        )
        reporter = GitHubCheckRunReporter(client)
        await reporter.publish(
            repository_full_name="octo-org/change-echo",
            head_sha="0123456789abcdef0123456789abcdef01234567",
            result=RenderedCheckRun(
                conclusion=CheckRunConclusion.NEUTRAL,
                title="Historical echo detected",
                summary="Found one relevant pull request.",
                text="Advisory result.",
            ),
            installation_token=SecretStr("installation-token"),
        )

    assert captured_request is not None
    assert captured_request.method == "POST"
    assert captured_request.url == (
        "https://api.github.test/repos/octo-org/change-echo/check-runs"
    )
    assert captured_request.headers["Authorization"] == "Bearer installation-token"
    assert captured_request.headers["Content-Type"] == "application/json"
    assert json.loads(captured_request.content) == {
        "name": "Change Echo",
        "head_sha": "0123456789abcdef0123456789abcdef01234567",
        "status": "completed",
        "conclusion": "neutral",
        "output": {
            "title": "Historical echo detected",
            "summary": "Found one relevant pull request.",
            "text": "Advisory result.",
        },
    }


@pytest.mark.asyncio
async def test_reporter_propagates_typed_github_errors_without_token_leak() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        client = GitHubClient(
            http_client,
            base_url="https://api.github.test",
            api_version="2026-03-10",
        )
        reporter = GitHubCheckRunReporter(client)

        with pytest.raises(GitHubPermissionError) as captured_error:
            await reporter.publish(
                repository_full_name="octo-org/change-echo",
                head_sha="0123456789abcdef0123456789abcdef01234567",
                result=RenderedCheckRun(
                    conclusion=CheckRunConclusion.SUCCESS,
                    title="No historical echo found",
                    summary="No meaningful historical echo found.",
                    text="Analyzed 1 changed file.",
                ),
                installation_token=SecretStr("sensitive-token"),
            )

    assert "sensitive-token" not in str(captured_error.value)


@pytest.mark.parametrize(
    ("repository_full_name", "head_sha", "message"),
    [
        ("invalid", "abc", "owner and repository"),
        ("owner/repository", "", "head_sha"),
    ],
)
@pytest.mark.asyncio
async def test_reporter_rejects_invalid_target(
    repository_full_name: str,
    head_sha: str,
    message: str,
) -> None:
    async with httpx.AsyncClient() as http_client:
        reporter = GitHubCheckRunReporter(
            GitHubClient(
                http_client,
                base_url="https://api.github.test",
                api_version="2026-03-10",
            )
        )

        with pytest.raises(ValueError, match=message):
            await reporter.publish(
                repository_full_name=repository_full_name,
                head_sha=head_sha,
                result=RenderedCheckRun(
                    conclusion=CheckRunConclusion.SUCCESS,
                    title="No historical echo found",
                    summary="No meaningful historical echo found.",
                    text="Analyzed 1 changed file.",
                ),
                installation_token=SecretStr("installation-token"),
            )
