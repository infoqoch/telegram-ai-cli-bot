"""Explicit real-CLI smoke tests for AI providers."""

from __future__ import annotations

import asyncio
import shlex
import shutil
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

from src.ai import get_default_model, get_provider_label, is_supported_provider
from src.ai.client_types import AIClient, ChatResponse
from src.ai.env_safety import build_cli_subprocess_env


@dataclass(frozen=True)
class ProviderSmokeStep:
    """One smoke-test step result."""

    name: str
    ok: bool
    detail: str


@dataclass(frozen=True)
class ProviderSmokeResult:
    """Provider smoke-test result."""

    provider: str
    steps: list[ProviderSmokeStep]
    session_id: Optional[str]
    workspace_path: Optional[str]
    elapsed_seconds: float

    @property
    def ok(self) -> bool:
        return bool(self.steps) and all(step.ok for step in self.steps)


ClientFactory = Callable[..., AIClient]


class ProviderSmokeTestService:
    """Run a small explicit real-CLI check for one provider."""

    def __init__(
        self,
        *,
        provider: str,
        base_dir: Path,
        prompt_file: Path,
        data_dir: Path,
        ai_command: str = "claude",
        client_factories: Optional[dict[str, ClientFactory]] = None,
    ):
        if not is_supported_provider(provider):
            raise ValueError(f"Unsupported provider: {provider}")

        self.provider = provider
        self.base_dir = Path(base_dir)
        self.prompt_file = Path(prompt_file)
        self.data_dir = Path(data_dir)
        self.ai_command = ai_command
        self.client_factories = client_factories or self._default_client_factories()

    async def run(self) -> ProviderSmokeResult:
        """Run the explicit smoke test."""
        started_at = time.time()
        steps: list[ProviderSmokeStep] = []
        session_id: Optional[str] = None
        workspace_path: Optional[str] = None

        command = self._provider_command()
        executable = self._executable_name(command)
        if not executable or not shutil.which(executable):
            return ProviderSmokeResult(
                provider=self.provider,
                steps=[ProviderSmokeStep("CLI", False, f"{command} not found on PATH")],
                session_id=None,
                workspace_path=None,
                elapsed_seconds=time.time() - started_at,
            )

        if self.provider == "agy":
            models_ok, models_detail = await self._check_agy_models(command)
            steps.append(ProviderSmokeStep("models", models_ok, models_detail))
            if not models_ok:
                return ProviderSmokeResult(self.provider, steps, None, None, time.time() - started_at)

        client = self._build_client(command)
        model = get_default_model(self.provider)
        label = get_provider_label(self.provider)
        marker = f"{self.provider.upper()}_SMOKE_OK"
        resume_marker = f"{self.provider.upper()}_RESUME_OK"

        new_response = await client.chat(
            f"Smoke test for {label}. Reply exactly {marker}.",
            model=model,
        )
        new_ok = self._response_ok(new_response, marker) and bool(new_response.session_id)
        session_id = new_response.session_id
        steps.append(
            ProviderSmokeStep(
                "new chat",
                new_ok,
                self._response_detail(new_response, required=marker),
            )
        )
        if not new_ok:
            return ProviderSmokeResult(self.provider, steps, session_id, None, time.time() - started_at)

        resume_response = await client.chat(
            f"Smoke test resume for {label}. Reply exactly {resume_marker}.",
            session_id=session_id,
            model=model,
        )
        steps.append(
            ProviderSmokeStep(
                "resume",
                self._response_ok(resume_response, resume_marker),
                self._response_detail(resume_response, required=resume_marker),
            )
        )

        workspace = self.data_dir / "provider-smoke-workspaces" / self.provider
        workspace.mkdir(parents=True, exist_ok=True)
        workspace_path = str(workspace.resolve())
        cwd_marker = f"{self.provider.upper()}_CWD:{workspace_path}"
        cwd_response = await client.chat(
            "Use a shell command to print the current working directory. "
            f"Reply exactly {cwd_marker}",
            model=model,
            workspace_path=workspace_path,
        )
        steps.append(
            ProviderSmokeStep(
                "workspace cwd",
                cwd_response.error is None and cwd_marker in (cwd_response.text or ""),
                self._response_detail(cwd_response, required=cwd_marker),
            )
        )

        return ProviderSmokeResult(self.provider, steps, session_id, workspace_path, time.time() - started_at)

    def _provider_command(self) -> str:
        return self.ai_command if self.provider == "claude" else self.provider

    def _build_client(self, command: str) -> AIClient:
        factory = self.client_factories[self.provider]
        if self.provider == "agy":
            return factory(
                command=command,
                system_prompt_file=self.prompt_file,
                timeout=90,
                print_timeout="60s",
            )
        return factory(
            command=command,
            system_prompt_file=self.prompt_file,
            timeout=90,
        )

    @staticmethod
    def _default_client_factories() -> dict[str, ClientFactory]:
        from src.agy.client import AgyClient
        from src.claude.client import ClaudeClient
        from src.codex.client import CodexClient
        from src.gemini.client import GeminiClient

        return {
            "claude": ClaudeClient,
            "codex": CodexClient,
            "gemini": GeminiClient,
            "agy": AgyClient,
        }

    async def _check_agy_models(self, command: str) -> tuple[bool, str]:
        output, error, returncode = await self._run_command(
            [*shlex.split(command), "models"],
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
    def _executable_name(command: str) -> str:
        try:
            parts = shlex.split(command or "")
        except ValueError:
            return ""
        return parts[0] if parts else ""

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
