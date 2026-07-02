# Command Schedule Implementation Guidelines

When the user requests to create a periodic background task (such as crawling, polling, or monitoring) in this domain, you MUST implement it as a `command` schedule instead of a `chat` schedule.

Follow these 3 core guidelines strictly:

1. **Standalone Script (`python abc.py`)**
   - Do not use AI inference for the scheduled task execution itself, as it wastes time and API tokens.
   - Instead, write a standalone Python script (e.g., `python fetch_data.py`) that fetches or processes the required data independently.
   - Provide a relative script path in the draft. The bot stores generated command scripts under `.scheduler/commands/` so they are easy to inspect but stay out of git.

2. **Telegram-Friendly Output Formatting (HTML)**
   - The script's output (`stdout`) should not be plain console logs. It will be sent directly to the user via Telegram.
   - Format the final output using Telegram-compatible HTML tags (e.g., `<b>`, `<i>`, `<code>`, `<pre>`) and appropriate emojis so it renders beautifully in the messenger.

3. **Draft-first Registration Protocol (Mandatory)**
   - Do NOT insert directly into the `schedules` table.
   - Do NOT call `reload_schedules()` yourself.
   - Do NOT say that a schedule has been registered.
   - Return a temporary command schedule draft as structured JSON. The bot will validate it, save the script, render a "Run command" button, and register the schedule only after the user confirms.
   - Your entire final response must be exactly:

     send_message:command_schedule_draft
     {
       "title": "short user-facing title",
       "description": "what message/result the user should expect",
       "script_path": "relative/path_inside_project.py",
       "script_content": "full Python script content",
       "command": "venv/bin/python relative/path_inside_project.py",
       "cron_expr": "5-field cron expression"
     }

   - The JSON must be valid JSON. Escape newlines in `script_content` as `\n`.
   - `script_path` must be a relative `.py` path inside the project. The bot will normalize it into `.scheduler/commands/<script_path>`.
   - `command` must run the same script path and should usually be `venv/bin/python <script_path>`.
   - If an MCP tool named `command_schedule_create_draft` is available, you may use it instead. After that tool returns success, your entire final response must be exactly `send_message:seq:<id>`.
