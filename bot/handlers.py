"""Telegram message handlers: sync group media to Google Photos."""

import logging
from typing import Any

from telegram import ChatMemberUpdated, Update
from telegram.ext import ChatMemberHandler, ContextTypes, MessageHandler, filters

from bot.google_photos import GooglePhotosClient, GooglePhotosError
from bot.media import FileTooLargeError, download_media

logger = logging.getLogger(__name__)

MEDIA_FILTER = filters.PHOTO | filters.VIDEO | filters.VIDEO_NOTE


async def handle_media(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Handle photo, video, or video_note in a group: dedup, download, upload to
    Google Photos album named after the group, mark processed.

    Detects group renames and creates new albums for the new title.
    Skips files that exceed Google Photos size limits.
    """
    if not update.message or not update.effective_chat:
        return

    message = update.message
    chat = update.effective_chat
    chat_id = chat.id
    message_id = message.id

    if chat.type not in ("group", "supergroup"):
        return

    bot_data: dict[str, Any] = context.bot_data
    db = bot_data.get("db")
    google_photos: GooglePhotosClient | None = bot_data.get("google_photos")
    config = bot_data.get("config")

    if not db or not google_photos or not config:
        logger.error("Handler missing db/google_photos/config in bot_data")
        return

    allowed = config.allowed_group_ids
    if allowed and chat_id not in allowed:
        logger.debug("Skipping chat_id=%s (not in ALLOWED_GROUP_IDS)", chat_id)
        return

    if await db.is_processed(chat_id, message_id):
        logger.debug("Already processed chat_id=%s message_id=%s", chat_id, message_id)
        return

    chat_title = chat.title or f"Chat_{chat_id}"
    if not chat_title.strip():
        chat_title = f"Chat_{chat_id}"

    # Detect group rename: if the stored title differs, invalidate old album cache
    stored_title = await db.get_chat_title(chat_id)
    if stored_title and stored_title != chat_title:
        logger.info(
            "Group renamed chat_id=%s old_title=%r new_title=%r",
            chat_id, stored_title, chat_title,
        )
        await db.delete_album_cache(stored_title)
    await db.set_chat_title(chat_id, chat_title)

    try:
        content = await download_media(context.bot, message)
    except FileTooLargeError as exc:
        logger.warning(
            "Skipping oversized file chat_id=%s message_id=%s label=%s "
            "size=%d limit=%d",
            chat_id, message_id, exc.label, exc.size_bytes, exc.limit_bytes,
        )
        return
    except Exception:
        logger.exception(
            "Failed to download media chat_id=%s message_id=%s", chat_id, message_id,
        )
        return

    if not content:
        logger.debug(
            "No supported media chat_id=%s message_id=%s", chat_id, message_id,
        )
        return

    try:
        album_id = await db.get_album_id(chat_title)
        if not album_id:
            album_id = await google_photos.get_or_create_album(chat_title)
            await db.set_album_id(chat_title, album_id)

        await google_photos.upload_media(
            content.data,
            content.filename,
            content.mime_type,
            album_id,
        )
    except GooglePhotosError as exc:
        logger.error(
            "Google Photos upload failed chat_id=%s message_id=%s "
            "status=%s error=%s",
            chat_id, message_id, exc.status_code, exc,
        )
        return
    except Exception:
        logger.exception(
            "Upload failed chat_id=%s message_id=%s", chat_id, message_id,
        )
        return

    await db.mark_processed(chat_id, message_id)
    logger.info(
        "Synced media album=%r chat_id=%s message_id=%s filename=%s",
        chat_title, chat_id, message_id, content.filename,
    )


async def handle_my_chat_member(
    update: Update, context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """Log when the bot is removed from a group (kicked or left)."""
    change: ChatMemberUpdated | None = update.my_chat_member
    if not change:
        return

    new_status = change.new_chat_member.status
    chat = change.chat

    if new_status in ("left", "kicked"):
        logger.info(
            "Bot removed from group chat_id=%s title=%r status=%s",
            chat.id, chat.title, new_status,
        )


def media_handler() -> MessageHandler[Update, ContextTypes.DEFAULT_TYPE]:
    """Return a MessageHandler for photo, video, and video_note."""
    return MessageHandler(MEDIA_FILTER, handle_media)


def my_chat_member_handler() -> ChatMemberHandler:
    """Return a handler for bot membership status changes."""
    return ChatMemberHandler(handle_my_chat_member, ChatMemberHandler.MY_CHAT_MEMBER)
