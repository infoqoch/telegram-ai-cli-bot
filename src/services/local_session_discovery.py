"""Discover recent local Claude/Codex sessions from provider-managed storage."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional


_CWD_RE = re.compile(r"<cwd>(.*?)</cwd>", re.DOTALL)


@dataclass(frozen=True)
class DiscoveredSession:
    """One provider-native session discovered from local storage."""

    provider: str
    provider_session_id: str
    title: str
    updated_at: str
    workspace_path: Optional[str] = None
    preview: str = ""
    message_count: Optional[int] = None

    @property
    def short_id(self) -> str:
        """Return a compact display id."""
        return self.provider_session_id[:8]


class LocalSessionDiscoveryService:
    """Best-effort discovery for locally persisted Claude/Codex sessions."""

    def __init__(self, home: Optional[Path] = None):
        self._home = home or Path.home()
        self._claude_projects_root = self._home / ".claude" / "projects"
        self._codex_index_path = self._home / ".codex" / "session_index.jsonl"
        self._codex_sessions_root = self._home / ".codex" / "sessions"

    def list_recent(
        self,
        provider: Optional[str] = None,
        limit: int = 10,
        offset: int = 0,
    ) -> list[DiscoveredSession]:
        """Return recent provider-native sessions ordered by last update."""
        sessions = self._load_sessions(provider)
        ordered = sorted(
            sessions,
            key=lambda item: self._parse_sort_key(item.updated_at),
            reverse=True,
        )
        safe_offset = max(offset, 0)
        if limit <= 0:
            return []
        return ordered[safe_offset:safe_offset + limit]

    def get(self, provider: str, provider_session_id: str) -> Optional[DiscoveredSession]:
        """Return one discovered session by provider-native id."""
        for session in self._load_provider_sessions(provider):
            if session.provider_session_id == provider_session_id:
                return session
        return None

    def _load_sessions(self, provider: Optional[str]) -> list[DiscoveredSession]:
        if provider:
            return self._load_provider_sessions(provider)

        sessions: list[DiscoveredSession] = []
        for provider_name in ("claude", "codex"):
            sessions.extend(self._load_provider_sessions(provider_name))
        return sessions

    def _load_provider_sessions(self, provider: str) -> list[DiscoveredSession]:
        if provider == "claude":
            return self._load_claude_sessions()
        if provider == "codex":
            return self._load_codex_sessions()
        return []

    def _load_claude_sessions(self) -> list[DiscoveredSession]:
        sessions_by_id: dict[str, DiscoveredSession] = {}
        if not self._claude_projects_root.exists():
            return []

        for index_path in self._claude_projects_root.rglob("sessions-index.json"):
            try:
                payload = json.loads(index_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue

            for entry in payload.get("entries", []):
                session_id = entry.get("sessionId")
                if not session_id:
                    continue

                updated_at = (
                    entry.get("modified")
                    or self._millis_to_iso(entry.get("fileMtime"))
                    or entry.get("created")
                    or ""
                )
                title = self._clean_text(
                    entry.get("summary") or entry.get("firstPrompt") or f"Claude {session_id[:8]}"
                )
                preview = self._clean_text(entry.get("firstPrompt") or entry.get("summary") or "")
                discovered = DiscoveredSession(
                    provider="claude",
                    provider_session_id=session_id,
                    title=title,
                    updated_at=updated_at,
                    workspace_path=entry.get("projectPath"),
                    preview=preview,
                    message_count=entry.get("messageCount"),
                )

                previous = sessions_by_id.get(session_id)
                if not previous or self._parse_sort_key(discovered.updated_at) >= self._parse_sort_key(previous.updated_at):
                    sessions_by_id[session_id] = discovered

        return list(sessions_by_id.values())

    def _load_codex_sessions(self) -> list[DiscoveredSession]:
        sessions: list[DiscoveredSession] = []
        if not self._codex_index_path.exists():
            return sessions

        try:
            lines = self._codex_index_path.read_text(encoding="utf-8").splitlines()
        except OSError:
            return sessions

        for raw_line in lines:
            line = raw_line.strip()
            if not line:
                continue

            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue

            session_id = entry.get("id")
            if not session_id:
                continue

            workspace_path = self._load_codex_workspace_path(session_id)
            title = self._clean_text(entry.get("thread_name") or f"Codex {session_id[:8]}")
            sessions.append(
                DiscoveredSession(
                    provider="codex",
                    provider_session_id=session_id,
                    title=title,
                    updated_at=entry.get("updated_at") or "",
                    workspace_path=workspace_path,
                    preview="",
                    message_count=None,
                )
            )

        return sessions

    def _load_codex_workspace_path(self, session_id: str) -> Optional[str]:
        session_path = self._find_codex_session_path(session_id)
        if not session_path:
            return None

        try:
            with session_path.open("r", encoding="utf-8") as handle:
                for _ in range(40):
                    raw_line = handle.readline()
                    if not raw_line:
                        break
                    try:
                        entry = json.loads(raw_line)
                    except json.JSONDecodeError:
                        continue

                    if entry.get("type") != "message" or entry.get("role") != "user":
                        continue

                    content = self._extract_codex_message_text(entry.get("content"))
                    if not content:
                        continue

                    match = _CWD_RE.search(content)
                    if match:
                        return match.group(1).strip()
        except OSError:
            return None

        return None

    def _find_codex_session_path(self, session_id: str) -> Optional[Path]:
        if not self._codex_sessions_root.exists():
            return None

        matches = sorted(self._codex_sessions_root.rglob(f"*{session_id}*.jsonl"))
        return matches[0] if matches else None

    @staticmethod
    def _extract_codex_message_text(content: object) -> str:
        if not isinstance(content, list):
            return ""

        parts: list[str] = []
        for item in content:
            if isinstance(item, dict):
                text = item.get("text")
                if isinstance(text, str):
                    parts.append(text)
        return "\n".join(parts).strip()

    @staticmethod
    def _millis_to_iso(value: object) -> str:
        if not isinstance(value, (int, float)):
            return ""
        return datetime.fromtimestamp(value / 1000, tz=timezone.utc).isoformat().replace("+00:00", "Z")

    @staticmethod
    def _clean_text(value: object, *, max_length: int = 70) -> str:
        if not isinstance(value, str):
            return ""
        normalized = re.sub(r"\s+", " ", value).strip()
        if not normalized:
            return ""
        if len(normalized) <= max_length:
            return normalized
        return normalized[: max_length - 3].rstrip() + "..."

    @staticmethod
    def _parse_sort_key(value: str) -> datetime:
        if not value:
            return datetime.min.replace(tzinfo=timezone.utc)

        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return datetime.min.replace(tzinfo=timezone.utc)

        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
