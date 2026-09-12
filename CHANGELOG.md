# Changelog

All notable changes to this project are documented here.
Format loosely follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

### Step 1 — Repo scaffold, tooling, CI
- `pyproject.toml`: project metadata, runtime deps (`httpx`, `pydantic`,
  `pydantic-settings`, `structlog`, `aiosqlite`, `starlette`, `uvicorn`),
  dev deps (`ruff`, `mypy`, `pytest`, `pytest-asyncio`, `pre-commit`). No
  `anthropic`/`mcp` here at all -- Step 0's confirmed design has this
  project talking to kb-agent purely over HTTP, never touching MCP or the
  Anthropic API directly.
- `src/kb_orchestrator/` package (src layout) with `__version__`.
- `tests/test_packaging.py`: smoke test validating the package imports.
- `.python-version` pinned to 3.12 (uv-managed) and committed from the
  start -- Project 1's Step 1 mistake (gitignoring it, fixing it in Step
  3), not repeated a third time.
- `.pre-commit-config.yaml`'s mypy hook pre-populated with the full known
  dependency set up front, same proactive fix Project 2 applied after
  Project 1 hit "pre-commit's isolated mypy env is missing a dependency"
  three separate times.
- `.gitignore` includes SQLite-specific entries (`data/`, `*.sqlite3`,
  etc.) from the start, unlike Project 2's (which had no database of its
  own) -- this project persists workflow state to SQLite from Step 4, so
  the entries are added proactively rather than after an accidental commit
  (Project 1's own live mistake).
- `.github/workflows/ci.yml`: lint, format check, type check, test on push/PR.
