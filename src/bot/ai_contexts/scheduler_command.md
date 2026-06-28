# Command Schedule Implementation Guidelines

When the user requests to create a periodic background task (such as crawling, polling, or monitoring) in this domain, you MUST implement it as a `command` schedule instead of a `chat` schedule.

Follow these 3 core guidelines strictly:

1. **Standalone Script (`python abc.py`)**
   - Do not use AI inference for the scheduled task execution itself, as it wastes time and API tokens.
   - Instead, write a standalone Python script (e.g., `python fetch_data.py`) that fetches or processes the required data independently.
   - Ensure the script is saved in the workspace.

2. **Telegram-Friendly Output Formatting (HTML)**
   - The script's output (`stdout`) should not be plain console logs. It will be sent directly to the user via Telegram.
   - Format the final output using Telegram-compatible HTML tags (e.g., `<b>`, `<i>`, `<code>`, `<pre>`) and appropriate emojis so it renders beautifully in the messenger.

3. **Interactive Testing and Feedback Loop (Crucial)**
   - Do NOT just write the script and silently register it.
   - **While chatting with the user**, you must explicitly run the script you just wrote using your terminal tools.
   - Show the actual output of the script to the user in your message so they can immediately see how it will look in Telegram.
   - Example: "제가 스크립트를 작성하여 테스트 실행해 보았습니다. 결과는 다음과 같습니다: [실행 결과]. 이 형태로 스케줄을 등록할까요?"
   - Once the user approves the output format, register the schedule using the `query_db` tool with `schedule_type = 'command'` and `message = 'python <your_script>.py'`, then call `reload_schedules()`.
