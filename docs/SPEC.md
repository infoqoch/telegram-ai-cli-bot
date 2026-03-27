# AI Bot - UI/UX Specification

> Layer 3: User experience intent, scenarios, and UX principles
> Documents planning information that does not exist in code.

---

## Overall UX Principles

| Principle | Description |
|-----------|-------------|
| **Immediate feedback** | Plugins/commands respond instantly without AI calls. Even when an AI call is made, the user is aware of a "processing" state |
| **One-tap completion** | Complete actions with a single inline button tap whenever possible. Multi-step flows are minimized |
| **Current state display** | All screens show contextual info such as current session, model, and counts |
| **Safe deletion** | Delete operations require 2-step confirmation (confirm button → execute). Exception: workspace deletion (single tap, since re-adding is easy) |
| **Empty state guidance** | When there is no data, show a button prompting the "add" action |
| **Non-destructive errors** | On error, guide the user to retry without any data loss |
| **English UI** | All user-facing text is in English. Only Claude responses are in Korean, as specified by the system prompt |
| **Plugin escape hatch** | Since plugins can intercept natural language, `/ai` always allows the user to query the current AI directly |

## Response Format Rules

- All responses use **Telegram HTML** (`<b>`, `<i>`, `<code>`, `<pre>`)
- Markdown syntax is forbidden (`**`, `*`, `#`, `` ` ``, `>`)
- If AI responses contain markdown, the `markdown_to_telegram_html()` converter automatically converts them to HTML
- Tables are not supported → use bulleted/numbered lists
- Mobile-optimized: concise text, 4096-character limit (safe margin: 4000 characters)
- Messages exceeding 4000 characters are automatically split at newline boundaries (or at 4000-character intervals if no newline exists)

## Model Representation

| Model | Emoji | List badge |
|-------|-------|------------|
| opus | 🧠 | `[O]` |
| sonnet | ⚡ | `[S]` |
| haiku | 🚀 | `[H]` |

---

## Full Command List

The Telegram slash command picker is automatically synced via API at startup, exposing only the following 5 commands.

| Public picker command | Description |
|-----------------------|-------------|
| `/menu` | Main service launcher |
| `/session` | Current session info |
| `/new` | Create a new session |
| `/sl` | Session list |
| `/tasks` | Active task/queue status |

All other commands are accessed via button hubs or direct input.

| Direct input / button command | Description |
|-------------------------------|-------------|
| `/start` | Start screen (entry to `/menu` / `/help`) |
| `/help` | Brief help + Back to Menu |
| `/help_extend` | Extended help index |
| `/help_session` | Session workflow guide |
| `/help_workspace` | Workspace workflow guide |
| `/help_scheduler` | Scheduler workflow guide |
| `/help_plugins` | Plugin usage guide |
| `/help_{plugin}` | Detailed help for individual plugins |
| `/auth <key>` | Authentication (when `REQUIRE_AUTH=true`) |
| `/status` | Check authentication status |
| `/select_ai` | Select current AI provider (`Claude` / `Codex` / `Gemini`) |
| `/model [model]` | Change or view the current session model |
| `/model_opus`, `/model_sonnet`, `/model_haiku` | Model change shortcuts |
| `/new_opus`, `/new_sonnet`, `/new_haiku` | Quick session creation by model |
| `/new_workspace path [model] [name]` | Create a workspace session |
| `/workspace` | Workspace management |
| `/scheduler` | Schedule management |
| `/plugins` | Plugin button hub |
| `/ai <question>` | Bypass plugins, query the current AI directly |
| `/chatid` | View my Chat ID |
| `/s_{id}` | Switch session |
| `/h_{id}` | Session history (alias `/history_{id}` supported) |
| `/d_{id}` | Delete session (alias `/delete_{id}` supported) |
| `/rename_name` | Rename the current session |
| `/r_{id}_name` | Rename a specific session |
| `/back` | Return to the previous session |
| `/{plugin}` | Redirects to `/help_{plugin}` instead of showing usage inline |
| `/reload [name]` | Reload plugin (admin only) |

---

## Access Control

### Authentication Flow

```
User message arrives
    │
    ├─ Not in ALLOWED_CHAT_IDS → "Access denied." (stop)
    │
    ├─ REQUIRE_AUTH=false → pass
    │
    └─ REQUIRE_AUTH=true
         ├─ Auth session valid → pass
         └─ Not authenticated / expired → "Authentication required." + /auth guidance (stop)
```

### `/auth` Command

- No argument: `Usage: /auth <secret_key>`
- Success: `Authenticated! Valid for {N} minutes.`
- Failure: `Authentication failed. Wrong key.`

### Authentication Status Display

| Location | Authenticated | Not authenticated | Auth not required |
|----------|---------------|-------------------|-------------------|
| `/start` | `Auth: Authenticated (Xm remaining)` | `Auth: Authentication required` | `No authentication required` |
| `/help` | Shows `/auth`, `/status` section | Same | Section hidden |
| `/status` | `Authenticated (Xm remaining)` | `Authentication required.` | - |

---

## Base Screens

### `/start`

```
CLI AI Bot

{auth status}
Current AI: {provider}
Session: [{session info}] ({N} messages)

/menu or /help
```

- Displays 2 buttons at the bottom: `Menu`, `Help`.

### `/help`

Shows brief help only. Detailed docs are separated under `/help_extend`.

- Displays a `Back to Menu` button at the bottom.
- Topic guides are available under `/help_session`, `/help_workspace`, `/help_scheduler`, and `/help_plugins`.

### Unknown Command

When an unregistered `/xxx` is entered: `Unknown command: {command}` + `/menu or /help`

### `/chatid`

```
My Info

- Chat ID: {chat_id}
- Username: @{username}
- Name: {first_name}

Add this ID to ALLOWED_CHAT_IDS.
```

### `/plugins`

```
Plugins

Builtin: {loaded builtin plugin commands or -}
Custom: {loaded custom plugin commands or -}

Tap a plugin button below.
Docs: /help_extend
```

- The plugin body shows only a one-line `Builtin` / `Custom` summary.
- Plugin command lists are generated from the currently loaded plugins, not hardcoded examples.
- Actual usage is opened via dynamic buttons. As more plugins are added, buttons are automatically added.
- Entering `/{plugin}` directly redirects to `/help_{plugin}` docs instead of showing usage inline.

No plugins loaded: `No plugins loaded.`

---

## Session Management

### AI Provider Selection

The user activates one AI provider at a time.

| Provider | Character | Session/model examples |
|----------|-----------|------------------------|
| `Claude` | Conversational coding assistant | `opus`, `sonnet`, `haiku` |
| `Codex` | ChatGPT login-based CLI coding agent | `GPT-5.4 High`, `GPT-5.4 XHigh`, `GPT-5.3 Codex Medium` |
| `Gemini` | Google Gemini CLI agent | `Pro`, `Flash`, `Flash Lite` |

### `/select_ai`

```
Current AI: Claude

[Claude] [Codex] [Gemini]
[Cancel]
```

- The current AI changes immediately upon selection.
- Session list (`/sl`), current session (`/session`), new session (`/new`), and model change (`/model`) all operate based on the currently selected AI.
- Switching AI does not delete the other AI's sessions.
- Only **1 current session** is maintained across the entire system, regardless of AI provider. When switching sessions, the other provider's current session is automatically deselected.
- `Current AI: {provider}` is always shown on the start screen, help, and session screens.

### User Scenarios

**First message from a new user:** If no session exists, one is automatically created with the current AI's default profile → the detached worker calls the provider CLI. The user can start chatting without being aware of sessions.

**Model-selected session creation:** `/new` → select model button → enter name (ForceReply) → creation complete. The shortcut command `/new_opus` etc. lets you skip one step.

**Preset sessions:** `/new_haiku_speedy` (for fast responses), `/new_opus_smarty` (for high-quality analysis). Common combinations in one step.

**Session switching:** `/sl` to list → click session name button → `/session` full info is displayed (model/history/buttons included). When using `/s_{id}` directly, only a brief switch message is shown.

**Session deletion:** `Del` button from the list → confirmation screen → `Delete` button. The current session cannot be deleted (switch to another session first).

**Return to previous session:** `/back` → instantly switches to the previously used session.

**Import Local Session:** Session list → `Import Local` → shows list of sessions created directly in the local CLI → select → register as a bot session.

### Import Local Session

#### Concept

When the user runs the `claude` or `codex` CLI directly in a terminal, those sessions are unknown to the bot. Import Local allows these locally-only sessions to be brought into the bot so conversations can continue from Telegram.

#### Data Sources

Directly scans the provider CLI storage on the local machine:

| Provider | Scan path |
|----------|-----------|
| Claude | `~/.claude/projects/*/sessions-index.json` + raw `*.jsonl` |
| Codex | `~/.codex/session_index.jsonl` + `~/.codex/sessions/YYYY/MM/DD/*.jsonl` |

Sessions not found in the above paths cannot be discovered (sessions from other machines, deleted sessions, etc.).

#### UI Flow

```
Session list → [Import Local]
    │
    ▼
Import Local Session
Recent local sessions across 📚 Claude, 🤖 Codex, and ♊ Gemini.

Showing 1-10 recent sessions.

1. Fix authentication bug
   📚 Claude • a1b2c3d4 • 03/13 14:30
   ~/AiSandbox/my-project

2. Refactor database layer
   🤖 Codex • e5f6g7h8 • 03/12 09:15

[📚 a1b2c3d4 Fix authenticat...]
[🤖 e5f6g7h8 Refactor databa...]
[← Newer] [Older →]                ← pagination
[Back]
```

#### Behavior After Selection

| Situation | Result |
|-----------|--------|
| First import | New session created in bot DB + session detail screen displayed |
| Already imported | Switch to existing bot session + "already attached" notice |
| Workspace path exists | Registered as a workspace session (cwd linked) |
| Workspace path missing/deleted | Registered as a regular session |

#### Notes

- After import, the bot history starts from the point of import (prior provider-side conversations are preserved but not shown in the bot UI)
- Sorted by `updated_at` descending (most recently used first)
- 10 sessions per page; Claude/Codex/Gemini sessions are shown mixed together
- Since only local files are scanned (independent of the bot DB), sessions deleted from the bot may reappear

### Session List Screen

```
Session List (HH:MM:SS)                 ← timestamp shown only on callback refresh
Current AI: Claude

🧠 Project Alpha ●
🤖 ⚡ Research Bot 🔒

[SessionName] [History] [Del]       ← action buttons per session
[OtherSess..] [History] [Del]

[New Session] [Refresh] [Tasks]
[Switch AI]
[Back]                              ← only shown when entered from `/menu -> Sessions`
```

- Maximum 30 sessions shown
- Session names truncated to 10 characters on buttons
- Claude and Codex sessions are shown together on one screen
- Only the current session of the currently selected AI is highlighted with `●`
- Each row shows both the provider icon and model badge

### Session Info Screen (`/session`)

Current session details + last 10 history entries + buttons for model change / rename / history / delete / Session List.
History items show the handler: `[cmd]` (command), `[plugin]` (plugin), `[x]` (rejected), none (AI).

- Current AI is shown at the top: `Current AI: Claude`
- Model buttons show only the models/profiles available for the current session's AI
- Claude and Codex sessions have different sets of model buttons

### Model Change (`/model`)

- `/model` (no argument): redirects to `/session` (shows session info + model change buttons)
- `/model {profile}`: changes the current AI's model/profile
- Same model: `Already using {model}.`
- No session: guidance to create a session
- Unsupported model: shows the supported list for the current AI
- Can also be changed via inline buttons (on `/session` screen, after session switch)

### Model/Profile UX

Users do not need to know the underlying CLI flags. The UI only shows human-readable names.

| AI | UI label | Internal meaning |
|----|----------|------------------|
| Claude | `Opus` | `opus` |
| Claude | `Sonnet` | `sonnet` |
| Claude | `Haiku` | `haiku` |
| Codex | `GPT-5.4 High` | `model=gpt-5.4`, `reasoning=high` |
| Codex | `GPT-5.4 XHigh` | `model=gpt-5.4`, `reasoning=xhigh` |
| Codex | `GPT-5.3 Codex Medium` | `model=gpt-5.3-codex`, `reasoning=medium` |
| Gemini | `Pro` | `gemini-2.5-pro` |
| Gemini | `Flash` | `gemini-2.5-flash` |
| Gemini | `Flash Lite` | `gemini-2.5-flash-lite` |

- The profile key is stored in the DB; the actual per-provider CLI flags are interpreted internally.
- Only UI labels are shown in buttons, session list, `/session`, and `/tasks`.
- Codex profiles are treated as "model profile" concepts that include reasoning depth.

### Session Rename (`/rename`)

- `/rename` (no argument): shows current name + usage guidance
- `/rename_newname`: renames the current session
- `/r_{id}_newname`: renames a specific session by ID
- Rename is also available via the rename button on the `/session` screen (ForceReply method)
- Maximum 50-character name limit

### Session History (`/h_{id}`)

```
Session History
- ID: {id}
- Messages: {count}

1. {message_preview}    ← truncated to 60 characters
2. {message_preview}
...

/s_{id} Switch to this session
```

Empty history: `No history.`

### Session Conflict Handling (Session Queue)

When a new message arrives while a detached worker is running for the current session:

```
Current session is processing
(message preview)

Options:
1. [Wait in this session (recommended)] → queued, auto-processed after completion
2. [other session button]               → switch to that session + process immediately
3. [+Opus/Sonnet/Haiku]                 → create new session + process immediately
4. [Cancel]                             → cancel the request
```

- Queue position shown: `Position: #N`
- Request temporary storage expiry: 5 minutes
- On expiry: `Request expired. Please resend the message.`
- The persistent queue saved after selecting `Wait in this session` does not auto-expire
- Session busy state is determined by the persistent lock, not bot memory
- Therefore, even after a bot restart, the same session remains busy and duplicate execution does not occur

### Response Preservation During Bot Restart

An AI agent during self-development may execute `./run.sh restart-soft`. The UX goal is "continued progress without any response loss."

| Situation | User experience |
|-----------|----------------|
| Immediately after request is received | Handler returns immediately. User does not feel the bot has stopped |
| Soft restart while processing | Existing work continues without any recovery prompt; the completed response arrives normally |
| New message to the same session during restart | Session still appears busy; Session Queue UI works as normal |
| `Wait in this session` selected | Request saved to persistent queue; auto-executed after current work completes |
| Worker itself crashes abnormally | Loss notification prompts user to resend |

---

## Workspace

### Concept

A session bound to a local directory. Responds in Telegram format while following the CLAUDE.md rules of that directory. The purpose is to use a per-project AI assistant in Telegram.

### User Scenarios

**Quick workspace session:** `/new_workspace ~/AiSandbox/my-app opus` → created immediately. Path/model/name in one line.

**Workspace registration + management:** `/workspace` (= `/ws`) → list screen → `+ Add New` → AI recommendation or manual input.

**AI-recommended registration flow:**
1. Enter purpose (ForceReply): "Investment analysis project" ("투자 분석 프로젝트")
2. AI recommends a suitable directory within `ALLOWED_PROJECT_PATHS`
3. Select from recommended list → enter name → registration complete
4. Falls back to manual input if recommendation fails

**Manual registration flow:**
1. Enter path (ForceReply) → validate that path exists
2. Enter name (ForceReply)
3. Enter description (ForceReply)
4. Registration complete

### Workspace List Screen

```
{activity emoji} WorkspaceName
   ~/short/path

[WorkspaceName] [Del]        ← per workspace (deletion is immediate, no confirmation)
[+ Add New] [Refresh]        ← fixed at bottom
```

- Activity: `🔥` if used more than 5 times, `📂` otherwise

### Workspace Detail → Action Selection

When a workspace is selected: choose `[Session]` (start session) / `[Schedule]` (register schedule).

- Start session: select model → create. If a session for the same workspace already exists, auto-switches to it (prevents duplicate creation).
- Uses model selection buttons based on the current AI. Even for the same workspace, Claude/Codex sessions can each exist separately.
- Register schedule: time → minute → `Daily` or `One-time` → model → enter message → registration complete.

---

## Scheduler

### Concept

Tasks that run automatically at a specified time. Three types: Chat (general conversation), Workspace (project context), Plugin (plugin action). The entire app uses a single local timezone (`APP_TIMEZONE`, default `Asia/Seoul`).

### User Scenarios

**View schedules:** `/scheduler` → list of registered schedules (sorted by next run time, showing active/inactive status).

**Add a Chat schedule:** `+ Chat` → hour (00~23h) → minute (5-minute intervals) → `Daily` or `One-time` → model → enter message → register.

- Regular/workspace schedules follow the current AI provider at the time of creation.
- Plugin schedules are independent of the AI provider.
- Complex recurrence patterns are not created directly in the basic UI; instead, the `cron` value is updated later via AI/admin routes.

**Add a Workspace schedule:** `+ Workspace` → select workspace → hour → minute → `Daily` or `One-time` → model → message → registration complete.

**Add a Plugin schedule:** `+ Plugin` → select plugin → select action → hour → minute → `Daily` or `One-time` → register. (No model/message required)

**Manage schedules:** Click a schedule from the list → detail screen → ON/OFF toggle, change time, delete.

### Schedule List Screen

```
{ON/OFF} {type} ScheduleName - Next run

[{ON/OFF} MM-DD HH:MM {type emoji} name]    ← button per schedule
[+ Chat] [+ Workspace] [+ Plugin]            ← add buttons
[Refresh]

System Jobs                                  ← system jobs (hourly_ping, etc.)
  {schedule_info} - {job_name}
```

- Type emoji: `💬` Chat, `📂` Workspace, `🔌` Plugin
- Status: `✅` active, `⏸` inactive

### Schedule Detail Screen

```
{type emoji} ScheduleName

Status: ON/OFF
Time: HH:MM or YYYY-MM-DD HH:MM
Schedule: Daily at HH:MM / Once at YYYY-MM-DD HH:MM
Next run: YYYY-MM-DD HH:MM
Model: model          ← Chat/Workspace only
Path: /path           ← Workspace only
Message: message...   ← Chat/Workspace only (truncated to 80 characters)
Plugin: name          ← Plugin only
Action: action        ← Plugin only
Runs: N

[ON/OFF toggle]
[Change Time (HH:MM)]
[Delete]
[Back]
```

### Change Time Flow

`Change Time` → select hour (00h~23h) → select minute (00~55, 5-minute intervals) → select recurrence (`Daily` / `One-time`) → change complete.

- Hours: based on app local timezone, 4-column grid (24 buttons)
- Minutes: 5-minute intervals, 4-column grid (12 buttons)
- Recurrence: currently configured type is pre-selected. Changing it automatically recalculates `cron_expr` / `run_at_local`.

### ON/OFF Toggle Behavior

- **OFF → ON**: For `once` schedules, resets `run_at_local` to the **nearest future time** relative to now. If today's time has not yet passed, set to today; if it has already passed, set to tomorrow.
- **ON → OFF**: Only removes from the runtime scheduler; configuration is preserved.

### Schedule Execution Result

Sent to the user upon completion (split if over 4000 characters):

```
📅 ScheduleName

{execution result text}

[💬 Session]        ← shown only for Chat/Workspace schedules
```

- `💬 Session` button: switches to the session linked to the execution result. If no session exists yet, registers the provider session from that run as a bot session and switches to it.
- Plugin schedules have no AI session, so no button is shown.

---

## `/ai` - Direct Query to Current AI

### Concept

Since plugins can intercept natural language patterns (e.g., typing "메모" is handled by the memo plugin), this command is an escape hatch for when the user intentionally wants to ask the current AI a question.

### Scenarios

- `/ai 메모란 뭐야` (What is memo?) → bypasses the memo plugin; the current AI answers about "memo"
- `/ai` (no argument) → shows usage guidance

---

## Built-in Plugins

For built-in plugin (Todo, Memo, Weather) specifications, see [SPEC_PLUGINS_BUILTIN.md](SPEC_PLUGINS_BUILTIN.md).

---

## Claude Conversation

### Response Format

```
[SessionInfo|#HistoryCount]
question_preview

{Claude response body}

[Session]
```

- Session info and history number are shown in the header → identifies which session the response belongs to
- Only one shortcut at the bottom: `Session`
- This shortcut button does not modify the original AI response. The click result must open as a new message (follow-up).
- AI response shortcut callbacks and screen navigation callbacks are separate. The former uses non-destructive follow-up; the latter uses same-message edit.

### Error Responses

| Situation | Message |
|-----------|---------|
| Detached watchdog timeout (30 min) | `Task exceeded 30 minutes and was stopped. Please try again.` |
| Provider internal timeout | `Response timed out. Please try again.` |
| Empty response | `{question_preview} Response is empty. Please try again.` |
| CLI error | `Error: {error_detail}` |
| Exception during processing | `An error occurred. Please try again later.` |
| Session initializing | `Session initializing... Please try again shortly!` |
| Session creation failed | `Failed to create Claude session. Please try again.` |

### Long-Running Task Policy

| Elapsed time | Behavior |
|--------------|----------|
| 0–5 minutes | Processing (no additional notification) |
| 5 minutes | Notification: `Task taking N+ minutes. Still running. I will notify you when it finishes.` |
| 5–30 minutes | Detached worker continues running. No separate 5-minute hard timeout on the provider client |
| 30 minutes | Detached watchdog stops the task, stores `watchdog_timeout` in DB, sends timeout message to user |
| Complete (took 5+ minutes) | Notification sent only on success: `Task complete! (Mm Ss)` |
| Bot soft restart | Detached worker keeps running and sends the result after completion |

### Concurrent Request Policy

Session-level serialization is the priority. Only one detached worker is attached to a given session at a time.

| Session state | Behavior |
|---------------|----------|
| Session idle | Detached worker starts immediately |
| Same session busy | Session Queue UI is shown |
| `Wait` selected | Saved to persistent queue; auto-processed after current work completes |
| Different session selected | Switch session + process immediately |
| New session selected | Create new session + process immediately |

### SQLite WAL Notes

- Even with WAL, the model is multiple readers + one writer.
- This means even writes touching different sessions/rows are serialized when they reach the commit phase simultaneously.
- For this reason, the project relies on app-level `session_locks` to serialize workers on the same session rather than on DB row locks. Each write is kept short using autocommit as the baseline principle.

### AI Conversation Execution Scenario

1. User sends a message
2. If the session is idle: create a `message_log` row + reserve `session_locks` + spawn a detached worker
3. Worker calls the provider CLI; after 5 minutes, sends only a "still running" notification
4. If a new message arrives for the same session: show Session Queue UI
5. If user presses `Wait in this session`: request is saved to persistent queue
6. When the current worker finishes (success/failure/timeout): continue processing the next queued message while holding the lock
7. After the last queued message is processed: release the lock
8. The final AI response's bottom shortcut follows the `non-destructive follow-up` rule. Currently only one `Session` button is provided and the original response is not overwritten.

### System Prompt

Global prompt passed to Claude CLI:
- Use Telegram HTML format (no markdown)
- Concise responses (mobile-optimized)
- Respond in Korean (unless otherwise requested)

Workspace sessions: Workspace CLAUDE.md rules + Telegram format rules apply simultaneously.

---

## Task Status (`/tasks`)

Real-time dashboard of messages being processed and in the queue.

```
Processing (2)

1. session-name
   3m 45s elapsed
   Can you summarize the...       ← truncated to 40 characters (when displayed)

2. research
   1m 12s elapsed
   Write a function...

Queue (1)
- session-name: Waiting message pre...  ← truncated to 30 characters

[Refresh] [Session List]
```

- No tasks: `No active tasks`
- Multi-line messages are normalized to a one-line preview for display
- Processing/queue status is calculated from the DB, so it is uninterrupted even immediately after a bot restart

---

## Error / Edge Case Policy

### Error Message Tone

| Type | Tone | Example |
|------|------|---------|
| Access denied | Concise, firm | `Access denied.` |
| Authentication required | Includes guidance | `Authentication required. /auth <key>` |
| Input error | Specific guidance | `Unsupported model: xxx. Available: opus, sonnet, haiku` |
| Not found | States the fact | `Session 'xxx' not found.` |
| System error | Retry guidance | `An error occurred. Please try again later.` |
| Button expired | Retry guidance | `Button expired. Please try again.` |

### Callback Error Handling

| Error type | Handling |
|------------|----------|
| `Message is not modified` | Ignored (duplicate click of same button) |
| `Query is too old` | Expiry notification message |
| `message to edit not found` | Notify that message was deleted |
| Plugin not found | `{Plugin} plugin not found.` |
| Other exceptions | `Error occurred.` + error code displayed |

### Edge Cases

| Situation | Handling |
|-----------|----------|
| Attempt to delete current session | Rejected + "switch to another session first" guidance |
| Message sent without a session | Auto-create with default model (sonnet) |
| `/model` without a session | Guidance to create a session |
| Session name exceeds 50 characters | Rejection message |
| More than 30 memos | Addition rejected + guidance to delete |
| Empty todo input | Rejection message |
| City not found (weather) | Prompt to re-enter |
| No workspace for schedule | Guidance to register via `/workspace` |
| No schedulable plugins | Guidance to implement `get_scheduled_actions()` |
| ForceReply input expired | `Input expired. Please try again.` |
| Session conflict UI selection expired (5 min) | `Request expired. Please resend the message.` |
| Same workspace session already exists | Auto-switch to existing session (prevents duplicate creation) |

---

## Language Policy

| Area | Language | Notes |
|------|----------|-------|
| Bot UI (command responses, buttons, errors) | English | Fully standardized |
| Claude responses | Korean | Specified via system prompt |
| Plugin triggers | Korean | Natural language like `할일` (todo), `메모` (memo), `날씨` (weather) |
| Internal error/debug logs | English | Fully converted |

---

## Future Plans

- Consider removing the `/model` command (redundant with `/session` integration)
