# Telegram Group Media → Google Photos Sync Bot

A Telegram bot that automatically syncs media (photos, videos, circular/quick videos) from group chats to Google Photos. Each group gets its own album named after the group.

## Features

- Syncs photos, videos, and circular video notes from Telegram groups to Google Photos
- Each group gets a dedicated album named after the group
- Deduplication — never processes the same message twice
- Automatic album management when a group is renamed (new album for new name)
- Retry with exponential backoff on transient failures (network, API rate limits)
- File size validation against Google Photos upload limits before downloading
- Graceful handling of bot removal from groups
- Optional allowlist to restrict which groups are synced
- Local Bot API server support for files larger than 20 MB

## Stack

| Layer | Choice | Rationale |
|-------|--------|-----------|
| Language | Python 3.12+ | Mature Telegram & Google API libraries, async-native |
| Telegram SDK | `python-telegram-bot` v21+ | Async, actively maintained, full Bot API coverage |
| Google Photos | `google-auth` + REST API | Google Photos Library API via HTTP (no official Python SDK for Photos) |
| HTTP Client | `httpx` | Async HTTP for Google API calls and media downloads |
| Persistence | SQLite via `aiosqlite` | Lightweight state tracking (processed messages, album IDs) |
| Deployment | Docker + Docker Compose | Single-command deployment, env-based configuration |

## Architecture

```
┌─────────────────────┐      ┌──────────────────┐      ┌─────────────────┐
│   Telegram Groups   │─────▶│   Bot Service    │─────▶│  Google Photos   │
│  (photos/videos)    │      │                  │      │  (albums/media)  │
└─────────────────────┘      │  ┌────────────┐  │      └─────────────────┘
                             │  │  SQLite DB │  │
                             │  │ (state)    │  │
                             │  └────────────┘  │
                             └──────────────────┘
```

### How It Works

1. Bot is added to a Telegram group
2. A user sends a photo, video, or circular video in the group
3. Bot detects the media message via handler
4. Bot checks SQLite to skip already-processed messages
5. Bot downloads the media file from Telegram (with retry on failure)
6. Bot looks up (or creates) a Google Photos album named after the group
7. Bot uploads the media to that album (with retry on 429/5xx)
8. Bot records the message ID in SQLite to prevent future duplicates

## Prerequisites

- **Docker** and **Docker Compose** (for production deployment)
- **Python 3.12+** (only needed for the one-time token setup scripts)
- A **Google Cloud** project with the Photos Library API enabled
- A **Telegram bot** created via [@BotFather](https://t.me/BotFather)
- **Telegram API credentials** from [my.telegram.org](https://my.telegram.org) (required for the local Bot API server)

## Quick Start

```bash
# 1. Clone and configure
git clone <repo-url> && cd telegramPhotos
cp .env.example .env
# Edit .env — fill in all required variables (see setup guides below)

# 2. Obtain Google OAuth refresh token (one-time, on your machine)
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python scripts/obtain_token.py
# Copy the printed GOOGLE_REFRESH_TOKEN into .env

# 3. Log out from official API (one-time, before using local Bot API server)
python scripts/logout_from_telegram_org.py

# 4. Deploy
docker compose up -d

# 5. Add the bot to your Telegram group(s)
# The bot will automatically sync new media to Google Photos
```

## Setup Guides

### 1. Google Cloud Project

The Google Photos Library API requires OAuth 2.0 **user credentials** (not a service account). You need to create a Google Cloud project, enable the API, and obtain a refresh token.

#### Create project and enable API

1. Go to [Google Cloud Console](https://console.cloud.google.com/)
2. Click **Select a project** → **New Project**, give it a name, and create it
3. Make sure the new project is selected in the top bar
4. Go to **APIs & Services** → **Library**
5. Search for **Photos Library API** and click **Enable**

#### Configure OAuth consent screen

1. Go to **APIs & Services** → **OAuth consent screen**
2. Select **External** user type (unless you have a Google Workspace org) and click **Create**
3. Fill in the required fields:
   - **App name**: any name (e.g., "Telegram Photos Bot")
   - **User support email**: your email
   - **Developer contact**: your email
4. Click **Save and Continue**
5. On the **Scopes** page, click **Add or Remove Scopes** and add:
   - `https://www.googleapis.com/auth/photoslibrary.appendonly`
   - `https://www.googleapis.com/auth/photoslibrary.readonly.appcreateddata`
6. Click **Save and Continue** through the remaining steps
7. On the **Test users** page, add the Google account that owns the Photos library you want to sync to

> **Note:** While the app is in "Testing" publishing status, only test users you explicitly add can authorize. The refresh token works indefinitely for test users — no need to publish the app.

#### Create OAuth credentials

1. Go to **APIs & Services** → **Credentials**
2. Click **Create Credentials** → **OAuth client ID**
3. Application type: **Desktop app**
4. Give it a name and click **Create**
5. Copy the **Client ID** and **Client Secret** — add them to your `.env` file as `GOOGLE_CLIENT_ID` and `GOOGLE_CLIENT_SECRET`

#### Obtain refresh token

Run the setup script on a machine with a browser:

```bash
source .venv/bin/activate
python scripts/obtain_token.py
```

The script opens a browser for Google sign-in. After authorizing, it prints a refresh token. Copy it into your `.env` file as `GOOGLE_REFRESH_TOKEN`.

You can also pass credentials as arguments instead of environment variables:

```bash
python scripts/obtain_token.py --client-id YOUR_ID --client-secret YOUR_SECRET
```

### 2. Telegram Bot

#### Create the bot

1. Open Telegram and message [@BotFather](https://t.me/BotFather)
2. Send `/newbot` and follow the prompts to name your bot
3. BotFather responds with a **bot token** — copy it into `.env` as `TELEGRAM_BOT_TOKEN`

#### Configure privacy mode

By default, bots in groups only receive messages that are commands or mention the bot. To see all media messages, you need **one** of:

- **Disable privacy mode**: message BotFather → `/setprivacy` → select your bot → **Disable**
- **Grant admin rights**: add the bot as a group admin (this automatically bypasses privacy mode)

#### Get Telegram API credentials (required for local Bot API server)

1. Go to [my.telegram.org](https://my.telegram.org) and log in with your Telegram account
2. Navigate to **API development tools**
3. Create an application (if you haven't already) — the app name/description don't matter
4. Copy **API ID** and **API Hash** into `.env` as `TELEGRAM_API_ID` and `TELEGRAM_API_HASH`

#### Finding group IDs

To restrict syncing to specific groups via `ALLOWED_GROUP_IDS`, you need the numeric chat IDs. The easiest methods:

- **From bot logs**: start the bot without `ALLOWED_GROUP_IDS`, send a photo in the group, and check the logs — the chat_id is logged with each synced message
- **Via @userinfobot**: add [@userinfobot](https://t.me/userinfobot) to the group, and it will report the group's chat ID

Group IDs are negative numbers (e.g., `-1001234567890`).

### 3. Configuration Reference

All configuration is via environment variables (set in `.env`):

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `TELEGRAM_BOT_TOKEN` | Yes | — | Bot token from [@BotFather](https://t.me/BotFather) |
| `TELEGRAM_API_ID` | Yes (Docker) | — | API ID from [my.telegram.org](https://my.telegram.org) — used by the local Bot API server |
| `TELEGRAM_API_HASH` | Yes (Docker) | — | API hash from [my.telegram.org](https://my.telegram.org) — used by the local Bot API server |
| `TELEGRAM_BOT_API_URL` | No | `http://telegram-bot-api:8081` | Local Bot API base URL. Set empty to use official API only (20 MB limit). |
| `GOOGLE_CLIENT_ID` | Yes | — | OAuth 2.0 client ID from Google Cloud Console |
| `GOOGLE_CLIENT_SECRET` | Yes | — | OAuth 2.0 client secret |
| `GOOGLE_REFRESH_TOKEN` | Yes | — | Long-lived refresh token (obtained via `scripts/obtain_token.py`) |
| `ALLOWED_GROUP_IDS` | No | *(all groups)* | Comma-separated list of group chat IDs to restrict syncing |
| `DB_PATH` | No | `/data/bot.db` | SQLite database file path |
| `LOG_LEVEL` | No | `INFO` | Logging level: `DEBUG`, `INFO`, `WARNING`, `ERROR`, `CRITICAL` |

## Deployment

### Docker Compose (recommended)

The default `docker-compose.yml` runs two services:

- **telegram-bot-api** — a local Telegram Bot API server that removes the 20 MB download limit
- **bot** — the Python bot service

```bash
# Start in background
docker compose up -d

# View logs
docker compose logs -f bot

# Stop
docker compose down

# Rebuild after code changes
docker compose up -d --build
```

#### Data persistence

Two named Docker volumes are used:

| Volume | Purpose |
|--------|---------|
| `bot-data` | SQLite database (processed messages, album cache) — mounted at `/data` |
| `telegram-bot-api-data` | Local Bot API server state — shared read-only with the bot for local file access |

To back up the database:

```bash
docker compose cp bot:/data/bot.db ./bot.db.backup
```

#### Log out from official API (one-time)

Before the local Bot API server can receive updates, the bot must be logged out from the official `api.telegram.org`:

```bash
source .venv/bin/activate
python scripts/logout_from_telegram_org.py
```

This only needs to be done once per bot token. After logout, the bot receives updates exclusively from your local server.

> **Important:** After logging out, the bot will not work with the official API for ~10 minutes. Plan accordingly.

### Official API only (no local server)

If you don't need files larger than 20 MB, you can skip the local Bot API server entirely:

1. Set `TELEGRAM_BOT_API_URL=` (empty value) in `.env`
2. **Do not** run `logout_from_telegram_org.py`
3. Run only the bot service:

```bash
docker compose up -d bot
```

Or run directly with Python:

```bash
source .venv/bin/activate
python -m bot.main
```

### Local development

For development, run the local Bot API server in Docker and the bot on your host:

1. Set `TELEGRAM_BOT_API_URL=http://localhost:8081` in `.env`
2. Start the API server:

```bash
docker compose up -d telegram-bot-api
```

3. Run the bot:

```bash
source .venv/bin/activate
python -m bot.main
```

## Running Tests

```bash
source .venv/bin/activate
pip install -r requirements.txt

# Run all tests
python -m pytest tests/ -v

# Run a specific test file
python -m pytest tests/test_handlers.py -v

# Run with short output
python -m pytest tests/
```

Tests use an in-memory SQLite database and mock all external services (Telegram API, Google Photos API). No credentials or network access needed.

## Project Structure

```
telegramPhotos/
├── bot/
│   ├── __init__.py
│   ├── main.py              # Entry point, bot initialization
│   ├── config.py            # Environment variable loading & validation
│   ├── handlers.py          # Telegram message handlers (media sync, bot removal)
│   ├── media.py             # Media download with retry and size validation
│   ├── google_photos.py     # Google Photos API client (auth, albums, uploads, retry)
│   └── database.py          # SQLite operations (dedup, album cache, title tracking)
├── scripts/
│   ├── obtain_token.py      # One-time: get Google OAuth refresh token
│   └── logout_from_telegram_org.py  # One-time: move bot to local API server
├── tests/
│   ├── __init__.py
│   ├── conftest.py           # Shared test fixtures
│   ├── test_database.py
│   ├── test_google_photos.py
│   ├── test_handlers.py
│   └── test_media.py
├── pytest.ini
├── Dockerfile
├── docker-compose.yml
├── requirements.txt
├── .env.example
├── .gitignore
└── README.md
```

## Troubleshooting

### Bot doesn't respond to media in the group

- **Privacy mode**: make sure privacy mode is disabled (`/setprivacy` → Disabled in BotFather) or the bot has admin rights in the group.
- **Allowed groups**: if `ALLOWED_GROUP_IDS` is set, verify the group's chat ID is in the list.
- **Logs**: check `docker compose logs -f bot` for errors.

### "File too large" warnings in logs

The bot validates file sizes before download. Google Photos limits: **200 MB** for photos, **10 GB** for videos. Files exceeding these limits are skipped with a warning.

If you're hitting Telegram's 20 MB download limit, make sure the local Bot API server is running and the bot is logged out from the official API.

### Google Photos upload fails with 429

The Google Photos Library API has rate limits. The bot automatically retries with exponential backoff (starting at 30 seconds, up to 4 attempts). If uploads consistently fail:

- Check that your Google Cloud project hasn't exceeded its quota (Cloud Console → APIs & Services → Photos Library API → Quotas)
- Reduce the volume of media being sent in groups, or add a delay between messages

### "Configuration error" on startup

The bot validates all required environment variables at startup. Check that `.env` contains non-empty values for:

- `TELEGRAM_BOT_TOKEN`
- `GOOGLE_CLIENT_ID`
- `GOOGLE_CLIENT_SECRET`
- `GOOGLE_REFRESH_TOKEN`

### Token refresh fails

If the Google refresh token stops working:

1. Verify the Google Cloud project still exists and the Photos Library API is enabled
2. Check that your Google account is still listed as a test user (if the app is in "Testing" status)
3. Re-run `python scripts/obtain_token.py` to get a new refresh token

### Bot API server: "bot logged out" issues

After running `logout_from_telegram_org.py`, wait ~10 minutes before switching back to the official API. If you need to switch:

1. Stop the local Bot API server
2. Set `TELEGRAM_BOT_API_URL=` in `.env`
3. Wait 10 minutes
4. Restart the bot — it will use the official API

### Group renamed — old album stays

When a group is renamed, the bot creates a new album with the new name. Previously synced media remains in the old album. This is by design — the Google Photos Library API (with `appendonly` scope) does not support renaming albums.

## License

MIT
