# Session Management

## Overview
Manages bot conversation sessions. Sessions maintain AI conversation history and allow multiple sessions to be stored and switched between.

## DB Tables

### sessions
| Column | Description |
|--------|-------------|
| id | Session ID (UUID) |
| user_id | Telegram user ID |
| ai_provider | AI provider (claude / codex) |
| provider_session_id | External CLI session ID |
| model | Model profile key |
| name | Session name |
| workspace_path | Workspace path (NULL if none) |
| recycled | Inactive archive flag (0/1) |
| deleted | Soft-delete flag (0/1) |
| created_at | Creation time |
| last_used | Last activity time |

### user_provider_state
The current active session is managed via this table, not the `sessions` table directly.

| Column | Description |
|--------|-------------|
| user_id | Telegram user ID |
| ai_provider | AI provider (claude / codex) |
| current_session_id | Currently active session ID |
| previous_session_id | Previous session ID |

## Session Lifecycle

| State | Condition | Description |
|-------|-----------|-------------|
| Active | recycled=0, deleted=0 | Default state, shown in /sl |
| Recycled | recycled=1 | Auto-archived after 24h inactivity |
| Deleted | deleted=1 | Soft-deleted after 7 days |

## AI Assistance Scope
- Query and analyze current session list
- Suggest cleanup of old sessions
- Analyze session usage patterns
- Find and inspect specific sessions

## MCP Tools

Use `query_db` to access data. `{chat_id}` placeholder is auto-replaced.

- Active sessions: `query_db("SELECT id, name, ai_provider, model, last_used FROM sessions WHERE user_id = '{chat_id}' AND recycled = 0 AND deleted = 0 ORDER BY last_used DESC LIMIT 30")`
- Recycled sessions: `query_db("SELECT id, name, ai_provider, last_used FROM sessions WHERE user_id = '{chat_id}' AND recycled = 1 AND deleted = 0 ORDER BY last_used DESC")`
- Current session: `query_db("SELECT s.id, s.name, s.ai_provider, s.model FROM sessions s JOIN user_provider_state ups ON s.id = ups.current_session_id WHERE ups.user_id = '{chat_id}' AND s.deleted = 0")`
- Session message counts: `query_db("SELECT s.name, COUNT(m.id) as msg_count FROM sessions s LEFT JOIN message_log m ON s.id = m.session_id WHERE s.user_id = '{chat_id}' AND s.deleted = 0 GROUP BY s.id ORDER BY msg_count DESC")`
