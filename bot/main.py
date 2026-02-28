"""Entry point for the Telegram → Google Photos sync bot."""

import asyncio
import logging
import sys

from telegram.ext import ApplicationBuilder

from bot.config import Config, get_log_level_int
from bot.database import Database
from bot.google_photos import GooglePhotosClient
from bot.handlers import link_command_handler, media_handler, my_chat_member_handler

logger = logging.getLogger(__name__)


def main() -> None:
    # Python 3.14+ no longer auto-creates an event loop in the main thread.
    # Ensure one exists so run_polling()'s internal get_event_loop() succeeds.
    try:
        asyncio.get_event_loop()
    except RuntimeError:
        asyncio.set_event_loop(asyncio.new_event_loop())

    logging.basicConfig(
        level=get_log_level_int(),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    try:
        config = Config.from_env()
    except ValueError as exc:
        logger.error("Configuration error: %s", exc)
        sys.exit(1)

    db = Database(config.db_path)
    google_photos = GooglePhotosClient(
        client_id=config.google_client_id,
        client_secret=config.google_client_secret,
        refresh_token=config.google_refresh_token,
    )

    async def post_init(application) -> None:
        await db.open()
        application.bot_data["db"] = db
        application.bot_data["google_photos"] = google_photos
        application.bot_data["config"] = config
        logger.info("Bot started — syncing group media to Google Photos")

    async def post_shutdown(_application) -> None:
        await db.close()
        logger.info("Bot stopped — database closed")

    builder = (
        ApplicationBuilder()
        .token(config.telegram_bot_token)
        .post_init(post_init)
        .post_shutdown(post_shutdown)
    )
    if config.telegram_bot_api_url:
        builder = (
            builder
            .base_url(f"{config.telegram_bot_api_url}/bot")
            .local_mode(True)
        )
    application = builder.build()
    application.add_handler(link_command_handler())
    application.add_handler(media_handler())
    application.add_handler(my_chat_member_handler())

    application.run_polling(allowed_updates=["message", "my_chat_member"])


if __name__ == "__main__":
    main()
