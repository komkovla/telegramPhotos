"""Async SQLite wrapper for processed messages, album cache, and chat title tracking."""

import logging
from pathlib import Path

import aiosqlite

logger = logging.getLogger(__name__)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS processed_messages (
    chat_id INTEGER NOT NULL,
    message_id INTEGER NOT NULL,
    PRIMARY KEY (chat_id, message_id)
);

CREATE TABLE IF NOT EXISTS album_cache (
    group_title TEXT NOT NULL PRIMARY KEY,
    album_id TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS chat_titles (
    chat_id INTEGER NOT NULL PRIMARY KEY,
    group_title TEXT NOT NULL
);
"""


class Database:
    """Async SQLite database for deduplication, album ID cache, and title tracking."""

    def __init__(self, db_path: str) -> None:
        self._db_path = db_path
        self._conn: aiosqlite.Connection | None = None

    async def open(self) -> None:
        """Create parent directory if needed, connect, and initialize schema."""
        path = Path(self._db_path)
        path.parent.mkdir(parents=True, exist_ok=True)

        self._conn = await aiosqlite.connect(self._db_path)
        self._conn.row_factory = aiosqlite.Row
        await self._conn.executescript(_SCHEMA)
        await self._conn.commit()
        logger.info("Database opened path=%s", self._db_path)

    async def close(self) -> None:
        """Close the database connection."""
        if self._conn:
            await self._conn.close()
            self._conn = None
            logger.debug("Database closed")

    def _require_conn(self) -> aiosqlite.Connection:
        if self._conn is None:
            raise RuntimeError("Database not open; call open() first")
        return self._conn

    # ── Deduplication ────────────────────────────────────────────────

    async def is_processed(self, chat_id: int, message_id: int) -> bool:
        """Return True if this (chat_id, message_id) has already been processed."""
        conn = self._require_conn()
        cursor = await conn.execute(
            "SELECT 1 FROM processed_messages WHERE chat_id = ? AND message_id = ?",
            (chat_id, message_id),
        )
        row = await cursor.fetchone()
        await cursor.close()
        return row is not None

    async def mark_processed(self, chat_id: int, message_id: int) -> None:
        """Record that this (chat_id, message_id) has been processed."""
        conn = self._require_conn()
        await conn.execute(
            "INSERT OR IGNORE INTO processed_messages (chat_id, message_id) VALUES (?, ?)",
            (chat_id, message_id),
        )
        await conn.commit()

    # ── Album cache ──────────────────────────────────────────────────

    async def get_album_id(self, group_title: str) -> str | None:
        """Return the cached Google Photos album ID for this group title, or None."""
        conn = self._require_conn()
        cursor = await conn.execute(
            "SELECT album_id FROM album_cache WHERE group_title = ?",
            (group_title,),
        )
        row = await cursor.fetchone()
        await cursor.close()
        return row["album_id"] if row else None

    async def set_album_id(self, group_title: str, album_id: str) -> None:
        """Store the Google Photos album ID for this group title."""
        conn = self._require_conn()
        await conn.execute(
            "INSERT OR REPLACE INTO album_cache (group_title, album_id) VALUES (?, ?)",
            (group_title, album_id),
        )
        await conn.commit()

    async def delete_album_cache(self, group_title: str) -> None:
        """Remove the cached album entry for this group title."""
        conn = self._require_conn()
        await conn.execute(
            "DELETE FROM album_cache WHERE group_title = ?",
            (group_title,),
        )
        await conn.commit()

    # ── Chat title tracking (group rename detection) ─────────────────

    async def get_chat_title(self, chat_id: int) -> str | None:
        """Return the last-known group title for this chat_id, or None."""
        conn = self._require_conn()
        cursor = await conn.execute(
            "SELECT group_title FROM chat_titles WHERE chat_id = ?",
            (chat_id,),
        )
        row = await cursor.fetchone()
        await cursor.close()
        return row["group_title"] if row else None

    async def set_chat_title(self, chat_id: int, group_title: str) -> None:
        """Store or update the group title for this chat_id."""
        conn = self._require_conn()
        await conn.execute(
            "INSERT OR REPLACE INTO chat_titles (chat_id, group_title) VALUES (?, ?)",
            (chat_id, group_title),
        )
        await conn.commit()
