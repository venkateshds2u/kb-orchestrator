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
