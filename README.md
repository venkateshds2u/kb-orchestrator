# kb-orchestrator

Coordinates multi-step, multi-role workflows over
[kb-agent](../project-2-agent-app)'s HTTP+SSE API: sequencing, parallel
fan-out, retries, human-in-the-loop pauses, and observability across a
whole workflow, not just one request. Hand-rolls its own workflow engine
(state machine + SQLite persistence) rather than using an existing
workflow library (Temporal, Prefect, ...) — same reasoning as Projects 1
and 2's choice to hand-roll rather than use a framework: the mechanics are
the point.

Status: **under construction** — this README grows as the project does. See
`ARCHITECTURE.md` for design decisions and `CHANGELOG.md` for what's landed so far.

## Prerequisites

- [uv](https://docs.astral.sh/uv/) (manages the Python interpreter and dependencies)
- [project-2-agent-app](../project-2-agent-app) set up and reachable over
  HTTP (this app calls its `/chat` endpoint — it never talks to MCP or the
  Anthropic API directly)

## Setup

```bash
cd project-3-orchestrator
uv sync --dev
cp .env.example .env
# edit .env: set KB_ORCHESTRATOR_KB_AGENT_BASE_URL,
# KB_ORCHESTRATOR_KB_AGENT_AUTH_TOKEN (kb-agent's own KB_AGENT_HTTP_AUTH_TOKEN),
# and KB_ORCHESTRATOR_HTTP_AUTH_TOKEN (this app's own -- pick any secret)
```

## Running checks locally

```bash
uv run ruff check .        # lint
uv run ruff format --check .  # format check
uv run mypy                # type check (strict)
uv run pytest -v           # tests
```

## Running the orchestrator

```bash
uv run kb-orchestrator
```

Serves an HTTP API on `127.0.0.1:8001` by default (a different port than
kb-agent's own 8000, so both can run on one machine without
reconfiguration). Every endpoint except `/health` requires
`Authorization: Bearer <KB_ORCHESTRATOR_HTTP_AUTH_TOKEN>`.

```bash
# Define a workflow (steps run in dependency order; independent steps
# run concurrently). This one has "draft" depend on "research", so it
# waits for research to finish before running.
curl -s -X POST http://127.0.0.1:8001/workflows \
  -H "Authorization: Bearer $KB_ORCHESTRATOR_HTTP_AUTH_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "research and draft",
    "steps": [
      {"id": "research", "name": "Research", "message": "search my notes for kafka"},
      {"id": "draft", "name": "Draft", "message": "summarize the findings",
       "depends_on": ["research"], "requires_approval": true}
    ]
  }'
# -> {"id": "<workflow_id>", "status": "pending", ...}

# Run it. Blocks until it finishes, fails, or pauses on an approval gate.
curl -s -X POST http://127.0.0.1:8001/workflows/<workflow_id>/run \
  -H "Authorization: Bearer $KB_ORCHESTRATOR_HTTP_AUTH_TOKEN"
# -> {"status": "waiting_for_approval", ...} ("draft" requires approval)

# Approve the paused step, then run again to let its dependents proceed
# (or POST .../reject with {"reason": "..."} instead).
curl -s -X POST http://127.0.0.1:8001/workflows/<workflow_id>/steps/draft/approve \
  -H "Authorization: Bearer $KB_ORCHESTRATOR_HTTP_AUTH_TOKEN"
curl -s -X POST http://127.0.0.1:8001/workflows/<workflow_id>/run \
  -H "Authorization: Bearer $KB_ORCHESTRATOR_HTTP_AUTH_TOKEN"
# -> {"status": "succeeded", ...}

# Inspect a workflow at any time.
curl -s http://127.0.0.1:8001/workflows/<workflow_id> \
  -H "Authorization: Bearer $KB_ORCHESTRATOR_HTTP_AUTH_TOKEN"
```

A step failing blocks only its own downstream dependents, not unrelated
independent branches, which still run to completion. A failed step is
retried automatically (`KB_ORCHESTRATOR_STEP_MAX_ATTEMPTS`, default 3)
with exponential backoff before being reported as `failed`. `/run` is
resumable: calling it again on a workflow that previously paused
(waiting on approval, or that hit a transient failure now resolved) picks
up exactly where it left off, never re-running already-`succeeded` steps.
