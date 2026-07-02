"""Command schedule draft plugin tests."""

import pytest

from plugins.builtin.command_schedule.plugin import CommandSchedulePlugin
from src.repository import init_repository, reset_connection, shutdown_repository
from src.repository.adapters import RepositoryPluginDatabase


@pytest.fixture
def command_plugin(tmp_path):
    db_path = tmp_path / "bot.db"
    repo = init_repository(db_path)
    plugin = CommandSchedulePlugin()
    plugin.bind_runtime(repo)
    RepositoryPluginDatabase(repo).executescript(plugin.get_schema())
    yield plugin
    shutdown_repository()
    reset_connection()


@pytest.mark.asyncio
async def test_create_draft_tool_and_completion_hook_render_buttons(command_plugin, monkeypatch):
    monkeypatch.setenv("ADMIN_CHAT_ID", "1")

    result = command_plugin._tool_create_draft(
        title="1234 test",
        description="Prints 1234 every minute.",
        script_path="scripts/test_1234.py",
        script_content="print('<b>1234</b>')\n",
        command="python scripts/test_1234.py",
        cron_expr="* * * * *",
    )

    assert result.startswith("OK: draft created.")
    draft_id = int(result.rsplit(":", 1)[1])

    rendered = await command_plugin.handle_ai_completion(
        "render_draft",
        1,
        {},
        ai_response=f"send_message:seq:{draft_id}",
        ai_error=None,
        session_id="session",
    )
    assert "1234 test" in rendered["text"]
    assert ".data/command_schedules/scripts/test_1234.py" in rendered["text"]
    callbacks = [
        button["callback_data"]
        for row in rendered["delivery_buttons"]
        for button in row
    ]
    assert f"cmdsched:test:{draft_id}" in callbacks
    assert f"cmdsched:register:{draft_id}" in callbacks


@pytest.mark.asyncio
async def test_completion_hook_creates_draft_from_structured_ai_response(command_plugin):
    response = """send_message:command_schedule_draft
{
  "title": "123 test",
  "description": "Prints 123 every minute.",
  "script_path": "scripts/test_123.py",
  "script_content": "print('<b>123</b>')\\n",
  "command": "venv/bin/python scripts/test_123.py",
  "cron_expr": "* * * * *"
}
"""

    rendered = await command_plugin.handle_ai_completion(
        "render_draft",
        7,
        {},
        ai_response=response,
        ai_error=None,
        session_id="session",
    )

    assert "123 test" in rendered["text"]
    assert "venv/bin/python .data/command_schedules/scripts/test_123.py" in rendered["text"]
    callbacks = [
        button["callback_data"]
        for row in rendered["delivery_buttons"]
        for button in row
    ]
    assert callbacks[0].startswith("cmdsched:test:")
    assert any(callback.startswith("cmdsched:register:") for callback in callbacks)
