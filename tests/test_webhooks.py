import hashlib
import hmac
import json
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from app.config import Settings
from app.github.models import PullRequestWebhookPayload
from app.main import create_app

WEBHOOK_SECRET = "signed-fixture-secret"
DELIVERY_ID = "00000000-0000-4000-8000-000000000001"
FIXTURE_PATH = Path(__file__).parent / "fixtures" / "pull_request_opened.json"


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
) -> AsyncIterator[AsyncClient]:
    settings = Settings(_env_file=None, github_webhook_secret=secret)
    application = create_app(settings)
    transport = ASGITransport(app=application)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


@pytest.fixture
def pull_request_payload() -> bytes:
    return FIXTURE_PATH.read_bytes()


@pytest.mark.asyncio
async def test_valid_supported_delivery_is_accepted_and_logged(
    pull_request_payload: bytes,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO, logger="app.api.webhooks")

    async with webhook_client() as client:
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
    assert WEBHOOK_SECRET not in caplog.text


@pytest.mark.asyncio
async def test_invalid_signature_is_rejected_before_payload_parsing() -> None:
    malformed_payload = b"not-json"
    headers = github_headers(malformed_payload)
    headers["X-Hub-Signature-256"] = "sha256=invalid"

    async with webhook_client() as client:
        response = await client.post(
            "/webhooks/github",
            content=malformed_payload,
            headers=headers,
        )

    assert response.status_code == 401
    assert response.json() == {"detail": "Invalid webhook signature"}


@pytest.mark.asyncio
async def test_missing_signature_is_rejected(pull_request_payload: bytes) -> None:
    headers = github_headers(pull_request_payload)
    del headers["X-Hub-Signature-256"]

    async with webhook_client() as client:
        response = await client.post(
            "/webhooks/github",
            content=pull_request_payload,
            headers=headers,
        )

    assert response.status_code == 401


@pytest.mark.asyncio
async def test_unsupported_event_is_ignored_without_parsing() -> None:
    malformed_payload = b"not-json"

    async with webhook_client() as client:
        response = await client.post(
            "/webhooks/github",
            content=malformed_payload,
            headers=github_headers(malformed_payload, event="issues"),
        )

    assert response.status_code == 200
    assert response.json() == {"status": "ignored"}


@pytest.mark.asyncio
async def test_unsupported_pull_request_action_is_ignored(
    pull_request_payload: bytes,
) -> None:
    payload_data = json.loads(pull_request_payload)
    payload_data["action"] = "closed"
    payload = json.dumps(payload_data).encode()

    async with webhook_client() as client:
        response = await client.post(
            "/webhooks/github",
            content=payload,
            headers=github_headers(payload),
        )

    assert response.status_code == 200
    assert response.json() == {"status": "ignored"}


@pytest.mark.asyncio
async def test_supported_delivery_requires_pull_request_context() -> None:
    payload = b'{"action":"opened"}'

    async with webhook_client() as client:
        response = await client.post(
            "/webhooks/github",
            content=payload,
            headers=github_headers(payload),
        )

    assert response.status_code == 400
    assert response.json() == {"detail": "Invalid webhook payload"}


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
    async with webhook_client(secret=None) as client:
        response = await client.post(
            "/webhooks/github",
            content=pull_request_payload,
            headers=github_headers(pull_request_payload),
        )

    assert response.status_code == 503
    assert response.json() == {"detail": "Webhook receiver is not configured"}
