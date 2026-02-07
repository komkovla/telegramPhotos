"""Shared test fixtures."""

import pytest

from bot.database import Database


@pytest.fixture
async def db(tmp_path):
    """Provide an open in-memory-like SQLite database (on temp disk)."""
    database = Database(str(tmp_path / "test.db"))
    await database.open()
    yield database
    await database.close()
