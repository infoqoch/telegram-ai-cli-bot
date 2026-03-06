"""Session service - session management business logic."""

from datetime import datetime, timedelta
from typing import Optional

from src.logging_config import logger
from src.repository import Repository


class SessionService:
    """Session management service.

    Handles all session-related business logic:
    - Create/delete sessions
    - Switch sessions
    - Session expiration
    - History management
    """

    def __init__(self, repo: Repository, session_timeout_hours: int = 24):
        self._repo = repo
        self._timeout_hours = session_timeout_hours

    def _is_expired(self, last_used: str) -> bool:
        """Check if session is expired."""
        try:
            last_used_dt = datetime.fromisoformat(last_used.replace("Z", "+00:00"))
            if last_used_dt.tzinfo is None:
                last_used_dt = last_used_dt.replace(tzinfo=None)
                now = datetime.utcnow()
            else:
                now = datetime.now(last_used_dt.tzinfo)
            return (now - last_used_dt) > timedelta(hours=self._timeout_hours)
        except (ValueError, TypeError):
            return False

    def get_current_session_id(self, user_id: str) -> Optional[str]:
        """Get current session ID with expiration check."""
        session_id = self._repo.get_current_session_id(user_id)
        if not session_id:
            return None

        session = self._repo.get_session(session_id)
        if not session or session.deleted:
            return None

        if self._is_expired(session.last_used):
            return None

        return session_id

    def get_previous_session_id(self, user_id: str) -> Optional[str]:
        """Get previous session ID."""
        return self._repo.get_previous_session_id(user_id)

    def create_session(
        self,
        user_id: str,
        session_id: str,
        model: str = "sonnet",
        name: Optional[str] = None,
        workspace_path: Optional[str] = None,
        first_message: str = "",
    ) -> None:
        """Create new session and switch to it."""
        self._repo.create_session(
            user_id=user_id,
            session_id=session_id,
            model=model,
            name=name,
            workspace_path=workspace_path,
            switch_to=True
        )
        if first_message:
            self._repo.add_message(session_id, first_message, processed=True, processor="claude")

    def delete_session(self, user_id: str, session_id: str) -> bool:
        """Soft delete session."""
        result = self._repo.soft_delete_session(session_id)
        if result:
            current = self._repo.get_current_session_id(user_id)
            if current == session_id:
                previous = self._repo.get_previous_session_id(user_id)
                self._repo.update_user_current_session(user_id, previous, None)
        return result

    def switch_session(self, user_id: str, session_id: str) -> bool:
        """Switch to a different session."""
        return self._repo.switch_session(user_id, session_id)

    def add_message(
        self,
        session_id: str,
        message: str,
        processed: bool = False,
        processor: Optional[str] = None
    ) -> None:
        """Add message to session history."""
        self._repo.add_message(session_id, message, processed, processor)

    def get_session_history(self, session_id: str, limit: Optional[int] = None) -> list[str]:
        """Get session history as list of messages."""
        return self._repo.get_session_history(session_id, limit)

    def get_session_model(self, session_id: str) -> Optional[str]:
        """Get session model."""
        return self._repo.get_session_model(session_id)

    def update_session_model(self, session_id: str, model: str) -> bool:
        """Update session model."""
        return self._repo.update_session_model(session_id, model)

    def update_session_name(self, session_id: str, name: str) -> bool:
        """Update session name."""
        return self._repo.update_session_name(session_id, name)

    def update_last_used(self, session_id: str) -> None:
        """Update session last_used timestamp."""
        self._repo.update_session_last_used(session_id)

    def is_workspace_session(self, session_id: str) -> bool:
        """Check if session is a workspace session."""
        return self._repo.is_workspace_session(session_id)

    def get_workspace_path(self, session_id: str) -> Optional[str]:
        """Get workspace path for session."""
        return self._repo.get_session_workspace_path(session_id)

    def list_sessions(
        self,
        user_id: str,
        include_deleted: bool = False,
        limit: Optional[int] = None
    ) -> list[dict]:
        """List sessions for user."""
        sessions = self._repo.list_sessions(user_id, include_deleted, limit)
        current_id = self._repo.get_current_session_id(user_id)

        result = []
        for s in sessions:
            history = self._repo.get_session_history_entries(s.id)
            result.append({
                "id": s.id,
                "session_id": s.id[:8],
                "created_at": s.created_at,
                "last_used": s.last_used,
                "history_count": len(history),
                "model": s.model,
                "name": s.name,
                "workspace_path": s.workspace_path,
                "deleted": s.deleted,
                "is_current": s.id == current_id,
            })
        return result

    def get_session_info(self, session_id: str) -> str:
        """Get formatted session info."""
        if not session_id:
            return "없음"

        session = self._repo.get_session(session_id)
        if not session:
            return session_id[:8]

        short_id = session_id[:8]
        if session.name:
            return f"{short_id} ({session.name})"
        return short_id

    def get_session_by_prefix(self, user_id: str, prefix: str) -> Optional[dict]:
        """Find session by ID prefix."""
        sessions = self._repo.list_sessions(user_id, include_deleted=False)
        for s in sessions:
            if s.id.startswith(prefix):
                history = self._repo.get_session_history_entries(s.id)
                return {
                    "id": s.id,
                    "session_id": s.id[:8],
                    "created_at": s.created_at[:19] if s.created_at else "",
                    "last_used": s.last_used[:19] if s.last_used else "",
                    "history_count": len(history),
                    "name": s.name or "",
                    "model": s.model or "sonnet",
                    "workspace_path": s.workspace_path or "",
                }
        return None

    def get_history_count(self, session_id: str) -> int:
        """Get message count in session history."""
        if not session_id:
            return 0
        history = self._repo.get_session_history_entries(session_id)
        return len(history)

    def clear_session_history(self, session_id: str) -> int:
        """Clear session history."""
        return self._repo.clear_session_history(session_id)

    def get_all_sessions_summary(self, user_id: str) -> str:
        """Get all sessions summary for display."""
        sessions = self._repo.list_sessions(user_id, include_deleted=False)
        current_id = self._repo.get_current_session_id(user_id)

        if not sessions:
            return "세션이 없습니다."

        lines = []
        for s in sessions:
            emoji = "📍" if s.id == current_id else "💬"
            if s.workspace_path:
                emoji = "📂" if s.id == current_id else "🗂"

            display_name = s.name or s.id[:8]
            model_badge = {"opus": "🟣", "sonnet": "🔵", "haiku": "🟢"}.get(s.model, "⚪")

            history = self._repo.get_session_history_entries(s.id)
            msg_count = len(history)

            lines.append(f"{emoji} {model_badge} <b>{display_name}</b> ({msg_count}개)")

        return "\n".join(lines)

    def get_session_name(self, session_id: str) -> str:
        """Get session name."""
        if not session_id:
            return ""
        session = self._repo.get_session(session_id)
        if not session:
            return ""
        return session.name or ""

    def get_session_history_entries(
        self,
        session_id: str,
        limit: Optional[int] = None
    ) -> list[dict]:
        """Get session history as list of dicts."""
        entries = self._repo.get_session_history_entries(session_id, limit)
        return [
            {
                "message": e.message,
                "timestamp": e.timestamp,
                "processed": e.processed,
                "processor": e.processor,
            }
            for e in entries
        ]

    def rename_session(self, session_id: str, new_name: str) -> bool:
        """Rename a session."""
        return self._repo.update_session_name(session_id, new_name)

    def set_current(self, user_id: str, session_id: Optional[str]) -> None:
        """Set current session ID."""
        previous = self._repo.get_current_session_id(user_id)
        self._repo.update_user_current_session(user_id, session_id, previous)

    def set_previous_session_id(self, user_id: str, session_id: Optional[str]) -> None:
        """Store previous session ID for /back command."""
        current = self._repo.get_current_session_id(user_id)
        self._repo.update_user_current_session(user_id, current, session_id)
