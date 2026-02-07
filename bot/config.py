"""Environment configuration loading and validation."""

import os
import logging
from dataclasses import dataclass


LOG_LEVELS = frozenset({"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"})


DEFAULT_TELEGRAM_BOT_API_URL = "http://telegram-bot-api:8081"


@dataclass(frozen=True)
class Config:
    """Application configuration from environment variables."""

    telegram_bot_token: str
    google_client_id: str
    google_client_secret: str
    google_refresh_token: str
    telegram_bot_api_url: str
    allowed_group_ids: tuple[int, ...]
    db_path: str
    log_level: str

    @classmethod
    def from_env(cls) -> "Config":
        """Load and validate configuration from environment. Raises ValueError on failure."""
        token = _required("TELEGRAM_BOT_TOKEN")
        client_id = _required("GOOGLE_CLIENT_ID")
        client_secret = _required("GOOGLE_CLIENT_SECRET")
        refresh_token = _required("GOOGLE_REFRESH_TOKEN")

        raw_api_url = os.environ.get("TELEGRAM_BOT_API_URL")
        if raw_api_url is None:
            telegram_bot_api_url = DEFAULT_TELEGRAM_BOT_API_URL
        else:
            telegram_bot_api_url = raw_api_url.strip()

        allowed = _allowed_group_ids(os.environ.get("ALLOWED_GROUP_IDS", ""))
        db_path = os.environ.get("DB_PATH", "/data/bot.db").strip() or "/data/bot.db"
        log_level = _log_level(os.environ.get("LOG_LEVEL", "INFO"))

        return cls(
            telegram_bot_token=token,
            google_client_id=client_id,
            google_client_secret=client_secret,
            google_refresh_token=refresh_token,
            telegram_bot_api_url=telegram_bot_api_url,
            allowed_group_ids=allowed,
            db_path=db_path,
            log_level=log_level,
        )


def _required(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise ValueError(f"Missing required environment variable: {name}")
    return value


def _allowed_group_ids(raw: str) -> tuple[int, ...]:
    if not raw.strip():
        return ()
    ids: list[int] = []
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        try:
            ids.append(int(part))
        except ValueError:
            raise ValueError(
                f"ALLOWED_GROUP_IDS must be comma-separated integers, got: {part!r}"
            )
    return tuple(ids)


def _log_level(raw: str) -> str:
    value = (raw or "INFO").strip().upper()
    if value not in LOG_LEVELS:
        raise ValueError(
            f"LOG_LEVEL must be one of {sorted(LOG_LEVELS)}, got: {raw!r}"
        )
    return value


def get_log_level_int() -> int:
    """Return the logging module constant for the configured level."""
    level_name = os.environ.get("LOG_LEVEL", "INFO").strip().upper()
    if level_name not in LOG_LEVELS:
        level_name = "INFO"
    return getattr(logging, level_name)
