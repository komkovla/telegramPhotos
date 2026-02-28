"""Tests for bot.handlers — Telegram message handlers."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from bot.config import Config
from bot.database import Database
from bot.google_photos import GooglePhotosClient, GooglePhotosError
from bot.handlers import handle_link_command, handle_media, handle_my_chat_member
from bot.media import FileTooLargeError, MediaContent


# ── Helpers ──────────────────────────────────────────────────────────


def _make_config(allowed_group_ids: tuple[int, ...] = ()) -> Config:
    return Config(
        telegram_bot_token="token",
        google_client_id="cid",
        google_client_secret="csecret",
        google_refresh_token="rtoken",
        telegram_bot_api_url="",
        allowed_group_ids=allowed_group_ids,
        db_path="/tmp/test.db",
        log_level="INFO",
    )


def _make_update(
    chat_id: int = -100,
    message_id: int = 1,
    chat_type: str = "supergroup",
    chat_title: str = "Test Group",
    *,
    has_photo: bool = True,
) -> MagicMock:
    update = MagicMock()
    message = MagicMock()
    message.id = message_id
    message.photo = [MagicMock()] if has_photo else None
    message.video = None
    message.video_note = None

    chat = MagicMock()
    chat.id = chat_id
    chat.type = chat_type
    chat.title = chat_title

    update.message = message
    update.effective_chat = chat
    return update


def _make_context(
    db: Database,
    google_photos: MagicMock | None = None,
    config: Config | None = None,
) -> MagicMock:
    if google_photos is None:
        google_photos = AsyncMock(spec=GooglePhotosClient)
        google_photos.get_or_create_album = AsyncMock(return_value="album_1")
        google_photos.upload_media = AsyncMock()
    if config is None:
        config = _make_config()

    ctx = MagicMock()
    ctx.bot_data = {"db": db, "google_photos": google_photos, "config": config}
    ctx.bot = AsyncMock()
    return ctx


# ── Tests ────────────────────────────────────────────────────────────


class TestHandleMediaSkips:
    async def test_skips_when_no_message(self, db: Database):
        update = MagicMock()
        update.message = None
        update.effective_chat = MagicMock()
        ctx = _make_context(db)
        await handle_media(update, ctx)  # should not raise

    async def test_skips_when_no_chat(self, db: Database):
        update = MagicMock()
        update.message = MagicMock()
        update.effective_chat = None
        ctx = _make_context(db)
        await handle_media(update, ctx)

    async def test_skips_private_chat(self, db: Database):
        update = _make_update(chat_type="private")
        ctx = _make_context(db)
        await handle_media(update, ctx)
        # Should not call download
        ctx.bot.get_file.assert_not_called()

    async def test_skips_disallowed_group(self, db: Database):
        config = _make_config(allowed_group_ids=(-200,))
        update = _make_update(chat_id=-100)
        ctx = _make_context(db, config=config)
        await handle_media(update, ctx)
        ctx.bot.get_file.assert_not_called()

    async def test_skips_already_processed(self, db: Database):
        await db.mark_processed(-100, 1)
        update = _make_update(chat_id=-100, message_id=1)
        ctx = _make_context(db)
        await handle_media(update, ctx)
        ctx.bot.get_file.assert_not_called()

    async def test_skips_missing_bot_data(self, db: Database):
        update = _make_update()
        ctx = MagicMock()
        ctx.bot_data = {}
        await handle_media(update, ctx)


class TestHandleMediaPipeline:
    @patch("bot.handlers.download_media")
    async def test_full_pipeline(self, mock_download, db: Database):
        mock_download.return_value = MediaContent(
            data=b"img_bytes", filename="photo.jpg", mime_type="image/jpeg",
        )
        gp = AsyncMock(spec=GooglePhotosClient)
        gp.get_or_create_album = AsyncMock(return_value="album_1")
        gp.upload_media = AsyncMock()

        update = _make_update(chat_id=-100, message_id=42, chat_title="Vacation")
        ctx = _make_context(db, google_photos=gp)

        await handle_media(update, ctx)

        gp.upload_media.assert_called_once_with(
            b"img_bytes", "photo.jpg", "image/jpeg", "album_1",
        )
        assert await db.is_processed(-100, 42)
        assert await db.get_album_id("Vacation") == "album_1"
        assert await db.get_chat_title(-100) == "Vacation"

    @patch("bot.handlers.download_media")
    async def test_uses_cached_album_id(self, mock_download, db: Database):
        await db.set_album_id("Cached", "album_cached")
        mock_download.return_value = MediaContent(
            data=b"x", filename="p.jpg", mime_type="image/jpeg",
        )
        gp = AsyncMock(spec=GooglePhotosClient)
        gp.upload_media = AsyncMock()

        update = _make_update(chat_title="Cached")
        ctx = _make_context(db, google_photos=gp)
        await handle_media(update, ctx)

        # Should NOT have called get_or_create_album since album is cached
        gp.get_or_create_album.assert_not_called()
        gp.upload_media.assert_called_once()


class TestHandleMediaErrors:
    @patch("bot.handlers.download_media", side_effect=Exception("network"))
    async def test_download_failure_does_not_mark_processed(
        self, _mock, db: Database
    ):
        update = _make_update(chat_id=-100, message_id=5)
        ctx = _make_context(db)
        await handle_media(update, ctx)
        assert await db.is_processed(-100, 5) is False

    @patch("bot.handlers.download_media")
    async def test_upload_failure_does_not_mark_processed(
        self, mock_download, db: Database
    ):
        mock_download.return_value = MediaContent(
            data=b"x", filename="p.jpg", mime_type="image/jpeg",
        )
        gp = AsyncMock(spec=GooglePhotosClient)
        gp.get_or_create_album = AsyncMock(return_value="album_1")
        gp.upload_media = AsyncMock(
            side_effect=GooglePhotosError("fail", status_code=500)
        )

        update = _make_update(chat_id=-100, message_id=7)
        ctx = _make_context(db, google_photos=gp)
        await handle_media(update, ctx)
        assert await db.is_processed(-100, 7) is False

    @patch(
        "bot.handlers.download_media",
        side_effect=FileTooLargeError("photo", 999999999, 200000000),
    )
    async def test_file_too_large_skips_gracefully(self, _mock, db: Database):
        update = _make_update(chat_id=-100, message_id=8)
        ctx = _make_context(db)
        await handle_media(update, ctx)
        assert await db.is_processed(-100, 8) is False

    @patch("bot.handlers.download_media", return_value=None)
    async def test_no_media_content_skips(self, _mock, db: Database):
        update = _make_update(chat_id=-100, message_id=9)
        ctx = _make_context(db)
        await handle_media(update, ctx)
        assert await db.is_processed(-100, 9) is False


class TestGroupRename:
    @patch("bot.handlers.download_media")
    async def test_detects_rename_and_creates_new_album(
        self, mock_download, db: Database
    ):
        # Seed old title
        await db.set_chat_title(-100, "Old Name")
        await db.set_album_id("Old Name", "album_old")

        mock_download.return_value = MediaContent(
            data=b"x", filename="p.jpg", mime_type="image/jpeg",
        )
        gp = AsyncMock(spec=GooglePhotosClient)
        gp.get_or_create_album = AsyncMock(return_value="album_new")
        gp.upload_media = AsyncMock()

        update = _make_update(chat_id=-100, message_id=10, chat_title="New Name")
        ctx = _make_context(db, google_photos=gp)
        await handle_media(update, ctx)

        # Old album cache should be deleted
        assert await db.get_album_id("Old Name") is None
        # New album should be cached
        assert await db.get_album_id("New Name") == "album_new"
        # Chat title should be updated
        assert await db.get_chat_title(-100) == "New Name"
        gp.get_or_create_album.assert_called_once_with("New Name")


class TestHandleLinkCommand:
    async def test_returns_album_url(self, db: Database):
        await db.set_album_id("Test Group", "album_123")
        gp = AsyncMock(spec=GooglePhotosClient)
        gp.get_album_product_url = AsyncMock(
            return_value="https://photos.google.com/lr/album/album_123"
        )

        update = _make_update(chat_title="Test Group")
        update.message.reply_text = AsyncMock()
        ctx = _make_context(db, google_photos=gp)
        await handle_link_command(update, ctx)

        gp.get_album_product_url.assert_called_once_with("album_123")
        update.message.reply_text.assert_called_once()
        reply_text = update.message.reply_text.call_args[0][0]
        assert "https://photos.google.com/lr/album/album_123" in reply_text

    async def test_no_album_cached(self, db: Database):
        gp = AsyncMock(spec=GooglePhotosClient)
        update = _make_update(chat_title="Empty Group")
        update.message.reply_text = AsyncMock()
        ctx = _make_context(db, google_photos=gp)
        await handle_link_command(update, ctx)

        gp.get_album_product_url.assert_not_called()
        update.message.reply_text.assert_called_once()
        reply_text = update.message.reply_text.call_args[0][0]
        assert "No album found" in reply_text

    async def test_skips_private_chat(self, db: Database):
        update = _make_update(chat_type="private")
        update.message.reply_text = AsyncMock()
        ctx = _make_context(db)
        await handle_link_command(update, ctx)
        update.message.reply_text.assert_not_called()

    async def test_skips_disallowed_group(self, db: Database):
        config = _make_config(allowed_group_ids=(-200,))
        update = _make_update(chat_id=-100)
        update.message.reply_text = AsyncMock()
        ctx = _make_context(db, config=config)
        await handle_link_command(update, ctx)
        update.message.reply_text.assert_not_called()

    async def test_handles_google_photos_error(self, db: Database):
        await db.set_album_id("Test Group", "album_gone")
        gp = AsyncMock(spec=GooglePhotosClient)
        gp.get_album_product_url = AsyncMock(
            side_effect=GooglePhotosError("not found", status_code=404)
        )

        update = _make_update(chat_title="Test Group")
        update.message.reply_text = AsyncMock()
        ctx = _make_context(db, google_photos=gp)
        await handle_link_command(update, ctx)

        update.message.reply_text.assert_called_once()
        reply_text = update.message.reply_text.call_args[0][0]
        assert "Could not retrieve" in reply_text


class TestHandleMyChatMember:
    async def test_bot_removed_logs_without_error(self):
        update = MagicMock()
        member = MagicMock()
        member.new_chat_member.status = "kicked"
        member.chat.id = -100
        member.chat.title = "Test Group"
        update.my_chat_member = member

        ctx = MagicMock()
        await handle_my_chat_member(update, ctx)  # should not raise

    async def test_no_chat_member_update(self):
        update = MagicMock()
        update.my_chat_member = None
        ctx = MagicMock()
        await handle_my_chat_member(update, ctx)  # should not raise

    async def test_bot_still_member_no_action(self):
        update = MagicMock()
        member = MagicMock()
        member.new_chat_member.status = "member"
        member.chat.id = -100
        member.chat.title = "Test Group"
        update.my_chat_member = member

        ctx = MagicMock()
        await handle_my_chat_member(update, ctx)  # should not raise
