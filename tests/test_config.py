from app.config import Settings


def test_settings_have_safe_defaults(monkeypatch) -> None:
    monkeypatch.delenv("APP_ENV", raising=False)
    monkeypatch.delenv("LOG_LEVEL", raising=False)
    monkeypatch.delenv("GITHUB_APP_ID", raising=False)
    monkeypatch.delenv("GITHUB_PRIVATE_KEY_PATH", raising=False)
    monkeypatch.delenv("GITHUB_WEBHOOK_SECRET", raising=False)
    monkeypatch.delenv("GITHUB_API_BASE_URL", raising=False)
    monkeypatch.delenv("GITHUB_API_VERSION", raising=False)
    monkeypatch.delenv("ECHO_MAX_CURRENT_FILES", raising=False)
    monkeypatch.delenv("ECHO_MAX_COMMITS_PER_PATH", raising=False)
    monkeypatch.delenv("ECHO_MAX_UNIQUE_CANDIDATES", raising=False)

    settings = Settings(_env_file=None)

    assert settings.app_env == "development"
    assert settings.log_level == "INFO"
    assert settings.github_app_id is None
    assert settings.github_private_key_path is None
    assert settings.github_webhook_secret is None
    assert settings.github_api_base_url == "https://api.github.com"
    assert settings.github_api_version == "2026-03-10"
    assert settings.echo_max_current_files == 100
    assert settings.echo_max_commits_per_path == 20
    assert settings.echo_max_unique_candidates == 40


def test_settings_read_environment(monkeypatch) -> None:
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("LOG_LEVEL", "DEBUG")
    monkeypatch.setenv("GITHUB_APP_ID", "123456")
    monkeypatch.setenv("GITHUB_PRIVATE_KEY_PATH", "secrets/github-app.pem")
    monkeypatch.setenv("GITHUB_WEBHOOK_SECRET", "local-test-secret")
    monkeypatch.setenv("GITHUB_API_BASE_URL", "https://github.example/api/v3")
    monkeypatch.setenv("GITHUB_API_VERSION", "2026-03-10")
    monkeypatch.setenv("ECHO_MAX_CURRENT_FILES", "250")
    monkeypatch.setenv("ECHO_MAX_COMMITS_PER_PATH", "25")
    monkeypatch.setenv("ECHO_MAX_UNIQUE_CANDIDATES", "50")

    settings = Settings(_env_file=None)

    assert settings.app_env == "test"
    assert settings.log_level == "DEBUG"
    assert settings.github_app_id == 123456
    assert settings.github_private_key_path is not None
    assert settings.github_private_key_path.as_posix() == "secrets/github-app.pem"
    assert settings.github_webhook_secret is not None
    assert settings.github_webhook_secret.get_secret_value() == "local-test-secret"
    assert settings.github_api_base_url == "https://github.example/api/v3"
    assert settings.github_api_version == "2026-03-10"
    assert settings.echo_max_current_files == 250
    assert settings.echo_max_commits_per_path == 25
    assert settings.echo_max_unique_candidates == 50
