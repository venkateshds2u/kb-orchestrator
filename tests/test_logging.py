"""Tests for configure_logging.

Asserts against `capsys.readouterr().out`, not `.err`: this app logs to
stdout (see logging.py's module docstring for why -- a genuinely
independent choice from both kb-mcp-server's and kb-agent's own logging
destinations, not copied from either).
"""

import json
import logging

import pytest
import structlog

from conftest import REQUIRED_SETTINGS_FIELDS as _REQUIRED
from kb_orchestrator.config import Settings
from kb_orchestrator.logging import configure_logging


def test_production_renders_json_on_stdout(capsys: pytest.CaptureFixture[str]) -> None:
    configure_logging(Settings(**_REQUIRED, environment="production"))

    structlog.get_logger("kb_orchestrator.test").info("workflow_started", iteration=1)

    captured = capsys.readouterr()
    assert captured.err == ""
    line = captured.out.strip().splitlines()[-1]
    payload = json.loads(line)
    assert payload["event"] == "workflow_started"
    assert payload["iteration"] == 1
    assert payload["level"] == "info"


def test_development_renders_human_readable_on_stdout(capsys: pytest.CaptureFixture[str]) -> None:
    configure_logging(Settings(**_REQUIRED, environment="development"))

    structlog.get_logger("kb_orchestrator.test").info("workflow_started")

    captured = capsys.readouterr()
    assert captured.err == ""
    assert "workflow_started" in captured.out


def test_log_level_filters_below_threshold(capsys: pytest.CaptureFixture[str]) -> None:
    configure_logging(Settings(**_REQUIRED, environment="production", log_level="WARNING"))

    structlog.get_logger("kb_orchestrator.test").info("should_be_filtered")

    assert capsys.readouterr().out == ""


def test_stdlib_logging_goes_through_same_pipeline(capsys: pytest.CaptureFixture[str]) -> None:
    configure_logging(Settings(**_REQUIRED, environment="production"))

    logging.getLogger("some_third_party_lib").warning("connection retrying")

    line = capsys.readouterr().out.strip().splitlines()[-1]
    payload = json.loads(line)
    assert payload["event"] == "connection retrying"
    assert payload["level"] == "warning"
