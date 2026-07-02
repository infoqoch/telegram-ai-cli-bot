"""Tests for the Antigravity CLI client."""

from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from src.agy.client import AgyClient
from src.ai.client_types import ChatError


AGY_SESSION_ID = "8d6e9031-721c-4368-877d-d8b803012d80"


def make_client(tmp_path: Path, prompt_file: Path | None = None) -> AgyClient:
    return AgyClient(
        command="agy",
        system_prompt_file=prompt_file,
        agy_root=tmp_path / "antigravity-cli",
        session_create_lock_path=tmp_path / "locks" / "agy.lock",
        log_dir=tmp_path / "logs",
        prepare_project_mcp=False,
    )


def test_build_command_uses_exact_model_timeout_log_and_prompt(tmp_path):
    prompt_file = tmp_path / "telegram.md"
    prompt_file.write_text("You are free to use standard Markdown formatting.", encoding="utf-8")
    client = make_client(tmp_path, prompt_file)
    log_file = tmp_path / "agy.log"

    cmd = client._build_command(
        "hello",
        session_id=AGY_SESSION_ID,
        model="agy-pro-high",
        log_file=log_file,
    )

    assert cmd[:2] == ["agy", "--model"]
    assert "Gemini 3.1 Pro (High)" in cmd
    conversation_index = cmd.index("--conversation")
    timeout_index = cmd.index("--print-timeout")
    log_index = cmd.index("--log-file")
    assert ["--conversation", AGY_SESSION_ID] == cmd[conversation_index:conversation_index + 2]
    assert ["--print-timeout", "30m"] == cmd[timeout_index:timeout_index + 2]
    assert ["--log-file", str(log_file)] == cmd[log_index:log_index + 2]
    assert cmd[-2] == "--print"
    assert "You are free to use standard Markdown formatting." in cmd[-1]
    assert "<EXECUTION_CONTEXT>" in cmd[-1]
    assert f"Intended working directory: {Path.cwd().resolve()}" in cmd[-1]
    assert "<USER_REQUEST>\nhello\n</USER_REQUEST>" in cmd[-1]


def test_build_command_includes_workspace_execution_context(tmp_path):
    client = make_client(tmp_path)
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    cmd = client._build_command(
        "hello",
        model="agy-flash-high",
        workspace_path=str(workspace),
    )

    assert "--add-dir" in cmd
    assert str(workspace) in cmd
    assert f"Intended working directory: {workspace.resolve()}" in cmd[-1]


def test_default_subprocess_timeout_tracks_print_timeout(tmp_path):
    client = make_client(tmp_path)

    assert client.timeout == 31 * 60


def test_custom_subprocess_timeout_overrides_print_timeout(tmp_path):
    client = AgyClient(
        command="agy",
        timeout=42,
        print_timeout="5m0s",
        agy_root=tmp_path / "antigravity-cli",
        session_create_lock_path=tmp_path / "locks" / "agy.lock",
        log_dir=tmp_path / "logs",
        prepare_project_mcp=False,
    )

    assert client.timeout == 42


def test_parse_duration_seconds_accepts_agy_timeout_forms():
    assert AgyClient._parse_duration_seconds("30m") == 1800
    assert AgyClient._parse_duration_seconds("5m0s") == 300
    assert AgyClient._parse_duration_seconds("1h30m5s") == 5405
    assert AgyClient._parse_duration_seconds("bad") is None


def test_resolve_created_session_ignores_unchanged_cached_conversation(tmp_path):
    client = make_client(tmp_path)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    cached_id = "267e6e7f-5f56-4a8a-b8f4-a59797f5e84e"

    db_path = tmp_path / "antigravity-cli" / "conversations" / f"{cached_id}.db"
    db_path.parent.mkdir(parents=True)
    db_path.write_text("", encoding="utf-8")
    cache = tmp_path / "antigravity-cli" / "cache" / "last_conversations.json"
    cache.parent.mkdir(parents=True)
    cache.write_text(f'{{"{workspace.resolve()}":"{cached_id}"}}', encoding="utf-8")

    before = client._snapshot_sessions()

    assert client._resolve_created_session_id(
        before,
        workspace_path=str(workspace),
        started_at=db_path.stat().st_mtime + 10,
    ) is None


@pytest.mark.asyncio
async def test_missing_existing_session_returns_session_not_found(tmp_path):
    client = make_client(tmp_path)
    client._run_command = AsyncMock(side_effect=AssertionError("should not run"))

    response = await client.chat("hello", session_id=AGY_SESSION_ID, model="agy-pro-high")

    assert response.error == ChatError.SESSION_NOT_FOUND
    assert response.session_id is None


@pytest.mark.asyncio
async def test_new_session_discovers_created_conversation_from_cache(tmp_path):
    client = make_client(tmp_path)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    created_id = "267e6e7f-5f56-4a8a-b8f4-a59797f5e84e"
    captured = {}

    async def fake_run(cmd, timeout=None, cwd=None):
        captured["cmd"] = cmd
        captured["cwd"] = cwd
        transcript = (
            tmp_path
            / "antigravity-cli"
            / "brain"
            / created_id
            / ".system_generated"
            / "logs"
            / "transcript.jsonl"
        )
        transcript.parent.mkdir(parents=True)
        transcript.write_text('{"type":"USER_INPUT","content":"hello"}\n', encoding="utf-8")
        cache = tmp_path / "antigravity-cli" / "cache" / "last_conversations.json"
        cache.parent.mkdir(parents=True)
        cache.write_text(f'{{"{workspace.resolve()}":"{created_id}"}}', encoding="utf-8")
        return "ok", "", 0

    client._run_command = fake_run

    response = await client.chat("hello", model="agy-flash-high", workspace_path=str(workspace))

    assert response.text == "ok"
    assert response.error is None
    assert response.session_id == created_id
    assert "--conversation" not in captured["cmd"]
    assert "Gemini 3.5 Flash (High)" in captured["cmd"]
    assert captured["cwd"] == str(workspace)


@pytest.mark.asyncio
async def test_warning_not_found_maps_to_session_not_found(tmp_path):
    client = make_client(tmp_path)
    db_path = tmp_path / "antigravity-cli" / "conversations" / f"{AGY_SESSION_ID}.db"
    db_path.parent.mkdir(parents=True)
    db_path.write_text("", encoding="utf-8")
    client._run_command = AsyncMock(
        return_value=(f'Warning: conversation "{AGY_SESSION_ID}" not found.\nhello', "", 0)
    )

    response = await client.chat("hello", session_id=AGY_SESSION_ID, model="agy-pro-high")

    assert response.error == ChatError.SESSION_NOT_FOUND
    assert response.session_id is None
