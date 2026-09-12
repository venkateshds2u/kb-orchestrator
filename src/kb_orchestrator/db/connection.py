"""Database connection lifecycle."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import aiosqlite

from kb_orchestrator.config import Settings
from kb_orchestrator.db.migrator import apply_migrations


@asynccontextmanager
async def open_database(settings: Settings) -> AsyncIterator[aiosqlite.Connection]:
    """Open a connection to the configured SQLite file, migrated and ready.

    A single long-lived connection is used rather than a pool: SQLite writes
    are serialized regardless of how many connections you open (a second
    writer just blocks on the first), so a pool buys nothing for writes and
    only adds complexity. WAL mode (below) is what lets *readers* proceed
    concurrently with a writer.
    """
    settings.db_path.parent.mkdir(parents=True, exist_ok=True)

    connection = await aiosqlite.connect(settings.db_path)
    connection.row_factory = aiosqlite.Row
    try:
        await connection.execute("PRAGMA journal_mode = WAL")
        await apply_migrations(connection)
        yield connection
    finally:
        await connection.close()
