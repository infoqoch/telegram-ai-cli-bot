# Setup And Operations

This document holds the setup, runtime, and configuration details that are useful after you decide to use the bot.

For the product overview and value proposition, see [README.md](../README.md).

## Quick Setup

### Prerequisites

- Python 3.11+
- Claude Code and/or Codex CLI installed and logged in
- A Telegram bot token from [@BotFather](https://t.me/BotFather)

### Install

```bash
git clone https://github.com/infoqoch/telegram-ai-cli-bot.git
cd telegram-ai-cli-bot
python -m venv venv && source venv/bin/activate
pip install -e .
```

### Setup Options

#### Option A: Guided setup with Claude Code

```bash
claude
# Then say: "help me set up"
```

Claude reads the project's `CLAUDE.md` and can guide the full setup flow.
That compact guide points Claude back to this document for the full operational details.

#### Option B: Manual setup

```bash
cp .env.example .env
# Edit .env: set TELEGRAM_TOKEN, ALLOWED_CHAT_IDS, ADMIN_CHAT_ID
./run.sh start
```

To find your Telegram chat ID, start the bot temporarily and send `/chatid`.

## Run Commands

```bash
./run.sh start            # Start the bot
./run.sh status           # Check status
./run.sh log              # View app logs
./run.sh restart-soft     # Soft restart (preserves in-flight AI workers)
./run.sh restart-hard     # Hard restart (terminates all workers)
./run.sh stop-soft        # Stop supervisor/main only
./run.sh stop-hard        # Stop everything including workers
./run.sh test             # Run unit tests
./run.sh test-integration # Run integration tests
```

## Security

| Layer | Protection |
|-------|-----------|
| Access control | `ALLOWED_CHAT_IDS` whitelist |
| Authentication | Optional `AUTH_SECRET_KEY` with 30-minute TTL |
| Concurrency | Per-user async locks |
| Session isolation | `session_locks` prevent duplicate execution on the same session |
| Delivery retry | Failed Telegram sends retried every 60s, up to 10 times |
| Message length | `MAX_MESSAGE_LENGTH` = 4096 enforced |

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `TELEGRAM_TOKEN` | (required) | Bot token from BotFather |
| `ALLOWED_CHAT_IDS` | (empty = allow all) | Comma-separated allowed chat IDs |
| `ADMIN_CHAT_ID` | `0` | Chat ID for admin notifications and dev reports |
| `AI_COMMAND` | `claude` | AI CLI command to invoke |
| `SESSION_TIMEOUT_HOURS` | `24` | Hours before an idle session is recycled |
| `SESSION_PURGE_DAYS` | `7` | Days before recycled sessions are deleted |
| `APP_TIMEZONE` | `Asia/Seoul` | Timezone for schedules and date display |
| `REQUIRE_AUTH` | `false` | Set to true to enable authentication |
| `AUTH_SECRET_KEY` | (required if REQUIRE_AUTH=true) | Secret key for authentication |
| `AUTH_TIMEOUT_MINUTES` | `30` | How long an authenticated session stays valid |
| `WORKING_DIR` | (project root) | Bot working directory |
| `ALLOWED_PROJECT_PATHS` | `~/AiSandbox/*,~/Projects/*` | Glob patterns for allowed workspace paths |
| `GOOGLE_SERVICE_ACCOUNT_FILE` | (none) | Path to Google service account JSON (Calendar plugin) |
| `GOOGLE_CALENDAR_ID` | `primary` | Google Calendar ID to use |
| `BOT_DATA_DIR` | `.data/` | Root directory for runtime files (locks, PID, logs) |
| `BOT_LOG_DIR` | `.data/logs/` | Log file directory |
| `BOT_MAIN_MENU_PLUGINS` | (none) | Comma-separated plugin names to promote to the main menu |
| `DEFAULT_MODEL_CLAUDE` | (none) | Default Claude model profile (overrides built-in default) |
| `DEFAULT_MODEL_CODEX` | (none) | Default Codex model profile (overrides built-in default) |
