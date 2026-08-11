from fastapi import FastAPI

from app.api.health import router as health_router
from app.config import Settings

settings = Settings()

app = FastAPI(
    title="Change Echo",
    debug=settings.app_env == "development",
)
app.include_router(health_router)
