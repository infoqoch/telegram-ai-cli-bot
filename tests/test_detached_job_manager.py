"""Unit tests for detached worker runtime management."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.bot.runtime import DetachedJobManager


class TestDetachedJobManager:
    """DetachedJobManager tests."""

    def test_start_job_reserves_lock_and_attaches_worker(self):
        repo = MagicMock()
        repo.enqueue_message.return_value = 41
        repo.reserve_session_lock.return_value = True
        repo.attach_worker_to_session_lock.return_value = True
        manager = DetachedJobManager(repo)

        with patch.object(manager, "spawn_worker", return_value=99123):
            job_id, error = manager.start_job(
                chat_id=12345,
                session_id="session-1",
                message="hello",
                model="sonnet",
                workspace_path=None,
            )

        assert (job_id, error) == (41, None)
        repo.enqueue_message.assert_called_once()
        repo.reserve_session_lock.assert_called_once_with("session-1", 41)
        repo.attach_worker_to_session_lock.assert_called_once_with("session-1", 41, 99123)

    def test_get_live_session_lock_cleans_dead_worker(self):
        repo = MagicMock()
        repo.get_session_lock.return_value = {
            "session_id": "session-1",
            "job_id": 41,
            "worker_pid": 99123,
        }
        repo.get_message_log.return_value = {"id": 41, "processed": 1}
        manager = DetachedJobManager(repo)

        with patch.object(manager, "_is_expected_worker_alive", return_value=False):
            lock = manager.get_live_session_lock("session-1")

        assert lock is None
        repo.release_session_lock.assert_called_once_with("session-1", 41)
        repo.complete_message.assert_called_once_with(41, error="worker_lost")

    def test_is_expected_worker_alive_rejects_reused_pid(self):
        manager = DetachedJobManager(MagicMock())

        with patch.object(manager, "_is_pid_alive", return_value=True), patch.object(
            manager,
            "_get_pid_command",
            return_value="/usr/bin/python -u -m src.worker_job --job-id 99",
        ):
            assert manager._is_expected_worker_alive(99123, 41) is False

    def test_spawn_worker_sanitizes_subscription_cli_env(self, tmp_path, monkeypatch):
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
        monkeypatch.setenv("ANTHROPIC_API_KEY", "anthropic-test")
        monkeypatch.setenv("GEMINI_API_KEY", "gemini-test")
        monkeypatch.setenv("CLAUDE_CODE_PROVIDER_MANAGED_BY_HOST", "1")
        monkeypatch.delenv("PYTHONPYCACHEPREFIX", raising=False)
        manager = DetachedJobManager(
            MagicMock(),
            base_dir=tmp_path,
            python_executable="/usr/bin/python3",
        )

        with patch("src.bot.runtime.detached_job_manager.get_log_dir", return_value=tmp_path / "logs"), patch(
            "src.bot.runtime.detached_job_manager.os.path.isfile",
            return_value=False,
        ), patch("src.bot.runtime.detached_job_manager.subprocess.Popen") as popen:
            popen.return_value.pid = 99123

            assert manager.spawn_worker(41) == 99123

        env = popen.call_args.kwargs["env"]
        assert env["PYTHONUNBUFFERED"] == "1"
        assert env["PYTHONPYCACHEPREFIX"] == ".build"
        assert "OPENAI_API_KEY" not in env
        assert "ANTHROPIC_API_KEY" not in env
        assert "GEMINI_API_KEY" not in env
        assert "CLAUDE_CODE_PROVIDER_MANAGED_BY_HOST" not in env

    @pytest.mark.asyncio
    async def test_cleanup_orphaned_jobs_notifies_for_stale_locks(self):
        repo = MagicMock()
        repo.clear_unattached_session_locks.return_value = [{"job_id": 41}]
        repo.get_message_log.side_effect = [
            {"id": 41, "processed": 1, "chat_id": 12345, "request": "hello"},
        ]
        repo.list_all_session_locks.return_value = []
        bot = MagicMock()
        bot.send_message = AsyncMock()
        manager = DetachedJobManager(repo)

        cleaned = await manager.cleanup_orphaned_jobs(bot)

        assert cleaned == 1
        repo.complete_message.assert_called_once_with(41, error="worker_spawn_timeout")
        bot.send_message.assert_called_once()
