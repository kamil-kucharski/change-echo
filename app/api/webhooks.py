import logging
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import ValidationError

from app.config import Settings
from app.github.auth import GitHubConfigurationError, InstallationTokenProvider
from app.github.client import (
    GitHubAPIError,
    GitHubNetworkError,
    GitHubRateLimitError,
    GitHubServerError,
    GitHubTimeoutError,
)
from app.github.models import (
    SUPPORTED_PULL_REQUEST_ACTIONS,
    PullRequestActionEnvelope,
    PullRequestWebhookPayload,
)
from app.github.signatures import verify_webhook_signature
from app.services.candidate_discovery import CandidateDiscoverer
from app.services.pull_request_inspection import (
    CompletePullRequestInspection,
    PullRequestInspector,
    PullRequestTooLarge,
)

logger = logging.getLogger(__name__)
router = APIRouter()


def settings_from_request(request: Request) -> Settings:
    app_settings: Settings = request.app.state.settings
    return app_settings


def installation_token_provider_from_request(
    request: Request,
) -> InstallationTokenProvider:
    provider: InstallationTokenProvider = request.app.state.installation_token_provider
    return provider


def pull_request_inspector_from_request(request: Request) -> PullRequestInspector:
    inspector: PullRequestInspector = request.app.state.pull_request_inspector
    return inspector


def candidate_discoverer_from_request(request: Request) -> CandidateDiscoverer:
    discoverer: CandidateDiscoverer = request.app.state.candidate_discoverer
    return discoverer


@router.post("/webhooks/github")
async def receive_github_webhook(
    request: Request,
    app_settings: Annotated[Settings, Depends(settings_from_request)],
    token_provider: Annotated[
        InstallationTokenProvider,
        Depends(installation_token_provider_from_request),
    ],
    pull_request_inspector: Annotated[
        PullRequestInspector,
        Depends(pull_request_inspector_from_request),
    ],
    candidate_discoverer: Annotated[
        CandidateDiscoverer,
        Depends(candidate_discoverer_from_request),
    ],
) -> dict[str, str]:
    raw_body = await request.body()
    webhook_secret = app_settings.github_webhook_secret

    if webhook_secret is None:
        logger.error("github_webhook status=configuration_error")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Webhook receiver is not configured",
        )

    signature = request.headers.get("X-Hub-Signature-256")
    if not verify_webhook_signature(
        raw_body,
        signature,
        webhook_secret.get_secret_value(),
    ):
        logger.warning("github_webhook status=rejected reason=invalid_signature")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid webhook signature",
        )

    event = request.headers.get("X-GitHub-Event")
    delivery_id = request.headers.get("X-GitHub-Delivery")
    if not event or not delivery_id:
        logger.warning(
            "github_webhook status=rejected reason=missing_headers "
            "delivery_id=%r event=%r",
            delivery_id,
            event,
        )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Missing required GitHub headers",
        )

    if event != "pull_request":
        logger.info(
            "github_webhook status=ignored delivery_id=%r event=%r",
            delivery_id,
            event,
        )
        return {"status": "ignored"}

    try:
        envelope = PullRequestActionEnvelope.model_validate_json(raw_body)
    except ValidationError:
        logger.warning(
            "github_webhook status=rejected reason=invalid_payload delivery_id=%r "
            "event=%r",
            delivery_id,
            event,
        )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid webhook payload",
        ) from None

    if envelope.action not in SUPPORTED_PULL_REQUEST_ACTIONS:
        logger.info(
            "github_webhook status=ignored delivery_id=%r event=%r action=%r",
            delivery_id,
            event,
            envelope.action,
        )
        return {"status": "ignored"}

    try:
        payload = PullRequestWebhookPayload.model_validate_json(raw_body)
    except ValidationError:
        logger.warning(
            "github_webhook status=rejected reason=invalid_payload delivery_id=%r "
            "event=%r action=%r",
            delivery_id,
            event,
            envelope.action,
        )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid webhook payload",
        ) from None

    context = payload.to_context(delivery_id)
    try:
        access_token = await token_provider.get_installation_access_token(
            context.installation_id
        )
    except GitHubConfigurationError:
        logger.error(
            "github_webhook status=authentication_failed reason=configuration "
            "delivery_id=%r repository=%r pr_number=%d",
            context.delivery_id,
            context.repository_full_name,
            context.pull_request_number,
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="GitHub App authentication is not configured",
        ) from None
    except (
        GitHubNetworkError,
        GitHubRateLimitError,
        GitHubServerError,
        GitHubTimeoutError,
    ) as error:
        logger.error(
            "github_webhook status=authentication_failed error_type=%s "
            "github_status=%r delivery_id=%r repository=%r pr_number=%d",
            type(error).__name__,
            error.status_code,
            context.delivery_id,
            context.repository_full_name,
            context.pull_request_number,
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="GitHub authentication is temporarily unavailable",
        ) from None
    except GitHubAPIError as error:
        logger.error(
            "github_webhook status=authentication_failed error_type=%s "
            "github_status=%r delivery_id=%r repository=%r pr_number=%d",
            type(error).__name__,
            error.status_code,
            context.delivery_id,
            context.repository_full_name,
            context.pull_request_number,
        )
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="GitHub authentication failed",
        ) from None

    try:
        inspection = await pull_request_inspector.inspect(
            repository_full_name=context.repository_full_name,
            pull_request_number=context.pull_request_number,
            installation_token=access_token.token,
            max_files=app_settings.echo_max_current_files,
        )
    except (
        GitHubNetworkError,
        GitHubRateLimitError,
        GitHubServerError,
        GitHubTimeoutError,
    ) as error:
        logger.error(
            "github_webhook status=analysis_failed error_type=%s "
            "github_status=%r delivery_id=%r repository=%r pr_number=%d",
            type(error).__name__,
            error.status_code,
            context.delivery_id,
            context.repository_full_name,
            context.pull_request_number,
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Pull request inspection is temporarily unavailable",
        ) from None
    except GitHubAPIError as error:
        logger.error(
            "github_webhook status=analysis_failed error_type=%s "
            "github_status=%r delivery_id=%r repository=%r pr_number=%d",
            type(error).__name__,
            error.status_code,
            context.delivery_id,
            context.repository_full_name,
            context.pull_request_number,
        )
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Pull request inspection failed",
        ) from None

    if isinstance(inspection, PullRequestTooLarge):
        logger.info(
            "github_webhook status=analysis_skipped reason=pull_request_too_large "
            "delivery_id=%r repository=%r pr_number=%d max_current_files=%d",
            context.delivery_id,
            context.repository_full_name,
            context.pull_request_number,
            inspection.max_files,
        )
        return {"status": "skipped", "reason": "pull_request_too_large"}

    if not isinstance(inspection, CompletePullRequestInspection):
        raise RuntimeError("Unexpected pull request inspection result")

    try:
        candidates = await candidate_discoverer.discover(
            repository_full_name=context.repository_full_name,
            current_pull_request_number=context.pull_request_number,
            current_file_paths=tuple(file.filename for file in inspection.files),
            installation_token=access_token.token,
            max_commits_per_path=app_settings.echo_max_commits_per_path,
            max_unique_candidates=app_settings.echo_max_unique_candidates,
        )
    except (
        GitHubNetworkError,
        GitHubRateLimitError,
        GitHubServerError,
        GitHubTimeoutError,
    ) as error:
        logger.error(
            "github_webhook status=analysis_failed stage=candidate_discovery "
            "error_type=%s github_status=%r delivery_id=%r repository=%r "
            "pr_number=%d",
            type(error).__name__,
            error.status_code,
            context.delivery_id,
            context.repository_full_name,
            context.pull_request_number,
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Historical candidate discovery is temporarily unavailable",
        ) from None
    except GitHubAPIError as error:
        logger.error(
            "github_webhook status=analysis_failed stage=candidate_discovery "
            "error_type=%s github_status=%r delivery_id=%r repository=%r "
            "pr_number=%d",
            type(error).__name__,
            error.status_code,
            context.delivery_id,
            context.repository_full_name,
            context.pull_request_number,
        )
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Historical candidate discovery failed",
        ) from None

    logger.info(
        "github_webhook status=accepted delivery_id=%r event=%r action=%r "
        "repository=%r pr_number=%d changed_files=%d candidate_count=%d",
        context.delivery_id,
        context.event,
        context.action,
        context.repository_full_name,
        context.pull_request_number,
        len(inspection.files),
        len(candidates),
    )
    return {"status": "accepted"}
