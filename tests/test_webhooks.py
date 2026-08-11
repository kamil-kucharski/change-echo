import hashlib
import hmac
import json
import logging
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient
from pydantic import SecretStr

from app.config import Settings
from app.github.auth import InstallationAccessToken
from app.github.client import GitHubAPIError, GitHubNotFoundError
from app.github.models import PullRequestWebhookPayload
from app.main import create_app
from app.services.candidate_discovery import HistoricalPullRequestCandidate
from app.services.pull_request_inspection import (
    CompletePullRequestInspection,
    PullRequestInspectionResult,
    PullRequestTooLarge,
)

WEBHOOK_SECRET = "signed-fixture-secret"
DELIVERY_ID = "00000000-0000-4000-8000-000000000001"
FIXTURE_PATH = Path(__file__).parent / "fixtures" / "pull_request_opened.json"


class StubInstallationTokenProvider:
    def __init__(self) -> None:
        self.requested_installation_ids: list[int] = []

    async def get_installation_access_token(
        self,
        installation_id: int,
    ) -> InstallationAccessToken:
        self.requested_installation_ids.append(installation_id)
        return InstallationAccessToken(
            token=SecretStr("temporary-installation-token"),
            expires_at=datetime.now(UTC),
        )


class StubPullRequestInspector:
    def __init__(
        self,
        result: PullRequestInspectionResult | None = None,
        error: GitHubAPIError | None = None,
    ) -> None:
        self.result = result or CompletePullRequestInspection(files=())
        self.error = error
        self.requests: list[tuple[str, int, int]] = []

    async def inspect(
        self,
        repository_full_name: str,
        pull_request_number: int,
        installation_token: SecretStr,
        max_files: int,
    ) -> PullRequestInspectionResult:
        assert installation_token.get_secret_value() == "temporary-installation-token"
        self.requests.append((repository_full_name, pull_request_number, max_files))
        if self.error is not None:
            raise self.error
        return self.result


class StubCandidateDiscoverer:
    def __init__(
        self,
        result: tuple[HistoricalPullRequestCandidate, ...] = (),
        error: GitHubAPIError | None = None,
    ) -> None:
        self.result = result
        self.error = error
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
        assert installation_token.get_secret_value() == "temporary-installation-token"
        self.requests.append(
            (
                repository_full_name,
                current_pull_request_number,
                tuple(current_file_paths),
                max_commits_per_path,
                max_unique_candidates,
            )
        )
        if self.error is not None:
            raise self.error
        return self.result


def sign_payload(payload: bytes, secret: str = WEBHOOK_SECRET) -> str:
    digest = hmac.new(secret.encode(), payload, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


def github_headers(payload: bytes, event: str = "pull_request") -> dict[str, str]:
    return {
        "X-Hub-Signature-256": sign_payload(payload),
        "X-GitHub-Event": event,
        "X-GitHub-Delivery": DELIVERY_ID,
    }


@asynccontextmanager
async def webhook_client(
    secret: str | None = WEBHOOK_SECRET,
    max_files: int = 100,
    inspection_result: PullRequestInspectionResult | None = None,
    inspection_error: GitHubAPIError | None = None,
    candidate_result: tuple[HistoricalPullRequestCandidate, ...] = (),
    candidate_error: GitHubAPIError | None = None,
) -> AsyncIterator[
    tuple[
        AsyncClient,
        StubInstallationTokenProvider,
        StubPullRequestInspector,
        StubCandidateDiscoverer,
    ]
]:
    settings = Settings(
        _env_file=None,
        github_webhook_secret=secret,
        echo_max_current_files=max_files,
    )
    token_provider = StubInstallationTokenProvider()
    inspector = StubPullRequestInspector(inspection_result, inspection_error)
    discoverer = StubCandidateDiscoverer(candidate_result, candidate_error)
    application = create_app(
        settings,
        installation_token_provider=token_provider,
        pull_request_inspector=inspector,
        candidate_discoverer=discoverer,
    )
    transport = ASGITransport(app=application)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client, token_provider, inspector, discoverer


@pytest.fixture
def pull_request_payload() -> bytes:
    return FIXTURE_PATH.read_bytes()


@pytest.mark.asyncio
async def test_valid_supported_delivery_is_accepted_and_logged(
    pull_request_payload: bytes,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO, logger="app.api.webhooks")

    async with webhook_client() as (client, token_provider, inspector, discoverer):
        response = await client.post(
            "/webhooks/github",
            content=pull_request_payload,
            headers=github_headers(pull_request_payload),
        )

    assert response.status_code == 200
    assert response.json() == {"status": "accepted"}
    assert DELIVERY_ID in caplog.text
    assert "octo-org/change-echo-test" in caplog.text
    assert "pr_number=42" in caplog.text
    assert "changed_files=0" in caplog.text
    assert "candidate_count=0" in caplog.text
    assert WEBHOOK_SECRET not in caplog.text
    assert token_provider.requested_installation_ids == [123456]
    assert inspector.requests == [("octo-org/change-echo-test", 42, 100)]
    assert discoverer.requests == [("octo-org/change-echo-test", 42, (), 20, 40)]


@pytest.mark.asyncio
async def test_invalid_signature_is_rejected_before_payload_parsing() -> None:
    malformed_payload = b"not-json"
    headers = github_headers(malformed_payload)
    headers["X-Hub-Signature-256"] = "sha256=invalid"

    async with webhook_client() as (client, token_provider, inspector, discoverer):
        response = await client.post(
            "/webhooks/github",
            content=malformed_payload,
            headers=headers,
        )

    assert response.status_code == 401
    assert response.json() == {"detail": "Invalid webhook signature"}
    assert token_provider.requested_installation_ids == []
    assert inspector.requests == []
    assert discoverer.requests == []


@pytest.mark.asyncio
async def test_missing_signature_is_rejected(pull_request_payload: bytes) -> None:
    headers = github_headers(pull_request_payload)
    del headers["X-Hub-Signature-256"]

    async with webhook_client() as (client, token_provider, inspector, discoverer):
        response = await client.post(
            "/webhooks/github",
            content=pull_request_payload,
            headers=headers,
        )

    assert response.status_code == 401
    assert token_provider.requested_installation_ids == []
    assert inspector.requests == []
    assert discoverer.requests == []


@pytest.mark.asyncio
async def test_unsupported_event_is_ignored_without_parsing() -> None:
    malformed_payload = b"not-json"

    async with webhook_client() as (client, token_provider, inspector, discoverer):
        response = await client.post(
            "/webhooks/github",
            content=malformed_payload,
            headers=github_headers(malformed_payload, event="issues"),
        )

    assert response.status_code == 200
    assert response.json() == {"status": "ignored"}
    assert token_provider.requested_installation_ids == []
    assert inspector.requests == []
    assert discoverer.requests == []


@pytest.mark.asyncio
async def test_unsupported_pull_request_action_is_ignored(
    pull_request_payload: bytes,
) -> None:
    payload_data = json.loads(pull_request_payload)
    payload_data["action"] = "closed"
    payload = json.dumps(payload_data).encode()

    async with webhook_client() as (client, token_provider, inspector, discoverer):
        response = await client.post(
            "/webhooks/github",
            content=payload,
            headers=github_headers(payload),
        )

    assert response.status_code == 200
    assert response.json() == {"status": "ignored"}
    assert token_provider.requested_installation_ids == []
    assert inspector.requests == []
    assert discoverer.requests == []


@pytest.mark.asyncio
async def test_supported_delivery_requires_pull_request_context() -> None:
    payload = b'{"action":"opened"}'

    async with webhook_client() as (client, token_provider, inspector, discoverer):
        response = await client.post(
            "/webhooks/github",
            content=payload,
            headers=github_headers(payload),
        )

    assert response.status_code == 400
    assert response.json() == {"detail": "Invalid webhook payload"}
    assert token_provider.requested_installation_ids == []
    assert inspector.requests == []
    assert discoverer.requests == []


def test_fixture_extracts_required_pull_request_context(
    pull_request_payload: bytes,
) -> None:
    payload = PullRequestWebhookPayload.model_validate_json(pull_request_payload)

    context = payload.to_context(DELIVERY_ID)

    assert context.delivery_id == DELIVERY_ID
    assert context.action == "opened"
    assert context.repository_full_name == "octo-org/change-echo-test"
    assert context.pull_request_number == 42
    assert context.head_sha == "0123456789abcdef0123456789abcdef01234567"
    assert context.installation_id == 123456


@pytest.mark.asyncio
async def test_unconfigured_receiver_fails_safely(pull_request_payload: bytes) -> None:
    async with webhook_client(secret=None) as (
        client,
        token_provider,
        inspector,
        discoverer,
    ):
        response = await client.post(
            "/webhooks/github",
            content=pull_request_payload,
            headers=github_headers(pull_request_payload),
        )

    assert response.status_code == 503
    assert response.json() == {"detail": "Webhook receiver is not configured"}
    assert token_provider.requested_installation_ids == []
    assert inspector.requests == []
    assert discoverer.requests == []


@pytest.mark.asyncio
async def test_large_pull_request_returns_explicit_skipped_result(
    pull_request_payload: bytes,
) -> None:
    result = PullRequestTooLarge(max_files=2)

    async with webhook_client(
        max_files=2,
        inspection_result=result,
    ) as (client, token_provider, inspector, discoverer):
        response = await client.post(
            "/webhooks/github",
            content=pull_request_payload,
            headers=github_headers(pull_request_payload),
        )

    assert response.status_code == 200
    assert response.json() == {
        "status": "skipped",
        "reason": "pull_request_too_large",
    }
    assert token_provider.requested_installation_ids == [123456]
    assert inspector.requests == [("octo-org/change-echo-test", 42, 2)]
    assert discoverer.requests == []


@pytest.mark.asyncio
async def test_pull_request_inspection_failure_is_reported_coherently(
    pull_request_payload: bytes,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.ERROR, logger="app.api.webhooks")
    error = GitHubNotFoundError("GitHub API resource not found", status_code=404)

    async with webhook_client(inspection_error=error) as (
        client,
        token_provider,
        inspector,
        discoverer,
    ):
        response = await client.post(
            "/webhooks/github",
            content=pull_request_payload,
            headers=github_headers(pull_request_payload),
        )

    assert response.status_code == 502
    assert response.json() == {"detail": "Pull request inspection failed"}
    assert "status=analysis_failed" in caplog.text
    assert "temporary-installation-token" not in caplog.text
    assert token_provider.requested_installation_ids == [123456]
    assert inspector.requests == [("octo-org/change-echo-test", 42, 100)]
    assert discoverer.requests == []


@pytest.mark.asyncio
async def test_candidate_discovery_failure_is_reported_coherently(
    pull_request_payload: bytes,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.ERROR, logger="app.api.webhooks")
    error = GitHubNotFoundError("GitHub API resource not found", status_code=404)

    async with webhook_client(candidate_error=error) as (
        client,
        token_provider,
        inspector,
        discoverer,
    ):
        response = await client.post(
            "/webhooks/github",
            content=pull_request_payload,
            headers=github_headers(pull_request_payload),
        )

    assert response.status_code == 502
    assert response.json() == {"detail": "Historical candidate discovery failed"}
    assert "stage=candidate_discovery" in caplog.text
    assert "temporary-installation-token" not in caplog.text
    assert token_provider.requested_installation_ids == [123456]
    assert inspector.requests == [("octo-org/change-echo-test", 42, 100)]
    assert discoverer.requests == [("octo-org/change-echo-test", 42, (), 20, 40)]
