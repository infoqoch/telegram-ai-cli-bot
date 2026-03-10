"""Tests for provider-local session discovery."""

import json

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
