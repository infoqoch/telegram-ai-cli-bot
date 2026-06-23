"""Tests for provider-local session discovery."""

import json
import os

from src.services.local_session_discovery import LocalSessionDiscoveryService


class TestLocalSessionDiscoveryService:
    """LocalSessionDiscoveryService tests."""

    def test_list_recent_claude_sessions_from_index(self, tmp_path):
        """Claude sessions-index files are normalized into recent sessions."""
        index_dir = tmp_path / ".claude" / "projects" / "demo-project"
        index_dir.mkdir(parents=True)
        index_path = index_dir / "sessions-index.json"
        index_path.write_text(
            json.dumps(
                {
                    "version": 1,
                    "entries": [
                        {
                            "sessionId": "older-session",
                            "summary": "Older summary",
                            "firstPrompt": "older prompt",
                            "messageCount": 2,
                            "projectPath": "/tmp/older",
                            "modified": "2026-03-09T09:00:00Z",
                        },
                        {
                            "sessionId": "newer-session",
                            "summary": "Newer summary",
                            "firstPrompt": "newer prompt",
                            "messageCount": 5,
                            "projectPath": "/tmp/newer",
                            "modified": "2026-03-10T09:00:00Z",
                        },
                    ],
                }
            ),
            encoding="utf-8",
        )

        service = LocalSessionDiscoveryService(home=tmp_path)
        sessions = service.list_recent("claude", limit=5)

        assert [session.provider_session_id for session in sessions] == ["newer-session", "older-session"]
        assert sessions[0].title == "Newer summary"
        assert sessions[0].workspace_path == "/tmp/newer"
        assert sessions[0].message_count == 5

    def test_list_recent_codex_sessions_extracts_workspace_path(self, tmp_path):
        """Codex session index uses session files to recover the original cwd."""
        codex_dir = tmp_path / ".codex"
        sessions_root = codex_dir / "sessions" / "2026" / "03" / "10"
        sessions_root.mkdir(parents=True)

        (codex_dir / "session_index.jsonl").write_text(
            json.dumps(
                {
                    "id": "019c18c5-8616-78e3-9730-49e989dc3f35",
                    "thread_name": "Codex import target",
                    "updated_at": "2026-03-10T10:00:00Z",
                }
            ) + "\n",
            encoding="utf-8",
        )
        (sessions_root / "rollout-2026-03-10T10-00-00-019c18c5-8616-78e3-9730-49e989dc3f35.jsonl").write_text(
            "\n".join(
                [
                    json.dumps({"id": "019c18c5-8616-78e3-9730-49e989dc3f35"}),
                    json.dumps(
                        {
                            "type": "message",
                            "role": "user",
                            "content": [
                                {
                                    "type": "input_text",
                                    "text": (
                                        "<environment_context>\n"
                                        "  <cwd>/tmp/codex-project</cwd>\n"
                                        "</environment_context>"
                                    ),
                                }
                            ],
                        }
                    ),
                ]
            ),
            encoding="utf-8",
        )

        service = LocalSessionDiscoveryService(home=tmp_path)
        sessions = service.list_recent("codex", limit=5)

        assert len(sessions) == 1
        assert sessions[0].provider_session_id == "019c18c5-8616-78e3-9730-49e989dc3f35"
        assert sessions[0].title == "Codex import target"
        assert sessions[0].workspace_path == "/tmp/codex-project"

    def test_list_recent_claude_sessions_falls_back_to_raw_files(self, tmp_path):
        """Claude raw session files are discovered even when no index entry exists."""
        session_path = (
            tmp_path
            / ".claude"
            / "projects"
            / "demo-project"
            / "550e8400-e29b-41d4-a716-446655440000.jsonl"
        )
        session_path.parent.mkdir(parents=True)
        session_path.write_text(
            "\n".join(
                [
                    json.dumps(
                        {
                            "sessionId": "550e8400-e29b-41d4-a716-446655440000",
                            "cwd": "/tmp/raw-claude",
                            "type": "progress",
                        }
                    ),
                    json.dumps(
                        {
                            "sessionId": "550e8400-e29b-41d4-a716-446655440000",
                            "cwd": "/tmp/raw-claude",
                            "type": "user",
                            "message": {"role": "user", "content": "안녕 클로드~ raw fallback 확인"},
                        }
                    ),
                ]
            ),
            encoding="utf-8",
        )

        service = LocalSessionDiscoveryService(home=tmp_path)
        sessions = service.list_recent("claude", limit=5)

        assert len(sessions) == 1
        assert sessions[0].provider_session_id == "550e8400-e29b-41d4-a716-446655440000"
        assert sessions[0].title == "안녕 클로드~ raw fallback 확인"
        assert sessions[0].workspace_path == "/tmp/raw-claude"

    def test_list_recent_codex_sessions_falls_back_to_raw_files(self, tmp_path):
        """Codex raw rollout files are discovered even when no index entry exists."""
        session_path = (
            tmp_path
            / ".codex"
            / "sessions"
            / "2026"
            / "03"
            / "11"
            / "rollout-2026-03-11T08-28-14-019cda14-6040-7bf3-b4d3-1d21fd14560f.jsonl"
        )
        session_path.parent.mkdir(parents=True)
        session_path.write_text(
            "\n".join(
                [
                    json.dumps(
                        {
                            "type": "session_meta",
                            "payload": {
                                "id": "019cda14-6040-7bf3-b4d3-1d21fd14560f",
                                "cwd": "/tmp/raw-codex",
                            },
                        }
                    ),
                    json.dumps(
                        {
                            "type": "response_item",
                            "payload": {
                                "type": "message",
                                "role": "user",
                                "content": [
                                    {
                                        "type": "input_text",
                                        "text": (
                                            "# AGENTS.md instructions for /Users/test\n"
                                            "<INSTRUCTIONS>ignored</INSTRUCTIONS>\n"
                                            "<environment_context>\n"
                                            "  <cwd>/tmp/raw-codex</cwd>\n"
                                            "</environment_context>\n"
                                            "하이 코덱스~ raw fallback 확인"
                                        ),
                                    }
                                ],
                            },
                        }
                    ),
                ]
            ),
            encoding="utf-8",
        )

        service = LocalSessionDiscoveryService(home=tmp_path)
        sessions = service.list_recent("codex", limit=5)

        assert len(sessions) == 1
        assert sessions[0].provider_session_id == "019cda14-6040-7bf3-b4d3-1d21fd14560f"
        assert sessions[0].title == "하이 코덱스~ raw fallback 확인"
        assert sessions[0].workspace_path == "/tmp/raw-codex"

    def test_list_recent_agy_sessions_extracts_user_request_and_workspace(self, tmp_path):
        """Agy transcripts and history are normalized into importable sessions."""
        session_id = "8d6e9031-721c-4368-877d-d8b803012d80"
        transcript_path = (
            tmp_path
            / ".gemini"
            / "antigravity-cli"
            / "brain"
            / session_id
            / ".system_generated"
            / "logs"
            / "transcript.jsonl"
        )
        transcript_path.parent.mkdir(parents=True)
        transcript_path.write_text(
            "\n".join(
                [
                    json.dumps(
                        {
                            "type": "USER_INPUT",
                            "content": (
                                "<USER_REQUEST>\n"
                                "안티그래비티 세션 가져오기 확인\n"
                                "</USER_REQUEST>\n"
                                "<ADDITIONAL_METADATA>ignore</ADDITIONAL_METADATA>"
                            ),
                        }
                    ),
                    json.dumps({"type": "PLANNER_RESPONSE", "content": "ok"}),
                ]
            ),
            encoding="utf-8",
        )
        history_path = tmp_path / ".gemini" / "antigravity-cli" / "history.jsonl"
        history_path.write_text(
            json.dumps(
                {
                    "conversationId": session_id,
                    "workspace": "/tmp/agy-project",
                    "display": "안티그래비티 세션 가져오기 확인",
                }
            )
            + "\n",
            encoding="utf-8",
        )

        service = LocalSessionDiscoveryService(home=tmp_path)
        sessions = service.list_recent("agy", limit=5)

        assert len(sessions) == 1
        assert sessions[0].provider_session_id == session_id
        assert sessions[0].title == "안티그래비티 세션 가져오기 확인"
        assert sessions[0].workspace_path == "/tmp/agy-project"
        assert sessions[0].message_count == 1

    def test_list_recent_dedupes_index_and_raw_sessions(self, tmp_path):
        """Index sessions are merged with raw metadata instead of duplicated."""
        claude_dir = tmp_path / ".claude" / "projects" / "demo-project"
        claude_dir.mkdir(parents=True)
        (claude_dir / "sessions-index.json").write_text(
            json.dumps(
                {
                    "version": 1,
                    "entries": [
                        {
                            "sessionId": "550e8400-e29b-41d4-a716-446655440000",
                            "summary": "Indexed summary",
                            "modified": "2026-03-09T09:00:00Z",
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )
        raw_path = claude_dir / "550e8400-e29b-41d4-a716-446655440000.jsonl"
        raw_path.write_text(
            json.dumps(
                {
                    "sessionId": "550e8400-e29b-41d4-a716-446655440000",
                    "cwd": "/tmp/merged-claude",
                    "type": "user",
                    "message": {"role": "user", "content": "raw prompt"},
                }
            ),
            encoding="utf-8",
        )
        os.utime(raw_path, (1_783_161_600, 1_783_161_600))  # 2026-07-04T10:40:00Z

        service = LocalSessionDiscoveryService(home=tmp_path)
        sessions = service.list_recent("claude", limit=5)

        assert len(sessions) == 1
        assert sessions[0].provider_session_id == "550e8400-e29b-41d4-a716-446655440000"
        assert sessions[0].workspace_path == "/tmp/merged-claude"
        assert sessions[0].updated_at == "2026-07-04T10:40:00Z"

    def test_list_recent_merges_all_providers_and_supports_offset(self, tmp_path):
        """Merged listing returns all providers ordered by recency and paged by offset."""
        claude_index_dir = tmp_path / ".claude" / "projects" / "demo-project"
        claude_index_dir.mkdir(parents=True)
        (claude_index_dir / "sessions-index.json").write_text(
            json.dumps(
                {
                    "version": 1,
                    "entries": [
                        {
                            "sessionId": "claude-newer",
                            "summary": "Claude newer",
                            "modified": "2026-03-10T11:00:00Z",
                        },
                        {
                            "sessionId": "claude-older",
                            "summary": "Claude older",
                            "modified": "2026-03-10T08:00:00Z",
                        },
                    ],
                }
            ),
            encoding="utf-8",
        )

        codex_dir = tmp_path / ".codex"
        codex_sessions_root = codex_dir / "sessions" / "2026" / "03" / "10"
        codex_sessions_root.mkdir(parents=True)
        (codex_dir / "session_index.jsonl").write_text(
            "\n".join(
                [
                    json.dumps(
                        {
                            "id": "codex-newest",
                            "thread_name": "Codex newest",
                            "updated_at": "2026-03-10T12:00:00Z",
                        }
                    ),
                    json.dumps(
                        {
                            "id": "codex-middle",
                            "thread_name": "Codex middle",
                            "updated_at": "2026-03-10T09:00:00Z",
                        }
                    ),
                ]
            ) + "\n",
            encoding="utf-8",
        )
        (codex_sessions_root / "codex-newest.jsonl").write_text(
            json.dumps({"type": "message", "role": "assistant"}),
            encoding="utf-8",
        )
        (codex_sessions_root / "codex-middle.jsonl").write_text(
            json.dumps({"type": "message", "role": "assistant"}),
            encoding="utf-8",
        )

        service = LocalSessionDiscoveryService(home=tmp_path)

        merged = service.list_recent(limit=3)
        paged = service.list_recent(limit=2, offset=1)

        assert [(session.provider, session.provider_session_id) for session in merged] == [
            ("codex", "codex-newest"),
            ("claude", "claude-newer"),
            ("codex", "codex-middle"),
        ]
        assert [(session.provider, session.provider_session_id) for session in paged] == [
            ("claude", "claude-newer"),
            ("codex", "codex-middle"),
        ]
