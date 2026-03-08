"""Detached worker lifecycle management for handler runtime."""

from __future__ import annotations

import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Optional

from src.logging_config import logger

if TYPE_CHECKING:
    from src.repository import Repository


class DetachedJobManager:
    """Manage detached worker spawn, lock checks, and orphan cleanup."""

    def __init__(
        self,
        repo: Optional["Repository"],
        *,
        base_dir: Optional[Path] = None,
        python_executable: Optional[str] = None,
    ):
        self._repo = repo
        self._base_dir = base_dir or Path(__file__).resolve().parents[3]
        self._python_executable = python_executable or sys.executable

    @staticmethod
    def _is_pid_alive(pid: Optional[int]) -> bool:
        """Return whether a local PID is still alive."""
        if not pid or pid <= 0:
            return False

        try:
            os.kill(pid, 0)
            return True
        except OSError:
            return False

    def get_live_session_lock(self, session_id: str) -> Optional[dict]:
        """Return a live detached-worker lock or clean it up if stale."""
        if not self._repo:
            return None

        lock = self._repo.get_session_lock(session_id)
        if not lock:
            return None

        worker_pid = lock.get("worker_pid")
        if worker_pid:
            if self._is_pid_alive(int(worker_pid)):
                return lock

            logger.warning(f"Dead detached worker lock cleaned: session={session_id[:8]}, pid={worker_pid}")
            self._repo.release_session_lock(session_id, lock.get("job_id"))
            job = self._repo.get_message_log(lock["job_id"])
            if job and job["processed"] != 2:
                self._repo.complete_message(lock["job_id"], error="worker_lost")
            return None

        acquired_at = lock.get("acquired_at")
        if acquired_at:
            age = datetime.now(timezone.utc) - datetime.fromisoformat(acquired_at)
            if age.total_seconds() > 60:
                logger.warning(f"Stale unattached lock cleaned: session={session_id[:8]}, job={lock['job_id']}")
                self._repo.release_session_lock(session_id, lock.get("job_id"))
                job = self._repo.get_message_log(lock["job_id"])
                if job and job["processed"] != 2:
                    self._repo.complete_message(lock["job_id"], error="worker_spawn_timeout")
                return None

        return lock

    def is_session_locked(self, session_id: str) -> bool:
        """Return whether a session currently has a live detached-worker lock."""
        return self.get_live_session_lock(session_id) is not None

    def spawn_worker(self, job_id: int) -> int:
        """Spawn one detached worker process for a queued message job."""
        env = os.environ.copy()
        env.pop("CLAUDECODE", None)
        env.setdefault("PYTHONUNBUFFERED", "1")
        env.setdefault("PYTHONPYCACHEPREFIX", ".build")

        process = subprocess.Popen(
            [self._python_executable, "-u", "-m", "src.worker_job", "--job-id", str(job_id)],
            cwd=str(self._base_dir),
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        logger.info(f"Detached worker spawned: job_id={job_id}, pid={process.pid}")
        return process.pid

    def prepare_job(
        self,
        *,
        chat_id: int,
        session_id: str,
        message: str,
        model: str,
        workspace_path: Optional[str] = None,
    ) -> tuple[Optional[int], Optional[str]]:
        """Create a queued message job and reserve its session lock."""
        if not self._repo:
            raise RuntimeError("Repository not initialized")

        job_id = self._repo.enqueue_message(
            chat_id=chat_id,
            session_id=session_id,
            request=message,
            model=model or "sonnet",
            workspace_path=workspace_path,
        )

        if not self._repo.reserve_session_lock(session_id, job_id):
            self._repo.complete_message(job_id, error="session_locked_before_spawn")
            return None, "session_locked"

        return job_id, None

    def attach_worker(self, session_id: str, job_id: int, worker_pid: int) -> None:
        """Attach one spawned worker PID to the reserved session lock."""
        if not self._repo:
            raise RuntimeError("Repository not initialized")

        attached = self._repo.attach_worker_to_session_lock(session_id, job_id, worker_pid)
        if attached:
            return

        lock = self._repo.get_session_lock(session_id)
        if not lock or lock["job_id"] != job_id:
            raise RuntimeError("failed to attach worker to reserved lock")

    def fail_job_spawn(self, session_id: str, job_id: int, exc: Exception) -> None:
        """Release lock reservation and mark the job failed when worker spawn fails."""
        if not self._repo:
            raise RuntimeError("Repository not initialized")

        self._repo.release_session_lock(session_id, job_id)
        self._repo.complete_message(job_id, error=f"worker_spawn_failed: {exc}")
        logger.exception(f"Detached worker spawn failed: job_id={job_id}, error={exc}")

    def start_job(
        self,
        *,
        chat_id: int,
        session_id: str,
        message: str,
        model: str,
        workspace_path: Optional[str] = None,
    ) -> tuple[Optional[int], Optional[str]]:
        """Create a message job, reserve the lock, and spawn a detached worker."""
        job_id, error = self.prepare_job(
            chat_id=chat_id,
            session_id=session_id,
            message=message,
            model=model or "sonnet",
            workspace_path=workspace_path,
        )
        if error:
            return None, error

        try:
            worker_pid = self.spawn_worker(job_id)
            self.attach_worker(session_id, job_id, worker_pid)
        except Exception as exc:
            self.fail_job_spawn(session_id, job_id, exc)
            raise

        return job_id, None

    async def cleanup_orphaned_jobs(self, bot) -> int:
        """Cleanup stale lock reservations or dead detached workers after startup."""
        if not self._repo:
            return 0

        cleaned = 0

        for lock in self._repo.clear_unattached_session_locks(max_age_seconds=60):
            cleaned += 1
            job = self._repo.get_message_log(lock["job_id"])
            if job and job["processed"] != 2:
                self._repo.complete_message(lock["job_id"], error="worker_spawn_timeout")
                await self._notify_message_lost(bot, job, reason="worker start timed out")

        for lock in self._repo.list_all_session_locks():
            worker_pid = lock.get("worker_pid")
            if worker_pid and self._is_pid_alive(int(worker_pid)):
                continue

            cleaned += 1
            self._repo.release_session_lock(lock["session_id"], lock["job_id"])
            job = self._repo.get_message_log(lock["job_id"])
            if job and job["processed"] != 2:
                self._repo.complete_message(lock["job_id"], error="worker_lost")
                await self._notify_message_lost(bot, job, reason="worker stopped unexpectedly")

        if cleaned:
            logger.warning(f"Detached job cleanup completed: cleaned={cleaned}")

        return cleaned

    @staticmethod
    async def _notify_message_lost(bot, job: dict, reason: str) -> None:
        """Notify a user that a detached job response could not be delivered."""
        try:
            short_request = job["request"][:50]
            await bot.send_message(
                chat_id=job["chat_id"],
                text=(
                    f"⚠️ {reason} 때문에 아래 메시지의 응답을 전달하지 못했습니다.\n"
                    f"<code>{short_request}</code>\n\n"
                    f"다시 메시지를 보내주세요."
                ),
                parse_mode="HTML",
            )
        except Exception as exc:
            logger.error(f"유실 알림 전송 실패 (id={job['id']}): {exc}")
