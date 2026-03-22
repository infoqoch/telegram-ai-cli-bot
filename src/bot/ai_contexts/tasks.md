# Task Status - AI Task Processing State Monitor

## Feature Overview
A read-only monitoring feature for checking the real-time status of in-progress AI requests, queued messages, and session locks.

## Related DB Tables

### message_log (AI request tracking)
| Column | Description |
|--------|-------------|
| id | Request unique ID |
| chat_id | Telegram chat ID |
| session_id | Session ID |
| model | Model used |
| request | Request message content |
| request_at | Request time |
| processed | Processing state (0=pending, 1=processing, 2=complete) |
| processed_at | Processing completion time |
| response | AI response content |
| error | Error message |
| delivery_status | Delivery state (not_ready / pending / delivered / failed) |
| delivery_attempts | Number of delivery attempts |

### queued_messages (concurrent request queue)
| Column | Description |
|--------|-------------|
| id | Queue item ID |
| session_id | Target session ID |
| user_id | User ID |
| chat_id | Telegram chat ID |
| message | Queued message content |
| model | Model used |
| created_at | Queue registration time |

### session_locks (session locks)
| Column | Description |
|--------|-------------|
| session_id | Locked session ID |
| job_id | ID of the job being processed |
| worker_pid | Worker process PID |
| acquired_at | Lock acquisition time |

## User Operations
- **View status**: Check the list of currently in-progress and queued tasks
- **Refresh**: Update to the latest state
- Read-only feature — tasks cannot be cancelled or modified

## AI Assistance Areas
- Explain and interpret task status
- Diagnose stalled tasks and suggest solutions
- Analyze task processing patterns (average duration, error frequency, etc.)
- System optimization suggestions

## MCP Tools

Use the `query_db` tool when you need to query data. The `{chat_id}` placeholder is substituted automatically.

- In-progress tasks: `query_db("SELECT * FROM message_log WHERE chat_id = {chat_id} AND processed = 1 ORDER BY request_at DESC LIMIT 10")`
- Queued messages: `query_db("SELECT * FROM queued_messages WHERE chat_id = {chat_id} ORDER BY created_at ASC")`
- Session lock status: `query_db("SELECT * FROM session_locks")`
- Inspect table structure: `db_schema("message_log")`
