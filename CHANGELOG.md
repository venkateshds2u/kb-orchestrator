# Changelog

All notable changes to this project are documented here.
Format loosely follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

### Step 2 — Config + logging
- `config.py`: `Settings` with `KB_ORCHESTRATOR_`-prefixed env vars,
  required `kb_agent_base_url` (`HttpUrl` -- real URL validation at
  startup, not just a string) and `kb_agent_auth_token` (`SecretStr` --
  the bearer token this app authenticates *as a client* to kb-agent's own
  HTTP API), `get_settings()` cached factory. No workflow-specific
  settings yet (retry policy, HITL, tracing, this app's own HTTP API host/
  port) -- each deferred to its own step, same precedent as Project 2's
  Step 8/11 settings arriving with their steps rather than upfront.
- `logging.py`: same structlog+stdlib bridge pattern as both prior
  projects, but writes to **stdout** -- a third, independently-reasoned
  choice, not copied from either. kb-mcp-server uses stdout because
  nothing else needs that descriptor (its real stdout use, the MCP wire
  protocol, is diverted elsewhere by the SDK); kb-agent uses stderr
  specifically because its own stdout is claimed by an interactive CLI
  (Step 10). kb-orchestrator has neither conflict -- no wire protocol, no
  CLI -- so stdout, the conventional destination for a plain backend
  service (12-factor: write logs to stdout, let the runtime collect them),
  is the correct default here.
- `tests/conftest.py`: shared `REQUIRED_SETTINGS_FIELDS` TypedDict (using
  `HttpUrl`/`SecretStr`, not plain `str` -- mypy's synthesized constructor
  expects each field's declared type exactly, same gap Project 2's own
  conftest.py documents) + global state reset fixture.
- 13 tests, `mypy --strict`/`ruff` clean, pre-commit passing on the first
  run (the pre-populated `additional_dependencies` from Step 1 paid off,
  same as both prior projects' Step 2s).

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
