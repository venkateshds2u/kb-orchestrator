"""Application configuration, loaded from environment variables.

Every setting is validated at construction time (pydantic-settings does this
automatically), so a missing or malformed value fails fast at process
startup rather than surfacing as a confusing error deep inside a request.
"""

from functools import lru_cache
from typing import Literal

from pydantic import HttpUrl, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration.

    All variables are read with a ``KB_ORCHESTRATOR_`` prefix (e.g.
    ``KB_ORCHESTRATOR_LOG_LEVEL``) -- distinct from kb-mcp-server's ``KB_``
    prefix and kb-agent's ``KB_AGENT_`` prefix, since all three processes
    may run on the same machine and could otherwise be set from the same
    shell/`.env` by mistake.
    """

    model_config = SettingsConfigDict(
        env_prefix="KB_ORCHESTRATOR_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="forbid",
        frozen=True,
    )

    environment: Literal["development", "production", "test"] = "development"
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"

    # Where kb-agent's HTTP+SSE API is reachable. `HttpUrl`, not a plain
    # `str`: real validation (must actually be a URL) at startup, the same
    # fail-fast-on-shape philosophy as every other required setting in this
    # curriculum -- a typo'd URL should fail loudly at process start, not
    # surface as a confusing connection error on the first workflow step.
    kb_agent_base_url: HttpUrl
    # SecretStr: kb-agent's own HTTP API requires bearer auth (Project 2,
    # Step 11) -- this is the token this app authenticates *as a client*,
    # kept out of repr()/str() so it can't end up in a stray log line.
    kb_agent_auth_token: SecretStr

    @field_validator("log_level", mode="before")
    @classmethod
    def _normalize_log_level(cls, value: object) -> object:
        """Accept ``info`` / ``Info`` / ``INFO`` interchangeably."""
        return value.upper() if isinstance(value, str) else value


@lru_cache
def get_settings() -> Settings:
    """Return the process-wide Settings instance, parsed once and cached."""
    # kb_agent_base_url/kb_agent_auth_token have no default -- required at
    # runtime, resolved from env vars by pydantic-settings. mypy's
    # dataclass_transform-synthesized __init__ only sees "no default, no
    # arg passed" and flags a call-arg error; it has no visibility into
    # pydantic-settings' env-var resolution. Same category of gap
    # documented in both prior projects' ARCHITECTURE.md files.
    return Settings()  # type: ignore[call-arg]
