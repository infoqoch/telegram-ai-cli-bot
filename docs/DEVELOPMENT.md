# Development Reference

This is the deep maintainer reference for the project.

It is intentionally separate from the root `CLAUDE.md` so the always-loaded Claude Code context can stay small. Keep stable, high-signal implementation guidance here. Keep user-facing behavior in [`SPEC.md`](./SPEC.md), product messaging in [`README.md`](../README.md), and setup/operations in [`SETUP.md`](./SETUP.md).

## Product Model

- Telegram is the front-end.
- Claude Code and Codex CLI on the local machine are the execution engines.
- The bot reuses existing CLI logins and subscriptions instead of introducing a separate provider API flow.
- Core value comes from remote access, multi-session handling, workspace-aware execution, schedules, and extensibility.

## Documentation Contract

- [`README.md`](../README.md): product positioning, why it is useful, quick start.
- [`docs/SETUP.md`](./SETUP.md): installation, runtime commands, security controls, environment variables.
- [`docs/SPEC.md`](./SPEC.md): user-visible Telegram behavior and stable UX rules.
- [`docs/SPEC_PLUGINS_BUILTIN.md`](./SPEC_PLUGINS_BUILTIN.md): detailed built-in plugin UI/UX contract.
- [`docs/UI_EMOJI_SYSTEM.md`](./UI_EMOJI_SYSTEM.md): canonical emoji and label mapping for Telegram UI.
- [`docs/DEVELOPMENT.md`](./DEVELOPMENT.md): architecture, extension patterns, runtime flows, maintainer reference.
- [`CLAUDE.md`](../CLAUDE.md): compact always-loaded instructions for Claude Code.
- [`.ai/rules/`](../.ai/rules): project-local AI guidance for focused edits.

## Documentation Strategy

### Why `DEVELOPMENT.md` Lives In `docs/`

- This file is intentionally not in an agent-specific root file because it is not meant to be auto-loaded into every AI session.
- It is a deep maintainer reference: useful, but too large and too infrequently needed to justify always-on context cost.
- The compact root [`CLAUDE.md`](../CLAUDE.md) should hold only high-signal rules worth paying for every session.
- [`.ai/rules/`](../.ai/rules) should hold focused project guidance without tying the repository to one provider's config directory.

### Why `SPEC` Stays Detailed

- For this project, detailed UI/UX transcripts are not clutter. They are intentional product contracts.
- Screen text, button layout, flow ordering, empty states, and callback behavior matter enough to preserve explicitly.
- Therefore [`docs/SPEC.md`](./SPEC.md), [`docs/SPEC_PLUGINS_BUILTIN.md`](./SPEC_PLUGINS_BUILTIN.md), and [`docs/UI_EMOJI_SYSTEM.md`](./UI_EMOJI_SYSTEM.md) are allowed to stay long and specific.
- The right optimization target is the always-loaded Claude context, not the UX contract docs.

### Development Style: UX Contract First

- This project is closer to spec-first development than code-first development.
- For meaningful Telegram UI/UX changes, define or update the intended contract in `SPEC` before implementation, or in the same change at minimum.
- The reason is practical: this product has many stateful flows, callback transitions, queue/restart edge cases, and compact mobile screens. Letting code invent those ad hoc is how drift starts.
- Internal refactors can stay code-first when user-visible behavior is unchanged.
- But if the user's experience changes, the spec is part of the change itself, not cleanup afterward.

### Update Rules

- Change Telegram-visible behavior: update [`docs/SPEC.md`](./SPEC.md)
- Change built-in plugin UX: update [`docs/SPEC_PLUGINS_BUILTIN.md`](./SPEC_PLUGINS_BUILTIN.md)
- Change canonical emoji or shared labels: update [`docs/UI_EMOJI_SYSTEM.md`](./UI_EMOJI_SYSTEM.md)
- Change runtime boundaries, extension patterns, or maintainer-level guidance: update [`docs/DEVELOPMENT.md`](./DEVELOPMENT.md)

## Runtime Map

### Composition Root

- [`src/main.py`](../src/main.py): Telegram `Application` wiring and process lifecycle.
- [`src/bootstrap.py`](../src/bootstrap.py): runtime dependency graph construction.
- [`src/supervisor.py`](../src/supervisor.py): bot process supervision and restart behavior.

Rule: `main.py` wires collaborators. It should not own business rules.

### Handler Layer

- [`src/bot/handlers/`](../src/bot/handlers): commands, callbacks, message entrypoints.
- [`src/bot/runtime/`](../src/bot/runtime): runtime-only collaborators for pending state, detached workers, and transport-adjacent concerns.

Rule: handlers choose which flow to run. They should not absorb queue draining, session lock cleanup, or detached worker lifecycle logic inline.

### Service Layer

- [`src/services/session_service.py`](../src/services/session_service.py): session creation, session listing, active session state.
- [`src/services/job_service.py`](../src/services/job_service.py): detached AI job lifecycle, queue drain, final delivery.
- [`src/services/schedule_execution_service.py`](../src/services/schedule_execution_service.py): execution and delivery of schedules.
- [`src/services/local_session_discovery.py`](../src/services/local_session_discovery.py): import of local Claude/Codex sessions that were created outside the bot.

Rule: one service should own one runtime story.

### Infrastructure Layer

- [`src/repository/`](../src/repository): SQLite persistence and adapters.
- [`src/ai/`](../src/ai): provider registry and shared CLI client base.
- [`src/claude/`](../src/claude), [`src/codex/`](../src/codex), [`src/gemini/`](../src/gemini), and [`src/agy/`](../src/agy): provider-specific CLI wrappers.
- [`src/plugins/`](../src/plugins): plugin runtime and loader.
- [`prompts/telegram.md`](../prompts/telegram.md): Telegram formatting and response-shaping prompt.

## Provider And Prompt Model

- The shared Telegram prompt is loaded from [`prompts/telegram.md`](../prompts/telegram.md).
- Non-workspace calls pass that prompt as the main system prompt.
- Workspace calls set `cwd=<workspace>` and append the Telegram prompt, allowing the workspace's own `CLAUDE.md` to shape coding behavior while the bot keeps Telegram formatting constraints.
- Root [`CLAUDE.md`](../CLAUDE.md) therefore matters most when Claude Code is run from the repository root or for non-workspace Claude sessions.

### Per-Provider Integration Notes

Each provider CLI has a different mechanism for system prompts, MCP, and session management. Reference when adding a new provider:

| Concern | Claude | Codex | Gemini | Antigravity |
|---------|--------|-------|--------|-------------|
| System prompt | `--system-prompt` flag | `--instructions` flag | `GEMINI.md` file in cwd | Prepended to each `--print` request |
| MCP config | `--mcp-config` flag | `--mcp-config` flag | `.gemini/settings.json` in cwd | `.agents/mcp.json` in cwd |
| Session resume | `--resume <uuid>` | `--session <id>` | `--resume <uuid>` | `--conversation <uuid>` |
| Model selection | `--model <name>` | `--model <name>` | `-m <name>` | `--model <display name>` |
| Auto-approve | `--dangerously-skip-permissions` | `--full-auto` | `--approval-mode yolo` | `--dangerously-skip-permissions` |
| Output format | `--output-format json` (`result` field) | `--output-format json` | `--output-format json` (`response` field) | Raw text only |
| Session storage | `~/.claude/projects/` | `~/.codex/` | `~/.gemini/tmp/<project_hash>/chats/` | `~/.gemini/antigravity-cli/` |

Antigravity note: subprocess `cwd` and `--add-dir` alone are not enough to make Agy tools execute from the intended project directory. `AgyClient` injects an execution context naming the intended working directory and tells shell commands to `cd` there first.

**Adding a new provider:** create `src/<provider>/client.py` subclassing `BaseCLIClient`, register in `src/ai/registry.py` `build_default_registry`, add profiles to `src/ai/catalog.py`, and add the provider icon to `src/ui_emoji.py`.

## Core Runtime Flows

### Message To AI

1. Handler resolves the current session and provider.
2. If the session is busy, the user gets queue/conflict UI rather than concurrent execution on the same session.
3. If idle, the bot writes message state, reserves the session lock, and spawns a detached worker.
4. The detached worker invokes the selected provider CLI, sends the final Telegram response, drains persistent queue items for that session, and then releases the lock.

### Busy Session And Persistent Queue

- Session-level isolation is deliberate. The project avoids same-session concurrent execution.
- `Wait in this session` persists the follow-up request instead of keeping it only in memory.
- Queue draining happens through the same detached worker lifecycle after the active job finishes.

### Restart And Delivery Reliability

- Long-running jobs live in detached workers rather than in the main polling loop.
- Soft restart aims to preserve in-flight work.
- Delivery retry exists so a successful AI result is less likely to be lost because Telegram delivery failed once.
- SQLite is used for operational durability, not just feature storage.

### Network Guard And Delivery Semantics

The shared network guard lives in [`src/network_guard.py`](../src/network_guard.py). It is a callable interceptor plus circuit breaker, not a Java-style reflection proxy. Each guarded call names a dependency key, and circuit state is isolated per key.

Current dependency keys:

| Dependency | Covered Boundary |
|------------|------------------|
| `telegram` | PTB request layer, direct Telegram sends, admin notification HTTP calls |
| `google_calendar` | Google Calendar `request.execute()` calls |
| `weather` | Weather plugin `httpx` geocoding and forecast calls |

The Telegram boundary is primarily enforced by [`src/telegram_request.py`](../src/telegram_request.py), which wraps PTB's `BaseRequest`. Direct send wrappers still exist around delivery-critical paths so tests with mocked bots and fallback decisions use the same transient error classification.

Transient dependency failures raise `NetworkUnavailable`. Once the threshold is reached, later calls raise `CircuitOpen` without touching the network until the reset window expires. A `CircuitOpen` does not count as a Telegram delivery attempt unless an actual send attempt already happened in the same delivery flow.

Generated detached AI responses are persisted via `store_generated_message()` before Telegram delivery starts. Scheduled chat/workspace/plugin/command results that produce a Telegram notification are also inserted into `message_log` before delivery through `insert_schedule_delivery_log()`. Delivery status then follows this model:

- `pending`: generated response exists and delivery is being attempted
- `sent`: Telegram returned success; retry ignores the row
- `failed`: response is preserved for retry
- `retrying`: retry worker has claimed the row with an optimistic update
- `abandoned`: max actual delivery attempts reached

This is at-least-once delivery. It prevents losing completed AI responses, but it cannot prove exactly-once delivery because Telegram may accept a message and then the client may time out before seeing the success response. Chunked messages can also partially deliver before a later chunk fails. Do not treat delivery retry as a duplicate-free transport.

Claude MCP config is written to a stable `.data/mcp_config.json` path. Do not delete it after each call; concurrent CLI processes may be starting with the same config path.

### Local Session Discovery And Adoption

- The bot keeps its own session DB.
- Users may also create sessions directly in the `claude`, `codex`, or `gemini` CLI.
- [`LocalSessionDiscoveryService`](../src/services/local_session_discovery.py) scans provider-local session storage and exposes those sessions for import into the bot.
- Imported local sessions may carry over provider, title, and workspace path so Telegram can continue an existing local workflow instead of forcing a restart.

### Workspace Sessions

- A workspace session is bound to a local directory.
- The AI process runs with that directory as `cwd`.
- The workspace's own `CLAUDE.md` can then shape coding behavior for that project.
- Telegram-specific response formatting is layered on top through the bot prompt.

#### One Session Per Workspace Invariant

Only one active session may exist for a given `(user_id, ai_provider, workspace_path)` tuple. This is enforced by a partial `UNIQUE` index (`idx_sessions_workspace_unique`) with predicate `workspace_path IS NOT NULL AND deleted = 0 AND recycled = 0` in [`src/repository/schema.sql`](../src/repository/schema.sql).

`recycled` is in the predicate so a stale-but-not-yet-purged session does NOT block creating a new workspace session. `deleted` is in the predicate so soft-deleted rows never block new inserts.

Path normalization: `Repository.create_session` and `Repository.find_session_by_workspace` both route through `normalize_workspace_path()` (expands `~`, resolves relative segments and symlinks). Callers should not need to canonicalize the path themselves, but doing so upstream (e.g., in handlers that echo the path back to the user) is still encouraged for display consistency.

**Collision handling by call site:**

| Call site | Strategy | Rationale |
|-----------|----------|-----------|
| `/new_workspace <path>` (`session_handlers.py`) | pre-check via `find_session_by_workspace` → `switch_session` + message | User explicitly asked for the workspace session — adopt existing matches intent. |
| `/workspace` → `Session` button (`workspace_handlers.py`) | pre-check over `list_sessions` → `switch_session` + message | Same intent as `/new_workspace` — user-explicit creation. |
| Schedule log → `resp:sched:` (`session_callbacks.py`) | pre-check → create new session with `workspace_path=None` | User clicked a specific log entry; silently adopting the existing workspace session would overwrite its `provider_session_id` and lose the user's in-progress conversation. The schedule-originated provider session still works via `--resume`; only the workspace tag is dropped. |

When adding a new `create_session(workspace_path=...)` call site, pick the strategy that matches the caller's intent — do NOT centralize into a single "silent drop" policy at the service layer.

### Schedule Types

- `chat`: regular AI schedule using the selected provider/model.
- `workspace`: AI schedule bound to a workspace path.
- `plugin`: schedule that invokes plugin-owned actions rather than a free-form AI prompt.
- `command`: local shell command schedule. The command is executed directly by the bot process and its stdout/stderr is delivered to Telegram without an AI call.

### Command Schedule Draft Flow

Command schedules use a draft-first confirmation flow because the final registration must be owned by the bot, not by an AI claim in free-form text.

- The hidden built-in plugin [`plugins/builtin/command_schedule`](../plugins/builtin/command_schedule) owns draft storage, test execution, and final registration callbacks.
- AI work for command schedules should return either `send_message:seq:<id>` after creating a draft through `command_schedule_create_draft`, or a structured `send_message:command_schedule_draft` JSON payload.
- The bot validates `script_path`, `command`, and `cron_expr` before creating a draft. Registration happens only after the user taps `Register schedule`.
- AI Work sessions persist their domain metadata in `ai_work_sessions`. Command Schedule follow-up messages restore the saved `command_schedule.render_draft` completion hook from that table, so structured draft JSON is rendered as a draft card instead of being delivered as plain chat text.
- Generated command scripts are stored under [`.scheduler/commands/`](../.scheduler/commands) and `.scheduler/` is gitignored. AI-provided paths such as `scripts/foo.py` are treated as suggestions and normalized to `.scheduler/commands/scripts/foo.py`.
- `Run command` writes the script and executes the exact command through [`CommandExecutionService`](../src/services/command_execution_service.py), so the user can inspect the Telegram output before registration.
- Final schedule execution also uses `CommandExecutionService`; command stdout is treated as Telegram HTML and stderr is escaped inside `<pre>`.

### Scheduler Reload Boundary

The supervisor and the main bot process have separate PID artifacts:

- `.data/telegram-bot.pid`: supervisor PID
- `.data/telegram-bot-supervisor.lock`: supervisor PID
- `.data/telegram-bot.lock`: main bot PID

`SIGUSR1` schedule reload is handled by the main bot process only. Runtime reload helpers must read `get_main_lock_path()` / `.data/telegram-bot.lock`; sending `SIGUSR1` to the supervisor will not update in-memory jobs.

## Plugin System

### Plugin Runtime Surface

The base plugin class lives in [`src/plugins/loader.py`](../src/plugins/loader.py).

Required fields:

- `name`
- `description`
- `usage`
- `handle()`

Common optional hooks:

- `CALLBACK_PREFIX`
- `FORCE_REPLY_MARKER`
- `get_schema()`
- `build_storage(repository)`
- `get_scheduled_actions()`
- `execute_scheduled_action()`
- `handle_ai_completion()`
- `register_system_jobs(context)`
- `get_tool_specs()`
- `ai_context.md`

### Plugin Storage Rule

- Plugin source should not call `self.repository` directly.
- Use `build_storage(repository)` to return a plugin-owned bounded adapter.
- Access persistence through `self.storage`, usually wrapped by a typed `store` property.
- Shared plugin-facing adapters live in [`src/repository/adapters/plugin_storage.py`](../src/repository/adapters/plugin_storage.py).

### Plugin Isolation Rule

- Adding a plugin must not require hardcoding names or behavior into core runtime paths.
- Plugin tables own their own DDL via `get_schema()`.
- Callback routing should use unique plugin-owned prefixes.
- ForceReply routing should use unique plugin-owned markers.

### Reference Implementations

- [`plugins/builtin/memo/plugin.py`](../plugins/builtin/memo/plugin.py): simple CRUD and callback flow.
- [`plugins/builtin/todo/plugin.py`](../plugins/builtin/todo/plugin.py): callbacks, ForceReply, scheduled actions.
- [`plugins/builtin/diary/plugin.py`](../plugins/builtin/diary/plugin.py): richer multi-step plugin flow.
- [`plugins/builtin/calendar/plugin.py`](../plugins/builtin/calendar/plugin.py): external API + MCP tools + scheduled actions.
- [`plugins/custom/hourly_ping/plugin.py`](../plugins/custom/hourly_ping/plugin.py): system job registration example.

### Plugin AI And MCP Model

- Static plugin guidance lives in each plugin's `ai_context.md`.
- Live data access should happen through MCP tools rather than pre-expanded dynamic context.
- The bridge server is [`mcp_servers/plugin_bridge_server.py`](../mcp_servers/plugin_bridge_server.py).
- Plugin tool handlers must tolerate the separate-process boundary. They cannot rely on bot in-memory state.
- SQLite reads are fine. SQLite writes need care because the MCP bridge is a separate process.
- Plugin ForceReply handlers may return `dispatch_ai=True`, `ai_message`, optional `ai_session_name`, optional `delivery_buttons`, and optional `post_completion_hook` to hand off a generated prompt to the normal AI job system.
- `delivery_buttons` decorate the final detached AI response without changing its body.
- `post_completion_hook` lets a plugin replace the final delivered body after the AI job completes. Use this when the AI should update domain data, and the plugin should render the user-facing result card from that stored state.
- Scheduled plugin actions may receive the full persisted `schedule` object through `execute_scheduled_action(action_name, chat_id, schedule=...)`. Use that when a plugin needs per-schedule config stored in plugin-owned tables.
- Do not add plugin-specific AI dispatch branches to core handlers. Keep the handoff generic and put domain prompts in the plugin.

## Persistence And Concurrency

- SQLite is the source of truth.
- Keep writes explicit and short.
- `session_locks` prevent duplicate same-session execution at the app level.
- Detached jobs, queued work, sessions, schedules, and plugin tables are all persisted rather than depending only on in-memory state.

## Testing And Validation

Primary commands:

```bash
venv/bin/python -m pytest -q
venv/bin/python -m pytest tests/integration/test_commands.py -q
python -m py_compile plugins/custom/my_plugin/plugin.py
```

What to test first:

- user-visible command or callback behavior
- detached worker and session queue flows
- scheduler behavior
- plugin boundary rules
- provider client command building / parsing

## Where To Edit

- Product positioning or quick-start messaging: [`README.md`](../README.md)
- User setup or runtime operations: [`docs/SETUP.md`](./SETUP.md)
- Stable Telegram-visible behavior: [`docs/SPEC.md`](./SPEC.md)
- Built-in plugin UI/UX behavior: [`docs/SPEC_PLUGINS_BUILTIN.md`](./SPEC_PLUGINS_BUILTIN.md)
- Canonical emoji and labels: [`docs/UI_EMOJI_SYSTEM.md`](./UI_EMOJI_SYSTEM.md)
- Maintainer reference / architecture / plugin model: [`docs/DEVELOPMENT.md`](./DEVELOPMENT.md)
- Always-loaded Claude Code instructions: [`CLAUDE.md`](../CLAUDE.md)

## Deliberate Non-Goals For Docs

- Do not mirror code line-for-line in docs.
- Do not move detailed UI transcripts out of `SPEC` when they are serving as the intended UX contract.
- Do keep giant UI transcripts out of `CLAUDE.md` and out of general maintainer guidance unless they are truly needed there.
- Do not treat `SPEC` updates as optional polish after user-visible flow changes.
- Do not duplicate the same rule across README, SPEC, DEVELOPMENT, and CLAUDE unless it truly belongs in all of them.
