# Changelog

All notable changes to this project are documented here.
Format loosely follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

### Step 11 — HTTP API
- `config.py`: `http_host`/`http_port` (default **8001**, deliberately not
  kb-agent's own 8000 -- both typically run on one machine during local
  dev) and `http_auth_token` (unconditionally required -- no `mode` toggle
  like kb-agent's, since this API *is* kb-orchestrator's whole interface).
- New `http.py`: `POST /workflows` (create), `GET /workflows/{id}`
  (inspect), `POST /workflows/{id}/run` (execute -- blocks until the
  workflow finishes, fails, or pauses on an approval gate),
  `POST /workflows/{id}/steps/{step_id}/approve`, `.../reject` (body:
  `{"reason": "..."}`). `GET /health` is exempt from the same bearer-auth
  scheme as kb-agent/kb-mcp-server. No separate "resume" endpoint --
  calling `/run` again on a paused workflow *is* resuming it,
  `run_workflow` (Step 6) already being resumable by construction.
- Own wire models (`WorkflowBody`, `StepBody`, ...), not the domain
  `Workflow`/`Step` serialized directly: confirmed first (not assumed)
  that pydantic's `model_dump()` excludes plain `@property` fields, which
  would have silently dropped `Workflow.status` -- the one field a client
  most needs -- from every response.
- `src/kb_orchestrator/__main__.py` (new): this project's console script
  was declared in Step 1's `pyproject.toml` but nothing needed the module
  to exist until this step, the same way kb-agent's own `__main__.py`
  first became real at its Step 5.
- **Found and fixed a real, pre-existing bug while building this step**:
  `Step.requires_approval` (Step 9) was never wired into
  `WorkflowRepository` -- no column, not read or written by
  `_step_to_row`/`_row_to_step`. Step 9's own tests never caught it
  because they mostly worked with in-memory `Workflow` objects; this
  step's `POST /run` handler is the first code path that creates a
  workflow in one call and re-fetches it from the database in a separate
  one, which is exactly where a step created with `requires_approval:
  true` silently came back `false`. Fixed with a new migration
  (`0003_add_step_requires_approval.sql`) plus a repository-level
  regression test proving the round trip now works.
- Also fixed `test_running_twice_is_a_noop`'s hardcoded migration count,
  broken a second time by this step's new migration (first broken in Step
  8) -- now computed from the actual `migrations/` directory instead of a
  number someone has to remember to update.
- 15 new tests (`test_http.py`) covering auth, request validation
  (missing fields, duplicate step ids), the full create → run → approve →
  run-again lifecycle entirely over HTTP, and 404/409 error paths --
  plus manually smoke-tested the real server binary end-to-end (`uv run
  kb-orchestrator`, hit with `curl`: health, unauthorized rejection, and
  an authorized workflow creation, with real structured logs and a clean
  graceful shutdown).

### Step 10 — Observability
- `execution.py`: `workflow_id` bound via `structlog.contextvars` for the
  whole `run_workflow` call, `step_id` additionally bound within
  `_run_ready_step` -- every log line emitted anywhere underneath
  (including from other modules) automatically carries both, via the
  `merge_contextvars` processor `logging.py` has had wired in since Step
  2 but nothing used until now. New events: `workflow_run_started`,
  `workflow_wave_started` (with the ready step ids), `workflow_run_completed`,
  `workflow_run_skipped` (already-failed no-op), `step_attempt_started`,
  `step_retrying`, `step_succeeded`, `step_failed`, `step_waiting_for_approval`.
  `domain/service.py` gains `workflow_created`/`step_approved`/`step_rejected`.
- Verified, not assumed, that this holds up under real concurrency:
  a throwaway script confirmed `asyncio.gather`'s per-task context
  isolation means one concurrently-running step's bound `step_id` never
  leaks into a sibling's logs, while both still inherit the parent's
  `workflow_id` -- then proved the same thing again through this app's
  actual logging pipeline (JSON-rendered output, not just the abstract
  mechanism) with two independent steps in one wave.
- `agent_client.py`: `HttpAgentClient` now sends `X-Workflow-Id`/
  `X-Step-Id` headers, read from whatever's already bound in the ambient
  structlog context -- `AgentClient.send_message`'s signature is
  unchanged, so no ripple through the Protocol or any test double. kb-agent
  doesn't consume these headers today; sending them is forward-compatible
  plumbing, not a claim of full cross-service trace correlation.
- No OpenTelemetry (or any tracing library) added -- named explicitly as
  the real production alternative, not built here, consistent with this
  curriculum's standing choice to hand-roll mechanics (the workflow engine
  itself, the agent loop in Project 2) rather than adopt a framework.
- 5 new tests: correlation headers present/absent depending on bound
  context, log output actually carrying `workflow_id`/`step_id` for both
  a workflow-level and a step-level event (proving the scope boundary --
  a workflow-level event has no `step_id`), and two concurrent steps'
  logs never mixing up which `step_id` belongs to which.

### Step 9 — Human-in-the-loop
- `domain/models.py`: `Step.requires_approval: bool = False`. A
  successful agent call on such a step becomes `waiting_for_approval`
  instead of `succeeded` (`execute_step`), with the same `result` already
  attached -- approval only gates *success*; a failure is still just a
  failure regardless of this flag.
- `domain/errors.py`: `StepNotFoundError`, `StepNotAwaitingApprovalError`.
- `domain/service.py`: `StepSpec.requires_approval` (threaded into
  `create_workflow`); `WorkflowService.approve_step()`/`reject_step()` --
  move a `waiting_for_approval` step to `succeeded`/`failed`. Rejection
  reuses `Step.error` for its `reason`, not a new field -- a human
  rejection and an agent failure are both, from a workflow's point of
  view, "this step did not produce an accepted outcome."
- `run_workflow`'s wave loop now tracks `waiting_ids` alongside
  `failed_ids`: a waiting step is excluded from re-running (it's not
  re-attempted while a human hasn't decided), and blocks its own
  dependents the same way a failure does -- but only *for this call*,
  since approval, unlike a failure, isn't permanent. A later
  `run_workflow` call after `approve_step` sees the step as `succeeded`
  and lets dependents proceed normally. Independent branches that don't
  depend on the waiting step keep running now, the same isolation
  principle Step 7 established for failures.
- Found and fixed a real bug while writing the very first `run_workflow`
  test for this step: a step transitioning to `waiting_for_approval`
  *during* a wave was falling into the `else: failed_ids.add(...)`
  branch (only `"succeeded"` was checked explicitly), which would have
  permanently blocked its dependents instead of just pausing them.
- 9 new tests: `execute_step` pausing instead of succeeding, a dependent
  staying `pending` while blocked, an independent branch completing
  anyway, and the full two-call approve-then-resume flow (`run_workflow`
  → `approve_step` → `run_workflow` again) that's the actual point of
  this step -- plus `WorkflowService.approve_step`/`reject_step` unit
  tests (result kept, reason recorded, not-found and not-awaiting-approval
  error paths).

### Step 8 — Retries & failure handling
- `domain/models.py`: `Step.attempt: int = 0` -- how many times a step
  has actually been attempted, deferred from Step 3 specifically to land
  here. Persisted, not just tracked in memory during one `run_workflow`
  call, so it survives a crash mid-retry-loop like every other piece of
  step state.
- `db/migrations/0002_add_step_attempt.sql`: `ALTER TABLE steps ADD
  COLUMN attempt`. `WorkflowRepository.increment_attempt()` (new): an
  atomic `attempt = attempt + 1` at the database level, returning the new
  count -- a separate method from `update_step`, not an extra parameter
  there, so incrementing can't race a Python-side read-modify-write.
- `config.py`: `step_max_attempts` (default 3, `1..10`) and
  `step_retry_backoff_seconds` (default 1.0, exponential: attempt *n*
  waits `backoff * 2^(n-2)`, no jitter or cap -- not needed for a handful
  of attempts at most).
- `_run_ready_step` now retries a failing step in place, up to
  `step_max_attempts` times, before persisting a final `failed` outcome.
  Both of `execute_step`'s failure categories (`AgentCallError` and
  kb-agent's own `execution_failure`) are retried uniformly -- Step 5 kept
  them distinct specifically so this could differ later without a
  restructure, but nothing yet demands that distinction.
- 9 new tests: a transient failure recovering on retry (`attempt == 2`),
  first-try success recording `attempt == 1`, exhausting all attempts and
  keeping the *last* attempt's error, exponential backoff durations
  (`asyncio.sleep` faked to record durations instead of actually
  waiting -- keeps the suite fast and deterministic), a resumed
  already-succeeded step's `attempt` count staying untouched, plus
  `increment_attempt` unit tests and a domain-model default check. Full
  suite (81 tests) still runs in ~0.1s.
- Fixed two now-stale tests found by actually running the suite after
  adding the second migration and the retry loop: `test_running_twice_is_a_noop`
  hardcoded "1 migration applied" (now 2); the stop-on-failure test's
  single-failure scripted client was exhausted by the new default retry
  loop before Step 8 code even existed to explain why -- fixed by
  disabling retries there explicitly (`step_max_attempts=1`), since that
  test is about blocking behavior, not retries.

### Step 7 — Parallel/fan-out execution
- `run_workflow` rewritten around a wave-based loop: every step whose
  dependencies are already satisfied runs *concurrently* with its
  wave-mates (`asyncio.gather`), not one at a time. A linear chain is
  simply the special case where every wave contains exactly one step --
  this is a strict generalization of Step 6's runner (all 5 of Step 6's
  own tests kept passing unchanged against the new implementation, not
  rewritten to match it).
- A step failing now blocks only its *own* downstream dependents (they
  can never satisfy "all dependencies completed," so they stay `pending`
  forever) -- not unrelated, independent branches, which keep running to
  completion. This directly replaces Step 6's "stop entirely on first
  failure," which that step explicitly flagged as needing revisiting once
  real parallel branches existed.
- `topological_order()` is still called once at the top of `run_workflow`
  -- now purely for its cycle-detection side effect (the wave loop
  computes its own execution order from live readiness, not from that
  function's returned list), so a cyclic workflow definition still fails
  loudly and immediately rather than silently sitting as "pending
  forever" once no wave ever becomes ready.
- 4 new tests: independent steps proven to run *concurrently* (an
  in-flight counter with no lock needed, since the increment/check has no
  `await` between them and can't be interleaved), a fan-in step's message
  correctly includes both of two independent dependencies' results, an
  independent branch completing after an unrelated sibling fails (the
  actual point of this step), and cycle detection still raising through
  the new implementation.

### Step 6 — Sequential multi-step workflows
- `execution.py` gains `topological_order()` (Kahn's algorithm; ties
  resolve in original list order) and `DependencyCycleError` -- closing
  the cycle-detection gap Steps 3/4 explicitly deferred here, since this
  is the layer that actually needs a real execution order to exist.
- `_compose_message()`: a later step's message is its own text, prefixed
  with every completed dependency's result under a `[step name]: result`
  heading -- no template/placeholder syntax, since nothing yet needs a
  dependency's result spliced into the *middle* of a step's own message.
- `run_workflow(client, repository, workflow) -> Workflow`: executes
  every eligible step in dependency order, one at a time, persisting each
  transition via the repository as it goes. **Resumable**: a step already
  `succeeded` (from a prior, interrupted run of the same workflow) is
  skipped, its stored result reused as context for whatever depends on
  it -- Step 4's persistence finally has a real payoff, not just a
  write-only log. A workflow already `failed` is returned untouched;
  retrying is Step 8's job. Stops entirely on the first new failure --
  correct for the linear chains this step builds (every later step
  already depends, transitively, on every earlier one), revisited once
  Step 7 adds real independent branches.
- 15 new tests: `topological_order` (linear chain, diamond dependencies,
  tie-order stability, cycle detection) and `run_workflow` (dependency
  order, message composition, stop-on-failure leaving later steps
  `pending`, resuming a partially-completed workflow, no-op on an
  already-failed workflow) -- the last three using a real in-memory
  repository, not a fake, same pattern as Steps 4/5.

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
