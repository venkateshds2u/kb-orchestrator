"""Entry point: runs the HTTP API (Step 11).

Unlike kb-agent's `__main__.py`, there's no CLI mode to dispatch to --
kb-orchestrator's entire interface *is* this API, so there's nothing else
`main()` could reasonably do.
"""

import asyncio

from kb_orchestrator.config import get_settings
from kb_orchestrator.http import run_http
from kb_orchestrator.logging import configure_logging


def main() -> None:
    settings = get_settings()
    configure_logging(settings)
    asyncio.run(run_http(settings))


if __name__ == "__main__":
    main()
