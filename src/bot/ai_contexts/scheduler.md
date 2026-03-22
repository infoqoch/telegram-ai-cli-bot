# Scheduler - Scheduled Message and Task Management

## Feature Overview
A feature that lets users register and manage AI conversations, workspace tasks, and plugin actions that run automatically at specified times.

## Schedule Types (schedule_type)
- **chat**: Regular AI conversation schedule. Sends a message to the AI at the specified time and receives a response.
- **workspace**: Workspace-based schedule. Runs an AI task applying the CLAUDE.md rules of a specific project directory.
- **plugin**: Plugin action schedule. Runs actions provided by plugins such as todo checks or diary reminders (no AI model or message required).

## Trigger Types (trigger_type)
- **cron**: Daily repeat (based on hour and minute)
- **run_at**: One-time execution (runs at the time specified in run_at_local, then auto-disables)
- **cron_expr**: Repeat based on a cron expression (e.g., specific days of the week only)

## DB Schema (schedules table)
| Column | Description |
|--------|-------------|
| id | Schedule unique ID |
| user_id | User ID |
| chat_id | Telegram chat ID |
| hour, minute | Execution time (0-23 hours, 0-59 minutes) |
| message | Message to send to AI (chat/workspace types) |
| name | Schedule name (displayed to the user) |
| schedule_type | chat / workspace / plugin |
| trigger_type | cron / run_at / cron_expr |
| cron_expr | Cron expression (when trigger_type=cron_expr) |
| run_at_local | One-time execution time (when trigger_type=run_at) |
| ai_provider | AI provider (claude / codex) |
| model | AI model profile key |
| workspace_path | Workspace path (workspace type) |
| plugin_name | Plugin name (plugin type) |
| action_name | Plugin action name (plugin type) |
| enabled | Active status (1=ON, 0=OFF) |
| last_run | Last execution time |
| last_error | Last error message |
| run_count | Total execution count |

## User Operations
- **Add**: Register a new schedule (select time, message, and type)
- **Enable/Disable**: Toggle schedule active/inactive
- **Change time**: Modify execution time
- **Delete**: Remove a schedule
- **View list**: See all registered schedules

## AI Assistance Areas
- Schedule optimization suggestions (spread across time zones, remove duplicates)
- New schedule recommendations (based on usage patterns)
- Analyze schedule execution results
- When errors occur, diagnose the cause and suggest solutions

## MCP Tools

Use the `query_db` tool when you need to query or modify data. The `{chat_id}` placeholder is substituted automatically.

- List all: `query_db("SELECT * FROM schedules WHERE chat_id = {chat_id}")`
- Active schedules only: `query_db("SELECT * FROM schedules WHERE chat_id = {chat_id} AND enabled = 1")`
- Schedules with errors: `query_db("SELECT * FROM schedules WHERE chat_id = {chat_id} AND last_error IS NOT NULL")`
- Inspect table structure: `db_schema("schedules")`

### reload_schedules

**Important:** After modifying schedule data via `query_db`, call `reload_schedules()` to apply changes to the running scheduler.

After adding, modifying, or deleting schedules via `query_db`, you must call `reload_schedules()` for the changes to take effect in the runtime scheduler. Modifying the DB alone will not affect actual behavior until the bot is restarted.
