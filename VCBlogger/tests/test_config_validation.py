"""Configuration validation regression tests for deployment failures."""
import pytest

from VCBlogger import config


def _set_valid_telegram_credentials(monkeypatch):
    monkeypatch.setattr(config, "BOT_TOKEN", "12345:valid-test-token")
    monkeypatch.setattr(config, "API_ID", 12345)
    monkeypatch.setattr(config, "API_HASH", "valid-test-hash")
    monkeypatch.setattr(config, "SESSION_STRING", "valid-test-user-session")
    monkeypatch.setattr(config, "CONFIG_ERRORS", [])


def test_invalid_mongodb_uri_fails_early_with_actionable_message(monkeypatch):
    _set_valid_telegram_credentials(monkeypatch)
    monkeypatch.setattr(config, "DB_PROVIDER", "auto")
    monkeypatch.setattr(config, "MONGO_URI", "cluster.example.mongodb.net/db")
    monkeypatch.setattr(config, "POSTGRES_ARCHIVE_URL", "")
    monkeypatch.setattr(config, "REDIS_URL", "")

    with pytest.raises(RuntimeError, match=r"MONGO_URI must start with mongodb:// or mongodb\+srv://"):
        config.validate_required_config()


def test_valid_mongodb_uri_passes_scheme_validation(monkeypatch):
    _set_valid_telegram_credentials(monkeypatch)
    monkeypatch.setattr(config, "DB_PROVIDER", "auto")
    monkeypatch.setattr(config, "MONGO_URI", "mongodb+srv://user:pass@cluster.example.mongodb.net/vcblogger")
    monkeypatch.setattr(config, "POSTGRES_ARCHIVE_URL", "")
    monkeypatch.setattr(config, "REDIS_URL", "")

    config.validate_required_config()


def test_bot_config_does_not_require_assistant_session(monkeypatch):
    _set_valid_telegram_credentials(monkeypatch)
    monkeypatch.setattr(config, "SESSION_STRING", "")
    monkeypatch.setattr(config, "USER_SESSION_STRINGS", [])
    monkeypatch.setattr(config, "DB_PROVIDER", "auto")
    monkeypatch.setattr(config, "MONGO_URI", "mongodb+srv://user:pass@cluster.example.mongodb.net/vcblogger")
    monkeypatch.setattr(config, "POSTGRES_ARCHIVE_URL", "")
    monkeypatch.setattr(config, "REDIS_URL", "")

    # The bot can answer commands without an Assistant; detailed VC participant
    # monitoring is reported as unavailable by the application startup.
    config.validate_required_config()
