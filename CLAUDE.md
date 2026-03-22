# Telegram AI CLI Bot - Claude Code Guide

This file is intentionally compact.

Claude Code loads `CLAUDE.md` into session context, so only keep instructions here that are worth paying for every time. Deep reference material belongs in [`docs/DEVELOPMENT.md`](docs/DEVELOPMENT.md). User-visible behavior belongs in [`docs/SPEC.md`](docs/SPEC.md). Setup and operations belong in [`docs/SETUP.md`](docs/SETUP.md).

## Project Identity

- Telegram front-end for local Claude Code and Codex CLI sessions.
- Reuses local CLI logins/subscriptions instead of a separate provider API workflow.
- Supports new sessions, local session import, multi-session switching, workspace sessions, schedules, MCP-backed AI work, and custom plugins.
- Reliability matters: detached workers, persistent queueing, delivery retry, and session-level isolation are core product behavior.

## Start Here

- Product overview: [`README.md`](README.md)
- Setup / operations: [`docs/SETUP.md`](docs/SETUP.md)
- Deep implementation reference: [`docs/DEVELOPMENT.md`](docs/DEVELOPMENT.md)
- Stable Telegram UX / command behavior: [`docs/SPEC.md`](docs/SPEC.md)

## When The User Asks For Setup

Use [`docs/SETUP.md`](docs/SETUP.md) as the source of truth.

Required inputs:

- `TELEGRAM_TOKEN`
- `ALLOWED_CHAT_IDS`
- `ADMIN_CHAT_ID`

Optional / situational:

- `AUTH_SECRET_KEY` when `REQUIRE_AUTH=true`
- Google Calendar credentials for the calendar plugin

Helpful setup flow:

1. Create `.env` from `.env.example`.
2. Install dependencies and start the bot.
3. Use `/chatid` to discover the Telegram chat ID if needed.
4. Restart after `.env` changes.

## Working Rules

- Keep handlers thin. They choose flows; they should not become detached-worker or queue-lifecycle owners.
- `main.py` and `bootstrap.py` are composition roots, not business-logic dumping grounds.
- Preserve session-level isolation. Do not introduce same-session concurrent execution casually.
- Detached workers are part of the product's stability model. Do not move long-running AI execution back into the main polling process.
- Keep SQLite writes explicit and short.
- For user-visible UI/UX changes, follow a spec-first mindset: define or update the intended screen/flow contract in `SPEC` before implementation, or at minimum in the same change.
- Update docs when behavior changes:
  - `README.md`: product framing and quick start
  - `docs/SETUP.md`: installation / runtime operations
  - `docs/SPEC.md`: Telegram-visible behavior
  - `docs/DEVELOPMENT.md`: deep maintainer reference

## File Map

- [`src/main.py`](src/main.py): Telegram app entrypoint
- [`src/bootstrap.py`](src/bootstrap.py): runtime construction
- [`src/supervisor.py`](src/supervisor.py): process supervision
- [`src/bot/handlers/`](src/bot/handlers): command/callback/message entrypoints
- [`src/services/`](src/services): session, job, schedule, local-session services
- [`src/repository/`](src/repository): SQLite persistence
- [`src/plugins/loader.py`](src/plugins/loader.py): plugin base class and loader
- [`plugins/builtin/`](plugins/builtin): built-in plugin reference implementations
- [`plugins/custom/`](plugins/custom): custom plugin location
- [`prompts/telegram.md`](prompts/telegram.md): Telegram response prompt

## High-Signal Runtime Notes

### Prompt Model

- The bot's Telegram response-shaping prompt is [`prompts/telegram.md`](prompts/telegram.md).
- Workspace sessions run with `cwd=<workspace>` and append the Telegram prompt so the workspace's own `CLAUDE.md` can guide coding behavior.
- Because root `CLAUDE.md` can affect repo-root Claude sessions, keep this file compact.

### Session And Queue Model

- One active job per session is the intended model.
- If a session is busy, the user should get queue/conflict handling rather than parallel same-session execution.
- Queue drain happens after the active detached job finishes.

### Local Session Import

- Users may have Claude/Codex sessions created outside the bot.
- The bot can discover and adopt them so Telegram can continue existing local work.
- Relevant code: [`src/services/local_session_discovery.py`](src/services/local_session_discovery.py)

### Workspace And Scheduler

- Workspace sessions are directory-bound AI sessions.
- Scheduler supports `chat`, `workspace`, and `plugin` schedule types.
- Workspace schedules should preserve the workspace path and the workspace's own rules.

## Plugin Rules

Detailed plugin reference lives in [`docs/DEVELOPMENT.md`](docs/DEVELOPMENT.md) and in the built-in plugin examples.

Hard rules:

- Do not hardcode plugin names or plugin behavior into unrelated core runtime files.
- Plugin source should not call `self.repository` directly.
- Use `build_storage(repository)` and `self.storage` with bounded adapters.
- Plugin tables own their own DDL through `get_schema()`.
- `CALLBACK_PREFIX` and `FORCE_REPLY_MARKER` must be unique.

Reference implementations:

- [`plugins/builtin/memo/plugin.py`](plugins/builtin/memo/plugin.py)
- [`plugins/builtin/todo/plugin.py`](plugins/builtin/todo/plugin.py)
- [`plugins/builtin/calendar/plugin.py`](plugins/builtin/calendar/plugin.py)
- [`plugins/custom/hourly_ping/plugin.py`](plugins/custom/hourly_ping/plugin.py)

## Validation

Preferred commands:

```bash
venv/bin/python -m pytest -q
venv/bin/python -m pytest tests/integration/test_commands.py -q
python -m py_compile plugins/custom/my_plugin/plugin.py
```

Run focused tests for the feature area you changed before defaulting to the whole suite.

## Documentation Discipline

- `CLAUDE.md` is the always-loaded layer. Keep it compact on purpose.
- If a detail is mainly for humans or maintainers to look up occasionally, put it in `docs/`.
- [`docs/DEVELOPMENT.md`](docs/DEVELOPMENT.md) stays under `docs/` on purpose: it is a deep reference, not something we want auto-loaded into every Claude Code session.
- If a rule must be available automatically to Claude Code but only for specific files or directories, prefer [`.claude/rules/`](.claude/rules).
- Keep detailed UI/UX transcripts and button-by-button flows in [`docs/SPEC.md`](docs/SPEC.md) and [`docs/SPEC_PLUGINS_BUILTIN.md`](docs/SPEC_PLUGINS_BUILTIN.md). Those files are intentionally verbose because they are product contracts.
- This project is intentionally UX-contract-driven. When screen behavior matters, code should implement the spec, not replace it.
- Do not aggressively compress `SPEC` just to make the docs stack look cleaner. Shrink `CLAUDE.md` first.
- Avoid copying large chunks of implementation detail back into `CLAUDE.md`.

### Required Doc Updates

- Telegram-visible flow, command output, callback behavior, restart/queue UX changes: update [`docs/SPEC.md`](docs/SPEC.md)
- Built-in plugin screens or plugin-owned UX changes: update [`docs/SPEC_PLUGINS_BUILTIN.md`](docs/SPEC_PLUGINS_BUILTIN.md)
- Canonical emoji, labels, or UI naming changes: update [`docs/UI_EMOJI_SYSTEM.md`](docs/UI_EMOJI_SYSTEM.md)
- Runtime ownership, plugin architecture, MCP model, or maintainer-facing implementation guidance: update [`docs/DEVELOPMENT.md`](docs/DEVELOPMENT.md)

### Spec-First Interpretation

- New or changed Telegram flows should be treated as product design work, not just implementation work.
- If the intended button layout, wording, empty state, confirmation step, or callback behavior is not clear yet, clarify it in `SPEC` before coding.
- Small internal refactors that do not change user-visible behavior do not require pre-editing `SPEC`, but the spec must still remain correct after the change.
