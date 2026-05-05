"""Codex CLI client tests."""

import asyncio
import json
from pathlib import Path
import signal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.ai.client_types import ChatError
from src.codex.client import CodexClient


@pytest.fixture
def client():
    """Basic Codex client."""
    return CodexClient(command="codex", timeout=60)


class TestCodexClient:
    """CodexClient unit tests."""

    @pytest.mark.asyncio
    async def test_run_command_kills_subprocess_on_timeout(self, client):
        """Timeout must terminate the subprocess before returning."""
        with patch("asyncio.create_subprocess_exec") as mock_exec:
            mock_process = AsyncMock()
            mock_process.pid = 12345
            mock_process.communicate = AsyncMock(
                side_effect=[
                    asyncio.TimeoutError(),
                    (b"", b""),
                ]
            )
            mock_process.kill = MagicMock()
            mock_exec.return_value = mock_process

            with patch("src.ai.base_client.os.killpg") as mock_killpg:
                with pytest.raises(asyncio.TimeoutError):
                    await client._run_command(["codex", "exec"], timeout=1)

            mock_killpg.assert_called_once_with(12345, signal.SIGKILL)
            assert mock_process.communicate.await_count >= 1

    @pytest.mark.asyncio
    async def test_chat_returns_timeout_on_subprocess_timeout(self, client):
        """chat() converts subprocess timeout into ChatError.TIMEOUT."""
        with patch.object(client, "_run_command", side_effect=asyncio.TimeoutError):
            response = await client.chat("hello")

        assert response.error == ChatError.TIMEOUT
        assert response.text == ""

    def test_build_command_includes_project_mcp_overrides(self, client):
        """Codex commands should expose the shared project-local MCP bridge."""
        cmd = client._build_command("Hello", session_id=None, model="xhigh", workspace_path=None)

        import sys
        root = Path(__file__).resolve().parents[1]
        expected_command = f'mcp_servers.bot-plugins.command="{sys.executable}"'
        expected_args = (
            f'mcp_servers.bot-plugins.args=["{root / "mcp_servers" / "plugin_bridge_server.py"}"]'
        )

        assert expected_command in cmd
        assert expected_args in cmd
        assert "-m" in cmd
        assert cmd[cmd.index("-m") + 1] == "gpt-5.5"
        assert 'model_reasoning_effort="xhigh"' in cmd

    def test_build_command_skips_mcp_overrides_without_config(self, client):
        """Codex should not emit MCP config overrides when no project config exists."""
        with patch.object(CodexClient, "_load_project_mcp_servers", return_value={}):
            cmd = client._build_command("Hello", session_id=None, model="xhigh", workspace_path=None)

        assert not any(part.startswith("mcp_servers.") for part in cmd)

    def test_build_command_accepts_legacy_gpt54_alias(self, client):
        """Legacy saved profile keys should route to the current Codex model."""
        cmd = client._build_command("Hello", session_id=None, model="gpt54_high", workspace_path=None)

        assert cmd[cmd.index("-m") + 1] == "gpt-5.5"
        assert 'model_reasoning_effort="high"' in cmd

    @pytest.mark.asyncio
    async def test_run_command_sanitizes_cli_env(self, client, monkeypatch):
        """Provider subprocesses should not inherit API-key auth routes."""
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
        monkeypatch.setenv("ANTHROPIC_API_KEY", "anthropic-test")
        monkeypatch.setenv("GEMINI_API_KEY", "gemini-test")

        with patch("asyncio.create_subprocess_exec") as mock_exec:
            mock_process = AsyncMock()
            mock_process.pid = 12345
            mock_process.communicate = AsyncMock(return_value=(b"{}", b""))
            mock_exec.return_value = mock_process

            await client._run_command(["codex", "exec"], timeout=1)

        env = mock_exec.call_args.kwargs["env"]
        assert "OPENAI_API_KEY" not in env
        assert "ANTHROPIC_API_KEY" not in env
        assert "GEMINI_API_KEY" not in env

    @pytest.mark.asyncio
    async def test_chat_uses_model_instructions_file_for_fresh_session(self, tmp_path, monkeypatch):
        """Fresh Codex sessions should pass system prompts via a temp instruction file."""
        monkeypatch.setenv("BOT_DATA_DIR", str(tmp_path / "data"))
        prompt_path = tmp_path / "telegram.md"
        prompt_path.write_text("system prompt\nwith quotes: \"ok\"", encoding="utf-8")
        client = CodexClient(command="codex", system_prompt_file=prompt_path, timeout=60)
        captured: dict[str, object] = {}

        async def fake_run_command(cmd, timeout=None, cwd=None):
            captured["cmd"] = cmd
            config_values = [cmd[index + 1] for index, value in enumerate(cmd) if value == "-c"]
            instruction_configs = [
                value for value in config_values if value.startswith("model_instructions_file=")
            ]
            assert len(instruction_configs) == 1
            prompt_file = Path(json.loads(instruction_configs[0].split("=", 1)[1]))
            captured["prompt_file"] = prompt_file
            assert prompt_file.read_text(encoding="utf-8") == prompt_path.read_text(encoding="utf-8")
            assert not any(value.startswith("instructions=") for value in config_values)
            return (
                "\n".join(
                    [
                        json.dumps({"type": "thread.started", "thread_id": "thread-1"}),
                        json.dumps(
                            {
                                "type": "item.completed",
                                "item": {"type": "agent_message", "text": "ok"},
                            }
                        ),
                    ]
                ),
                "",
                0,
            )

        with patch.object(CodexClient, "_load_project_mcp_servers", return_value={}), patch.object(
            client,
            "_run_command",
            side_effect=fake_run_command,
        ):
            response = await client.chat("Hello", session_id=None, model="xhigh")

        assert response.text == "ok"
        assert response.session_id == "thread-1"
        assert isinstance(captured["prompt_file"], Path)
        assert not captured["prompt_file"].exists()

    @pytest.mark.asyncio
    async def test_chat_does_not_reinject_model_instructions_file_on_resume(self, tmp_path, monkeypatch):
        """Resumed Codex sessions should rely on the existing thread instructions."""
        monkeypatch.setenv("BOT_DATA_DIR", str(tmp_path / "data"))
        prompt_path = tmp_path / "telegram.md"
        prompt_path.write_text("system prompt", encoding="utf-8")
        client = CodexClient(command="codex", system_prompt_file=prompt_path, timeout=60)
        captured: dict[str, list[str]] = {}

        async def fake_run_command(cmd, timeout=None, cwd=None):
            captured["cmd"] = cmd
            return (
                json.dumps(
                    {
                        "type": "item.completed",
                        "item": {"type": "agent_message", "text": "resumed"},
                    }
                ),
                "",
                0,
            )

        with patch.object(CodexClient, "_load_project_mcp_servers", return_value={}), patch.object(
            client,
            "_run_command",
            side_effect=fake_run_command,
        ):
            response = await client.chat("Hello", session_id="thread-1", model="xhigh")

        config_values = [
            captured["cmd"][index + 1]
            for index, value in enumerate(captured["cmd"])
            if value == "-c"
        ]
        assert response.text == "resumed"
        assert not any(value.startswith("model_instructions_file=") for value in config_values)
        assert not any(value.startswith("instructions=") for value in config_values)
