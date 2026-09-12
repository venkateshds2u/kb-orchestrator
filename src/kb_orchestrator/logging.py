"""Structured logging setup.

Wires structlog and the standard library ``logging`` module together so
that every log line -- ours and any third-party library's -- goes through
the same processor chain and comes out in the same shape: JSON in
production, a readable colorized format in development.

Logs go to **stdout** here -- a third, independently-reasoned choice,
not a copy of either prior project's. kb-mcp-server writes to stdout
because nothing else needs that file descriptor (its own stdout use, the
MCP wire protocol, is deliberately diverted elsewhere by the SDK).
kb-agent writes to **stderr** specifically because it has a competing,
real use for stdout: an interactive CLI (Step 10) printing the assistant's
replies there, which structlog output would otherwise interleave with and
corrupt. kb-orchestrator has no such competing use -- no interactive CLI,
no wire protocol living on its stdout, just an HTTP server (Step 11)
that never prints application output to the terminal at all -- so stdout,
the conventional destination for a plain backend service's logs (the
12-factor app convention: write logs to stdout, let the runtime collect
them), is the correct default here, not stderr by inherited habit.
"""

import logging
import sys

import structlog
from structlog.typing import Processor

from kb_orchestrator.config import Settings


def configure_logging(settings: Settings) -> None:
    """Configure stdlib logging + structlog as a single pipeline.

    Must be called once, as early as possible in process startup, before
    any other module obtains a logger.
    """
    shared_processors: list[Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ]

    renderer: Processor = (
        structlog.processors.JSONRenderer()
        if settings.environment == "production"
        else structlog.dev.ConsoleRenderer()
    )

    structlog.configure(
        processors=[
            *shared_processors,
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )

    formatter = structlog.stdlib.ProcessorFormatter(
        foreign_pre_chain=shared_processors,
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            renderer,
        ],
    )

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(formatter)

    root_logger = logging.getLogger()
    root_logger.handlers = [handler]
    root_logger.setLevel(settings.log_level)
