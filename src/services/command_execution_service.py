"""Shared shell command execution for command schedules and drafts."""

from __future__ import annotations

import asyncio
import html
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


@dataclass(frozen=True)
class CommandExecutionResult:
    """Result of one command invocation."""

    command: str
    cwd: str
    stdout: str
    stderr: str
    returncode: Optional[int]
    timed_out: bool = False

    @property
    def ok(self) -> bool:
        return self.returncode == 0 and not self.timed_out


class CommandExecutionService:
    """Execute command schedule scripts with bounded runtime and output."""

    def __init__(
        self,
        *,
        default_cwd: Path | str,
        timeout_seconds: float = 60,
        max_output_chars: int = 12000,
    ):
        self._default_cwd = Path(default_cwd).resolve()
        self._timeout_seconds = timeout_seconds
        self._max_output_chars = max_output_chars

    async def run(
        self,
        command: str,
        *,
        cwd: Path | str | None = None,
        timeout_seconds: float | None = None,
    ) -> CommandExecutionResult:
        """Run one shell command and capture stdout/stderr."""
        effective_cwd = Path(cwd).resolve() if cwd else self._default_cwd
        timeout = timeout_seconds if timeout_seconds is not None else self._timeout_seconds

        process = await asyncio.create_subprocess_shell(
            command,
            cwd=str(effective_cwd),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=os.environ.copy(),
        )
        try:
            stdout_raw, stderr_raw = await asyncio.wait_for(process.communicate(), timeout=timeout)
            return CommandExecutionResult(
                command=command,
                cwd=str(effective_cwd),
                stdout=self._decode(stdout_raw),
                stderr=self._decode(stderr_raw),
                returncode=process.returncode,
            )
        except asyncio.TimeoutError:
            process.kill()
            stdout_raw, stderr_raw = await process.communicate()
            timeout_msg = f"Command timed out after {timeout:g}s."
            stderr = self._decode(stderr_raw)
            stderr = f"{stderr}\n{timeout_msg}".strip()
            return CommandExecutionResult(
                command=command,
                cwd=str(effective_cwd),
                stdout=self._decode(stdout_raw),
                stderr=stderr,
                returncode=process.returncode,
                timed_out=True,
            )

    def build_telegram_body(self, result: CommandExecutionResult) -> str | None:
        """Return the Telegram body for a command result, preserving stdout HTML."""
        stdout = result.stdout.strip()
        stderr = result.stderr.strip()

        if not stdout and not stderr:
            return None

        chunks: list[str] = []
        if stdout:
            chunks.append(stdout)
        if stderr:
            label = "Errors" if result.ok else "Command failed"
            chunks.append(f"<b>{html.escape(label)}</b>\n<pre>{html.escape(stderr)}</pre>")
        return "\n\n".join(chunks)

    def _decode(self, data: bytes) -> str:
        text = data.decode("utf-8", errors="replace").strip()
        if len(text) <= self._max_output_chars:
            return text
        omitted = len(text) - self._max_output_chars
        return f"{text[:self._max_output_chars]}\n... ({omitted} chars omitted)"
