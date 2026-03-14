"""Shared base class for CLI-based AI clients."""

import asyncio
from contextlib import suppress
import os
import signal
from typing import Optional

from src.logging_config import logger


class BaseCLIClient:
    """Base class providing shared subprocess management for CLI-based AI clients."""

    _DRAIN_TIMEOUT_SECONDS = 5

    @classmethod
    async def _drain_process(
        cls,
        process: asyncio.subprocess.Process,
    ) -> tuple[bytes, bytes]:
        return await asyncio.wait_for(
            process.communicate(),
            timeout=cls._DRAIN_TIMEOUT_SECONDS,
        )

    @staticmethod
    def _kill_process_tree(process: asyncio.subprocess.Process) -> None:
        pid = process.pid
        if not pid:
            return

        try:
            os.killpg(pid, signal.SIGKILL)
            return
        except ProcessLookupError:
            return
        except Exception:
            pass

        with suppress(ProcessLookupError):
            process.kill()

    async def _run_command(
        self,
        cmd: list[str],
        timeout: Optional[int] = None,
        cwd: Optional[str] = None,
    ) -> tuple[str, str, int]:
        """Execute command and return (stdout, stderr, returncode).

        Args:
            cmd: Command to execute
            timeout: Optional timeout in seconds. If None, wait indefinitely.
            cwd: Working directory for the command. If None, use current directory.
        """
        cmd_preview = " ".join(cmd[:5]) + f" ... ({len(cmd)} parts)"
        logger.trace(f"_run_command() - cmd={cmd_preview}")
        logger.trace(f"timeout={timeout}s" if timeout else "timeout=None (unlimited)")
        logger.trace(f"cwd={cwd or '(current directory)'}")

        logger.trace("creating subprocess")
        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=cwd,
            start_new_session=True,
        )
        logger.trace(f"subprocess created - pid={process.pid}")

        logger.trace("waiting for process")
        try:
            if timeout:
                stdout, stderr = await asyncio.wait_for(
                    process.communicate(),
                    timeout=timeout,
                )
            else:
                # wait indefinitely without timeout
                stdout, stderr = await process.communicate()
        except asyncio.CancelledError:
            self._kill_process_tree(process)
            with suppress(Exception):
                await self._drain_process(process)
            raise
        except asyncio.TimeoutError:
            self._kill_process_tree(process)
            with suppress(Exception):
                await self._drain_process(process)
            raise
        stdout_str = stdout.decode("utf-8").strip()
        stderr_str = stderr.decode("utf-8").strip()

        logger.trace(f"process complete - returncode={process.returncode}")
        logger.trace(f"stdout length={len(stdout_str)}")
        logger.trace(f"stderr length={len(stderr_str)}")

        if stderr_str:
            logger.trace(f"stderr content: {stderr_str[:200]}")

        return (stdout_str, stderr_str, process.returncode)
