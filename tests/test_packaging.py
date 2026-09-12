"""Smoke test: proves the package installs and imports correctly.

This exists to validate the pipeline (uv sync -> pytest -> CI), not
application logic. Later steps add real behavioral tests alongside their
code.
"""

from kb_orchestrator import __version__


def test_version_is_set() -> None:
    assert __version__ == "0.1.0"
