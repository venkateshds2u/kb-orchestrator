# syntax=docker/dockerfile:1

# ---- builder ------------------------------------------------------------
# A standard single-project build -- unlike kb-agent's own Dockerfile,
# nothing here needs a sibling project's source bundled in: kb-orchestrator
# only ever talks to kb-agent over HTTP (Step 0's confirmed design), never
# spawns it as a subprocess, so there's no second codebase this image
# needs to contain.
FROM python:3.12-slim AS builder

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /usr/local/bin/
ENV UV_LINK_MODE=copy

WORKDIR /app

# Dependencies first, from the lockfile alone, before this app's own
# source is even copied in -- this layer only invalidates when
# pyproject.toml/uv.lock change, not on every source edit.
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-install-project --no-dev

COPY src/ src/
COPY README.md ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --no-editable

# ---- runtime --------------------------------------------------------------
FROM python:3.12-slim AS runtime

# A dedicated, unprivileged user -- same reasoning as both prior projects'
# images: never run as root, and a fixed numeric UID/GID keeps ownership
# reproducible across rebuilds.
RUN groupadd --gid 1000 kb && useradd --uid 1000 --gid kb --create-home kb

WORKDIR /app
COPY --from=builder /app/.venv /app/.venv

# No `uv` binary copied into this stage, unlike kb-agent's own image:
# kb-orchestrator never shells out to anything at runtime (it calls
# kb-agent over HTTP, not `uv run` a subprocess), so there's nothing here
# that would ever need it after the build.
ENV PATH="/app/.venv/bin:$PATH" \
    KB_ORCHESTRATOR_HTTP_HOST=0.0.0.0 \
    KB_ORCHESTRATOR_HTTP_PORT=8001 \
    KB_ORCHESTRATOR_DB_PATH=/app/data/workflows.sqlite3 \
    KB_ORCHESTRATOR_ENVIRONMENT=production

# KB_ORCHESTRATOR_HTTP_HOST=0.0.0.0, not the app's own 127.0.0.1 default:
# inside a container, binding to 127.0.0.1 only accepts connections from
# *within that same network namespace* -- a port mapping from the host
# would connect, then hang, since the process never listens on the
# interface Docker actually forwards traffic to.

RUN mkdir -p /app/data && chown -R kb:kb /app/data
USER kb

EXPOSE 8001

# No credentials baked in: KB_ORCHESTRATOR_HTTP_AUTH_TOKEN and
# KB_ORCHESTRATOR_KB_AGENT_AUTH_TOKEN are both required at runtime, and
# Settings already fails fast at startup without them -- this HEALTHCHECK
# hits the one endpoint that deliberately needs no token.
HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
    CMD ["python", "-c", "import urllib.request as u,sys; sys.exit(0 if u.urlopen('http://127.0.0.1:8001/health',timeout=2).status==200 else 1)"]

# Exec form, not shell form: makes this process PID 1, so `docker stop`
# delivers SIGTERM directly to it -- uvicorn's own graceful-shutdown
# handling (verified live in Step 11's manual smoke test) depends on it.
CMD ["kb-orchestrator"]
