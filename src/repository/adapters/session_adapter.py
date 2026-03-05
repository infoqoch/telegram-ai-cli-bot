"""Session store adapter for backward compatibility."""

from datetime import datetime, timedelta
from typing import Any, Optional, TypedDict

from ..repository import Repository, SessionData, HistoryEntry


class HistoryEntryDict(TypedDict):
    """History entry as dict (for backward compatibility)."""
    message: str
    timestamp: str
    processed: bool
    processor: Optional[str]


class SessionStoreAdapter:
    """Adapter that provides SessionStore-compatible interface over Repository.

    This adapter maintains the same API as the original SessionStore class
    to ensure backward compatibility with existing code.
    """

    def __init__(self, repo: Repository, session_timeout_hours: int = 24):
        self._repo = repo
        self._session_timeout_hours = session_timeout_hours

    def _is_session_expired(self, last_used: str) -> bool:
        """Check if session is expired based on last_used timestamp."""
        try:
            last_used_dt = datetime.fromisoformat(last_used.replace("Z", "+00:00"))
            # Handle timezone-naive datetime
            if last_used_dt.tzinfo is None:
                last_used_dt = last_used_dt.replace(tzinfo=None)
                now = datetime.utcnow()
            else:
                now = datetime.now(last_used_dt.tzinfo)
            return (now - last_used_dt) > timedelta(hours=self._session_timeout_hours)
        except (ValueError, TypeError):
            return False

    def get_current_session_id(self, user_id: str) -> Optional[str]:
        """Get current session ID with expiration check."""
        session_id = self._repo.get_current_session_id(user_id)
        if not session_id:
            return None

        session = self._repo.get_session(session_id)
        if not session:
            return None

        # Check if deleted
        if session.deleted:
            return None

        # Check if expired
        if self._is_session_expired(session.last_used):
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
        workspace_path: Optional[str] = None
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

    def create_session_without_switch(
        self,
        user_id: str,
        session_id: str,
        model: str = "sonnet",
        name: Optional[str] = None,
        workspace_path: Optional[str] = None
    ) -> None:
        """Create session without switching to it."""
        self._repo.create_session_without_switch(
            user_id=user_id,
            session_id=session_id,
            model=model,
            name=name,
            workspace_path=workspace_path
        )

    def get_session(self, session_id: str) -> Optional[dict[str, Any]]:
        """Get session data as dict."""
        session = self._repo.get_session(session_id)
        if not session:
            return None

        # Get history
        history = self._repo.get_session_history_entries(session_id)

        return {
            "created_at": session.created_at,
            "last_used": session.last_used,
            "history": [h.to_dict() for h in history],
            "model": session.model,
            "name": session.name,
            "workspace_path": session.workspace_path,
            "deleted": session.deleted,
        }

    def get_session_model(self, session_id: str) -> Optional[str]:
        """Get model for session."""
        return self._repo.get_session_model(session_id)

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
        """Get session history as list of messages (legacy format)."""
        return self._repo.get_session_history(session_id, limit)

    def get_session_history_entries(
        self,
        session_id: str,
        limit: Optional[int] = None
    ) -> list[HistoryEntryDict]:
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

    def update_session_name(self, session_id: str, name: str) -> bool:
        """Update session name."""
        return self._repo.update_session_name(session_id, name)

    def soft_delete_session(self, user_id: str, session_id: str) -> bool:
        """Soft delete session.

        If deleting current session, switch to previous or None.
        """
        result = self._repo.soft_delete_session(session_id)
        if result:
            current = self._repo.get_current_session_id(user_id)
            if current == session_id:
                previous = self._repo.get_previous_session_id(user_id)
                self._repo.update_user_current_session(user_id, previous, None)
        return result

    def hard_delete_session(self, user_id: str, session_id: str) -> bool:
        """Hard delete session."""
        result = self._repo.hard_delete_session(session_id)
        if result:
            current = self._repo.get_current_session_id(user_id)
            if current == session_id:
                previous = self._repo.get_previous_session_id(user_id)
                self._repo.update_user_current_session(user_id, previous, None)
        return result

    def restore_session(self, session_id: str) -> bool:
        """Restore soft-deleted session."""
        return self._repo.restore_session(session_id)

    def list_sessions(
        self,
        user_id: str,
        include_deleted: bool = False,
        limit: Optional[int] = None
    ) -> list[dict[str, Any]]:
        """List sessions for user."""
        sessions = self._repo.list_sessions(user_id, include_deleted, limit)
        current_id = self._repo.get_current_session_id(user_id)

        result = []
        for s in sessions:
            history = self._repo.get_session_history_entries(s.id)
            result.append({
                "id": s.id,
                "created_at": s.created_at,
                "last_used": s.last_used,
                "history": [h.to_dict() for h in history],
                "model": s.model,
                "name": s.name,
                "workspace_path": s.workspace_path,
                "deleted": s.deleted,
                "is_current": s.id == current_id,
            })
        return result

    def switch_session(self, user_id: str, session_id: str) -> bool:
        """Switch to a different session."""
        return self._repo.switch_session(user_id, session_id)

    def is_workspace_session(self, session_id: str) -> bool:
        """Check if session is a workspace session."""
        return self._repo.is_workspace_session(session_id)

    def get_session_workspace_path(self, session_id: str) -> Optional[str]:
        """Get workspace path for session."""
        return self._repo.get_session_workspace_path(session_id)

    def get_all_sessions_summary(self, user_id: str) -> str:
        """Get all sessions summary for manager display."""
        sessions = self._repo.list_sessions(user_id, include_deleted=False)
        current_id = self._repo.get_current_session_id(user_id)

        if not sessions:
            return "세션이 없습니다."

        lines = []
        for s in sessions:
            # Emoji indicators
            emoji = "📍" if s.id == current_id else "💬"
            if s.workspace_path:
                emoji = "📂" if s.id == current_id else "🗂"

            # Session name or ID
            display_name = s.name or s.id[:8]

            # Model badge
            model_badge = {"opus": "🟣", "sonnet": "🔵", "haiku": "🟢"}.get(s.model, "⚪")

            # History count
            history = self._repo.get_session_history_entries(s.id)
            msg_count = len(history)

            lines.append(f"{emoji} {model_badge} <b>{display_name}</b> ({msg_count}개)")

        return "\n".join(lines)

    def clear_session_history(self, session_id: str) -> int:
        """Clear session history."""
        return self._repo.clear_session_history(session_id)

    def update_last_used(self, session_id: str) -> None:
        """Update session last_used timestamp."""
        self._repo.update_session_last_used(session_id)
