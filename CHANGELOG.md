# Changelog

All notable changes to this project are documented here.
Format loosely follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

### Step 5 — Single-step execution
- `agent_client.py`: `AgentClient` (a `Protocol`, same interface-
  segregation pattern as Project 2's `ToolProvider`), `HttpAgentClient`
  (the real implementation, over `httpx.AsyncClient`), and this app's own
  wire-facing pydantic models (`AgentChatResult`, `AgentToolCall`,
  `AgentExecutionFailure`) mirroring kb-agent's `ChatResponseBody` --
  not a reuse of kb-agent's actual classes across the package boundary,
  since the only real contract between these two apps is the HTTP wire
  format, not shared Python types.
- No `history` parameter on `send_message`: every call is a fresh,
  standalone request. Chaining context between workflow steps (Step 6)
  happens by composing a step's `message` text to include a prior step's
  result, not by threading kb-agent's own multi-turn conversation state
  across calls representing different *roles*, not turns in one chat.
- Hand-rolled SSE parsing (`_iter_sse`) matching kb-agent's own
  `_format_sse` encoding exactly. `AgentCallError` covers everything that
  means the HTTP call itself didn't work (connection failure, non-2xx,
  an `error` SSE event, a stream ending with no `done` event) -- distinct
  from `AgentChatResult.execution_failure`, a *successful* call whose body
  reports kb-agent's own tool execution failed.
- `execution.py`: `execute_step(client, step) -> Step` -- pure with
  respect to persistence (no repository call in here); returns a new
  `Step` reflecting the outcome, leaving the original untouched (frozen,
  Step 3). Doesn't check `step.status`/`depends_on` itself -- deciding
  which steps are eligible to run is Step 6's execution engine's job, not
  something to re-litigate at this layer.
- A response with real `text` but `hit_iteration_limit`/`hit_token_budget`
  set (kb-agent's own graceful wrap-up, Project 2 Step 8) still counts as
  the step succeeding -- a usable answer arrived, just possibly a less
  complete one; nothing downstream needs a finer-grained distinction yet.
- Tested at the right boundary for what's actually available: unit tests
  for `execute_step` use a fake `AgentClient` (no HTTP at all);
  `HttpAgentClient`'s own tests use `httpx.MockTransport` (a real httpx
  testing utility) to fake the transport while exercising real request
  building and real SSE-parsing code. Neither a real kb-agent process nor
  an import of its Python internals was an option: the former needs a
  real, billed Anthropic API key (this project's inherited standing
  choice); the latter would violate the HTTP-only boundary Step 0
  deliberately drew between these two packages.
- **Additionally verified for real, once, outside the committed test
  suite**: a throwaway script spun up an actual kb-agent HTTP server
  (real MCP connection, mocked LLM -- kb-agent's own established test
  pattern) and hit it with this project's real `HttpAgentClient`, proving
  the hand-written SSE parser matches kb-agent's *actual* wire format, not
  just a hand-crafted guess at it from reading its source. Script deleted
  after use, consistent with Project 2's own Step 13 precedent of not
  leaving throwaway verification scripts behind.
- 20 new tests across `test_agent_client.py` and `test_execution.py`.

### Step 4 — SQLite-backed workflow persistence
- `config.py`: `db_path: Path` (default `./data/workflows.sqlite3`), same
  shape as kb-mcp-server's own `db_path` -- not validated for existence,
  `open_database` creates the parent directory itself.
- `db/migrator.py` + `db/migrations/0001_create_workflows.sql`: the exact
  same generic migration runner as Project 1's, copied verbatim -- there's
  nothing project-specific about "run unapplied .sql files once, track
  what ran." Schema: `workflows` (id, name, timestamps -- no `status`
  column; it's always derived from `steps`, per Step 3's design) and
  `steps`, keyed on a **composite** `(workflow_id, id)` primary key so
  step ids are scoped per-workflow, not globally unique.
- `db/connection.py`: `open_database()`, identical structure to Project
  1's -- one long-lived connection (SQLite serializes writes regardless of
  pool size), WAL mode for concurrent reads.
- `db/repository.py`: `WorkflowRepository` works directly with the domain
  `Workflow`/`Step` models (Step 3) rather than introducing a parallel
  "Record" type the way Project 1's `NoteRecord`/`Note` split does --
  there's no present divergence between a row and the domain shape to
  justify a second type hierarchy here. `depends_on` (no native SQLite
  array type) is JSON-encoded/decoded privately inside this module, the
  one place that's allowed to know that's a storage detail.
- `domain/errors.py` (new): `WorkflowNotFoundError`, matching Project 1's
  `NoteNotFoundError` pattern.
- `domain/service.py` (new): `StepSpec` (a caller's input for one step --
  id/name/message/depends_on) and `WorkflowService` (id/timestamp
  assignment, `WorkflowNotFoundError` on a missing read) -- matching
  Project 1's `NoteService` split exactly: no SQL here, a repository is
  injected in.
- **Retroactive fix to Step 3's domain model, found while designing this
  step's schema**: `Workflow` was missing a check for duplicate step ids.
  Two steps sharing an id would have silently collapsed into one entry in
  the `known_ids` set the dependency-reference validator builds, masking
  a real data problem. Added `_step_ids_are_unique`, plus a test proving
  it -- exactly the kind of gap this curriculum's discipline (build the
  next thing, let it surface what the last thing missed) exists to catch.
- 20 new tests across `test_connection.py`, `test_migrator.py`,
  `test_repository.py` (including step-id scoping across two different
  workflows, `depends_on` JSON round-tripping, and a full write-then-read
  proving `Workflow.status`, a derived property, correctly reflects
  updated step state after persistence), and `test_service.py`.

### Step 3 — Workflow/step domain model
- `domain/models.py`: `Step` (one message to send to kb-agent -- `name`,
  `message`, `depends_on`, `status`, `result`/`error`) and `Workflow` (a
  named collection of `Step`s). Both frozen (`ConfigDict(frozen=True)`),
  matching Project 1's `Note` -- "updating" a step's state means
  constructing a new one, not mutating in place; a future
  persistence/execution layer owns that, not this module.
- `Step.depends_on` (a list of predecessor step ids) is the one structure
  expressing both sequential chains and independent, parallel steps --
  no separate "step type" needed for either, and it's exactly the shape
  Steps 6/7 need to build a real execution order from.
- `Workflow.status` is a derived `@property`, not a stored field:
  computed from its steps' statuses every time it's read, so it can never
  drift out of sync with what the steps actually say happened. Precedence
  when steps disagree: any `failed` step makes the whole workflow
  `failed`, even alongside successes; `waiting_for_approval` outranks
  `running`; only every step `succeeded` makes the workflow `succeeded`.
- `id`/`created_at`/`updated_at` have no defaults, matching Project 1's
  `Note` exactly -- generating them is a future service/builder layer's
  job (Steps 4/6), not something this module does implicitly.
- Structural validation only, not execution-order validation: a step
  can't depend on itself, and every `depends_on` id must reference a real
  step in the same workflow. Full cycle detection/topological ordering is
  deferred to Steps 6/7, which actually need to compute an execution
  order -- adding a graph-traversal algorithm here, before anything
  consumes it, would be speculative.
- 15 new tests: field validation (blank name/message rejected,
  self-dependency rejected, unknown-dependency-reference rejected,
  frozen-ness), and 8 covering `Workflow.status`'s precedence rules
  directly, including the two-failure-modes-at-once edge cases (a failure
  alongside a success; a failure alongside a pending approval).

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
