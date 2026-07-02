# Project Backlog

Updated: 2026-07-02

This is the single active backlog for repository cleanup and follow-up engineering work.

## Completed In Current Cleanup Pass

- Command schedule draft workflow implemented and committed.
- Scheduler reload now targets the main bot process lock PID.
- Test command schedule and draft rows were removed from the local DB.
- `.claude/rules/*` was moved to `.ai/rules/*`.
- Public Telegram command picker was realigned with `docs/SPEC.md`.
- Usage-limit delivery no longer double-escapes already escaped HTML.
- Startup guardrails were added for Python version and provider CLI availability.
- Duplicate review/audit/research files were consolidated into this backlog.

## Active: Correctness And Reliability

### Queued-work promotion atomicity

Risk: a worker can pop a queued row, create a message log, and rebind a session lock across multiple persistence operations. A crash in the middle can lose queued work or leave state partially advanced.

Next action:

- Add one transactional repository API that promotes a queued request into a message log and rebinds `session_locks` together.
- Add a failure-path test for worker loss between queue pop and lock rebind.

### Schedule delivery persistence ordering

Risk: schedule execution can produce a result and then fail during Telegram delivery or persistence. The durable truth should clearly distinguish generated, sent, retrying, failed, and abandoned states.

Next action:

- Re-check schedule execution persistence against delivery retry semantics.
- Add focused tests for Telegram send failure during schedule execution.

### Lock cleanup side effects

Risk: stale lock cleanup during read-oriented paths can mutate runtime state in surprising places.

Next action:

- Identify call sites that clean stale locks while reading status.
- Move mutation to explicit maintenance or claim paths where practical.

## Active: Setup And Provider Guardrails

### Provider auth/install diagnostics

Need: `/status` or startup logs should make local provider availability clearer without exposing credentials.

Next action:

- Show whether configured `claude`, `codex`, `gemini`, and `agy` commands are installed.
- Where practical, show whether each provider appears locally authenticated.
- Keep API keys and token values out of logs and Telegram messages.

### Antigravity restart hang

Observation: previous local notes reported that `agy --print` may hang when instructed to restart the bot process.

Current guardrail:

- `AgyClient` derives a subprocess timeout from `--print-timeout` plus a short grace period, so non-interactive Agy calls are not allowed to run forever.

Next action:

- Reproduce with the current version before adding any workaround.
- Do not add `.agents/AGENTS.md`; provider-specific rules should not bypass the repository's `.ai/` isolation direction.
- Prefer process-timeout or command-shaping fixes inside the Antigravity client if the issue still exists.

Remaining review notes:

- Agy session creation is deferred until the first real chat because the CLI does not expose the same explicit session-create flow as Claude/Codex.
- New-session binding still depends on local artifact discovery under `~/.gemini/antigravity-cli/`; this is weaker than providers that return a session ID directly.
- The current implementation avoids binding to an unchanged stale `last_conversations.json` cache, but concurrent external Agy CLI activity can still make discovery ambiguous.
- Workspace MCP setup writes `.agents/mcp.json` into the target workspace. This is CLI-compatible but provider-specific local state; keep it gitignored and do not generalize it into tracked project rules.
- Add a lightweight real-CLI smoke test plan before treating Agy parity as complete: `agy models`, one new `--print` call, one `--conversation` resume, and one workspace call with `--add-dir`.

## Active: UX And Spec Consistency

### Command picker

Current decision:

- Public picker remains compact: `/menu`, `/session`, `/new`, `/sl`, `/tasks`.
- `/sessions`, `/workspace`, and `/scheduler` remain direct input/button commands.

Next action:

- Keep `src/bot/command_catalog.py`, `docs/SPEC.md`, and startup sync tests aligned when commands change.

### Response formatting ownership

Current decision:

- User/AI Markdown responses pass through `markdown_to_telegram_html`.
- System/plugin HTML responses must not pass through the Markdown converter.
- Provider error text is escaped exactly once and then sent as Telegram HTML.
- Command schedule stdout is treated as Telegram HTML; stderr is escaped in a `<pre>` block.

Next action:

- Add regression tests when changing provider error handling, plugin completion hooks, or command schedule delivery.

## Active: Runtime Observability

- Add MCP bridge coverage for tool discovery and hidden plugin tools.
- Add delivery retry tests around retrying and abandoned states.
- Add startup wiring characterization tests for scheduler, plugin loader, and AI registry composition.
- Improve logs for selected CLI path, sanitized env keys, provider session reuse, and invalidation reason.

## Deferred Architecture Work

These are valid but should wait until the correctness guardrails above are stronger:

- Shrink `BaseHandler` by extracting pending request, detached job orchestration, and UI draft state collaborators.
- Split `message_handlers.handle_message()` into explicit dispatch stages.
- Consolidate detached-job lifecycle ownership into one state machine.
- Type multi-step draft state instead of passing loose dictionaries.
- Split the large repository surface into narrower persistence services.

## Parked Research

### OpenClaw comparison

Adopted or already handled:

- CLI child environment sanitization is implemented in `src/ai/env_safety.py`.
- Direct OAuth transport and auth-profile storage are not a fit for this project right now.

Still worth considering later:

- Codex prompt-file strategy, after validating current local Codex CLI compatibility.
- Session binding metadata and invalidation when model, workspace, system prompt, or MCP config changes.
- Streaming/no-output watchdog only after core runtime delivery semantics are tighter.

### Generated provider files

Current behavior:

- `GEMINI.md` is generated by `src/gemini/client.py` for Gemini CLI prompt support and is gitignored.
- `.agents/mcp.json` is generated Antigravity MCP config and is gitignored.
- AI-created command schedule scripts are generated under `.scheduler/commands/` and are gitignored.

Potential later cleanup:

- If root cleanliness becomes strict, move non-workspace Gemini execution into a generated runtime directory such as `.data/gemini-root/`.
- Do not change this casually; Gemini uses cwd-local `GEMINI.md` as its prompt mechanism.
