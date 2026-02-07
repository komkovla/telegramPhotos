"""Tests for bot.media — Telegram media download with retry and size checks."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from bot.media import (
    FileTooLargeError,
    MediaContent,
    PHOTO_MAX_BYTES,
    VIDEO_MAX_BYTES,
    _check_file_size,
    _download_with_retry,
    _safe_filename,
    download_media,
)


class TestSafeFilename:
    def test_simple_filename(self):
        assert _safe_filename("photo.jpg", "fallback.jpg") == "photo.jpg"

    def test_empty_returns_fallback(self):
        assert _safe_filename("", "fallback.jpg") == "fallback.jpg"

    def test_whitespace_returns_fallback(self):
        assert _safe_filename("   ", "fallback.jpg") == "fallback.jpg"

    def test_strips_path_with_forward_slash(self):
        assert _safe_filename("photos/image.jpg", "fallback.jpg") == "image.jpg"

    def test_strips_path_with_backslash(self):
        assert _safe_filename("photos\\image.jpg", "fallback.jpg") == "image.jpg"

    def test_deep_path(self):
        assert _safe_filename("a/b/c/photo.jpg", "fallback.jpg") == "photo.jpg"

    def test_trailing_slash_returns_fallback(self):
        assert _safe_filename("some/path/", "fallback.jpg") == "fallback.jpg"


class TestCheckFileSize:
    def test_within_limit_does_not_raise(self):
        _check_file_size(1000, 2000, "photo")  # no exception

    def test_none_size_does_not_raise(self):
        _check_file_size(None, 2000, "photo")  # no exception

    def test_exactly_at_limit_does_not_raise(self):
        _check_file_size(2000, 2000, "photo")  # no exception

    def test_exceeds_limit_raises(self):
        with pytest.raises(FileTooLargeError) as exc_info:
            _check_file_size(3000, 2000, "photo")
        assert exc_info.value.size_bytes == 3000
        assert exc_info.value.limit_bytes == 2000
        assert exc_info.value.label == "photo"


class TestFileTooLargeError:
    def test_attributes(self):
        err = FileTooLargeError("video", 5000, 2000)
        assert err.label == "video"
        assert err.size_bytes == 5000
        assert err.limit_bytes == 2000
        assert "5000" in str(err)
        assert "2000" in str(err)


class TestDownloadWithRetry:
    async def test_succeeds_first_attempt(self):
        bot = AsyncMock()
        tg_file = AsyncMock()
        tg_file.download_as_bytearray.return_value = bytearray(b"data")
        bot.get_file.return_value = tg_file

        result_file, result_data = await _download_with_retry(bot, "file_123")
        assert result_file is tg_file
        assert result_data == bytearray(b"data")
        bot.get_file.assert_called_once_with("file_123")

    @patch("bot.media.RETRY_BASE_DELAY_SEC", 0)
    async def test_retries_on_failure_then_succeeds(self):
        bot = AsyncMock()
        tg_file = AsyncMock()
        tg_file.download_as_bytearray.return_value = bytearray(b"ok")
        bot.get_file.side_effect = [Exception("network"), tg_file]
        tg_file_mock = AsyncMock()
        tg_file_mock.download_as_bytearray.return_value = bytearray(b"ok")

        # First call raises, second succeeds
        bot.get_file.side_effect = [Exception("network"), tg_file_mock]

        result_file, result_data = await _download_with_retry(bot, "file_x")
        assert result_data == bytearray(b"ok")
        assert bot.get_file.call_count == 2

    @patch("bot.media.RETRY_BASE_DELAY_SEC", 0)
    @patch("bot.media.MAX_DOWNLOAD_RETRIES", 2)
    async def test_raises_after_all_retries_exhausted(self):
        bot = AsyncMock()
        bot.get_file.side_effect = Exception("persistent failure")

        with pytest.raises(Exception, match="persistent failure"):
            await _download_with_retry(bot, "file_x")
        assert bot.get_file.call_count == 2


class TestDownloadMedia:
    def _make_message(self, *, photo=None, video=None, video_note=None):
        msg = MagicMock()
        msg.photo = photo
        msg.video = video
        msg.video_note = video_note
        return msg

    async def test_returns_none_when_no_media(self):
        bot = AsyncMock()
        msg = self._make_message()
        result = await download_media(bot, msg)
        assert result is None

    async def test_downloads_photo(self):
        bot = AsyncMock()
        tg_file = AsyncMock()
        tg_file.file_path = "photos/img_001.jpg"
        tg_file.download_as_bytearray.return_value = bytearray(b"\xff\xd8")
        bot.get_file.return_value = tg_file

        photo_size = MagicMock()
        photo_size.file_id = "photo_id"
        photo_size.file_size = 5000

        msg = self._make_message(photo=[photo_size])
        result = await download_media(bot, msg)

        assert isinstance(result, MediaContent)
        assert result.data == b"\xff\xd8"
        assert result.filename == "img_001.jpg"
        assert result.mime_type == "image/jpeg"

    async def test_downloads_video(self):
        bot = AsyncMock()
        tg_file = AsyncMock()
        tg_file.file_path = "videos/clip.mp4"
        tg_file.download_as_bytearray.return_value = bytearray(b"\x00\x00")
        bot.get_file.return_value = tg_file

        video = MagicMock()
        video.file_id = "video_id"
        video.file_size = 10000
        video.file_name = "holiday.mp4"
        video.mime_type = "video/mp4"

        msg = self._make_message(video=video)
        result = await download_media(bot, msg)

        assert isinstance(result, MediaContent)
        assert result.filename == "holiday.mp4"
        assert result.mime_type == "video/mp4"

    async def test_downloads_video_note(self):
        bot = AsyncMock()
        tg_file = AsyncMock()
        tg_file.file_path = "video_notes/note.mp4"
        tg_file.download_as_bytearray.return_value = bytearray(b"\x00")
        bot.get_file.return_value = tg_file

        video_note = MagicMock()
        video_note.file_id = "vnote_id"
        video_note.file_size = 2000

        msg = self._make_message(video_note=video_note)
        result = await download_media(bot, msg)

        assert isinstance(result, MediaContent)
        assert result.filename == "note.mp4"
        assert result.mime_type == "video/mp4"

    async def test_raises_file_too_large_for_photo(self):
        bot = AsyncMock()
        photo_size = MagicMock()
        photo_size.file_id = "photo_id"
        photo_size.file_size = PHOTO_MAX_BYTES + 1

        msg = self._make_message(photo=[photo_size])
        with pytest.raises(FileTooLargeError):
            await download_media(bot, msg)

    async def test_raises_file_too_large_for_video(self):
        bot = AsyncMock()
        video = MagicMock()
        video.file_id = "video_id"
        video.file_size = VIDEO_MAX_BYTES + 1

        msg = self._make_message(video=video)
        with pytest.raises(FileTooLargeError):
            await download_media(bot, msg)

    async def test_photo_with_empty_sizes_returns_none(self):
        bot = AsyncMock()
        msg = self._make_message(photo=[])
        result = await download_media(bot, msg)
        assert result is None
