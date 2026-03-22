# Telegram AI CLI Bot

Control local AI coding agents (Claude Code, Codex CLI) from your phone via Telegram.

---

## Why This Project?

| | |
|---|---|
| **Reuse existing CLI subscriptions** | Works with your existing Claude Code / Codex CLI login — no API key needed |
| **Remote access from anywhere** | Manage local AI agent sessions from your phone, on the go |
| **Multi-session management** | Independent sessions per project, auto-recycled after 24h idle |
| **Plugin fast-path** | Instant responses for memo, todo, weather — no AI call required |
| **MCP data bridge** | Let your AI query the bot's own SQLite database for context-aware answers |
| **Long-running task support** | Detached worker survives bot restarts; AI responses are delivered end-to-end |
| **Security** | Chat ID whitelist + optional authentication layer |

---

## Architecture

The bot has three layers working in concert:

```
┌─────────────────────────────────────────────────────┐
│  Telegram Client (your phone)                        │
└──────────────────────┬──────────────────────────────┘
                       │
┌──────────────────────▼──────────────────────────────┐
│  Layer 1: Plugin Launcher                            │
│  Pattern-matched fast-path (0.1s)                    │
│  Todo, Memo, Diary, Calendar, Weather                │
└──────────────────────┬──────────────────────────────┘
                       │ no match
┌──────────────────────▼──────────────────────────────┐
│  Layer 2: AI Conversation                            │
│  src.main → spawn → src.worker_job                   │
│  Claude Code CLI / Codex CLI (subprocess)            │
│  Detached worker — survives soft restarts            │
└──────────────────────┬──────────────────────────────┘
                       │ tool calls
┌──────────────────────▼──────────────────────────────┐
│  Layer 3: MCP Data Bridge                            │
│  query_db, db_schema, list_events, create_event      │
│  AI queries bot's own SQLite for live context        │
└─────────────────────────────────────────────────────┘
```

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

## Features

### Message Routing

Every incoming message flows through four stages in order:

```
1. Command handler   /menu, /new, /sl, /tasks — instant response
        ↓ (not a command)
2. ForceReply check  Route reply to its originating handler
        ↓ (not a reply)
3. Plugin Launcher   Pattern-match → instant response (no AI call)
        ↓ (no match)
4. AI dispatch       Send to Claude Code or Codex via detached worker
```

### Plugin System

Five built-in plugins handle common tasks instantly without touching the AI:

| Plugin | Keywords | What it does |
|--------|----------|--------------|
| **Todo** | `todo`, `할일`, `투두` | Add, list, complete todo items |
| **Memo** | `memo`, `메모` | Save and search text notes |
| **Diary** | `diary`, `일기` | Daily journal entries |
| **Calendar** | `calendar`, `cal`, `캘린더`, `일정`, `달력` | Google Calendar integration |
| **Weather** | `weather`, `날씨`, `기온` | Real-time weather via Open-Meteo |

**Keyword + natural language → AI with context:** Type a keyword followed by a request (e.g., `할일 오늘 뭐 해야돼?`) and the AI automatically receives the plugin's context + MCP data access. No button needed.

Each plugin also provides a "✨ AI Work" button for contextual AI assistance with full MCP data access.

**Adding custom plugins:** Drop a `plugin.py` into `plugins/custom/` — no core code changes required.

### Session Management

- **Multi-provider:** Switch between Claude Code and Codex with `/select_ai`
- **Named sessions:** `/new opus coding-helper` creates a session with a name
- **Auto-recycling:** Sessions idle for 24h are recycled; deleted after 7 days
- **Random nicknames:** Sessions get friendly names like `Buddy📚` or `Sparky🤖`
- **Import local sessions:** Bring in sessions you started directly in the terminal

```
/menu          → Main hub (sessions, workspaces, scheduler, plugins)
/new           → Create a new session
/sl            → List all sessions
/session       → Current session info
/select_ai     → Switch provider (Claude / Codex)
/tasks         → View active AI tasks
```

### MCP Data Bridge

When Claude Code runs inside the bot's workspace, it can call MCP tools to query live data:

| Tool | What it does |
|------|-------------|
| `query_db` | Run SQL on the bot's SQLite database (SELECT/INSERT/UPDATE/DELETE) |
| `db_schema` | Explore table structure and column definitions |
| `list_events` | Fetch upcoming Google Calendar events |
| `create_event` | Create a new calendar event |

This lets the AI answer questions like "what todos do I have this week?" by reading real data rather than relying on conversation history.

### Scheduler

Create scheduled AI tasks from the Telegram UI:

- **Chat schedules:** Run a prompt on a cron schedule using the current provider
- **Workspace schedules:** Run inside a specific project directory (applies that project's `CLAUDE.md`)
- **Plugin schedules:** Trigger a plugin action (e.g., daily diary reminder) on a schedule

```
Scheduler UI flow:
  Hour (00–23) → Minute (5-min steps) → Daily / One-time → Message → Register
```

Schedules are sorted by next execution time. Timezone is controlled by `APP_TIMEZONE` (default: `Asia/Seoul`).

### Detached Worker Architecture

Long-running AI tasks run in a separate process so the bot stays responsive:

```
src.supervisor          watches and restarts src.main
    └─ src.main         receives requests, creates job record, spawns worker, returns immediately
         └─ src.worker_job   owns the CLI subprocess, streams response to Telegram, drains queue
```

`./run.sh restart-soft` restarts only supervisor and main — in-flight workers continue running and deliver their responses. The DB (`message_log`, `queued_messages`, `session_locks`) is the source of truth for job state, not memory.

### Workspace Sessions

Bind a session to a local directory. The AI operates with that project's `CLAUDE.md` as its system context, making it project-aware without manual setup.

### Security

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
| `APP_TIMEZONE` | `Asia/Seoul` | Timezone for schedules and date display |
| `REQUIRE_AUTH` | `true` | Require authentication before bot access |
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
| [docs/SPEC.md](docs/SPEC.md) | UI/UX specification, session/schedule/restart scenarios |
| [docs/SPEC_PLUGINS_BUILTIN.md](docs/SPEC_PLUGINS_BUILTIN.md) | Builtin plugin UI/UX specifications |

---

## Built With

Developed with [Claude Code](https://claude.ai/claude-code) and [Codex CLI](https://github.com/openai/codex).

## License

MIT
