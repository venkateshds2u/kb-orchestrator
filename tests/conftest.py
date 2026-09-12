"""Shared pytest fixtures and test data."""

import logging
from collections.abc import Generator
from typing import TypedDict

import pytest
import structlog
from pydantic import HttpUrl, SecretStr

from kb_orchestrator.config import get_settings


class RequiredSettingsFields(TypedDict):
    """`Settings`'s two fields with no default. Typed (not `dict[str,
    object]`) so `Settings(**REQUIRED_SETTINGS_FIELDS, ...)` type-checks
    precisely against the real constructor -- mypy can't verify a loosely
    typed dict splat against specific parameters, but it can verify a
    TypedDict's. `HttpUrl`/`SecretStr`, not plain `str`: pydantic accepts a
    plain `str` at runtime and coerces it, but mypy's synthesized
    constructor signature expects each field's declared type exactly (same
    gap documented in Project 2's own conftest.py)."""

    kb_agent_base_url: HttpUrl
    kb_agent_auth_token: SecretStr


REQUIRED_SETTINGS_FIELDS: RequiredSettingsFields = {
    "kb_agent_base_url": HttpUrl("http://127.0.0.1:8000"),
    "kb_agent_auth_token": SecretStr("test-token"),
}


@pytest.fixture(autouse=True)
def _reset_global_state() -> Generator[None, None, None]:
    """Logging config and the settings cache are process-global.

    Reset them after every test so one test's `configure_logging()` call or
    monkeypatched env vars can't leak into the next test.
    """
    yield
    structlog.reset_defaults()
    logging.getLogger().handlers = []
    get_settings.cache_clear()
