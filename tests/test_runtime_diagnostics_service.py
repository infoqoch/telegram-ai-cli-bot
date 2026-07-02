"""Runtime diagnostics service tests."""

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

from src.services.runtime_diagnostics_service import RuntimeDiagnosticsService


def _service(tmp_path: Path) -> RuntimeDiagnosticsService:
    prompt_file = tmp_path / "prompts" / "telegram.md"
    prompt_file.parent.mkdir()
    prompt_file.write_text("prompt", encoding="utf-8")
    bridge = tmp_path / "mcp_servers" / "plugin_bridge_server.py"
    bridge.parent.mkdir()
    bridge.write_text("print('bridge')", encoding="utf-8")
    context_dir = tmp_path / "src" / "bot" / "ai_contexts"
    context_dir.mkdir(parents=True)
    (context_dir / "scheduler.md").write_text("scheduler", encoding="utf-8")
    (context_dir / "scheduler_command.md").write_text("scheduler command", encoding="utf-8")
    return RuntimeDiagnosticsService(
        base_dir=tmp_path,
        ai_command="claude",
        prompt_file=prompt_file,
    )


def test_provider_diagnostics_separates_cli_registry_prompt_and_mcp(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "src.services.runtime_diagnostics_service.shutil.which",
        lambda name: f"/bin/{name}" if name in {"claude", "codex"} else None,
    )
    plugin_loader = MagicMock()

    rows = _service(tmp_path).provider_diagnostics(
        registered_providers=["claude"],
        plugin_loader=plugin_loader,
    )
    by_provider = {row.provider: row for row in rows}

    assert by_provider["claude"].ready is True
    assert by_provider["claude"].label == "READY"
    assert by_provider["codex"].cli is True
    assert by_provider["codex"].registry is False
    assert by_provider["codex"].label == "UNAVAILABLE"
    assert by_provider["gemini"].cli is False


def test_ai_work_diagnostic_reports_unregistered_default_provider(tmp_path):
    plugin_loader = SimpleNamespace(get_plugin_by_name=lambda name: object())

    result = _service(tmp_path).ai_work_diagnostic(
        selected_provider="agy",
        registered_providers=["claude"],
        plugin_loader=plugin_loader,
    )

    assert result.ready is False
    assert "not registered" in result.reason


def test_ai_work_diagnostic_ready_when_provider_context_and_hook_exist(tmp_path):
    plugin_loader = SimpleNamespace(get_plugin_by_name=lambda name: object())

    result = _service(tmp_path).ai_work_diagnostic(
        selected_provider="claude",
        registered_providers=["claude"],
        plugin_loader=plugin_loader,
    )

    assert result.ready is True
    assert result.reason == ""
