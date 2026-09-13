# Architecture

This document tracks design decisions as the project is built, step by
step. Updated at the end of each step — treat it as the "why," not the
"what" (the code is the what).

## System shape (target, as of Step 1)

```
┌───────────────────────────────────────────────────────────────┐
│                        kb-orchestrator                          │
│                                                                   │
│   ┌────────────────┐   persists/reads    ┌───────────────────┐ │
│   │ Workflow engine │◄────────────────────►│  Workflow store    │ │
│   │ (state machine) │       state          │  (SQLite)          │ │
│   └────────┬────────┘                      └───────────────────┘ │
│            │ HTTP (one call per step, to kb-agent's /chat)        │
└────────────┼───────────────────────────────────────────────────┘
             ▼
     ┌───────────────┐
     │   kb-agent      │
     │  (Project 2)    │
     │  HTTP+SSE API   │
     └───────────────┘
```

Rationale: kb-orchestrator's only job is coordinating *when* and *in what
order* calls to kb-agent happen, and remembering that coordination durably
across process restarts -- it never needs to know MCP or the Anthropic API
exist. kb-agent stays a black box behind one HTTP endpoint (`/chat`);
everything MCP-related (tool schemas, kb-mcp-server, the model itself)
lives entirely on the other side of that boundary.

## Decisions log

### Step 8 — Retries & failure handling
- **Both `AgentCallError` and `execution_failure` are retried the same
  way, even though Step 5 deliberately kept them as distinct
  exception/data shapes.** Considered treating them differently now (e.g.
  retry a transient `AgentCallError` more readily than a `execution_failure`
  representing kb-agent's own already-considered failure) and decided
  against it for this step: nothing in this curriculum's own scenarios
  demonstrates a real difference in how these should be retried, and
  Step 5's whole point in keeping them distinct was to make that future
  refinement *possible* without restructuring anything, not to force it
  now before there's a concrete reason.
- **`increment_attempt` is its own repository method, not a new parameter
  on `update_step`.** An atomic `UPDATE steps SET attempt = attempt + 1`
  at the database level can't lose an increment the way a Python-side
  "read current attempt, add one, write it back" could under concurrent
  access. Not a real risk today (one step is only ever being retried by
  one code path at a time), but the atomic form costs nothing extra and
  removes the question entirely rather than trusting a invariant that
  happens to hold for now.
- **Exponential backoff, no jitter, no cap -- named as real production
  refinements this app doesn't need yet, not omissions.** Jitter exists to
  prevent many clients from retrying in lockstep and re-overwhelming a
  struggling service; a cap exists to bound how long a single retry
  sequence can stretch out. Both are genuine concerns at real production
  scale. At `step_max_attempts`'s own upper bound (10), even uncapped
  exponential backoff from a 1-second base stays small (session lifetime
  in seconds, not hours) -- there's no problem here for a cap to solve
  yet, and jitter matters at a scale (many concurrent clients hammering
  one shared service) this project doesn't operate at.
- **Two real regressions found by actually running the suite after
  landing this step's code, not anticipated in advance.** Adding a second
  migration file broke a test that had hardcoded "exactly 1 migration
  applied" -- a reasonable assumption when it was written, invalidated by
  this step's own schema change, not a design flaw in either. More
  interestingly: the *existing* stop-on-failure test from Step 6 broke
  because the new default retry loop now attempts a failing step 3 times
  before giving up, exhausting that test's single-failure scripted
  client on the 2nd attempt with an unrelated `IndexError`. Both fixed
  directly (updating the stale count; disabling retries via
  `step_max_attempts=1` for a test that was never about retries in the
  first place) -- exactly the kind of interaction between an old test's
  implicit assumptions and new step's behavior this curriculum's
  "build the next thing, let it surface what came before" discipline
  exists to catch.

### Step 7 — Parallel/fan-out execution
- **A failure blocks only its downstream dependents, not the whole
  workflow -- resolving the exact tension Step 6 flagged and deferred,
  not a new design question.** With independent branches now real, "stop
  everything" and "stop the affected branch" genuinely diverge for the
  first time. The chosen behavior (block only what actually depends on
  the failure) matches how real workflow engines behave and lets a
  workflow's *other* useful work still complete and be reported, rather
  than discarding independent results just because something unrelated
  broke.
- **Concurrency is per-wave (`asyncio.gather` over one wave at a time),
  not a single global "run everything with unmet dependencies satisfied
  as soon as possible" scheduler.** A wave-based design was chosen over
  a more eagerly-reactive one (e.g., a task pool that launches a step
  the instant its last dependency completes, without waiting for
  sibling steps in the same "logical wave") because it's simpler to
  reason about and test: every step in one `asyncio.gather` call is
  guaranteed to have had its dependencies fully resolved *before* the
  call, so `_compose_message` never needs to worry about a dependency
  completing mid-wave. The eager alternative would start steps
  marginally sooner in some shapes, but that's not a real requirement
  yet, and the added scheduling complexity isn't earned by anything this
  curriculum's own workflows need.
- **Verified concurrency is real, not just "eventually both get called" --
  with a counter, not a mock's call-order.** `_ConcurrencyTrackingClient`
  proves two `send_message` calls were simultaneously in flight (an
  in-flight counter reaching 2), which a purely sequential
  implementation could never produce regardless of the order it calls
  things in. This is a meaningfully stronger claim than "both steps ran"
  -- it's evidence the implementation is actually concurrent, not
  fast-sequential.
- **`topological_order`'s returned *order* is now unused by
  `run_workflow` -- only its cycle-detection side effect is kept.** The
  wave loop computes readiness directly from `completed`/`failed_ids` at
  each iteration, which is a different (and for this purpose, more
  useful) notion of "order" than a single flattened list can express --
  a flat topological order can't represent "these three steps are all
  simultaneously eligible," only "put them somewhere consistent." Calling
  it anyway, and discarding the list, was preferred over duplicating a
  cycle-check algorithm a second time; one already-correct, tested
  implementation reused for a different purpose beats two similar ones
  drifting apart.

### Step 6 — Sequential multi-step workflows
- **Context chains between steps by prepending a plain-text block, not a
  template language.** Considered a `{{placeholder}}` substitution scheme
  (more flexible -- a step could reference a dependency's result anywhere
  in its own message, not just at the start) and rejected it for now:
  nothing in this curriculum's actual scenarios needs mid-message
  splicing, and building a parser (escaping rules, missing-placeholder
  errors, etc.) for a need that doesn't exist yet is exactly the
  speculative machinery this project's principles rule out. Prepending
  `[name]: result` blocks ahead of the step's own task text is the
  simplest thing that makes a later step aware of an earlier one's
  answer, and it required zero new parsing code.
- **The composed message is never persisted -- `Step.message` stays the
  original, human-authored text.** `WorkflowRepository.update_step` only
  ever touches `status`/`result`/`error`/`updated_at`; it has no
  `message`-updating path, and that absence is deliberate, not an
  oversight: a workflow's definition (what each step was *asked* to do)
  is more meaningful on later inspection than an ephemeral, fully-expanded
  prompt reconstructible from the workflow's own steps and results at any
  time. Capturing exactly what was sent, for real observability, is
  Step 10's job if it turns out to matter.
- **`run_workflow` is resumable by construction, not as an afterthought.**
  A step already `succeeded` is skipped and its stored result folded
  into context for later steps -- built now specifically because Step 4's
  whole justification for persisting workflow state (surviving a process
  crash) is hollow until something actually resumes from it. Without this,
  Step 4 would have shipped a write-only audit log with no real use yet.
- **Stops entirely on the first failure -- a scope-specific
  simplification for *this* step, named explicitly as one to revisit, not
  a permanent design.** Every workflow `run_workflow` can execute right
  now is a single linear chain (Step 6's own scope) -- every step depends,
  directly or transitively, on every step before it, so a failure
  anywhere always blocks everything after it; "stop entirely" and "stop
  only the affected branch" are the same behavior in a chain with no
  branches. Step 7 introduces independent parallel steps, where those two
  behaviors genuinely diverge -- that's the point where this decision
  gets revisited, mirroring exactly how Project 2's Step 6 flagged its own
  hard-stop-vs-graceful-stop tradeoff for Step 8 to resolve later rather
  than solving it prematurely here.
- **Cycle detection lives in `topological_order`, not back-ported into
  Step 3's domain model.** Both Steps 3 and 4 explicitly flagged this gap
  and deferred it to "whichever layer actually needs a real execution
  order" -- that's this one. Kahn's algorithm computes the order *and*
  detects a cycle as the same side effect (nodes that never reach
  zero remaining dependencies), so there was no reason to write a
  separate cycle-only check first and a separate ordering algorithm
  second.

### Step 5 — Single-step execution
- **No `history` parameter on `AgentClient.send_message` -- resolved, not
  deferred.** Step 0 flagged this as an open question. The answer: a
  workflow's steps are different *roles* ("research", "draft"), not turns
  in one continuous conversation with one persona, so reusing kb-agent's
  multi-turn `history` mechanism across them would be a category error --
  a "conversation" that jumps between unrelated personas isn't a
  conversation. Context instead flows by a future step's `message` text
  being composed (Step 6) to explicitly include a prior step's `result`.
  Every `send_message` call is a fresh, stateless request.
- **This app defines its own wire-facing pydantic models
  (`AgentChatResult` etc.), not a reuse of kb-agent's `ChatResponseBody`
  across the package boundary.** The two packages' only real contract is
  the HTTP/JSON wire format kb-agent's Step 11 defined -- depending on its
  actual Python classes would mean importing kb-agent's code, which Step
  0 deliberately ruled out (these apps talk over HTTP only). Two field-
  compatible pydantic models on either side of one wire contract is the
  correct amount of coupling; sharing one Python class across a process/
  package boundary that only ever communicates over a network is not.
- **`AgentCallError` (the HTTP call broke) and
  `AgentChatResult.execution_failure` (a successful call reporting kb-
  agent's own tool failure) are two distinct failure categories, handled
  as two separate cases in `execute_step` -- mirroring the exact
  distinction Project 2 drew inside its own agent loop (a raised exception
  from `call_tool` vs. a tool's own `is_error=True`).** Both currently
  produce the same *outcome* on a `Step` (`status="failed"`, `error` set)
  -- there's no present reason for a workflow to react differently to
  "kb-agent is unreachable" versus "kb-agent's own tool call failed" -- but
  keeping them as genuinely distinct exception/data shapes internally means
  Step 8's retry logic can later choose to treat them differently (e.g.
  retry a transient `AgentCallError` more readily than a `execution_failure`
  that already represents kb-agent's own considered failure) without
  restructuring anything built now.
- **`execute_step` is pure with respect to persistence -- no repository
  dependency, no I/O beyond the one HTTP call.** Considered folding
  persistence in directly (mark "running" before calling, persist the
  result after), but that conflates two concerns this step doesn't need
  conflated yet: *what happens when one step runs* (this step's whole
  job) versus *when and in what order steps get chosen to run, and how
  that gets persisted* (Step 6's actual job, iterating a whole workflow
  graph). Building the second prematurely, before the execution engine
  that would actually drive it exists, would mean guessing at an
  interface Step 6 hasn't earned yet.
- **A response with real `text` but `hit_iteration_limit`/
  `hit_token_budget` set still counts as `succeeded`, not a distinct
  partial-success status.** kb-agent's own graceful-stop design (its
  Step 8) already guarantees a coherent answer even when it hit a policy
  limit -- from this workflow step's point of view, a usable result
  arrived. Introducing a finer-grained "succeeded, but limited" status now
  would be speculative: nothing downstream reads that distinction yet, and
  `Step.result` already carries whatever text kb-agent actually returned.
- **Verification strategy chosen by what's actually available, not by
  default habit.** Three options existed for testing `HttpAgentClient`:
  spawn a real kb-agent subprocess (needs a real, billed Anthropic API
  key -- ruled out by this project's inherited Step 5 standing choice from
  Project 2), import kb-agent's Python internals directly to construct a
  mocked-LLM test harness in-process (would violate the HTTP-only
  boundary Step 0 deliberately drew between these packages), or mock at
  the transport level with `httpx.MockTransport` (keeps kb-orchestrator's
  test suite fully self-contained, while still exercising real request
  construction and real SSE-parsing code). The third was chosen for the
  committed test suite -- but specifically *because* it only proves this
  app's parsing matches a hand-crafted example of kb-agent's format, a
  one-time throwaway script (deleted after use) crossed the import
  boundary just long enough to prove that parsing also matches kb-agent's
  *actual* wire output, the same "throwaway verification, not a permanent
  dependency" pattern Project 2's own Step 13 walkthrough used.

### Step 4 — SQLite-backed workflow persistence
- **`WorkflowRepository` works directly with domain `Workflow`/`Step`,
  not a parallel `WorkflowRecord`/`StepRecord` hierarchy -- a deliberate
  departure from Project 1's `NoteRecord`/`Note` split, not an
  oversight.** Project 1 justified that split by naming a plausible future
  divergence (a soft-delete flag that shouldn't be exposed to callers).
  The same kind of justification was considered here (Step 8's retry
  count could plausibly be a row-only field) and rejected for now: there
  is *zero* present divergence between what a row looks like and what the
  domain model already validates, and introducing a second, near-identical
  type hierarchy purely on the chance of a future need is exactly the
  speculative generality this curriculum's own principles rule out
  elsewhere. If Step 8 does introduce row-only fields, that's the point
  where this decision gets revisited -- not before.
- **`steps` is keyed on a composite `(workflow_id, id)` primary key, not a
  bare global `id`.** Considered explicitly while designing the schema:
  step ids are meant to be short, human-chosen, memorable labels
  ("research", "draft", "approve") that a caller picks when defining a
  workflow (see `service.py`'s `StepSpec` -- steps do NOT get
  server-generated ids the way the workflow itself does). A bare global
  primary key would make "research" usable as a step id exactly once,
  ever, across every workflow anyone creates -- clearly wrong. Scoping
  uniqueness to one workflow, the same way Airflow scopes `task_id` to one
  DAG rather than the whole system, is what actually matches how these
  ids are meant to be used.
- **This scoping decision is *why* `StepSpec` (caller-supplied step
  definitions) works at all without a two-phase "create then patch in
  real ids" dance.** If step ids had to be server-generated (like the
  workflow's own UUID), a caller wanting step B to depend on step A
  couldn't write that dependency before A's id existed -- some kind of
  temporary-reference indirection would be needed. Letting the caller
  choose stable ids up front sidesteps that problem entirely: `StepSpec(id
  ="draft", depends_on=["research"])` is just valid data, no placeholder
  resolution required.
- **A real gap found while designing this step, fixed retroactively in
  Step 3's file, not worked around here.** Realizing `steps` needed a
  composite key (not a bare one) surfaced a matching gap in the domain
  model: nothing had ever checked that a workflow's own step ids were
  unique *within* that workflow. Two same-id steps would silently collapse
  into one entry in the `known_ids` set the dependency-reference validator
  builds off of, masking a real data-integrity problem instead of
  rejecting it. Fixed at the source (`domain/models.py`), with a test,
  rather than only enforced at the database layer (a `UNIQUE` constraint
  alone would surface this as an opaque `sqlite3.IntegrityError` from deep
  inside a repository call, not a clear validation error at construction
  time where the problem actually originates).
- **`Workflow` has no `status` column in the schema, matching Step 3's own
  decision that it's a derived property, never stored.** Only `steps` need
  a `status` column; reconstructing a `Workflow` on every read re-runs its
  structural validators too, so corrupted or hand-edited data fails loudly
  at read time instead of being trusted silently by whatever queries it
  next.
- **`get_workflow` returns `None` on a missing row (repository layer);
  `WorkflowService.get_workflow` raises `WorkflowNotFoundError` instead
  (service layer).** Exactly Project 1's `NoteRepository`/`NoteService`
  split: a missing row is data at the persistence layer (the caller might
  legitimately want to check-and-create), but a genuine business error
  once something asks for a *specific* workflow it expects to exist.
- **The migration runner is copied verbatim from Project 1, not
  reimplemented.** "Apply unapplied `.sql` files in order, track what
  ran" has nothing project-specific in it -- rewriting it differently here
  would be change for its own sake, not a real design decision.

### Step 3 — Workflow/step domain model
- **`Step`/`Workflow` are frozen, matching Project 1's `Note` -- not a
  mutable, progressively-updated stateful object.** Considered explicitly:
  a mutable domain model (`step.status = "running"`, updated in place)
  would read naturally for something that changes many times over a
  workflow's life, but it's inconsistent with how *both* prior projects
  modeled entities that change over time -- Project 1's `Note` is
  immutable, with the database as the actual source of mutable truth and
  every read producing a fresh snapshot; Project 2's `LoopResult` is a
  frozen dataclass, always constructed fresh, never mutated. Following
  that precedent here means "advancing" a step is a future operation that
  produces a *new* `Step`/`Workflow` (likely via `model_copy(update=...)`)
  -- exactly mirroring how Project 1's `update_note` produces a new `Note`
  rather than mutating an existing one, and setting up Step 4's
  persistence layer to be the actual owner of state transitions, the same
  role Project 1's repository plays for `Note`.
- **`Workflow.status` is derived, never stored.** Storing it as an
  independent field would create two sources of truth (the stored status,
  and what the steps' own statuses actually say) that could disagree --
  e.g. a workflow manually marked `succeeded` while one of its steps is
  still `failed`. A `@property` computed fresh from `self.steps` every
  time makes that disagreement structurally impossible, the same
  "don't duplicate a truth that can drift" reasoning behind Project 1's
  `Page.has_more`. This also simplifies Step 4's persistence design before
  it's even designed: only `Step` rows need a `status` column; `Workflow`
  never needs one, since it's always recomputed from whatever `Step` rows
  say.
- **`depends_on` as a plain list of predecessor ids, not a separate
  "step type" enum (sequential vs. parallel).** A step with an empty
  `depends_on` can run as soon as the workflow starts -- if several steps
  are all empty, they're implicitly parallel; if each depends on the one
  before it, that's implicitly sequential. One structure expresses both
  shapes Steps 6 and 7 need, without the domain model needing to know
  which shape a given workflow uses.
- **Structural validation only -- self-dependency and unknown-reference
  checks, not full cycle detection.** A two-step cycle (A depends on B,
  B depends on A) is *not* rejected by this model as written: catching it
  would need a graph traversal (DFS/topological sort), which is exactly
  the algorithm Steps 6/7's execution engine needs anyway to compute a
  run order. Building that algorithm twice -- once here just to validate,
  once there to actually execute -- would be duplicated logic for a
  concern (arbitrary-length cycles) that self-dependency and
  unknown-reference checks don't fully cover, but narrower, structural
  invariants are worth enforcing at construction time either way. Flagged
  here explicitly as a known gap this model does not close, not an
  oversight: Step 6/7 must reject cycles as part of building an execution
  order, since a naive executor would otherwise wait forever on a step
  that can never become unblocked.
- **`id`/`created_at`/`updated_at` have no defaults.** Exactly Project 1's
  `Note` precedent: generating an id or a timestamp is a decision belonging
  to whatever code actually creates a workflow (a future builder/service
  layer), not something that should happen invisibly inside the model
  every time one gets constructed -- including in every test, which is
  why explicit values are threaded through every fixture in this step's
  own tests rather than relying on a hidden default.

### Step 2 — Config & logging
- **Logs to stdout -- a third, independently-derived choice, not copied
  from either prior project.** Checked what justified each predecessor's
  choice before picking this project's own: kb-mcp-server uses stdout
  because the MCP SDK's `stdio_server()` claims the real stdout descriptor
  for the wire protocol and diverts anything else writing to the old
  `sys.stdout` object onto stderr instead -- so stdout is genuinely
  contested there. kb-agent uses stderr because *it* has a real,
  contested use for stdout: an interactive CLI (Step 10) printing the
  assistant's replies. kb-orchestrator has no interactive CLI and no wire
  protocol living on its stdout -- nothing competes for that descriptor --
  so the more conventional destination (12-factor apps: write logs to
  stdout, let the runtime collect them) is correct here without needing to
  invent a reason to diverge from either predecessor.
- **`kb_agent_base_url` is `HttpUrl`, not a plain `str`.** Real validation
  at startup (a malformed URL fails loudly and specifically at process
  start) rather than surfacing later as a confusing `httpx` connection
  error the first time a workflow step actually tries to call kb-agent --
  same fail-fast-on-shape philosophy as every other required setting
  across this curriculum.
- **No retry/HITL/tracing/own-HTTP-API settings yet.** Each belongs with
  its own step (8, 9, 10, 11 respectively) once the actual design for that
  concern exists -- adding placeholder config fields now, before there's
  any code that reads them, would be speculative surface area with nothing
  to validate against. Same restraint Project 2 applied to its own Step
  8/11 settings.

### Step 0 — Concepts
- **Hand-roll the workflow engine (state machine + SQLite persistence),
  not an existing workflow library (Temporal, Prefect, ...).** Named
  explicitly as the alternative before choosing: a real library would
  reach a working result faster and is closer to what a production system
  would actually use, but it would hide exactly the mechanics (durable
  state, retry semantics, resumability) this project exists to teach --
  same reasoning that ruled out the Claude Agent SDK for Project 2's agent
  loop.
- **"Multiple agents" means multiple *roles*, all backed by the one real
  kb-agent -- not a second, distinct agent built for real heterogeneity.**
  Named explicitly as the alternative before choosing: building a second,
  genuinely different worker agent would make the multi-agent scenario
  more realistic, but is real new scope (designing and building a second
  agent) before any orchestration logic could even start. A workflow's
  steps instead each call kb-agent's existing `/chat` endpoint with a
  different task/message -- e.g. a "research" step and a "draft" step are
  both just kb-agent calls, orchestrated as if they were different agents.
- **kb-orchestrator never touches MCP.** A direct consequence of the
  "multiple roles, one kb-agent" decision: since every step is an HTTP
  call to kb-agent, kb-orchestrator needs no `mcp` SDK, no subprocess
  spawning, and no Anthropic API key of its own -- `httpx` (calling
  kb-agent) is the only thing standing in for what would otherwise be a
  much larger dependency footprint. This is a real simplification that
  falls out of the decision above, not a separate choice.
- **Testing without a real API key gets a layer harder here, flagged now,
  not solved yet.** kb-orchestrator can't inject a mocked LLM into a
  kb-agent it only ever talks to over HTTP the way Project 2's own tests
  injected mocks directly into Python objects. Expect Step 5 to introduce
  a `Protocol`-based fake agent client for unit tests, plus a small
  in-process kb-agent test harness (similar to Project 2 Step 13's
  throwaway walkthrough scripts) for real integration coverage --
  deferred to when it's actually needed, not designed speculatively now.

### Step 1 — Tooling
- **Same tooling standard as Projects 1 and 2** (uv, ruff, mypy strict,
  pytest, pre-commit, structlog, pydantic-settings) -- no new decisions
  needed here, just consistency across the curriculum.
- **Dependency set matches Step 0's confirmed design, not Project 2's
  shape.** `httpx`, `pydantic`, `pydantic-settings`, `structlog`,
  `aiosqlite` (workflow persistence, Step 4), `starlette`/`uvicorn` (this
  project's own HTTP API, Step 11) -- declared upfront since the
  architecture is already scoped, same precedent as both prior projects'
  Step 1s. Deliberately no `anthropic`/`mcp`: this project never calls
  either directly.
- **`.gitignore` includes SQLite entries from Step 1**, unlike Project 2's
  (which never had a database). This project persists workflow state from
  Step 4 onward, so the entries are proactive here rather than a reactive
  fix after an accidental commit -- Project 1's own live mistake, already
  corrected once, not worth repeating a second time.
