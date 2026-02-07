#!/usr/bin/env python3
"""
One-time script to move the bot from api.telegram.org to your local Bot API server.

Call this once before (or after) switching to a local server so the bot stops receiving
updates from the official API and receives them only from your local instance.

Requires TELEGRAM_BOT_TOKEN in the environment. Uses the default (official) API.
"""

import asyncio
import os
import sys


def main() -> int:
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    if not token:
        print("Error: Set TELEGRAM_BOT_TOKEN in the environment.", file=sys.stderr)
        return 1

    async def run() -> None:
        from telegram import Bot

        bot = Bot(token)
        await bot.log_out()
        print("Bot logged out from api.telegram.org. You can now use the local Bot API server.")

    asyncio.run(run())
    return 0


if __name__ == "__main__":
    sys.exit(main())
