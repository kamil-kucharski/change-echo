from app.config import Settings


def test_settings_have_safe_defaults(monkeypatch) -> None:
    monkeypatch.delenv("APP_ENV", raising=False)
    monkeypatch.delenv("LOG_LEVEL", raising=False)
    monkeypatch.delenv("GITHUB_WEBHOOK_SECRET", raising=False)

    settings = Settings(_env_file=None)

    assert settings.app_env == "development"
    assert settings.log_level == "INFO"
    assert settings.github_webhook_secret is None


def test_settings_read_environment(monkeypatch) -> None:
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("LOG_LEVEL", "DEBUG")
    monkeypatch.setenv("GITHUB_WEBHOOK_SECRET", "local-test-secret")

    settings = Settings(_env_file=None)

    assert settings.app_env == "test"
    assert settings.log_level == "DEBUG"
    assert settings.github_webhook_secret is not None
    assert settings.github_webhook_secret.get_secret_value() == "local-test-secret"
