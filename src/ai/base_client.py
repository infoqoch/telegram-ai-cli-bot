"""Shared base class for CLI-based AI clients."""

import asyncio
from contextlib import suppress
from dataclasses import dataclass
import os
import shlex
import signal
from pathlib import Path
from typing import Optional

from src.logging_config import logger


@dataclass(frozen=True)
class PromptConfig:
    """Resolved prompts for one AI call."""

    system: Optional[str] = None
    append: Optional[str] = None


class BaseCLIClient:
    """Base class providing shared subprocess management and prompt resolution."""

    _DRAIN_TIMEOUT_SECONDS = 5

    def __init__(
        self,
        command: str,
        system_prompt_file: Optional[Path] = None,
        timeout: Optional[int] = None,
    ):
        self.command_parts = shlex.split(command)
        self.system_prompt = self._load_system_prompt(system_prompt_file)
        self.timeout = timeout

    @staticmethod
    def _load_system_prompt(path: Optional[Path]) -> Optional[str]:
        if path and path.exists():
            return path.read_text(encoding="utf-8")
        return None

    def _resolve_prompts(self, workspace_path: Optional[str]) -> PromptConfig:
        """Determine prompts for one AI call.

        Non-workspace: system_prompt as main system prompt.
        Workspace: system_prompt as append (workspace has its own context).
        """
        if workspace_path:
            return PromptConfig(append=self.system_prompt)
        return PromptConfig(system=self.system_prompt)

    def _inject_prompt_args(self, cmd: list[str], prompts: PromptConfig) -> None:
        """Inject prompt arguments into CLI command. Override in subclass."""
        raise NotImplementedError

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
