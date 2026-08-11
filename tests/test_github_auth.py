from datetime import UTC, datetime

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from app.github.auth import (
    GitHubAppAuthenticator,
    GitHubConfigurationError,
    generate_app_jwt,
)
from app.github.client import GitHubClient


def generate_test_key_pair() -> tuple[bytes, bytes]:
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private_key_bytes = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    public_key_bytes = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    return private_key_bytes, public_key_bytes


def test_app_jwt_uses_rs256_and_conservative_timestamps() -> None:
    private_key, public_key = generate_test_key_pair()
    now = datetime(2026, 8, 11, 12, 0, tzinfo=UTC)

    token = generate_app_jwt(123456, private_key, now=now)

    header = jwt.get_unverified_header(token)
    claims = jwt.decode(
        token,
        public_key,
        algorithms=["RS256"],
        options={"verify_exp": False, "verify_iat": False},
    )
    assert header["alg"] == "RS256"
    assert claims["iss"] == "123456"
    assert claims["iat"] == int(now.timestamp()) - 60
    assert claims["exp"] == int(now.timestamp()) + (9 * 60)


@pytest.mark.asyncio
async def test_installation_id_drives_access_token_request(tmp_path) -> None:
    private_key, _ = generate_test_key_pair()
    private_key_path = tmp_path / "github-app-private-key.pem"
    private_key_path.write_bytes(private_key)
    captured_requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured_requests.append(request)
        return httpx.Response(
            201,
            json={
                "token": "ghs_123456_opaque.jwt.value",
                "expires_at": "2026-08-11T13:00:00Z",
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        client = GitHubClient(
            http_client,
            base_url="https://api.github.test",
            api_version="2026-03-10",
        )
        authenticator = GitHubAppAuthenticator(
            client,
            app_id=123456,
            private_key_path=private_key_path,
        )

        access_token = await authenticator.get_installation_access_token(987654)

    assert len(captured_requests) == 1
    request = captured_requests[0]
    assert request.method == "POST"
    assert request.url.path == "/app/installations/987654/access_tokens"
    assert request.headers["Authorization"].startswith("Bearer ")
    assert access_token.token.get_secret_value() == "ghs_123456_opaque.jwt.value"
    assert "ghs_123456_opaque.jwt.value" not in repr(access_token)


@pytest.mark.asyncio
async def test_missing_app_credentials_fail_without_http_request() -> None:
    requests_made = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal requests_made
        requests_made += 1
        return httpx.Response(500, request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        client = GitHubClient(
            http_client,
            base_url="https://api.github.test",
            api_version="2026-03-10",
        )
        authenticator = GitHubAppAuthenticator(client, None, None)

        with pytest.raises(GitHubConfigurationError):
            await authenticator.get_installation_access_token(987654)

    assert requests_made == 0
