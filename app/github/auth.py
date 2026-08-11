from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Protocol

import jwt
from pydantic import BaseModel, Field, SecretStr, ValidationError

from app.github.client import GitHubClient, GitHubResponseError

JWT_CLOCK_SKEW = timedelta(seconds=60)
JWT_LIFETIME = timedelta(minutes=9)


class GitHubConfigurationError(Exception):
    pass


class InstallationAccessToken(BaseModel):
    token: SecretStr = Field(min_length=1)
    expires_at: datetime


class InstallationTokenProvider(Protocol):
    async def get_installation_access_token(
        self,
        installation_id: int,
    ) -> InstallationAccessToken: ...


def generate_app_jwt(
    app_id: int,
    private_key: str | bytes,
    now: datetime | None = None,
) -> str:
    issued_at = now if now is not None else datetime.now(UTC)
    if issued_at.tzinfo is None:
        raise ValueError("JWT timestamp must include timezone information")

    payload = {
        "iat": int((issued_at - JWT_CLOCK_SKEW).timestamp()),
        "exp": int((issued_at + JWT_LIFETIME).timestamp()),
        "iss": str(app_id),
    }
    return jwt.encode(payload, private_key, algorithm="RS256")


class GitHubAppAuthenticator:
    def __init__(
        self,
        client: GitHubClient,
        app_id: int | None,
        private_key_path: Path | None,
    ) -> None:
        self._client = client
        self._app_id = app_id
        self._private_key_path = private_key_path

    async def get_installation_access_token(
        self,
        installation_id: int,
    ) -> InstallationAccessToken:
        if self._app_id is None or self._private_key_path is None:
            raise GitHubConfigurationError(
                "GitHub App authentication is not configured"
            )
        if installation_id <= 0:
            raise ValueError("installation_id must be greater than zero")

        try:
            private_key = self._private_key_path.read_bytes()
            app_jwt = generate_app_jwt(self._app_id, private_key)
        except (OSError, jwt.PyJWTError, ValueError, TypeError) as error:
            raise GitHubConfigurationError(
                "GitHub App private key could not be used"
            ) from error

        response = await self._client.request(
            "POST",
            f"/app/installations/{installation_id}/access_tokens",
            app_jwt,
        )
        try:
            return InstallationAccessToken.model_validate(response.json())
        except (ValueError, ValidationError) as error:
            raise GitHubResponseError(
                "GitHub returned an invalid installation token response",
                status_code=response.status_code,
            ) from error
