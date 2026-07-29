# UI Emoji System

This document is the working spec for Telegram UI emoji usage.

Source of truth in code:
- `src/ui_emoji.py`: shared UI actions, status, entity, and navigation emoji
- `src/ai/catalog.py`: provider and model-profile emoji

Core rule:
- One emoji maps to one meaning only.
- If a new feature needs a new meaning, add a new emoji. Do not reuse an existing one.
- Domain-specific plugin emoji can exist outside this system, but core bot UX should use this file first.

## Canonical Mapping

### Providers and Models

| Category | Emoji | Meaning | Current code source | Notes |
| --- | --- | --- | --- | --- |
| Provider | `📚` | Claude provider | `src/ai/catalog.py` | Claude family marker |
| Provider | `🤖` | Codex provider | `src/ai/catalog.py` | Reserved for Codex only |
| Provider | `💎` | Gemini provider | `src/ui_emoji.py` | Gemini family marker |
| App/System | `🖥️` | CLI AI Bot / shell-facing app identity | `src/ui_emoji.py` | Avoids collision with Codex |
| Model tier | `🧠` | Top tier | `src/ai/catalog.py` | Opus / Sol XHigh |
| Model tier | `🚀` | Mid tier | `src/ai/catalog.py` | Sonnet / Sol High |
| Model tier | `⚡` | Light tier | `src/ai/catalog.py` | Haiku / Terra XHigh |

### Core Entities

| Category | Emoji | Meaning | Current code source | Notes |
| --- | --- | --- | --- | --- |
| AI chat / AI schedule | `💬` | Generic AI conversation or AI schedule type | `src/ui_emoji.py`, `src/repository/repository.py` | Scheduler AI type now standardized here |
| Workspace | `📂` | Active workspace / workspace-owned item | `src/ui_emoji.py` | Active workspace context |
| Workspace | `🗂` | Inactive workspace summary item | `src/ui_emoji.py` | Summary/list only |
| Plugin | `🔌` | Plugin item or plugin schedule | `src/ui_emoji.py` | Reserved for plugin features |
| Current session | `📍` | Current session in compact summaries | `src/ui_emoji.py` | Summary/list marker |
| Tasks | `📌` | Task/task list entry point | `src/ui_emoji.py` | Not for provider identity |

### Status and State

| Category | Emoji | Meaning | Current code source | Notes |
| --- | --- | --- | --- | --- |
| Success | `✅` | Success / completed | `src/ui_emoji.py` | Generic success state |
| Error | `❌` | Error / failed action | `src/ui_emoji.py` | Generic failure state |
| Warning | `⚠️` | Warning / degraded state | `src/ui_emoji.py` | Non-fatal issue |
| Locked | `🔒` | Locked / in use | `src/ui_emoji.py` | Session/task lock |
| Denied | `⛔` | Access denied | `src/ui_emoji.py` | Auth/permission failure |
| Open | `🔓` | No auth required / unlocked | `src/ui_emoji.py` | Auth state only |
| On | `🟢` | Active / enabled | `src/ui_emoji.py` | Scheduler runtime status |
| Off | `🔴` | Inactive / disabled | `src/ui_emoji.py` | Scheduler runtime status |
| Paused | `⏸` | Temporarily disabled | `src/ui_emoji.py` | Schedule detail toggle |

### Action Labels

| Action | Canonical label | Current code source | Notes |
| --- | --- | --- | --- |
| Session list | `📋 Session List` | `src/ui_emoji.py` | Session hub entry |
| New session | `🆕 New Session` | `src/ui_emoji.py` | Session creation entry |
| History | `📜 History` | `src/ui_emoji.py` | Session history/details |
| Rename | `✏️ Rename` | `src/ui_emoji.py` | Rename flow |
| Delete | `🗑️ Delete` | `src/ui_emoji.py` | Destructive action |
| Refresh | `🔄 Refresh` | `src/ui_emoji.py` | Reload current list/state |
| Tasks | `📌 Tasks` | `src/ui_emoji.py` | Detached jobs / queue entry |
| Switch AI | `🔀 Switch AI` | `src/ui_emoji.py` | Provider selection entry |
| Back | `← Back` | `src/ui_emoji.py` | Navigate to previous screen |
| Cancel | `↩️ Cancel` | `src/ui_emoji.py` | Abort current flow |
| Switch | `🔁 Switch` | `src/ui_emoji.py` | Switch to selected session |
| Generic list | `📋 List` | `src/ui_emoji.py` | Secondary list return |
| Schedule list | `📅 Schedule List` | `src/ui_emoji.py` | Scheduler hub entry |
| Add chat | `💬 Chat` | `src/ui_emoji.py` | Create a generic chat schedule using the current AI |
| Add new | `➕ Add New` | `src/ui_emoji.py` | Workspace add entry |
| Add workspace | `📂 Workspace` | `src/ui_emoji.py` | Scheduler workspace add |
| Add plugin | `🔌 Plugin` | `src/ui_emoji.py` | Scheduler plugin add |
| Workspace session | `💬 Session` | `src/ui_emoji.py` | Start workspace session |
| Workspace schedule | `📅 Schedule` | `src/ui_emoji.py` | Start workspace schedule |
| Manual input | `⌨️ Manual Input` | `src/ui_emoji.py` | Manual path entry |
| Schedules | `📅 Schedules` | `src/ui_emoji.py` | Return to scheduler hub |
| Workspaces | `📂 Workspaces` | `src/ui_emoji.py` | Return to workspace hub |

## Current Findings

| Issue | Before | Now |
| --- | --- | --- |
| `🤖` overlap | Used for generic bot identity and partially overloaded in scheduler-related semantics | Reserved for Codex provider only; generic app identity moved to `🖥️`; scheduler AI type standardized to `💬` |
| AI schedule emoji | Mixed between `🤖` and `💬` depending on code path | Standardized to `💬` |
| Action buttons | Mixed raw strings like `Del`, `Refresh`, `Back`, `Cancel`, `Schedule List` | Shared labels moved into `src/ui_emoji.py` |
| Provider display | Mixed plain text and icon+text | Moving toward icon+text via handler helpers |
| Scheduler provider emphasis | Detail/result screens repeated provider explicitly | Main scheduler screen now carries the current-provider note; action buttons use short emoji+text labels |

## Inventory Scope

The canonical system covers core bot UX:
- session UI
- provider selection UI
- scheduler UI
- workspace UI
- task/admin status UI

Excluded from the core standard:
- weather/domain emoji inside plugins
- deployment/notify emoji for operational messages
- test-only fixture strings

## Interaction Notes

- `/help` is a thin hub. It should show only high-level entry points.
- Detailed guides live under `/help_extend` and `/help_<topic>`.
- Admin-only operations such as `/reload` stay hidden from the main help and are exposed only through admin-scoped help.
- `/plugins` is a catalog, not a generic command template. Do not show placeholder guidance like `/plugin_name`.
- `/sl` is a selection-first screen. Prefer provider icon, tier badge, name, lock, and current pin in the list row. Keep model labels and IDs in detail screens when possible.

## Implementation Rule

When adding or changing Telegram UI:
1. Check `src/ui_emoji.py` first.
2. For provider/model badges, check `src/ai/catalog.py`.
3. If the meaning already exists, reuse the existing constant.
4. If the meaning is new, add it once to the registry and update this document.
