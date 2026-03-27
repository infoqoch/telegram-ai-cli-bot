"""Discover recent local Claude/Codex sessions from provider-managed storage."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional


_CWD_RE = re.compile(r"<cwd>(.*?)</cwd>", re.DOTALL)
_UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
_CODEX_ROLLOUT_ID_RE = re.compile(
    r"([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})$"
)
_AGENTS_BLOCK_RE = re.compile(r"^# AGENTS\.md instructions.*?</INSTRUCTIONS>\s*", re.DOTALL)
_ENVIRONMENT_BLOCK_RE = re.compile(r"<environment_context>.*?</environment_context>\s*", re.DOTALL)
_LOCAL_COMMAND_CAVEAT_RE = re.compile(r"<local-command-caveat>.*?</local-command-caveat>\s*", re.DOTALL)


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

    _RAW_HEADER_SCAN_LINES = 160

    def __init__(self, home: Optional[Path] = None):
        self._home = home or Path.home()
        self._claude_projects_root = self._home / ".claude" / "projects"
        self._codex_index_path = self._home / ".codex" / "session_index.jsonl"
        self._codex_sessions_root = self._home / ".codex" / "sessions"
        self._gemini_tmp_root = self._home / ".gemini" / "tmp"

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
        for provider_name in ("claude", "codex", "gemini"):
            sessions.extend(self._load_provider_sessions(provider_name))
        return sessions

    def _load_provider_sessions(self, provider: str) -> list[DiscoveredSession]:
        if provider == "claude":
            return self._load_claude_sessions()
        if provider == "codex":
            return self._load_codex_sessions()
        if provider == "gemini":
            return self._load_gemini_sessions()
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
                discovered = DiscoveredSession(
                    provider="claude",
                    provider_session_id=session_id,
                    title=self._clean_text(
                        entry.get("summary")
                        or entry.get("firstPrompt")
                        or self._default_title("claude", session_id)
                    ),
                    updated_at=updated_at,
                    workspace_path=entry.get("projectPath"),
                    preview=self._clean_text(entry.get("firstPrompt") or entry.get("summary") or ""),
                    message_count=entry.get("messageCount"),
                )
                self._store_discovered_session(sessions_by_id, discovered)

        for session_path in self._claude_projects_root.rglob("*.jsonl"):
            if "/subagents/" in session_path.as_posix():
                continue
            if not _UUID_RE.fullmatch(session_path.stem):
                continue

            discovered = self._load_claude_session_from_raw(session_path)
            if discovered:
                self._store_discovered_session(sessions_by_id, discovered)

        return list(sessions_by_id.values())

    def _load_claude_session_from_raw(self, session_path: Path) -> Optional[DiscoveredSession]:
        session_id = session_path.stem
        if not _UUID_RE.fullmatch(session_id):
            return None

        workspace_path: Optional[str] = None
        prompt = ""

        try:
            with session_path.open("r", encoding="utf-8") as handle:
                for line_number, raw_line in enumerate(handle, start=1):
                    if line_number > self._RAW_HEADER_SCAN_LINES:
                        break

                    try:
                        entry = json.loads(raw_line)
                    except json.JSONDecodeError:
                        continue

                    session_id = entry.get("sessionId") or session_id
                    workspace_path = workspace_path or entry.get("cwd")
                    if entry.get("type") != "user":
                        continue

                    message = entry.get("message")
                    if isinstance(message, dict):
                        prompt = self._extract_meaningful_prompt(message.get("content"))
                    elif isinstance(message, str):
                        prompt = self._extract_meaningful_prompt(message)
                    if prompt:
                        break
        except OSError:
            return None

        return DiscoveredSession(
            provider="claude",
            provider_session_id=session_id,
            title=prompt or self._default_title("claude", session_id),
            updated_at=self._path_mtime_to_iso(session_path),
            workspace_path=workspace_path,
            preview=prompt,
            message_count=None,
        )

    def _load_codex_sessions(self) -> list[DiscoveredSession]:
        sessions_by_id = self._load_codex_raw_sessions()
        if not self._codex_index_path.exists():
            return list(sessions_by_id.values())

        try:
            lines = self._codex_index_path.read_text(encoding="utf-8").splitlines()
        except OSError:
            return list(sessions_by_id.values())

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

            raw_session = sessions_by_id.get(session_id)
            discovered = DiscoveredSession(
                provider="codex",
                provider_session_id=session_id,
                title=self._clean_text(entry.get("thread_name"))
                or (raw_session.title if raw_session else self._default_title("codex", session_id)),
                updated_at=entry.get("updated_at") or (raw_session.updated_at if raw_session else ""),
                workspace_path=raw_session.workspace_path if raw_session else None,
                preview=raw_session.preview if raw_session else "",
                message_count=raw_session.message_count if raw_session else None,
            )
            self._store_discovered_session(sessions_by_id, discovered)

        return list(sessions_by_id.values())

    def _load_codex_raw_sessions(self) -> dict[str, DiscoveredSession]:
        sessions_by_id: dict[str, DiscoveredSession] = {}
        if not self._codex_sessions_root.exists():
            return sessions_by_id

        for session_path in self._codex_sessions_root.rglob("*.jsonl"):
            discovered = self._load_codex_session_from_raw(session_path)
            if discovered:
                self._store_discovered_session(sessions_by_id, discovered)
        return sessions_by_id

    def _load_codex_session_from_raw(self, session_path: Path) -> Optional[DiscoveredSession]:
        session_id_match = _CODEX_ROLLOUT_ID_RE.search(session_path.stem)
        session_id = session_id_match.group(1) if session_id_match else ""
        workspace_path: Optional[str] = None
        prompt = ""

        try:
            with session_path.open("r", encoding="utf-8") as handle:
                for line_number, raw_line in enumerate(handle, start=1):
                    if line_number > self._RAW_HEADER_SCAN_LINES:
                        break

                    try:
                        entry = json.loads(raw_line)
                    except json.JSONDecodeError:
                        continue

                    entry_type = entry.get("type")
                    payload = entry.get("payload")
                    payload = payload if isinstance(payload, dict) else {}

                    if entry_type == "session_meta":
                        session_id = payload.get("id") or session_id
                        workspace_path = workspace_path or payload.get("cwd")
                    elif entry_type == "turn_context":
                        workspace_path = workspace_path or payload.get("cwd")
                    elif entry_type == "event_msg" and payload.get("type") == "user_message":
                        prompt = prompt or self._extract_meaningful_prompt(payload.get("message"))
                    elif entry_type == "response_item":
                        if payload.get("type") == "message" and payload.get("role") == "user":
                            prompt = prompt or self._extract_meaningful_prompt(
                                self._extract_codex_message_text(payload.get("content"))
                            )
                    elif entry_type == "message" and entry.get("role") == "user" and not workspace_path:
                        content = self._extract_codex_message_text(entry.get("content"))
                        match = _CWD_RE.search(content)
                        if match:
                            workspace_path = match.group(1).strip()

                    if session_id and workspace_path and prompt:
                        break
        except OSError:
            return None

        if not session_id:
            return None

        return DiscoveredSession(
            provider="codex",
            provider_session_id=session_id,
            title=prompt or self._default_title("codex", session_id),
            updated_at=self._path_mtime_to_iso(session_path),
            workspace_path=workspace_path,
            preview=prompt,
            message_count=None,
        )

    def _load_gemini_sessions(self) -> list[DiscoveredSession]:
        """Scan ~/.gemini/tmp/*/chats/*.json for Gemini session files."""
        sessions_by_id: dict[str, DiscoveredSession] = {}
        if not self._gemini_tmp_root.exists():
            return []

        for session_file in self._gemini_tmp_root.glob("*/chats/*.json"):
            try:
                data = json.loads(session_file.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue

            session_id = data.get("sessionId")
            if not session_id or not _UUID_RE.fullmatch(session_id):
                continue

            messages = data.get("messages", [])
            title = ""
            for msg in messages:
                if msg.get("type") == "user":
                    title = self._clean_text(msg.get("content", ""))
                    if title:
                        break

            updated_at = data.get("lastUpdated") or self._path_mtime_to_iso(session_file)
            user_count = sum(1 for m in messages if m.get("type") == "user")

            discovered = DiscoveredSession(
                provider="gemini",
                provider_session_id=session_id,
                title=title or self._default_title("gemini", session_id),
                updated_at=updated_at,
                workspace_path=None,
                preview=title,
                message_count=user_count or None,
            )
            self._store_discovered_session(sessions_by_id, discovered)

        return list(sessions_by_id.values())

    def _store_discovered_session(
        self,
        sessions_by_id: dict[str, DiscoveredSession],
        discovered: DiscoveredSession,
    ) -> None:
        previous = sessions_by_id.get(discovered.provider_session_id)
        sessions_by_id[discovered.provider_session_id] = self._merge_sessions(previous, discovered)

    def _merge_sessions(
        self,
        previous: Optional[DiscoveredSession],
        candidate: DiscoveredSession,
    ) -> DiscoveredSession:
        if not previous:
            return candidate

        previous_key = self._parse_sort_key(previous.updated_at)
        candidate_key = self._parse_sort_key(candidate.updated_at)
        prefer_candidate = candidate_key >= previous_key
        provider = previous.provider or candidate.provider
        session_id = previous.provider_session_id or candidate.provider_session_id

        return DiscoveredSession(
            provider=provider,
            provider_session_id=session_id,
            title=self._pick_preferred_text(
                previous.title,
                candidate.title,
                provider=provider,
                session_id=session_id,
                prefer_candidate=prefer_candidate,
            ),
            updated_at=candidate.updated_at if prefer_candidate else previous.updated_at,
            workspace_path=self._pick_preferred_optional(
                previous.workspace_path,
                candidate.workspace_path,
                prefer_candidate=prefer_candidate,
            ),
            preview=self._pick_preferred_text(
                previous.preview,
                candidate.preview,
                provider=provider,
                session_id=session_id,
                prefer_candidate=prefer_candidate,
            ),
            message_count=self._pick_preferred_optional(
                previous.message_count,
                candidate.message_count,
                prefer_candidate=prefer_candidate,
            ),
        )

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

    @classmethod
    def _extract_meaningful_prompt(cls, value: object) -> str:
        if not isinstance(value, str):
            return ""

        stripped = value.strip()
        if not stripped:
            return ""

        stripped = _AGENTS_BLOCK_RE.sub("", stripped)
        stripped = _ENVIRONMENT_BLOCK_RE.sub("", stripped)
        stripped = _LOCAL_COMMAND_CAVEAT_RE.sub("", stripped)
        if "<command-name>" in stripped or "<command-message>" in stripped or "<local-command-stdout>" in stripped:
            return ""
        return cls._clean_text(stripped)

    @staticmethod
    def _default_title(provider: str, session_id: str) -> str:
        prefix = {"claude": "Claude", "codex": "Codex", "gemini": "Gemini"}.get(provider, provider.title())
        return f"{prefix} {session_id[:8]}"

    @classmethod
    def _is_placeholder_text(cls, value: str, provider: str, session_id: str) -> bool:
        if not value:
            return True
        return value == cls._default_title(provider, session_id)

    @classmethod
    def _pick_preferred_text(
        cls,
        previous: str,
        candidate: str,
        *,
        provider: str,
        session_id: str,
        prefer_candidate: bool,
    ) -> str:
        previous_placeholder = cls._is_placeholder_text(previous, provider, session_id)
        candidate_placeholder = cls._is_placeholder_text(candidate, provider, session_id)

        if candidate and (not previous or previous_placeholder) and not candidate_placeholder:
            return candidate
        if previous and (not candidate or candidate_placeholder) and not previous_placeholder:
            return previous
        if prefer_candidate and candidate:
            return candidate
        if previous:
            return previous
        return candidate

    @staticmethod
    def _pick_preferred_optional(previous, candidate, *, prefer_candidate: bool):
        if candidate is not None and (previous is None or prefer_candidate):
            return candidate
        return previous

    @staticmethod
    def _path_mtime_to_iso(path: Path) -> str:
        try:
            stat = path.stat()
        except OSError:
            return ""
        return datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat().replace("+00:00", "Z")

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
