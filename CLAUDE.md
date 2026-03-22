# Telegram AI CLI Bot - Project Rules

## Quick Setup (for new users)

If the user says "setup", "help me set up", or this is a fresh clone without `.env`, guide them through the setup process:

1. **Check prerequisites:**
   - Python 3.11+ installed
   - Claude Code (`claude --version`) or Codex CLI (`codex --version`) installed and logged in
   - A Telegram bot token from [@BotFather](https://t.me/BotFather)

2. **Create `.env` from template:**
   ```bash
   cp .env.example .env
   ```

3. **Walk through required variables** (ask the user for each value):
   | Variable | Required | How to get it |
   |----------|----------|---------------|
   | `TELEGRAM_TOKEN` | Yes | BotFather → `/newbot` → copy token |
   | `ALLOWED_CHAT_IDS` | Yes | Start bot, send `/chatid`, copy the number |
   | `ADMIN_CHAT_ID` | Yes | Same as ALLOWED_CHAT_IDS for single user |
   | `AUTH_SECRET_KEY` | If REQUIRE_AUTH=true | Any random string (e.g., `openssl rand -hex 16`) |

4. **Optional variables** (skip if user doesn't need):
   | Variable | For what |
   |----------|----------|
   | `GOOGLE_SERVICE_ACCOUNT_FILE` | Calendar plugin |
   | `GOOGLE_CALENDAR_ID` | Calendar plugin |
   | `DEFAULT_MODEL_CLAUDE` | Override default Claude model |
   | `DEFAULT_MODEL_CODEX` | Override default Codex model |

5. **Install and start:**
   ```bash
   python -m venv venv && source venv/bin/activate
   pip install -e .
   ./run.sh start
   ```

6. **Verify:** Ask user to send `/chatid` to the bot, then add the ID to `ALLOWED_CHAT_IDS` in `.env` and `./run.sh restart-soft`.

---

## Document Architecture

This project's documentation is organized into 3 layers.

| Layer | File | Nature | When to Reference |
|-------|------|--------|-------------------|
| **Layer 1: Development Rules** | `CLAUDE.md` | Meta-rules that cannot be expressed in code alone | Start and end of every task |
| **Layer 2: Development Interface** | `CLAUDE.md` | Contracts for extension points | When adding or modifying features |
| **Layer 3: UI/UX Specification** | `docs/SPEC.md` | User experience intent, scenarios, and UX principles | When planning new features or making UX decisions |

**Principles:**
- Layers 1 and 2 only document information that cannot be inferred from code alone or is expensive to reverse-engineer
- Layer 3 documents planning intent, user scenarios, and UX policies that do not exist in code
- Implementation details of single features that the code already explains are not documented

---

# Layer 1: Development Rules

## Development Principles (CRITICAL)

### Beta Development Mode

Currently in **beta development**. Strictly follow the principles below:

| Principle | Description |
|-----------|-------------|
| **No legacy concerns** | Do not write backward-compatibility code or fallback logic |
| **Code quality** | Only clean, simple, and clear code is allowed |
| **Migratable** | Convert existing data to the new format and process it |
| **Non-migratable** | Drop it cleanly (do not write complex compatibility code) |

### Beta Development Rules
1. The goal is to complete the entire agreed-upon plan. Break tasks by feature, role, or convenience. If no breakdown is needed, handle it as a single task.
2. For each broken-down task, follow this sequence:
    - Develop it.
    - Run all unit tests and integration tests.
    - If passing, commit and push.
    - Repeat step 2 until all tasks are complete.
3. Once all tasks are complete, run integration tests and apply any necessary improvements.
4. Perform a code review based on the plan.
5. Submit a report and restart the bot.

### Test Scope
- Telegram polling cannot be done directly, so mock it.
- All other resources may be used freely.
  - Repository, Claude CLI, sending messages to Telegram, etc.
  - Everything except polling is permitted.
- For large-scope development, use ralph/parallel/maximum resources.

### Test Writing Rules
- **Individual feature tests**: Unit behavior of each callback/handler must be tested.
- **Multi-step happy case**: Flows that go through multiple steps (inline keyboard → callback → ForceReply, etc.) require **at least 1 happy case** end-to-end test.
  - Example: workspace schedule registration (`ws:schedule` → time selection → minute selection → model selection → message input → registration complete)
  - Example: session deletion (`sess:del` → confirmation → deletion executed)
  - Example: scheduler time change (`sched:chtime` → time selection → minute selection → complete)
- **Test file locations**:
  - `tests/test_callback_flows.py` (multi-step callback flows)
  - `tests/test_handler_decomposition.py` (module decomposition, AI dispatch, HTML escape, N+1 queries)

### Forbidden Patterns

```python
# FORBIDDEN: legacy fallback
if new_system_available():
    use_new()
else:
    use_legacy()  # Do not write code like this

# FORBIDDEN: do not use send_chat_action (causes timeouts)
await context.bot.send_chat_action(chat_id=chat_id, action="typing")  # Absolutely forbidden!

# RECOMMENDED: use only the new system
def process():
    return new_system.process()  # Simple and clear
```

### Data Store

- Use **SQLite Repository** exclusively (`.data/bot.db`)
- JSON file-based storage is forbidden

### SQLite Runtime Rules (CRITICAL)

- Runtime SQLite connections operate with **`autocommit` as the default**.
- **Read-path methods do not perform DB writes.**
  - Calling `INSERT OR IGNORE` or `get_or_create_*` inside `get_*`, `list_*`, or status query layers is forbidden
- Writes should complete with **short, single SQL statements** as the baseline.
- Do not create **explicit transactions** unless there is a special atomicity requirement that mandates bundling multiple SQL statements together.
- If an explicit transaction is rarely needed:
  - There must be a justification in the code for why atomicity is required.
  - Minimize the scope.
  - First check for potential conflicts with the detached worker finalize path.

### DDL Management (CRITICAL)

- **`src/repository/schema.sql`** = **Single Source of Truth** for the DB schema
- When adding or changing tables, modify only `schema.sql`
- Idempotency guaranteed with `CREATE TABLE IF NOT EXISTS` + `CREATE INDEX IF NOT EXISTS`
- No separate migration system (unnecessary for a local single-user bot)
- On bot startup, `init_schema()` executes `schema.sql` → tables are created automatically

```
Bot start → get_connection() → init_schema(schema.sql) → Repository created
```

| Situation | Handling |
|-----------|----------|
| Adding a new table | Add `CREATE TABLE IF NOT EXISTS` to `schema.sql` |
| Adding a column | Modify `schema.sql` + recreate the existing DB |
| Changing table structure | Modify `schema.sql` + recreate the existing DB |
| First run | `schema.sql` creates all tables automatically |

## Development Routine

### Run Script (run.sh)
```bash
./run.sh start            # Start the bot
./run.sh stop-soft        # Stop supervisor/main only, attempt to keep detached workers alive
./run.sh stop-hard        # Stop bot + detached workers
./run.sh restart-soft     # Soft restart (attempt to keep in-flight workers alive)
./run.sh restart-hard     # Hard restart (including detached workers)
./run.sh status           # Check status
./run.sh log              # View app logs
./run.sh log boot         # View boot/watchdog logs
./run.sh trace            # Start with TRACE log level
./run.sh debug            # Start with DEBUG log level
./run.sh test             # Run unit tests
./run.sh test-integration # Run integration tests
./run.sh test-all         # Run all tests
```

### DB Management Script (db.sh)
Utility script for querying and managing the SQLite DB.

### Feature Branch Rules (CRITICAL)

When working on a feature branch, the developer must delete the branch after merging. The task is not considered complete until the branch is deleted.

| Step | Command | Description |
|------|---------|-------------|
| 1 | `git checkout main && git merge <branch>` | Merge to main |
| 2 | `git branch -d <branch>` | Delete local branch |
| 3 | `git push origin --delete <branch>` | Delete remote branch |

- Do not leave unmerged branches behind
- If a worktree is attached, run `git worktree remove` first
- Does not apply when working directly on main

### Completion Routine (CRITICAL - all steps required)
```bash
./run.sh test                             # 1. Tests
git add -A && git commit -m "type: msg"   # 2. Commit
git push origin main                      # 3. Push
./run.sh restart-soft                     # 4. Soft restart
source venv/bin/activate && \
  python -m src.notify "change1" -- "file1" # 5. Report (required!)
```

**Report format:**
```bash
source venv/bin/activate && python -m src.notify "main change 1" "change 2" -- "file1.py" "file2.py"
```
- Before `--`: change descriptions (multiple allowed)
- After `--`: list of modified files

## Commit Convention

| Type | Purpose |
|------|---------|
| `feat` | New feature |
| `fix` | Bug fix |
| `refactor` | Refactoring |
| `docs` | Documentation |
| `test` | Tests |
| `chore` | Miscellaneous |

```
Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>
```

## Code Rules

### Structure
```
src/
├── main.py                    # Bot entry point, handler registration
├── bootstrap.py               # Bot runtime configuration (handler/service assembly)
├── worker_job.py              # Claude detached worker entry point
├── config.py                  # Environment variable-based settings (Pydantic Settings)
├── constants.py               # Global constants (models, time, limits)
├── notify.py                  # Development report CLI
├── lock.py                    # File lock (singleton)
├── supervisor.py              # Process watchdog
├── scheduler_manager.py       # Unified job_queue manager
├── schedule_utils.py          # Schedule trigger parsing/display/calculation utils
├── time_utils.py              # Timezone configuration
├── ui_emoji.py                # UI emoji constants
├── runtime_exit_codes.py      # Process exit code constants
├── runtime_paths.py               # Runtime filesystem paths
├── logging_config.py          # Logging configuration
│
├── ai/
│   ├── base_client.py         # Common CLI client base (subprocess management)
│   ├── catalog.py             # Provider/model profile definitions
│   ├── registry.py            # Provider → client routing
│   └── client_types.py        # Common response types/protocols
│
├── bot/
│   ├── handlers/              # Command/callback/message handlers (domain-based mixins)
│   │   ├── base.py            # Common utilities, detached job, authentication
│   │   ├── callback_handlers.py  # Callback router + AI/plugin callbacks
│   │   ├── session_callbacks.py  # sess: callbacks (list/switch/delete/rename/model)
│   │   ├── scheduler_callbacks.py # sched: callbacks (add/toggle/change time/delete)
│   │   ├── session_queue_callbacks.py # sq: callbacks (session conflict resolution)
│   │   ├── session_handlers.py   # Session commands (/new, /sl, /session, etc.)
│   │   ├── message_handlers.py   # Message processing + AI dispatch
│   │   ├── workspace_handlers.py # Workspace commands/callbacks
│   │   ├── ai_work_handlers.py       # AI Work contextual assistance (✨ AI와 작업하기)
│   │   └── admin_handlers.py     # Admin commands (/tasks, /scheduler, etc.)
│   ├── command_catalog.py     # Shared command metadata (CommandSpec)
│   ├── middleware.py           # Authentication/authorization decorators
│   ├── formatters.py          # Message formatting (markdown→HTML, truncation, escape_html, split_message)
│   ├── runtime/               # Runtime components
│   │   ├── detached_job_manager.py  # Detached worker lifecycle management
│   │   └── pending_request_store.py # Pending request DB persistence
│   ├── ai_contexts/               # Core domain AI context markdown files
│   │   ├── scheduler.md
│   │   ├── workspace.md
│   │   ├── tasks.md
│   │   └── sessions.md
│   ├── constants.py           # UI constants (emoji, limits)
│   └── prompts/               # System prompts
│
├── claude/
│   └── client.py              # Claude CLI wrapper (inherits BaseCLIClient)
├── codex/
│   └── client.py              # Codex CLI wrapper (inherits BaseCLIClient)
│
├── plugins/
│   ├── loader.py              # Plugin base class + PluginLoader
│   └── storage.py             # Plugin storage Protocol (TodoStore, MemoStore, DiaryStore, etc.)
│
├── repository/
│   ├── database.py            # DB connection singleton
│   ├── repository.py          # Unified Repository (all data access)
│   ├── schema.sql             # DDL (Single Source of Truth)
│   └── adapters/              # Domain-specific adapters
│       ├── schedule_adapter.py
│       ├── workspace_adapter.py
│       └── plugin_storage.py
│
└── services/
    ├── session_service.py     # Session lifecycle
    ├── job_service.py         # Detached provider job execution + Telegram response
    ├── schedule_execution_service.py  # Schedule execution
    ├── delivery_retry_service.py      # Undelivered message auto-retry (60s interval, max 10 times)
    └── local_session_discovery.py     # Local CLI session discovery/import
```

**Default call flow:** Handler → Service → Repository → SQLite

**AI conversation flow:** Handler → Repository (job creation) → `src.worker_job` → `JobService` → provider CLI / Telegram

### Naming
- Files: `snake_case.py`
- Classes: `PascalCase`
- Functions/variables: `snake_case`
- Constants: `UPPER_SNAKE_CASE`

### Async
- I/O → `async/await`
- subprocess → `asyncio.create_subprocess_exec`

### Test Code
- Module: describe test intent (docstring)
- Method: brief description (docstring)

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `TELEGRAM_TOKEN` | (required) | Bot token |
| `ALLOWED_CHAT_IDS` | (empty) | Allowed chat IDs (comma-separated) |
| `ADMIN_CHAT_ID` | `0` | Admin notification/report recipient chat ID |
| `AI_COMMAND` | `claude` | AI CLI command |
| `SESSION_TIMEOUT_HOURS` | `24` | Hours before idle session is recycled |
| `SESSION_PURGE_DAYS` | `7` | Days before recycled session is deleted |
| `DEFAULT_MODEL_CLAUDE` | (empty) | Default Claude model profile key (opus/sonnet/haiku) |
| `DEFAULT_MODEL_CODEX` | (empty) | Default Codex model profile key |
| `APP_TIMEZONE` | `Asia/Seoul` | App timezone |
| `REQUIRE_AUTH` | `false` | Set to true to enable authentication |
| `AUTH_SECRET_KEY` | (conditionally required) | Auth key (required when `REQUIRE_AUTH=true`) |
| `AUTH_TIMEOUT_MINUTES` | `30` | Auth validity duration |
| `WORKING_DIR` | (none) | Bot working directory (defaults to project root if unset) |
| `ALLOWED_PROJECT_PATHS` | `~/AiSandbox/*,~/Projects/*` | Allowed workspace directories (glob patterns, comma-separated) |
| `GOOGLE_SERVICE_ACCOUNT_FILE` | (none) | Google service account JSON path (for Calendar plugin) |
| `GOOGLE_CALENDAR_ID` | `primary` | Google Calendar ID (e.g., `user@gmail.com`) |
| `BOT_DATA_DIR` | `.data/` | Runtime files root (locks, pid, symlink logs) |
| `BOT_LOG_DIR` | `.data/logs/` | Runtime log directory |
| `BOT_MAIN_MENU_PLUGINS` | (none) | Comma-separated plugin names promoted to main menu (overrides default) |

## Process Management (CRITICAL)

### Singleton Lock System

The bot uses a file lock system to prevent duplicate execution:

| Lock File | Purpose |
|-----------|---------|
| `.data/telegram-bot.lock` | main.py singleton |
| `.data/telegram-bot-supervisor.lock` | supervisor singleton |

### Process Management Rules (CRITICAL)

**Always use `./run.sh` commands only!**

| Situation | Correct Method | Forbidden |
|-----------|----------------|-----------|
| Restart bot | `./run.sh restart-soft` | `kill -9 PID` |
| Stop bot | `./run.sh stop-hard` | `pkill -f src.main` |
| Clean up duplicate processes | `./run.sh restart-hard` | manual kill |

### Why Manual Kill is Dangerous

1. **`kill -9` ignores signal handlers** → lock files are not cleaned up
2. **`kill -9 PID` can fail silently in zsh** → error is ignored and goes unnoticed
3. **Supervisor respawns child processes** → duplicates occur

### Detached Worker Architecture (CRITICAL)

Designed with the assumption that an AI agent during self-development can directly execute `./run.sh restart-soft`.

```
supervisor
    └─ main(bot)
         └─ spawn → worker_job (one-shot process per request)
```

| Process | Responsibility |
|---------|---------------|
| `src.supervisor` | Watches/restarts `src.main`, startup preflight, crash-loop prevention |
| `src.main` | Receives Telegram requests, decides session, creates job, spawns worker |
| `src.worker_job` | Owns provider CLI execution, responds directly to Telegram, drains queue |

**Rules:**
- The owner of AI requests for regular chat and `/ai` is `src.worker_job`, not `src.main`
- `src.main` creates a `message_log` job and returns immediately without waiting for the AI response
- The source of truth for whether a request is in-progress is the DB (`message_log`, `queued_messages`, `session_locks`), not memory
- `./run.sh restart-soft` restarts only `src.supervisor`/`src.main` and attempts to preserve in-flight `src.worker_job` processes
- `./run.sh stop-hard`/`restart-hard` also terminates `src.worker_job`
- `src.supervisor` does not hold durable app state and is not extended as a control plane
- `src.supervisor` stops automatic restarts when it detects an unrecoverable startup error or crash-loop
- "Ask AI again after bot reboot" is not used as the primary recovery strategy

### Multi-Provider Session Rules (CRITICAL)

Both `Claude` and `Codex` are supported simultaneously. Sessions and models are not designed as Claude-only concepts.

| Concept | Meaning |
|---------|---------|
| `sessions.id` | Internal bot session ID |
| `sessions.ai_provider` | `claude` or `codex` |
| `sessions.provider_session_id` | Claude conversation ID / Codex thread ID |
| `sessions.model` | Provider-specific profile key, not a raw CLI model name |

**Rules:**
- Do not assume the provider's external session ID is the DB primary key
- Only 1 current session is maintained across the entire system (switching providers sets the other provider's current to NULL)
- `/sl`, `/session`, `/model`, `/new` operate based on the currently selected provider
- Model buttons/display are managed in the catalog; CLI flags are interpreted by the client
- Traces of unsupported providers (e.g., `gemini`) are removed from code and the production DB

### Session Recycling

Sessions are automatically archived and cleaned up based on inactivity. Recycling is applied on every `/sl` view (no scheduler needed).

| Rule | Condition | Action |
|------|-----------|--------|
| Recycle | 24h without activity | `recycled = 1` — hidden from `/sl` default view |
| Purge | 7 days after recycling | `deleted = 1` — soft-deleted |
| Restore | User clicks ↩ in Recycled tab | `recycled = 0` — back to active list |

- `/sl` shows active sessions only (max 30, newest first)
- "🗂 Recycled" button appears when recycled sessions exist
- `sessions.recycled` column (INTEGER, 0/1) controls visibility

## Protection Mechanisms

| Layer | Threat | Protection |
|-------|--------|------------|
| Access | Unauthorized use | `ALLOWED_CHAT_IDS` |
| Authentication | Privilege hijacking | `AuthManager` (30-minute TTL) |
| Concurrency | Race Condition | `_user_locks` |
| Session | Duplicate execution on same session | `session_locks` |
| Restart | Response loss during self-restart | detached `src.worker_job` |
| State | In-progress/queue loss | `message_log`, `queued_messages`, `session_locks` |
| Delivery failure | Telegram send failure | `delivery_retry` (60s, max 10 times) |
| DoS | Long messages | `MAX_MESSAGE_LENGTH` (4096) |

## Logging System

### MDC Style (contextvars)

Per-request context maintained (`trace_id`, `user_id`, `session_id`):

```
22:15:30.123 | INFO | 123456789 | a1b2c3d4 | 8f9e0d1c | handlers:handle_message:1364 | Message received
              ↑ level  ↑ user_id   ↑ session  ↑ trace_id   ↑ location
```

## Forbidden

- Do not commit `.env`
- Do not commit `.data/`
- Do not hardcode tokens
- **Do not use manual `kill -9`** → use `./run.sh restart-soft` or `./run.sh restart-hard`

---

# Layer 2: Development Interface

## Plugin Architecture

### Directory Structure
```
plugins/
├── builtin/               # Git-managed (built-in plugins)
│   ├── todo/
│   │   ├── __init__.py
│   │   ├── plugin.py      # Callback, ForceReply, schedule implementation
│   │   ├── ai_context.md  # AI context document
│   │   └── scheduler.py   # Todo-specific schedule actions
│   ├── memo/
│   │   ├── __init__.py
│   │   ├── plugin.py
│   │   └── ai_context.md  # AI context document
│   ├── weather/
│   │   ├── __init__.py
│   │   ├── plugin.py
│   │   └── ai_context.md  # AI context document
│   ├── calendar/
│   │   ├── __init__.py
│   │   ├── plugin.py      # Calendar CRUD, callbacks, MCP tools, schedule
│   │   ├── google_client.py # Google Calendar API wrapper
│   │   ├── ui.py          # Calendar grid and navigation UI
│   │   └── ai_context.md  # AI context document
│   └── diary/
│       ├── __init__.py
│       ├── plugin.py      # Diary CRUD, callbacks, ForceReply, schedule
│       └── ai_context.md  # AI context document
└── custom/                # Git-ignored (personal use)
    └── hourly_ping/
        ├── __init__.py
        └── plugin.py      # Hourly ping notification
```

### Plugin Class Structure

```python
from src.plugins.loader import Plugin, PluginResult, ScheduledAction

class MyPlugin(Plugin):
    name = "myplugin"                    # Required: used as /myplugin command (opens plugin)
    description = "Plugin description"   # Required: shown in /plugins
    usage = (                            # Required: shown in /help_myplugin
        "<b>Usage</b>\n\n"
        "<code>command1</code> - description\n"
        "<code>command2</code> - description"
    )

    TRIGGER_KEYWORDS = ["myplugin", "내플러그인"]  # Exact match keywords (Korean + English)
    EXCLUDE_PATTERNS = [r"(란|이란)\s*뭐"]         # Exclude patterns → pass to AI

    # can_handle: provided by base class (exact keyword match). Override only for special cases.
    async def handle(self, message: str, chat_id: int) -> PluginResult: ...

    # --- Optional API ---
    # handle_callback(callback_data, chat_id) → dict            # Inline button callback (sync)
    # handle_callback_async(callback_data, chat_id) → dict      # Inline button callback (async)
    # handle_force_reply(message, chat_id) → dict               # ForceReply response
    # handle_interaction(message, chat_id, interaction) → dict   # Multi-step ForceReply flow
    # get_schema() → str                                        # Plugin table DDL
    # build_storage(repository) → Any                           # Plugin-specific storage adapter
    # get_scheduled_actions() → list[ScheduledAction]           # List of scheduled actions
    #   ScheduledAction(name, description, recommended_hour=None, recommended_minute=None)
    #     recommended_hour + minute set → "⭐ Recommended: HH:MM daily" button in scheduler UI
    #     recommended_hour=None, minute set → "⭐ Recommended: every {minute} min" (interval cron)
    # execute_scheduled_action(action_name, chat_id) → str | dict | None  # Execute scheduled action
    # register_system_jobs(context: PluginSystemJobContext)      # Register background jobs

    # --- AI Context API ---
    # ai_context_file = "ai_context.md"                            # AI context markdown file (relative to plugin dir)
    # get_ai_context(chat_id) → str                                # Static context from ai_context.md
    # get_tool_specs() → list[ToolSpec]                            # MCP tools for AI to invoke
```

Reference implementations: `plugins/builtin/todo/` (callbacks+ForceReply+schedule), `plugins/builtin/memo/` (simple CRUD), `plugins/builtin/diary/` (callbacks+ForceReply+schedule+monthly list)

### Plugin Rules (CRITICAL)

1. **Exclude patterns are required**: Natural language commands can conflict with AI questions
   - "What is memo?" → AI should answer, not the memo plugin
2. **Safe loading**: If a plugin fails to load, the bot continues to operate (try-catch isolation)
3. **Data storage**: The runtime injects a repository, but plugin source should use a bounded storage adapter via `build_storage(repository)` and `self.storage` rather than calling `self.repository` directly
4. **Validate before deployment**: `python -m py_compile plugins/custom/my.py`
5. **Scheduled response rules**: `execute_scheduled_action()` returns `str`, `dict`, or `None`.
   - `str` (non-empty): sent as plain text. Empty string `""` triggers a fallback message — avoid returning `""`.
   - `dict`: rich response with `text` and optional `reply_markup`, sent directly.
   - `None`: intentional silence — execution is recorded but no message is sent. Use for periodic checks (e.g., reminders) where "nothing to report" should be silent.
6. **AI context is required**: Every plugin must provide `ai_context.md` describing its feature, DB schema, available operations, AI assistance scope, and MCP tool usage (`query_db` examples). Dynamic data is accessed via MCP tools, not pre-fetched context.

### Plugin Data Storage Extension

For a plugin to store new data:
1. Return `CREATE TABLE IF NOT EXISTS` DDL from the plugin class's `get_schema()` method
2. Implement a plugin-facing storage adapter (see `src/repository/adapters/plugin_storage.py` and built-in plugin examples)
3. Return that adapter from `build_storage(repository)`
4. Use `self.storage` from the plugin, ideally through a typed `store` property
5. Do not call `self.repository.xxx()` directly from plugin source

7. **Plugin-core isolation (CRITICAL)**: Plugins must not leak into core code. Adding a new plugin must NOT require modifying any core file (`src/` directory). Specifically:
   - Do not hardcode plugin names, callback prefixes, or labels in core handlers
   - Do not add plugin tables to the core `schema.sql` — each plugin manages its own DDL via `get_schema()`
   - Do not add plugin dataclasses or models to core repository
   - Core code accesses plugins only through the `Plugin` interface (`self.plugins.get_plugin_by_name()`, `plugin.get_ai_context()`, etc.)
   - If core needs plugin metadata, the Plugin base class must provide it as an interface attribute

### Callback Handling Pattern

For a plugin to use inline buttons:

1. Define `CALLBACK_PREFIX = "myplugin:"` (must not conflict with existing prefixes)
2. Implement `handle_callback(callback_data, chat_id) → dict`
3. Auto-routed — no manual registration needed in `callback_handlers.py`

**Registered callback prefixes (no conflicts allowed):**

| Prefix | Target | Registration Location |
|--------|--------|-----------------------|
| `menu:` | Main menu navigation | `callback_handlers.py` |
| `ai:` | AI provider selection | `callback_handlers.py` |
| `resp:` | AI response follow-up buttons | `callback_handlers.py` → `session_callbacks.py` |
| `plug:` | Plugin hub navigation | `callback_handlers.py` |
| `td:` | Todo plugin | Plugin auto-routing |
| `memo:` | Memo plugin | Plugin auto-routing |
| `weather:` | Weather plugin | Plugin auto-routing |
| `diary:` | Diary plugin | Plugin auto-routing |
| `sess:` | Session management | `callback_handlers.py` |
| `sched:` | Scheduler | `callback_handlers.py` |
| `ws:` | Workspace | `callback_handlers.py` |
| `sq:` | Session queue (conflict handling) | `callback_handlers.py` |
| `tasks:` | Task status | `callback_handlers.py` |
| `aiwork:` | AI Work (contextual AI) | `callback_handlers.py` |
| `cal:` | Calendar plugin | Plugin auto-routing |

**ForceReply markers (no conflicts allowed):**

| Marker | Purpose | Routing Mechanism |
|--------|---------|-------------------|
| `sess_name:{model}` | Session name input | Direct pattern matching in `message_handlers.py` |
| `sess_rename:{session_id}` | Session rename | Direct pattern matching in `message_handlers.py` |
| `schedule_input` | Schedule message input | Direct pattern matching in `message_handlers.py` |
| `_ws_pending` | Workspace flow | Dict-based in `message_handlers.py` |
| `td:add` | Todo add | Plugin interaction (`_plugin_interactions`) |
| `memo_add` | Memo add | Plugin interaction (`_plugin_interactions`) |
| `diary_write` | Diary write and edit (distinguished by `interaction_action`) | Plugin interaction (`_plugin_interactions`) |
| `aiwork:{domain}` | AI contextual assistance | Pattern matching in `message_handlers.py` |
| `cal_title` | Calendar event title input | Plugin interaction (`_plugin_interactions`) |

### AI Work (✨ AI와 작업하기) Pattern

Every sub-menu (one level deep from the main menu) provides a "✨ AI와 작업하기" button for contextual AI assistance. When clicked, it gathers domain-specific data and sends it to the AI along with the user's request.

**Flow:**
```
[✨ AI와 작업하기] button clicked
    → callback: aiwork:{domain}
    → ForceReply prompt: "무엇을 도와드릴까요?"
    → User types request
    → Handler gathers domain data (todos, memos, schedules, etc.)
    → Prepends context to user's message
    → Dispatches to AI via _dispatch_to_ai()
    → AI responds with domain-aware answer
```

**Supported domains:**

| Domain | Callback | Context Data |
|--------|----------|-------------|
| `scheduler` | `aiwork:scheduler` | All schedules (name, type, time, on/off status) |
| `workspace` | `aiwork:workspace` | All workspaces (name, path) |
| `calendar` | `aiwork:calendar` | Today's calendar events |
| `tasks` | `aiwork:tasks` | Processing/queued AI tasks |
| `todo` | `aiwork:todo` | Today's todo list with completion status |
| `memo` | `aiwork:memo` | All saved memos |
| `weather` | `aiwork:weather` | Last queried weather location |
| `diary` | `aiwork:diary` | This month's diary entries |

**Implementation:**
- Handler mixin: `src/bot/handlers/ai_work_handlers.py` (`AiWorkHandlers`)
- Callback prefix: `aiwork:` (registered in `callback_handlers.py`)
- ForceReply marker: `aiwork:{domain}` (detected in `message_handlers.py`)
- UI constant: `BUTTON_AI_WORK` in `src/ui_emoji.py`

**AI Context System:**

On AI Work entry, static `ai_context.md` descriptions are sent to AI. Live data access and modifications are performed by the AI directly via MCP tools (`query_db`, `db_schema`, plugin ToolSpecs).

| Domain | Static Context | Data Access |
|--------|---------------|-------------|
| Plugin | `plugins/builtin/{name}/ai_context.md` | MCP tools (`query_db` or `get_tool_specs()`) |
| Core feature | `src/bot/ai_contexts/{domain}.md` | MCP tools (`query_db`) |

**Adding a new plugin:**
1. Write `ai_context.md` (include features, DB schema, `query_db` usage examples)
2. Add "✨ AI와 작업하기" button to sub-menu (`callback_data="aiwork:{name}"`)
3. Set `display_name` class attribute (used as AI Work UI label)
4. (Optional) When integrating external APIs, implement `get_tool_specs()`

### MCP Tool Integration (Plugin ↔ AI Agent)

The bot exposes data access capabilities as MCP tools that Claude CLI can call autonomously. MCP tools are available for all AI conversations, not just AI Work. Dynamic context (`get_ai_dynamic_context()`) is deprecated — all live data access goes through MCP.

**Architecture:**

```
Bot process                           MCP bridge process (separate)
├─ Telegram polling                   ├─ PluginLoader (lightweight load)
├─ Scheduler                          ├─ Repository (own DB connection)
├─ Plugins (runtime)                  ├─ Plugin tool handlers
│                                     └─ JSON communication with Claude CLI
│
└─ worker → Claude CLI --mcp-config
                └─ Spawns MCP bridge as subprocess
                └─ Bridge auto-terminates when CLI exits
```

The MCP bridge is a lightweight script that loads only DB + plugins — not the full bot (no Telegram, no scheduler, no supervisor). Environment variables (`.env`) propagate automatically: bot → worker → Claude CLI → MCP server.

**Plugin Interface:**

```python
@dataclass
class ToolSpec:
    name: str              # MCP tool name (e.g., "calendar_list_events")
    description: str       # Tool description (used by AI for decision-making)
    parameters: dict       # JSON Schema for parameters
    handler: callable      # Execution function

class Plugin(ABC):
    # Existing (unchanged)
    def get_schema(self) -> str: ...
    def get_scheduled_actions(self) -> list[ScheduledAction]: ...
    async def get_ai_context(self, chat_id) -> str: ...

    # New
    def get_tool_specs(self) -> list[ToolSpec]:
        """Return tools that AI can invoke via MCP. Override to expose capabilities."""
        return []
```

**Implementation Rules:**

| Rule | Description |
|------|-------------|
| Tool handlers are synchronous | MCP server runs in a separate process, independent of the bot's event loop |
| Process-independent resources only | Google API, SQLite (file), etc. Bot process memory (session locks, event cache) is inaccessible — separate process boundary |

**Constraints:**

| Resource | Available | Reason |
|----------|-----------|--------|
| External APIs (Google Calendar, etc.) | Yes | Stateless HTTP calls |
| SQLite read (memos, todos) | Yes | Same DB file accessed from separate process |
| SQLite write | Caution | Concurrent writes may cause SQLite locking |
| Bot in-memory state | No | Separate process — no access to session locks, caches, etc. |
| Claude only | Yes | Codex does not support MCP |

**Built-in MCP Tools:**

| Tool | Purpose |
|------|---------|
| `query_db(sql)` | SQL against bot SQLite (SELECT/INSERT/UPDATE/DELETE). Use `{chat_id}` placeholder for auto-replacement with `ADMIN_CHAT_ID`. DROP/ALTER blocked. |
| `db_schema(table_name?)` | List all tables, or show columns for a specific table |
| `reload_schedules()` | Send SIGUSR1 to bot process to hot-reload schedules from DB. Call after modifying schedules via `query_db`. |
| `calendar_list_events(start_date, end_date)` | Google Calendar event query (plugin ToolSpec) |
| `calendar_create_event(summary, start, all_day?)` | Google Calendar event creation (plugin ToolSpec) |

`query_db` and `db_schema` cover all DB-backed features (todo, memo, diary, sessions, schedules, workspaces) without needing per-plugin MCP tools.

**File Structure:**

```
mcp_servers/
└── plugin_bridge_server.py      # Bridge server (DB tools + plugin ToolSpecs)
```

**Activation:** Auto-enabled when `mcp_servers/plugin_bridge_server.py` exists. MCP config is generated dynamically at runtime with correct absolute paths — no static JSON file needed.

**Adding MCP tools to a plugin:**
1. Implement `get_tool_specs()` returning `list[ToolSpec]`
2. Each `ToolSpec.handler` should use existing plugin internals (e.g., `self._gcal.list_events()`)
3. No changes to `plugin_bridge_server.py` — tools are auto-registered
4. For DB-only queries, `query_db` is sufficient — no plugin ToolSpec needed

## Message Processing Architecture

Three layers handle user interactions with distinct responsibilities:

| Layer | Name | Trigger | Role |
|-------|------|---------|------|
| **Plugin Launcher** | UI workspace | Exact keyword or `/{name}` command | Open interactive UI (button-based). Minimal trigger scope — keywords only. |
| **AI Conversation** | Natural language | Everything else, or keyword + content | Intent understanding + response generation via AI provider |
| **MCP Data Bridge** | Data access | AI calls tools autonomously | Actual data operations (`query_db`, `calendar_list_events`, `db_schema`) |

**How they interact:**
```
"할일"              → Plugin Launcher → UI screen (buttons, callbacks)
"할일 정리해줘"      → AI Conversation → MCP Data Bridge (query_db) → response
"이번 달 일정"       → AI Conversation → MCP Data Bridge (calendar_list_events) → response
```

Plugin Launcher is the **quick entry point** for UI-driven tasks. AI Conversation is the **brain**. MCP Data Bridge is the **hands** — AI decides when and how to use it.

### Message Processing Flow

```
User message arrives
    │
    ▼
[1] Command (/command)
    │ /{plugin_name} → Plugin Launcher (open plugin UI)
    │ /help_{plugin_name} → show plugin help
    │ Other commands → immediate response
    │
    ▼ Not a command
[2] ForceReply response detection
    │ Extract marker from reply_to_message.text → route to appropriate handler
    │   • aiwork:{domain} → load static context → dispatch to AI
    │   • Other markers → sess_name, sess_rename, schedule_input, _ws_pending, plugin interactions
    │
    ▼ Not ForceReply
[3] Plugin Launcher (exact keyword match)
    │ "할일" → todo plugin opens (UI)
    │ "memo" → memo plugin opens (UI)
    │ Iterate plugins, can_handle() checks TRIGGER_KEYWORDS exact match
    │
    ▼ No exact match
[3.5] Keyword + content → AI Conversation with context
    │ "할일 오늘 뭐 해야돼?" → AI gets todo context + "오늘 뭐 해야돼?"
    │ match_plugin_keyword() detects keyword prefix with additional text
    │ Prepends plugin ai_context.md → dispatch to AI (MCP tools available)
    │
    ▼ No keyword match
[4] AI Conversation (background processing)
    │ AI has MCP Data Bridge tools available (query_db, db_schema, etc.)
    │ AI autonomously queries data as needed
```

### Plugin Trigger Rules

| Rule | Description |
|------|-------------|
| `TRIGGER_KEYWORDS` | Required. List of exact-match keywords (Korean + English). Base class `can_handle()` enforces exact match. Keep minimal — only bare keywords. |
| `EXCLUDE_PATTERNS` | Optional. Regex patterns to pass to AI instead (e.g., "메모란 뭐" → AI answers, not plugin) |
| `/{name}` command | Opens Plugin Launcher (same as menu button click) |
| `/help_{name}` | Shows plugin usage/help text |
| Keyword + content | Auto-routed to AI Conversation with plugin context prepended. AI uses MCP Data Bridge for actual data access. |

**Keyword + Content Examples:**

When a message starts with a plugin keyword followed by natural language, it is dispatched to AI with that plugin's context automatically:

| User Input | Routing | What Happens |
|------------|---------|-------------|
| `할일` | Plugin Launcher | Opens todo UI |
| `할일 오늘 뭐 해야돼?` | AI + todo context | AI queries todos via `query_db`, responds with today's list |
| `메모 정리해줘` | AI + memo context | AI queries memos via `query_db`, suggests organization |
| `일정 다음 주 비어있는 날 알려줘` | AI + calendar context | AI queries calendar via `calendar_list_events`, finds free days |
| `일기 이번 달 요약해줘` | AI + diary context | AI queries diaries via `query_db`, generates summary |

No special command needed — just type `{keyword} {your request}` and AI handles it with full data access via MCP.

**Plugin Launcher Registry:**

| Plugin | `/command` | Korean Keywords | English Keywords |
|--------|-----------|-----------------|------------------|
| Todo | `/todo` | `할일`, `투두` | `todo` |
| Memo | `/memo` | `메모` | `memo` |
| Diary | `/diary` | `일기` | `diary` |
| Calendar | `/calendar` | `캘린더`, `일정`, `달력` | `calendar`, `cal` |
| Weather | `/weather` | `날씨`, `기온` | `weather` |

**Core Feature Keywords (hardcoded in message_handlers.py):**

| Feature | `/command` | Korean Keywords | English Keywords |
|---------|-----------|-----------------|------------------|
| Sessions | `/sl` | `세션` | `session` |
| Scheduler | `/scheduler` | `스케줄` | `scheduler` |
| Workspace | `/workspace` | `워크스페이스` | `workspace` |

Same routing rules apply: exact keyword → open UI, keyword + content → AI with domain context.

## Telegram Command Rules

### Non-ASCII Command Limitation

The Telegram Bot API only allows **alphanumeric characters (a-z, 0-9) and underscores (_)** in commands (`/command`).

| Method | Example | Behavior |
|--------|---------|----------|
| English command | `/todo`, `/memo` | Clickable, processed by CommandHandler |
| Non-ASCII command | `/할일` | Telegram does not recognize it as a command |
| Korean natural language | `할일`, `메모` | Plugin `can_handle()` pattern matching |

**Conclusion:** Korean triggers must be handled via the plugin's `TRIGGER_KEYWORDS`. Do not register them as `/` commands.

### Underscore (_) Rules (CRITICAL)

Telegram recognizes **underscore-connected strings** as a single command:

| Input | Clickable Part | Reason |
|-------|---------------|--------|
| `/new_opus` | Entire `/new_opus` | Connected by underscore → single command |
| `/new opus` | `/new` only | Space → separate words |
| `/s_12345678` | Entire `/s_12345678` | Can include dynamic session IDs |

### Command Design Principles

1. **Fixed commands**: Connected with underscores (`/new_opus`, `/model_haiku`)
2. **Dynamic parameters**: Underscore + ID (`/s_{id}`, `/h_{id}`, `/d_{id}`)
3. **Shorthand commands**: Frequently used commands (`/sl` = `/session_list`, `/ws` = `/workspace`)

## Local Session Discovery (Import Local Session)

Feature to import Claude/Codex sessions created directly via CLI outside the bot.

### Overview

The bot manages sessions in its own DB, but sessions that the user created directly by running the `claude` or `codex` CLI in a terminal are unknown to the bot. `LocalSessionDiscoveryService` scans the provider CLI's local storage to discover such sessions, and when the user selects one, it is registered as a new session in the bot DB.

### Data Sources

| Provider | Source | Path | Content |
|----------|--------|------|---------|
| Claude | Index | `~/.claude/projects/*/sessions-index.json` | Session metadata (ID, summary, messageCount, cwd) |
| Claude | Raw | `~/.claude/projects/*/{uuid}.jsonl` | JSONL session log (supplements sessions not in index) |
| Codex | Index | `~/.codex/session_index.jsonl` | Session metadata (id, thread_name, updated_at) |
| Codex | Raw | `~/.codex/sessions/YYYY/MM/DD/*.jsonl` | JSONL session log |

**The search scope is determined by the provider CLI's storage conventions.** Sessions not found in any of these 4 sources cannot be discovered.

### Core Classes

| Class | Location | Role |
|-------|----------|------|
| `LocalSessionDiscoveryService` | `src/services/local_session_discovery.py` | Scans local sessions, sorts, merges duplicates |
| `DiscoveredSession` | Same file | Discovered session data (provider, id, title, updated_at, workspace_path, preview) |

### Behavior Rules

- **Read-only**: Only reads local files; does not modify the provider storage
- **On-demand scan**: Scans fresh every time the import UI is opened (no cache)
- **Duplicate merging**: If the same session ID appears in both index and raw, the most recent metadata by `updated_at` takes precedence
- **Raw scan limit**: For performance, only the first 160 lines of each file are read to extract the first user prompt and workspace path
- **Exclude subagents**: Files under `~/.claude/projects/*/subagents/` are skipped
- **On import**: A new session is created in the bot DB and linked to the external session via `provider_session_id`. If the session has already been imported, the existing session is switched to.

### Callback Prefix

`sess:import`, `sess:import:{offset}`, `sess:import_pick:{provider}:{id}` → handled in `session_callbacks.py`

## Workspace Sessions

Sessions bound to a local directory. The directory is specified with `--cwd`, and Telegram formatting rules are injected with `--append-system-prompt`.

| Layer | Source | Role |
|-------|--------|------|
| Workspace rules | `CLAUDE.md` in the `cwd` | Code style, build commands, commit rules |
| Telegram rules | `--append-system-prompt` | HTML format, concise responses |

## Schedule Types

| Type | Description |
|------|-------------|
| `chat` | Regular schedule (executed in a new session) |
| `workspace` | Workspace schedule (applies CLAUDE.md from the path) |
| `plugin` | Plugin action schedule (no model or message required) |
