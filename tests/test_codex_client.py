"""Codex CLI client tests."""

import asyncio
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
