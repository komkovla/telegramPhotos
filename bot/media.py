"""Download media from Telegram messages (photo, video, video_note) as bytes with metadata."""

import asyncio
import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from telegram import Bot, File, Message

logger = logging.getLogger(__name__)

# Google Photos upload limits
PHOTO_MAX_BYTES = 200 * 1024 * 1024  # 200 MB
VIDEO_MAX_BYTES = 10 * 1024 * 1024 * 1024  # 10 GB

MAX_DOWNLOAD_RETRIES = 3
RETRY_BASE_DELAY_SEC = 2


class FileTooLargeError(Exception):
    """Raised when a media file exceeds the upload size limit."""

    def __init__(self, label: str, size_bytes: int, limit_bytes: int) -> None:
        self.label = label
        self.size_bytes = size_bytes
        self.limit_bytes = limit_bytes
        super().__init__(
            f"File {label} size={size_bytes} exceeds limit={limit_bytes}"
        )


@dataclass(frozen=True)
class MediaContent:
    """Downloaded media: raw bytes plus filename and MIME type for upload."""

    data: bytes
    filename: str
    mime_type: str


async def download_media(bot: "Bot", message: "Message") -> MediaContent | None:
    """
    If the message contains a photo, video, or video_note, download it and
    return bytes plus filename and MIME type. Otherwise return None.

    Raises ``FileTooLargeError`` when the file exceeds Google Photos limits.
    """
    if message.photo:
        return await _download_photo(bot, message.photo)
    if message.video:
        return await _download_video(bot, message.video)
    if message.video_note:
        return await _download_video_note(bot, message.video_note)
    return None


async def _download_with_retry(bot: "Bot", file_id: str) -> tuple["File", bytearray]:
    """Download a file from Telegram with retry on transient failures."""
    last_exc: Exception | None = None

    for attempt in range(MAX_DOWNLOAD_RETRIES):
        if attempt > 0:
            delay = RETRY_BASE_DELAY_SEC * (2 ** (attempt - 1))
            logger.warning(
                "Telegram download retry attempt=%d/%d delay=%.1fs file_id=%s",
                attempt + 1, MAX_DOWNLOAD_RETRIES, delay, file_id,
            )
            await asyncio.sleep(delay)
        try:
            tg_file = await bot.get_file(file_id)
            data = await tg_file.download_as_bytearray()
            return tg_file, data
        except Exception as exc:
            last_exc = exc
            logger.warning(
                "Telegram download failed attempt=%d/%d file_id=%s error=%s",
                attempt + 1, MAX_DOWNLOAD_RETRIES, file_id, exc,
            )

    raise last_exc  # type: ignore[misc]


def _check_file_size(file_size: int | None, limit: int, label: str) -> None:
    """Raise ``FileTooLargeError`` if ``file_size`` exceeds ``limit``."""
    if file_size is not None and file_size > limit:
        raise FileTooLargeError(label, file_size, limit)


async def _download_photo(bot: "Bot", photo_sizes: list) -> MediaContent | None:
    if not photo_sizes:
        return None
    largest = photo_sizes[-1]
    _check_file_size(getattr(largest, "file_size", None), PHOTO_MAX_BYTES, "photo")
    tg_file, data = await _download_with_retry(bot, largest.file_id)
    filename = _safe_filename(tg_file.file_path or "photo.jpg", "photo.jpg")
    logger.debug("Downloaded photo file_id=%s size=%d", largest.file_id, len(data))
    return MediaContent(data=bytes(data), filename=filename, mime_type="image/jpeg")


async def _download_video(bot: "Bot", video) -> MediaContent | None:
    _check_file_size(getattr(video, "file_size", None), VIDEO_MAX_BYTES, "video")
    tg_file, data = await _download_with_retry(bot, video.file_id)
    filename = getattr(video, "file_name", None) or _safe_filename(
        tg_file.file_path or "video.mp4", "video.mp4"
    )
    mime_type = getattr(video, "mime_type", None) or "video/mp4"
    logger.debug("Downloaded video file_id=%s size=%d", video.file_id, len(data))
    return MediaContent(data=bytes(data), filename=filename, mime_type=mime_type)


async def _download_video_note(bot: "Bot", video_note) -> MediaContent | None:
    _check_file_size(getattr(video_note, "file_size", None), VIDEO_MAX_BYTES, "video_note")
    tg_file, data = await _download_with_retry(bot, video_note.file_id)
    filename = _safe_filename(tg_file.file_path or "video_note.mp4", "video_note.mp4")
    logger.debug("Downloaded video_note file_id=%s size=%d", video_note.file_id, len(data))
    return MediaContent(data=bytes(data), filename=filename, mime_type="video/mp4")


def _safe_filename(candidate: str, fallback: str) -> str:
    """Use candidate if it has a safe extension, otherwise fallback."""
    candidate = candidate.strip()
    if not candidate:
        return fallback
    if "/" in candidate or "\\" in candidate:
        candidate = candidate.replace("\\", "/").split("/")[-1]
    if not candidate:
        return fallback
    return candidate
