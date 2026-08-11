import logging

from fastapi import FastAPI

from app.api.health import router as health_router
from app.api.webhooks import router as webhook_router
from app.config import Settings


def create_app(app_settings: Settings | None = None) -> FastAPI:
    resolved_settings = app_settings if app_settings is not None else Settings()
    logging.getLogger("app").setLevel(resolved_settings.log_level.upper())

    application = FastAPI(
        title="Change Echo",
        debug=resolved_settings.app_env == "development",
    )
    application.state.settings = resolved_settings
    application.include_router(health_router)
    application.include_router(webhook_router)
    return application


app = create_app()
