import logging
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import ValidationError

from app.config import Settings
from app.github.models import (
    SUPPORTED_PULL_REQUEST_ACTIONS,
    PullRequestActionEnvelope,
    PullRequestWebhookPayload,
)
from app.github.signatures import verify_webhook_signature

logger = logging.getLogger(__name__)
router = APIRouter()


def settings_from_request(request: Request) -> Settings:
    app_settings: Settings = request.app.state.settings
    return app_settings


@router.post("/webhooks/github")
async def receive_github_webhook(
    request: Request,
    app_settings: Annotated[Settings, Depends(settings_from_request)],
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
    logger.info(
        "github_webhook status=accepted delivery_id=%r event=%r action=%r "
        "repository=%r pr_number=%d",
        context.delivery_id,
        context.event,
        context.action,
        context.repository_full_name,
        context.pull_request_number,
    )
    return {"status": "accepted"}
