# Telegram AI CLI Bot

Control local AI coding agents (Claude Code, Codex CLI) from your phone via Telegram.

Use your existing Claude Code / Codex CLI login from Telegram, create new sessions remotely, continue the sessions you already have on your machine, and keep longer-running work stable.

---

## Why It Is Practical

- Use the CLI subscriptions and logins you already have; no separate API-first setup is required
- Chat with Claude Code or Codex from Telegram wherever you are
- Create new sessions from Telegram when you want fresh work
- Import local CLI sessions and continue them from Telegram without starting over

## Why Sessions Matter

- Multi-session workflow across providers, projects, and tasks
- Workspace and folder aware execution that applies each project's `CLAUDE.md`
- Session switching, queueing, and session-level isolation for safer long-running work
- Fast-path plugin handling so simple actions do not always pay the AI latency cost

## Why It Feels Stable

- Long-running work runs in detached workers instead of living only in the main bot process
- SQLite-backed state adds persistence for locks, queued work, and delivery tracking
- Delivery retry and persistent queueing make responses less likely to disappear on restarts or send failures

## Why It Extends

- Scheduler-driven work for chat, workspace, folder, and plugin actions
- MCP-backed access to live bot data during AI work
- An extension surface for custom plugins, using the built-ins as reference implementations

## Built-In Plugins

Todo, Memo, Diary, Calendar, and Weather ship by default.

Treat them as both useful defaults and reference implementations for extension. Detailed behavior belongs in the plugin spec, not in this README:

- Built-in plugin spec: [docs/SPEC_PLUGINS_BUILTIN.md](docs/SPEC_PLUGINS_BUILTIN.md)
- Extension rules and plugin interfaces: [CLAUDE.md](CLAUDE.md)

---

## Quick Start

### 1. Prerequisites

- **Python 3.11+**
- **Claude Code** and/or **Codex CLI** installed and logged in
  ```bash
  claude --version   # Claude Code
  codex --version    # Codex CLI
  ```

### 2. Create a Telegram Bot

1. Open [@BotFather](https://t.me/BotFather) and run `/newbot`
2. Copy the API token

### 3. Install and Run

```bash
git clone https://github.com/infoqoch/telegram-ai-cli-bot.git
cd telegram-ai-cli-bot
python -m venv venv && source venv/bin/activate
pip install -e .
```

#### Option A: Setup with Claude Code (recommended)

If you have [Claude Code](https://claude.ai/claude-code) installed, let it guide you through setup interactively:

```bash
claude
# Then say: "help me set up"
```

Claude reads the project's `CLAUDE.md` automatically and walks you through creating `.env`, configuring tokens, and starting the bot.

#### Option B: Manual setup

```bash
cp .env.example .env
# Edit .env: set TELEGRAM_TOKEN, ALLOWED_CHAT_IDS, ADMIN_CHAT_ID
./run.sh start
```

> **Find your chat ID:** Start the bot temporarily, send `/chatid`, then add the ID to `.env`.

### Run Commands

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

---

## Security

| Layer | Protection |
|-------|-----------|
| Access control | `ALLOWED_CHAT_IDS` whitelist |
| Authentication | Optional `AUTH_SECRET_KEY` with 30-minute TTL |
| Concurrency | Per-user async locks |
| Session isolation | `session_locks` prevent duplicate execution on the same session |
| Delivery retry | Failed Telegram sends retried every 60s, up to 10 times |
| Message length | `MAX_MESSAGE_LENGTH` = 4096 enforced |

---

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

---

## Documentation

| Doc | Content |
|-----|---------|
| [CLAUDE.md](CLAUDE.md) | Development rules, architecture contracts, extension interfaces |
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | Runtime boundaries and code ownership |
| [docs/SPEC.md](docs/SPEC.md) | UI/UX specification, session/schedule/restart scenarios |
| [docs/SPEC_PLUGINS_BUILTIN.md](docs/SPEC_PLUGINS_BUILTIN.md) | Builtin plugin UI/UX specifications |

---

## Built With

Developed with [Claude Code](https://claude.ai/claude-code) and [Codex CLI](https://github.com/openai/codex).

## License

MIT
