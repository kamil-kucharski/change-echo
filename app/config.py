from pathlib import Path
from typing import Annotated

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_ignore_empty=True,
        extra="ignore",
    )

    app_env: str = "development"
    log_level: str = "INFO"
    github_app_id: Annotated[int, Field(gt=0)] | None = None
    github_private_key_path: Path | None = None
    github_webhook_secret: Annotated[SecretStr, Field(min_length=1)] | None = None
    github_api_base_url: str = "https://api.github.com"
    github_api_version: str = "2026-03-10"
    echo_max_current_files: Annotated[int, Field(gt=0, le=2999)] = 100
