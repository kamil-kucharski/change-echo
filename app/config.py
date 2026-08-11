from pathlib import Path
from typing import Annotated, Self

from pydantic import Field, SecretStr, model_validator
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
    echo_max_commits_per_path: Annotated[int, Field(gt=0, le=100)] = 20
    echo_max_unique_candidates: Annotated[int, Field(gt=0)] = 40
    echo_max_results: Annotated[int, Field(gt=0)] = 3
    echo_possible_threshold: Annotated[float, Field(ge=0.0, le=1.0)] = 0.55
    echo_strong_threshold: Annotated[float, Field(ge=0.0, le=1.0)] = 0.72

    @model_validator(mode="after")
    def validate_echo_thresholds(self) -> Self:
        if self.echo_possible_threshold > self.echo_strong_threshold:
            raise ValueError(
                "ECHO_POSSIBLE_THRESHOLD must not exceed ECHO_STRONG_THRESHOLD"
            )
        return self
