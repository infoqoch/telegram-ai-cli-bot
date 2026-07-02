"""Explicit real-CLI smoke test for Antigravity."""

from __future__ import annotations

import asyncio
import shutil
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

from src.agy.client import AgyClient
from src.ai import get_default_model
from src.ai.client_types import ChatResponse
from src.ai.env_safety import build_cli_subprocess_env


@dataclass(frozen=True)
class AgySmokeStep:
    """One smoke-test step result."""

    name: str
    ok: bool
    detail: str


@dataclass(frozen=True)
class AgySmokeResult:
    """Agy smoke-test result."""

    steps: list[AgySmokeStep]
    session_id: Optional[str]
    workspace_path: Optional[str]
    elapsed_seconds: float

    @property
    def ok(self) -> bool:
        return bool(self.steps) and all(step.ok for step in self.steps)


class AgySmokeTestService:
    """Run a small explicit Agy real-CLI check."""

    def __init__(
        self,
        *,
        base_dir: Path,
        prompt_file: Path,
        data_dir: Path,
        command: str = "agy",
        client_factory: Callable[..., AgyClient] = AgyClient,
    ):
        self.base_dir = Path(base_dir)
        self.prompt_file = Path(prompt_file)
        self.data_dir = Path(data_dir)
        self.command = command
        self.client_factory = client_factory

    async def run(self) -> AgySmokeResult:
        """Run the explicit smoke test."""
        started_at = time.time()
        steps: list[AgySmokeStep] = []
        session_id: Optional[str] = None
        workspace_path: Optional[str] = None

        if not shutil.which(self.command):
            return AgySmokeResult(
                steps=[AgySmokeStep("CLI", False, f"{self.command} not found on PATH")],
                session_id=None,
                workspace_path=None,
                elapsed_seconds=time.time() - started_at,
            )

        models_ok, models_detail = await self._check_models()
        steps.append(AgySmokeStep("models", models_ok, models_detail))
        if not models_ok:
            return AgySmokeResult(steps, None, None, time.time() - started_at)

        client = self.client_factory(
            command=self.command,
            system_prompt_file=self.prompt_file,
            timeout=90,
            print_timeout="60s",
        )
        model = get_default_model("agy")

        new_response = await client.chat(
            "Smoke test. Reply exactly AGY_SMOKE_OK.",
            model=model,
        )
        new_ok = self._response_ok(new_response, "AGY_SMOKE_OK") and bool(new_response.session_id)
        session_id = new_response.session_id
        steps.append(
            AgySmokeStep(
                "new chat",
                new_ok,
                self._response_detail(new_response, required="AGY_SMOKE_OK"),
            )
        )
        if not new_ok:
            return AgySmokeResult(steps, session_id, None, time.time() - started_at)

        resume_response = await client.chat(
            "Smoke test resume. Reply exactly AGY_RESUME_OK.",
            session_id=session_id,
            model=model,
        )
        steps.append(
            AgySmokeStep(
                "resume",
                self._response_ok(resume_response, "AGY_RESUME_OK"),
                self._response_detail(resume_response, required="AGY_RESUME_OK"),
            )
        )

        workspace = self.data_dir / "agy-smoke-workspace"
        workspace.mkdir(parents=True, exist_ok=True)
        workspace_path = str(workspace.resolve())
        cwd_response = await client.chat(
            "Use a shell command to print the current working directory. "
            f"Reply exactly AGY_CWD:{workspace_path}",
            model=model,
            workspace_path=workspace_path,
        )
        steps.append(
            AgySmokeStep(
                "workspace cwd",
                cwd_response.error is None and workspace_path in (cwd_response.text or ""),
                self._response_detail(cwd_response, required=workspace_path),
            )
        )

        return AgySmokeResult(steps, session_id, workspace_path, time.time() - started_at)

    async def _check_models(self) -> tuple[bool, str]:
        output, error, returncode = await self._run_command(
            [self.command, "models"],
            timeout=30,
            cwd=str(self.base_dir),
        )
        if returncode == 0:
            return True, self._summarize(output) or "ok"
        return False, self._summarize(error or output) or f"exit {returncode}"

    async def _run_command(
        self,
        cmd: list[str],
        *,
        timeout: int,
        cwd: str,
    ) -> tuple[str, str, int]:
        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=cwd,
            env=build_cli_subprocess_env(),
        )
        try:
            stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=timeout)
        except asyncio.TimeoutError:
            process.kill()
            await process.communicate()
            raise
        return (
            stdout.decode("utf-8", errors="ignore").strip(),
            stderr.decode("utf-8", errors="ignore").strip(),
            process.returncode or 0,
        )

    @staticmethod
    def _response_ok(response: ChatResponse, required: str) -> bool:
        return response.error is None and required in (response.text or "")

    @classmethod
    def _response_detail(cls, response: ChatResponse, *, required: str) -> str:
        if response.error:
            return f"{response.error.value}: {cls._summarize(response.text)}"
        if required not in (response.text or ""):
            return f"missing expected marker: {required}"
        if response.session_id:
            return f"ok, session={response.session_id[:8]}"
        return "ok"

    @staticmethod
    def _summarize(text: str, limit: int = 120) -> str:
        normalized = " ".join((text or "").split())
        if len(normalized) > limit:
            return normalized[:limit].rstrip() + "..."
        return normalized
