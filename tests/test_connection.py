"""Tests for the database connection lifecycle."""

from pathlib import Path

from conftest import REQUIRED_SETTINGS_FIELDS as _REQUIRED
from kb_orchestrator.config import Settings
from kb_orchestrator.db.connection import open_database


async def test_creates_parent_directory_and_migrates(tmp_path: Path) -> None:
    db_path = tmp_path / "nested" / "workflows.sqlite3"
    settings = Settings(**_REQUIRED, db_path=db_path)

    async with open_database(settings) as connection:
        cursor = await connection.execute("SELECT name FROM sqlite_master WHERE name = 'workflows'")
        row = await cursor.fetchone()
        assert row is not None

    assert db_path.exists()


async def test_enables_wal_mode(tmp_path: Path) -> None:
    settings = Settings(**_REQUIRED, db_path=tmp_path / "workflows.sqlite3")

    async with open_database(settings) as connection:
        cursor = await connection.execute("PRAGMA journal_mode")
        row = await cursor.fetchone()
        assert row is not None
        assert row[0].lower() == "wal"
