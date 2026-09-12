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
```

## Running checks locally

```bash
uv run ruff check .        # lint
uv run ruff format --check .  # format check
uv run mypy                # type check (strict)
uv run pytest -v           # tests
```

## Running the orchestrator

Not yet available — added starting Step 5.
