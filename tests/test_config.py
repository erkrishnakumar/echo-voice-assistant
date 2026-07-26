"""Tests for config loading and defaults."""

from echo.config import settings


def test_database_url_is_sqlite_in_tests():
    # conftest sets DATABASE_URL to sqlite
    assert settings.database_url.startswith("sqlite")


def test_defaults_present():
    assert settings.model
    assert settings.max_tool_rounds >= 1
    assert settings.timeout > 0
    assert settings.keep_alive
    assert settings.llm_retries >= 0