"""Tests for bot.database — async SQLite wrapper."""

import pytest

from bot.database import Database


class TestOpenClose:
    async def test_open_creates_tables(self, db: Database):
        conn = db._require_conn()
        cursor = await conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        )
        tables = [row["name"] for row in await cursor.fetchall()]
        await cursor.close()
        assert "album_cache" in tables
        assert "chat_titles" in tables
        assert "processed_messages" in tables

    async def test_require_conn_raises_when_not_open(self, tmp_path):
        database = Database(str(tmp_path / "nope.db"))
        with pytest.raises(RuntimeError, match="Database not open"):
            database._require_conn()

    async def test_close_is_idempotent(self, tmp_path):
        database = Database(str(tmp_path / "test.db"))
        await database.open()
        await database.close()
        await database.close()  # should not raise


class TestProcessedMessages:
    async def test_is_processed_false_for_unknown(self, db: Database):
        assert await db.is_processed(111, 1) is False

    async def test_mark_processed_then_is_processed(self, db: Database):
        await db.mark_processed(111, 1)
        assert await db.is_processed(111, 1) is True

    async def test_mark_processed_is_idempotent(self, db: Database):
        await db.mark_processed(111, 1)
        await db.mark_processed(111, 1)  # INSERT OR IGNORE
        assert await db.is_processed(111, 1) is True

    async def test_different_messages_are_independent(self, db: Database):
        await db.mark_processed(111, 1)
        assert await db.is_processed(111, 2) is False
        assert await db.is_processed(222, 1) is False


class TestAlbumCache:
    async def test_get_album_id_returns_none_for_unknown(self, db: Database):
        assert await db.get_album_id("unknown") is None

    async def test_set_and_get_album_id(self, db: Database):
        await db.set_album_id("My Group", "album_123")
        assert await db.get_album_id("My Group") == "album_123"

    async def test_set_album_id_overwrites(self, db: Database):
        await db.set_album_id("My Group", "album_old")
        await db.set_album_id("My Group", "album_new")
        assert await db.get_album_id("My Group") == "album_new"

    async def test_delete_album_cache(self, db: Database):
        await db.set_album_id("Old Name", "album_x")
        await db.delete_album_cache("Old Name")
        assert await db.get_album_id("Old Name") is None

    async def test_delete_album_cache_no_op_for_missing(self, db: Database):
        await db.delete_album_cache("nonexistent")  # should not raise


class TestChatTitles:
    async def test_get_chat_title_returns_none_for_unknown(self, db: Database):
        assert await db.get_chat_title(999) is None

    async def test_set_and_get_chat_title(self, db: Database):
        await db.set_chat_title(111, "Friends")
        assert await db.get_chat_title(111) == "Friends"

    async def test_set_chat_title_overwrites(self, db: Database):
        await db.set_chat_title(111, "Old Name")
        await db.set_chat_title(111, "New Name")
        assert await db.get_chat_title(111) == "New Name"

    async def test_different_chats_are_independent(self, db: Database):
        await db.set_chat_title(111, "Chat A")
        await db.set_chat_title(222, "Chat B")
        assert await db.get_chat_title(111) == "Chat A"
        assert await db.get_chat_title(222) == "Chat B"
