"""Tests for Settings: defaults, env var overrides, validation, caching."""

from pathlib import Path

import pytest
from pydantic import ValidationError

from conftest import REQUIRED_SETTINGS_FIELDS as _REQUIRED
from kb_orchestrator.config import Settings, get_settings


@pytest.fixture(autouse=True)
def _isolate_from_real_env_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """`Settings` reads a `.env` file from the current directory if one
    exists. `chdir` to an empty tmp_path sidesteps a developer's own local
    `.env` affecting test results -- same fix as Project 2's Step 2."""
    monkeypatch.chdir(tmp_path)


def test_defaults() -> None:
    settings = Settings(**_REQUIRED)
    assert settings.environment == "development"
    assert settings.log_level == "INFO"


def test_missing_required_fields_raise() -> None:
    with pytest.raises(ValidationError):
        Settings()  # type: ignore[call-arg]


def test_kb_agent_base_url_must_be_a_valid_url() -> None:
    # Deliberately passing an invalid string where `HttpUrl` is expected,
    # to prove pydantic rejects it at runtime -- mypy's synthesized
    # constructor signature expects `HttpUrl` exactly, so this line's
    # whole point (an invalid value) is unrepresentable without the
    # ignore, same category of gap documented throughout this curriculum.
    with pytest.raises(ValidationError):
        Settings(
            kb_agent_auth_token=_REQUIRED["kb_agent_auth_token"],
            kb_agent_base_url="not-a-url",  # type: ignore[arg-type]
        )


def test_env_prefix_overrides_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("KB_ORCHESTRATOR_KB_AGENT_BASE_URL", "http://127.0.0.1:8000")
    monkeypatch.setenv("KB_ORCHESTRATOR_KB_AGENT_AUTH_TOKEN", "some-token")
    monkeypatch.setenv("KB_ORCHESTRATOR_ENVIRONMENT", "production")
    monkeypatch.setenv("KB_ORCHESTRATOR_LOG_LEVEL", "debug")

    settings = Settings()  # type: ignore[call-arg]  # see config.py's get_settings()

    assert settings.environment == "production"
    assert settings.log_level == "DEBUG"  # validator upper-cases it


def test_unprefixed_env_vars_are_ignored(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LOG_LEVEL", "DEBUG")  # no KB_ORCHESTRATOR_ prefix -> not ours

    settings = Settings(**_REQUIRED)

    assert settings.log_level == "INFO"


def test_kb_agent_auth_token_is_not_in_repr_or_str() -> None:
    settings = Settings(**_REQUIRED)
    assert "test-token" not in repr(settings)
    assert "test-token" not in str(settings)


def test_settings_is_frozen() -> None:
    settings = Settings(**_REQUIRED)
    with pytest.raises(ValidationError):
        settings.log_level = "DEBUG"


def test_get_settings_is_cached(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("KB_ORCHESTRATOR_KB_AGENT_BASE_URL", "http://127.0.0.1:8000")
    monkeypatch.setenv("KB_ORCHESTRATOR_KB_AGENT_AUTH_TOKEN", "some-token")
    get_settings.cache_clear()
    assert get_settings() is get_settings()
