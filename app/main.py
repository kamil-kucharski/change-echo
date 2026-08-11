import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI

from app.api.health import router as health_router
from app.api.webhooks import router as webhook_router
from app.config import Settings
from app.github.auth import GitHubAppAuthenticator, InstallationTokenProvider
from app.github.client import DEFAULT_TIMEOUT_SECONDS, GitHubClient
from app.services.candidate_discovery import (
    CandidateDiscoverer,
    GitHubCandidateDiscoverer,
)
from app.services.pull_request_inspection import (
    GitHubPullRequestInspector,
    PullRequestInspector,
)


def create_app(
    app_settings: Settings | None = None,
    installation_token_provider: InstallationTokenProvider | None = None,
    pull_request_inspector: PullRequestInspector | None = None,
    candidate_discoverer: CandidateDiscoverer | None = None,
) -> FastAPI:
    resolved_settings = app_settings if app_settings is not None else Settings()
    logging.getLogger("app").setLevel(resolved_settings.log_level.upper())

    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        if (
            installation_token_provider is not None
            and pull_request_inspector is not None
            and candidate_discoverer is not None
        ):
            yield
            return

        async with httpx.AsyncClient() as http_client:
            github_client = GitHubClient(
                http_client=http_client,
                base_url=resolved_settings.github_api_base_url,
                api_version=resolved_settings.github_api_version,
                timeout_seconds=DEFAULT_TIMEOUT_SECONDS,
            )
            if installation_token_provider is None:
                application.state.installation_token_provider = GitHubAppAuthenticator(
                    client=github_client,
                    app_id=resolved_settings.github_app_id,
                    private_key_path=resolved_settings.github_private_key_path,
                )
            if pull_request_inspector is None:
                application.state.pull_request_inspector = GitHubPullRequestInspector(
                    github_client
                )
            if candidate_discoverer is None:
                application.state.candidate_discoverer = GitHubCandidateDiscoverer(
                    github_client
                )
            yield

    application = FastAPI(
        title="Change Echo",
        debug=resolved_settings.app_env == "development",
        lifespan=lifespan,
    )
    application.state.settings = resolved_settings
    if installation_token_provider is not None:
        application.state.installation_token_provider = installation_token_provider
    if pull_request_inspector is not None:
        application.state.pull_request_inspector = pull_request_inspector
    if candidate_discoverer is not None:
        application.state.candidate_discoverer = candidate_discoverer
    application.include_router(health_router)
    application.include_router(webhook_router)
    return application


app = create_app()
