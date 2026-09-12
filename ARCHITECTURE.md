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
