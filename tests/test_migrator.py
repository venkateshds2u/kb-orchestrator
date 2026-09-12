"""Tests for the migration runner."""

import aiosqlite

from kb_orchestrator.db.migrator import apply_migrations


async def test_creates_expected_tables(db_connection: aiosqlite.Connection) -> None:
    cursor = await db_connection.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY name"
    )
    names = {row[0] for row in await cursor.fetchall()}

    assert "workflows" in names
    assert "steps" in names
    assert "schema_migrations" in names


async def test_records_applied_version(db_connection: aiosqlite.Connection) -> None:
    cursor = await db_connection.execute("SELECT version FROM schema_migrations")
    versions = {row[0] for row in await cursor.fetchall()}

    assert "0001_create_workflows" in versions


async def test_running_twice_is_a_noop(db_connection: aiosqlite.Connection) -> None:
    # The fixture already applied migrations once; applying again must not
    # try to re-run CREATE TABLE and blow up with "table already exists".
    await apply_migrations(db_connection)

    cursor = await db_connection.execute("SELECT COUNT(*) FROM schema_migrations")
    row = await cursor.fetchone()
    assert row is not None
    assert row[0] == 1
