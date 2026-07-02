"""AI provider registry startup guardrail tests."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from src.ai.registry import _command_available, build_default_registry


def _settings(command: str = "claude") -> SimpleNamespace:
    return SimpleNamespace(
        ai_command=command,
        telegram_prompt_file=Path("prompts/telegram.md"),
    )


def test_command_available_checks_executable_portion(monkeypatch):
    monkeypatch.setattr("src.ai.registry.shutil.which", lambda name: f"/bin/{name}" if name == "uvx" else None)

    assert _command_available("uvx claude-code") is True
    assert _command_available("missing --flag") is False
    assert _command_available("") is False


def test_build_default_registry_registers_only_available_providers(monkeypatch):
    available = {"claude": "/usr/local/bin/claude", "codex": "/usr/local/bin/codex"}
    monkeypatch.setattr("src.ai.registry.shutil.which", lambda name: available.get(name))

    registry = build_default_registry(_settings())

    assert registry.supported_providers() == ["claude", "codex"]


def test_build_default_registry_skips_missing_claude(monkeypatch):
    available = {"codex": "/usr/local/bin/codex"}
    monkeypatch.setattr("src.ai.registry.shutil.which", lambda name: available.get(name))

    registry = build_default_registry(_settings())

    assert registry.supported_providers() == ["codex"]
    assert registry.get_default_client() is registry.get_client("codex")


def test_build_default_registry_fails_when_no_provider_cli_exists(monkeypatch):
    monkeypatch.setattr("src.ai.registry.shutil.which", lambda name: None)

    with pytest.raises(RuntimeError, match="No supported AI CLI provider found"):
        build_default_registry(_settings())
