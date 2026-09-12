"""Minimal SQL migration runner.

Applies numbered .sql files from the `migrations/` directory in filename
order, tracking what has already run in a `schema_migrations` table.

This project deliberately does not use an ORM migration framework (e.g.
Alembic): Alembic's value -- autogenerating diffs from ORM model state --
only pays for itself when there's an ORM. Here, the repository layer speaks
raw SQL directly, so a plain "run these files in order, once" runner is the
right amount of machinery. (Identical to Project 1's own migrator, verbatim
-- there's nothing project-specific about "run unapplied .sql files once.")
"""

from pathlib import Path

import aiosqlite
import structlog

logger = structlog.get_logger(__name__)

MIGRATIONS_DIR = Path(__file__).parent / "migrations"


async def apply_migrations(connection: aiosqlite.Connection) -> None:
    """Apply any migration in MIGRATIONS_DIR not yet recorded as applied."""
    await connection.execute(
        """
        CREATE TABLE IF NOT EXISTS schema_migrations (
            version TEXT PRIMARY KEY,
            applied_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
        )
        """
    )
    await connection.commit()

    cursor = await connection.execute("SELECT version FROM schema_migrations")
    applied = {row[0] for row in await cursor.fetchall()}

    for migration_path in sorted(MIGRATIONS_DIR.glob("*.sql")):
        version = migration_path.stem
        if version in applied:
            continue

        logger.info("migration_applying", version=version)
        # executescript() implicitly commits any open transaction first, then
        # runs the whole file; that's fine here since these are schema-only
        # DDL statements with no application data to protect mid-script.
        await connection.executescript(migration_path.read_text())
        await connection.execute("INSERT INTO schema_migrations (version) VALUES (?)", (version,))
        await connection.commit()
        logger.info("migration_applied", version=version)
