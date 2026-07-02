"""Runtime diagnostics for provider and AI work readiness."""

from __future__ import annotations

import shlex
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from src.ai import SUPPORTED_PROVIDERS, get_provider_label


@dataclass(frozen=True)
class ProviderDiagnostic:
    """One provider readiness row."""

    provider: str
    cli: bool
    registry: bool
    prompt: bool
    mcp: bool

    @property
    def ready(self) -> bool:
        """Whether the provider is ready for normal bot dispatch."""
        return self.cli and self.registry and self.prompt

    @property
    def label(self) -> str:
        """Human-facing readiness label."""
        if self.ready and self.mcp:
            return "READY"
        if self.ready:
            return "LIMITED"
        return "UNAVAILABLE"


@dataclass(frozen=True)
class AiWorkDiagnostic:
    """AI work readiness summary."""

    selected_provider: str
    selected_provider_available: bool
    context_ready: bool
    provider_call_ready: bool
    completion_hook_ready: bool
    action_ui_ready: bool
    reason: str

    @property
    def ready(self) -> bool:
        """Whether AI work can be attempted with the selected provider."""
        return (
            self.selected_provider_available
            and self.context_ready
            and self.provider_call_ready
            and self.completion_hook_ready
            and self.action_ui_ready
        )


class RuntimeDiagnosticsService:
    """Build low-risk runtime diagnostics without invoking provider CLIs."""

    def __init__(self, *, base_dir: Path, ai_command: str, prompt_file: Path):
        self.base_dir = Path(base_dir)
        self.ai_command = ai_command
        self.prompt_file = Path(prompt_file)

    def provider_diagnostics(
        self,
        *,
        registered_providers: Iterable[str],
        plugin_loader=None,
    ) -> list[ProviderDiagnostic]:
        """Return provider readiness for all known providers."""
        registered = set(registered_providers)
        mcp_bridge_ready = self._mcp_bridge_ready(plugin_loader)
        return [
            ProviderDiagnostic(
                provider=provider,
                cli=self._provider_cli_ready(provider),
                registry=provider in registered,
                prompt=self.prompt_file.exists(),
                mcp=mcp_bridge_ready,
            )
            for provider in SUPPORTED_PROVIDERS
        ]

    def ai_work_diagnostic(
        self,
        *,
        selected_provider: str,
        registered_providers: Iterable[str],
        plugin_loader=None,
    ) -> AiWorkDiagnostic:
        """Return readiness for AI work with the currently selected provider."""
        registered = set(registered_providers)
        selected_available = selected_provider in registered
        context_ready = self._core_context_ready(["scheduler", "scheduler_command"])
        hook_ready = self._plugin_loaded(plugin_loader, "command_schedule")
        action_ui_ready = hook_ready

        reason = ""
        if not selected_available:
            reason = f"{get_provider_label(selected_provider)} is not registered in this bot runtime"
        elif not context_ready:
            reason = "AI work context files are missing"
        elif not hook_ready:
            reason = "command_schedule completion hook is unavailable"

        return AiWorkDiagnostic(
            selected_provider=selected_provider,
            selected_provider_available=selected_available,
            context_ready=context_ready,
            provider_call_ready=selected_available,
            completion_hook_ready=hook_ready,
            action_ui_ready=action_ui_ready,
            reason=reason,
        )

    def _provider_cli_ready(self, provider: str) -> bool:
        """Return whether one provider executable appears on PATH."""
        command = self.ai_command if provider == "claude" else provider
        executable = self._executable_name(command)
        return bool(executable and shutil.which(executable))

    @staticmethod
    def _executable_name(command: str) -> str:
        try:
            parts = shlex.split(command or "")
        except ValueError:
            return ""
        return parts[0] if parts else ""

    def _mcp_bridge_ready(self, plugin_loader=None) -> bool:
        return bool(plugin_loader) and (self.base_dir / "mcp_servers" / "plugin_bridge_server.py").exists()

    def _core_context_ready(self, names: list[str]) -> bool:
        context_dir = self.base_dir / "src" / "bot" / "ai_contexts"
        return all((context_dir / f"{name}.md").exists() for name in names)

    @staticmethod
    def _plugin_loaded(plugin_loader, name: str) -> bool:
        getter = getattr(plugin_loader, "get_plugin_by_name", None)
        return bool(callable(getter) and getter(name))
