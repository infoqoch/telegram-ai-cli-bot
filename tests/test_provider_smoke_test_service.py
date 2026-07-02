"""Provider smoke-test service tests."""

from pathlib import Path

import pytest

from src.ai.client_types import ChatError, ChatResponse
from src.services.provider_smoke_test_service import ProviderSmokeTestService


class FakeClient:
    """Small async fake for provider smoke tests."""

    responses: list[ChatResponse] = []
    calls: list[dict] = []

    def __init__(self, **kwargs):
        self.kwargs = kwargs

    async def chat(self, message, session_id=None, model=None, workspace_path=None):
        self.calls.append(
            {
                "message": message,
                "session_id": session_id,
                "model": model,
                "workspace_path": workspace_path,
            }
        )
        return self.responses.pop(0)


def _service(tmp_path: Path, provider: str) -> ProviderSmokeTestService:
    prompt_file = tmp_path / "telegram.md"
    prompt_file.write_text("prompt", encoding="utf-8")
    return ProviderSmokeTestService(
        provider=provider,
        base_dir=tmp_path,
        prompt_file=prompt_file,
        data_dir=tmp_path / "data",
        client_factories={
            "claude": FakeClient,
            "codex": FakeClient,
            "gemini": FakeClient,
            "agy": FakeClient,
        },
    )


@pytest.mark.asyncio
async def test_provider_smoke_runs_new_resume_and_workspace(tmp_path, monkeypatch):
    monkeypatch.setattr("src.services.provider_smoke_test_service.shutil.which", lambda name: f"/bin/{name}")
    service = _service(tmp_path, "codex")
    session_id = "thread-123"
    workspace = str((tmp_path / "data" / "provider-smoke-workspaces" / "codex").resolve())
    FakeClient.responses = [
        ChatResponse("CODEX_SMOKE_OK", None, session_id),
        ChatResponse("CODEX_RESUME_OK", None, session_id),
        ChatResponse(f"CODEX_CWD:{workspace}", None, "another-session"),
    ]
    FakeClient.calls = []

    result = await service.run()

    assert result.ok is True
    assert [step.name for step in result.steps] == ["new chat", "resume", "workspace cwd"]
    assert FakeClient.calls[1]["session_id"] == session_id
    assert FakeClient.calls[2]["workspace_path"] == workspace


@pytest.mark.asyncio
async def test_provider_smoke_reports_missing_cli(tmp_path, monkeypatch):
    monkeypatch.setattr("src.services.provider_smoke_test_service.shutil.which", lambda name: None)

    result = await _service(tmp_path, "claude").run()

    assert result.ok is False
    assert result.steps[0].name == "CLI"
    assert "not found" in result.steps[0].detail


@pytest.mark.asyncio
async def test_provider_smoke_stops_when_new_chat_fails(tmp_path, monkeypatch):
    monkeypatch.setattr("src.services.provider_smoke_test_service.shutil.which", lambda name: f"/bin/{name}")
    service = _service(tmp_path, "gemini")
    FakeClient.responses = [
        ChatResponse("auth failed", ChatError.CLI_ERROR, None),
    ]
    FakeClient.calls = []

    result = await service.run()

    assert result.ok is False
    assert [step.name for step in result.steps] == ["new chat"]
    assert "CLI_ERROR" in result.steps[-1].detail


@pytest.mark.asyncio
async def test_agy_smoke_includes_models_check(tmp_path, monkeypatch):
    monkeypatch.setattr("src.services.provider_smoke_test_service.shutil.which", lambda name: f"/bin/{name}")
    service = _service(tmp_path, "agy")
    service._run_command = lambda *args, **kwargs: _async_result(("models", "", 0))
    session_id = "267e6e7f-5f56-4a8a-b8f4-a59797f5e84e"
    workspace = str((tmp_path / "data" / "provider-smoke-workspaces" / "agy").resolve())
    FakeClient.responses = [
        ChatResponse("AGY_SMOKE_OK", None, session_id),
        ChatResponse("AGY_RESUME_OK", None, session_id),
        ChatResponse(f"AGY_CWD:{workspace}", None, "another-session"),
    ]
    FakeClient.calls = []

    result = await service.run()

    assert result.ok is True
    assert [step.name for step in result.steps] == ["models", "new chat", "resume", "workspace cwd"]


async def _async_result(value):
    return value
