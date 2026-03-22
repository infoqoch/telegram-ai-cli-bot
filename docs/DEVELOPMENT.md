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
- [`docs/DEVELOPMENT.md`](./DEVELOPMENT.md): architecture, extension patterns, runtime flows, maintainer reference.
- [`CLAUDE.md`](../CLAUDE.md): compact always-loaded instructions for Claude Code.
- [`.claude/rules/`](../.claude/rules): path-scoped Claude Code guidance for focused edits.

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
- [`src/claude/`](../src/claude) and [`src/codex/`](../src/codex): provider-specific CLI wrappers.
- [`src/plugins/`](../src/plugins): plugin runtime and loader.
- [`prompts/telegram.md`](../prompts/telegram.md): Telegram formatting and response-shaping prompt.

## Provider And Prompt Model

- The shared Telegram prompt is loaded from [`prompts/telegram.md`](../prompts/telegram.md).
- Non-workspace calls pass that prompt as the main system prompt.
- Workspace calls set `cwd=<workspace>` and append the Telegram prompt, allowing the workspace's own `CLAUDE.md` to shape coding behavior while the bot keeps Telegram formatting constraints.
- Root [`CLAUDE.md`](../CLAUDE.md) therefore matters most when Claude Code is run from the repository root or for non-workspace Claude sessions.

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

### Local Session Discovery And Adoption

- The bot keeps its own session DB.
- Users may also create sessions directly in the `claude` or `codex` CLI.
- [`LocalSessionDiscoveryService`](../src/services/local_session_discovery.py) scans provider-local session storage and exposes those sessions for import into the bot.
- Imported local sessions may carry over provider, title, and workspace path so Telegram can continue an existing local workflow instead of forcing a restart.

### Workspace Sessions

- A workspace session is bound to a local directory.
- The AI process runs with that directory as `cwd`.
- The workspace's own `CLAUDE.md` can then shape coding behavior for that project.
- Telegram-specific response formatting is layered on top through the bot prompt.

### Schedule Types

- `chat`: regular AI schedule using the selected provider/model.
- `workspace`: AI schedule bound to a workspace path.
- `plugin`: schedule that invokes plugin-owned actions rather than a free-form AI prompt.

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
- Maintainer reference / architecture / plugin model: [`docs/DEVELOPMENT.md`](./DEVELOPMENT.md)
- Always-loaded Claude Code instructions: [`CLAUDE.md`](../CLAUDE.md)

## Deliberate Non-Goals For Docs

- Do not mirror code line-for-line in docs.
- Do not keep giant UI transcripts when the code and tests already explain the flow.
- Do not duplicate the same rule across README, SPEC, DEVELOPMENT, and CLAUDE unless it truly belongs in all of them.
