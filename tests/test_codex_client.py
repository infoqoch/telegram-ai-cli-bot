"""Codex CLI client tests."""

import asyncio
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
