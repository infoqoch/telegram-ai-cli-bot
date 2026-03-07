"""Unified Repository class for all data operations."""

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Optional
from uuid import uuid4


@dataclass
class HistoryEntry:
    """Session history entry."""
    id: int
    session_id: str
    message: str
    timestamp: str
    processed: bool
    processor: Optional[str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "message": self.message,
            "timestamp": self.timestamp,
            "processed": self.processed,
            "processor": self.processor,
        }


@dataclass
class QueuedMessage:
    """Message queue entry."""
    id: int
    chat_id: int
    session_id: str
    model: str
    workspace_path: Optional[str]
    request: str
    request_at: str
    processed: int  # 0: pending, 1: processing, 2: completed
    processed_at: Optional[str]
    response: Optional[str]
    error: Optional[str]


@dataclass
class SessionData:
    """Session data."""
    id: str
    user_id: str
    model: str
    name: Optional[str]
    workspace_path: Optional[str]
    created_at: str
    last_used: str
    deleted: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "user_id": self.user_id,
            "model": self.model,
            "name": self.name,
            "workspace_path": self.workspace_path,
            "created_at": self.created_at,
            "last_used": self.last_used,
            "deleted": self.deleted,
        }


@dataclass
class Schedule:
    """Schedule data."""
    id: str
    user_id: str
    chat_id: int
    hour: int
    minute: int
    message: str
    name: str
    schedule_type: str
    model: str
    workspace_path: Optional[str]
    plugin_name: Optional[str]
    action_name: Optional[str]
    enabled: bool
    created_at: str
    last_run: Optional[str]
    last_error: Optional[str]
    run_count: int

    @property
    def time_str(self) -> str:
        """Return formatted time string HH:MM KST."""
        return f"{self.hour:02d}:{self.minute:02d} KST"

    @property
    def type_emoji(self) -> str:
        """Return emoji representing the schedule type."""
        if self.schedule_type == "workspace":
            return "📂"
        if self.schedule_type == "plugin":
            return "🔌"
        return "🤖"

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "user_id": self.user_id,
            "chat_id": self.chat_id,
            "hour": self.hour,
            "minute": self.minute,
            "message": self.message,
            "name": self.name,
            "type": self.schedule_type,
            "model": self.model,
            "workspace_path": self.workspace_path,
            "plugin_name": self.plugin_name,
            "action_name": self.action_name,
            "enabled": self.enabled,
            "created_at": self.created_at,
            "last_run": self.last_run,
            "last_error": self.last_error,
            "run_count": self.run_count,
        }


@dataclass
class Workspace:
    """Workspace data."""
    id: str
    user_id: str
    path: str
    name: str
    description: str
    keywords: list[str]
    created_at: str
    last_used: Optional[str]
    use_count: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "user_id": self.user_id,
            "path": self.path,
            "name": self.name,
            "description": self.description,
            "keywords": self.keywords,
            "created_at": self.created_at,
            "last_used": self.last_used,
            "use_count": self.use_count,
        }

    @property
    def short_path(self) -> str:
        """Return path with ~ for home directory."""
        import os
        home = os.path.expanduser("~")
        if self.path.startswith(home):
            return "~" + self.path[len(home):]
        return self.path


@dataclass
class Memo:
    """Memo data."""
    id: int
    chat_id: int
    content: str
    created_at: str


@dataclass
class Todo:
    """Todo data."""
    id: int
    chat_id: int
    date: str
    slot: str
    text: str
    done: bool
    created_at: str
    updated_at: str


@dataclass
class WeatherLocation:
    """Weather location data."""
    chat_id: int
    name: str
    country: Optional[str]
    lat: float
    lon: float


class Repository:
    """Unified repository for all data operations."""

    def __init__(self, conn: sqlite3.Connection):
        self._conn = conn

    def _now(self) -> str:
        """Return current UTC timestamp in ISO format."""
        return datetime.now(timezone.utc).isoformat()

    # ========== User Operations ==========

    def get_or_create_user(self, user_id: str) -> dict[str, Any]:
        """Get or create user record."""
        cursor = self._conn.execute(
            "SELECT * FROM users WHERE id = ?", (user_id,)
        )
        row = cursor.fetchone()
        if row:
            return dict(row)

        self._conn.execute(
            "INSERT INTO users (id) VALUES (?)", (user_id,)
        )
        self._conn.commit()
        return {"id": user_id, "current_session_id": None, "previous_session_id": None}

    def get_user(self, user_id: str) -> Optional[dict[str, Any]]:
        """Get user by ID."""
        cursor = self._conn.execute(
            "SELECT * FROM users WHERE id = ?", (user_id,)
        )
        row = cursor.fetchone()
        return dict(row) if row else None

    def update_user_current_session(
        self, user_id: str, session_id: Optional[str], previous_session_id: Optional[str] = None
    ) -> None:
        """Update user's current session."""
        self.get_or_create_user(user_id)
        if previous_session_id is not None:
            self._conn.execute(
                "UPDATE users SET current_session_id = ?, previous_session_id = ? WHERE id = ?",
                (session_id, previous_session_id, user_id)
            )
        else:
            self._conn.execute(
                "UPDATE users SET current_session_id = ? WHERE id = ?",
                (session_id, user_id)
            )
        self._conn.commit()

    # ========== Session Operations ==========

    def get_current_session_id(self, user_id: str) -> Optional[str]:
        """Get current session ID for user."""
        user = self.get_user(user_id)
        if not user:
            return None
        return user.get("current_session_id")

    def get_previous_session_id(self, user_id: str) -> Optional[str]:
        """Get previous session ID for user."""
        user = self.get_user(user_id)
        if not user:
            return None
        return user.get("previous_session_id")

    def create_session(
        self,
        user_id: str,
        session_id: str,
        model: str = "sonnet",
        name: Optional[str] = None,
        workspace_path: Optional[str] = None,
        switch_to: bool = True
    ) -> SessionData:
        """Create a new session."""
        now = self._now()
        self.get_or_create_user(user_id)

        self._conn.execute(
            """INSERT INTO sessions (id, user_id, model, name, workspace_path, created_at, last_used)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (session_id, user_id, model, name, workspace_path, now, now)
        )

        if switch_to:
            current = self.get_current_session_id(user_id)
            self.update_user_current_session(user_id, session_id, current)

        self._conn.commit()

        return SessionData(
            id=session_id,
            user_id=user_id,
            model=model,
            name=name,
            workspace_path=workspace_path,
            created_at=now,
            last_used=now,
            deleted=False
        )

    def create_session_without_switch(
        self,
        user_id: str,
        session_id: str,
        model: str = "sonnet",
        name: Optional[str] = None,
        workspace_path: Optional[str] = None
    ) -> SessionData:
        """Create session without switching to it."""
        return self.create_session(
            user_id, session_id, model, name, workspace_path, switch_to=False
        )

    def get_session(self, session_id: str) -> Optional[SessionData]:
        """Get session by ID."""
        cursor = self._conn.execute(
            "SELECT * FROM sessions WHERE id = ?", (session_id,)
        )
        row = cursor.fetchone()
        if not row:
            return None
        return SessionData(
            id=row["id"],
            user_id=row["user_id"],
            model=row["model"],
            name=row["name"],
            workspace_path=row["workspace_path"],
            created_at=row["created_at"],
            last_used=row["last_used"],
            deleted=bool(row["deleted"])
        )

    def get_session_model(self, session_id: str) -> Optional[str]:
        """Get model for session."""
        session = self.get_session(session_id)
        return session.model if session else None

    def update_session_last_used(self, session_id: str) -> None:
        """Update session last_used timestamp."""
        self._conn.execute(
            "UPDATE sessions SET last_used = ? WHERE id = ?",
            (self._now(), session_id)
        )
        self._conn.commit()

    def update_session_name(self, session_id: str, name: str) -> bool:
        """Update session name."""
        cursor = self._conn.execute(
            "UPDATE sessions SET name = ? WHERE id = ?",
            (name, session_id)
        )
        self._conn.commit()
        return cursor.rowcount > 0

    def update_session_model(self, session_id: str, model: str) -> bool:
        """Update session model."""
        cursor = self._conn.execute(
            "UPDATE sessions SET model = ? WHERE id = ?",
            (model, session_id)
        )
        self._conn.commit()
        return cursor.rowcount > 0

    def soft_delete_session(self, session_id: str) -> bool:
        """Soft delete session (mark as deleted)."""
        cursor = self._conn.execute(
            "UPDATE sessions SET deleted = 1 WHERE id = ?",
            (session_id,)
        )
        self._conn.commit()
        return cursor.rowcount > 0

    def hard_delete_session(self, session_id: str) -> bool:
        """Hard delete session (remove from database)."""
        cursor = self._conn.execute(
            "DELETE FROM sessions WHERE id = ?",
            (session_id,)
        )
        self._conn.commit()
        return cursor.rowcount > 0

    def restore_session(self, session_id: str) -> bool:
        """Restore soft-deleted session."""
        cursor = self._conn.execute(
            "UPDATE sessions SET deleted = 0 WHERE id = ?",
            (session_id,)
        )
        self._conn.commit()
        return cursor.rowcount > 0

    def list_sessions(
        self,
        user_id: str,
        include_deleted: bool = False,
        limit: Optional[int] = None
    ) -> list[SessionData]:
        """List sessions for user."""
        query = "SELECT * FROM sessions WHERE user_id = ?"
        params: list[Any] = [user_id]

        if not include_deleted:
            query += " AND deleted = 0"

        query += " ORDER BY last_used DESC"

        if limit:
            query += " LIMIT ?"
            params.append(limit)

        cursor = self._conn.execute(query, params)
        return [
            SessionData(
                id=row["id"],
                user_id=row["user_id"],
                model=row["model"],
                name=row["name"],
                workspace_path=row["workspace_path"],
                created_at=row["created_at"],
                last_used=row["last_used"],
                deleted=bool(row["deleted"])
            )
            for row in cursor.fetchall()
        ]

    def switch_session(self, user_id: str, session_id: str) -> bool:
        """Switch to a different session."""
        session = self.get_session(session_id)
        if not session or session.user_id != user_id:
            return False

        current = self.get_current_session_id(user_id)
        self.update_user_current_session(user_id, session_id, current)
        self.update_session_last_used(session_id)
        return True

    def is_workspace_session(self, session_id: str) -> bool:
        """Check if session is a workspace session."""
        session = self.get_session(session_id)
        return bool(session and session.workspace_path)

    def get_session_workspace_path(self, session_id: str) -> Optional[str]:
        """Get workspace path for session."""
        session = self.get_session(session_id)
        return session.workspace_path if session else None

    # ========== Session History Operations ==========

    def add_message(
        self,
        session_id: str,
        message: str,
        processed: bool = False,
        processor: Optional[str] = None
    ) -> int:
        """Add message to session history."""
        cursor = self._conn.execute(
            """INSERT INTO session_history (session_id, message, timestamp, processed, processor)
               VALUES (?, ?, ?, ?, ?)""",
            (session_id, message, self._now(), int(processed), processor)
        )
        self._conn.commit()
        self.update_session_last_used(session_id)
        return cursor.lastrowid or 0

    def get_session_history(self, session_id: str, limit: Optional[int] = None) -> list[str]:
        """Get session history messages (legacy format)."""
        query = "SELECT message FROM session_history WHERE session_id = ? ORDER BY timestamp ASC"
        params: list[Any] = [session_id]

        if limit:
            query = f"""
                SELECT message FROM (
                    SELECT message, timestamp FROM session_history
                    WHERE session_id = ? ORDER BY timestamp DESC LIMIT ?
                ) ORDER BY timestamp ASC
            """
            params.append(limit)

        cursor = self._conn.execute(query, params)
        return [row["message"] for row in cursor.fetchall()]

    def get_session_history_entries(
        self,
        session_id: str,
        limit: Optional[int] = None
    ) -> list[HistoryEntry]:
        """Get session history as HistoryEntry objects."""
        query = "SELECT * FROM session_history WHERE session_id = ? ORDER BY timestamp ASC"
        params: list[Any] = [session_id]

        if limit:
            query = f"""
                SELECT * FROM (
                    SELECT * FROM session_history
                    WHERE session_id = ? ORDER BY timestamp DESC LIMIT ?
                ) ORDER BY timestamp ASC
            """
            params.append(limit)

        cursor = self._conn.execute(query, params)
        return [
            HistoryEntry(
                id=row["id"],
                session_id=row["session_id"],
                message=row["message"],
                timestamp=row["timestamp"],
                processed=bool(row["processed"]),
                processor=row["processor"]
            )
            for row in cursor.fetchall()
        ]

    def clear_session_history(self, session_id: str) -> int:
        """Clear all history for session."""
        cursor = self._conn.execute(
            "DELETE FROM session_history WHERE session_id = ?",
            (session_id,)
        )
        self._conn.commit()
        return cursor.rowcount

    # ========== Schedule Operations ==========

    def add_schedule(
        self,
        user_id: str,
        chat_id: int,
        hour: int,
        minute: int,
        message: str,
        name: str,
        schedule_type: str = "claude",
        model: str = "sonnet",
        workspace_path: Optional[str] = None,
        plugin_name: Optional[str] = None,
        action_name: Optional[str] = None,
    ) -> Schedule:
        """Add a new schedule."""
        schedule_id = uuid4().hex[:8]
        now = self._now()
        self.get_or_create_user(user_id)

        self._conn.execute(
            """INSERT INTO schedules
               (id, user_id, chat_id, hour, minute, message, name, schedule_type, model,
                workspace_path, plugin_name, action_name, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (schedule_id, user_id, chat_id, hour, minute, message, name,
             schedule_type, model, workspace_path, plugin_name, action_name, now)
        )
        self._conn.commit()

        return Schedule(
            id=schedule_id,
            user_id=user_id,
            chat_id=chat_id,
            hour=hour,
            minute=minute,
            message=message,
            name=name,
            schedule_type=schedule_type,
            model=model,
            workspace_path=workspace_path,
            plugin_name=plugin_name,
            action_name=action_name,
            enabled=True,
            created_at=now,
            last_run=None,
            last_error=None,
            run_count=0
        )

    def get_schedule(self, schedule_id: str) -> Optional[Schedule]:
        """Get schedule by ID."""
        cursor = self._conn.execute(
            "SELECT * FROM schedules WHERE id = ?", (schedule_id,)
        )
        row = cursor.fetchone()
        if not row:
            return None
        return self._row_to_schedule(row)

    def _row_to_schedule(self, row: sqlite3.Row) -> Schedule:
        """Convert database row to Schedule object."""
        return Schedule(
            id=row["id"],
            user_id=row["user_id"],
            chat_id=row["chat_id"],
            hour=row["hour"],
            minute=row["minute"],
            message=row["message"],
            name=row["name"],
            schedule_type=row["schedule_type"],
            model=row["model"],
            workspace_path=row["workspace_path"],
            plugin_name=row["plugin_name"],
            action_name=row["action_name"],
            enabled=bool(row["enabled"]),
            created_at=row["created_at"],
            last_run=row["last_run"],
            last_error=row["last_error"],
            run_count=row["run_count"]
        )

    def remove_schedule(self, schedule_id: str) -> bool:
        """Remove schedule."""
        cursor = self._conn.execute(
            "DELETE FROM schedules WHERE id = ?", (schedule_id,)
        )
        self._conn.commit()
        return cursor.rowcount > 0

    def toggle_schedule(self, schedule_id: str) -> Optional[bool]:
        """Toggle schedule enabled state. Returns new state."""
        schedule = self.get_schedule(schedule_id)
        if not schedule:
            return None

        new_state = not schedule.enabled
        self._conn.execute(
            "UPDATE schedules SET enabled = ? WHERE id = ?",
            (int(new_state), schedule_id)
        )
        self._conn.commit()
        return new_state

    def update_schedule_run(
        self,
        schedule_id: str,
        last_run: str,
        last_error: Optional[str] = None
    ) -> None:
        """Update schedule after run."""
        self._conn.execute(
            """UPDATE schedules
               SET last_run = ?, last_error = ?, run_count = run_count + 1
               WHERE id = ?""",
            (last_run, last_error, schedule_id)
        )
        self._conn.commit()

    def update_schedule_time(self, schedule_id: str, hour: int, minute: int) -> bool:
        """Update schedule time."""
        cursor = self._conn.execute(
            "UPDATE schedules SET hour = ?, minute = ? WHERE id = ?",
            (hour, minute, schedule_id)
        )
        self._conn.commit()
        return cursor.rowcount > 0

    def list_schedules_by_user(self, user_id: str) -> list[Schedule]:
        """List schedules for user."""
        cursor = self._conn.execute(
            "SELECT * FROM schedules WHERE user_id = ? ORDER BY hour, minute",
            (user_id,)
        )
        return [self._row_to_schedule(row) for row in cursor.fetchall()]

    def list_all_schedules(self) -> list[Schedule]:
        """List all schedules."""
        cursor = self._conn.execute(
            "SELECT * FROM schedules ORDER BY hour, minute"
        )
        return [self._row_to_schedule(row) for row in cursor.fetchall()]

    def list_enabled_schedules(self) -> list[Schedule]:
        """List all enabled schedules."""
        cursor = self._conn.execute(
            "SELECT * FROM schedules WHERE enabled = 1 ORDER BY hour, minute"
        )
        return [self._row_to_schedule(row) for row in cursor.fetchall()]

    # ========== Workspace Operations ==========

    def add_workspace(
        self,
        user_id: str,
        path: str,
        name: str,
        description: str = "",
        keywords: Optional[list[str]] = None
    ) -> Workspace:
        """Add a new workspace."""
        import os
        workspace_id = uuid4().hex[:8]
        now = self._now()
        normalized_path = os.path.normpath(os.path.expanduser(path))
        keywords = keywords or []
        self.get_or_create_user(user_id)

        self._conn.execute(
            """INSERT INTO workspaces
               (id, user_id, path, name, description, keywords, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (workspace_id, user_id, normalized_path, name, description,
             json.dumps(keywords), now)
        )
        self._conn.commit()

        return Workspace(
            id=workspace_id,
            user_id=user_id,
            path=normalized_path,
            name=name,
            description=description,
            keywords=keywords,
            created_at=now,
            last_used=None,
            use_count=0
        )

    def get_workspace(self, workspace_id: str) -> Optional[Workspace]:
        """Get workspace by ID."""
        cursor = self._conn.execute(
            "SELECT * FROM workspaces WHERE id = ?", (workspace_id,)
        )
        row = cursor.fetchone()
        if not row:
            return None
        return self._row_to_workspace(row)

    def _row_to_workspace(self, row: sqlite3.Row) -> Workspace:
        """Convert database row to Workspace object."""
        return Workspace(
            id=row["id"],
            user_id=row["user_id"],
            path=row["path"],
            name=row["name"],
            description=row["description"],
            keywords=json.loads(row["keywords"]),
            created_at=row["created_at"],
            last_used=row["last_used"],
            use_count=row["use_count"]
        )

    def get_workspace_by_path(
        self,
        path: str,
        user_id: Optional[str] = None
    ) -> Optional[Workspace]:
        """Get workspace by path."""
        import os
        normalized_path = os.path.normpath(os.path.expanduser(path))

        if user_id:
            cursor = self._conn.execute(
                "SELECT * FROM workspaces WHERE path = ? AND user_id = ?",
                (normalized_path, user_id)
            )
        else:
            cursor = self._conn.execute(
                "SELECT * FROM workspaces WHERE path = ?",
                (normalized_path,)
            )

        row = cursor.fetchone()
        return self._row_to_workspace(row) if row else None

    def remove_workspace(self, workspace_id: str) -> bool:
        """Remove workspace."""
        cursor = self._conn.execute(
            "DELETE FROM workspaces WHERE id = ?", (workspace_id,)
        )
        self._conn.commit()
        return cursor.rowcount > 0

    def mark_workspace_used(self, workspace_id: str) -> None:
        """Mark workspace as used."""
        self._conn.execute(
            """UPDATE workspaces
               SET last_used = ?, use_count = use_count + 1
               WHERE id = ?""",
            (self._now(), workspace_id)
        )
        self._conn.commit()

    def list_workspaces_by_user(self, user_id: str) -> list[Workspace]:
        """List workspaces for user."""
        cursor = self._conn.execute(
            """SELECT * FROM workspaces WHERE user_id = ?
               ORDER BY COALESCE(last_used, created_at) DESC""",
            (user_id,)
        )
        return [self._row_to_workspace(row) for row in cursor.fetchall()]

    def update_workspace(
        self,
        workspace_id: str,
        name: Optional[str] = None,
        description: Optional[str] = None,
        keywords: Optional[list[str]] = None
    ) -> bool:
        """Update workspace details."""
        updates = []
        params: list[Any] = []

        if name is not None:
            updates.append("name = ?")
            params.append(name)
        if description is not None:
            updates.append("description = ?")
            params.append(description)
        if keywords is not None:
            updates.append("keywords = ?")
            params.append(json.dumps(keywords))

        if not updates:
            return False

        params.append(workspace_id)
        cursor = self._conn.execute(
            f"UPDATE workspaces SET {', '.join(updates)} WHERE id = ?",
            params
        )
        self._conn.commit()
        return cursor.rowcount > 0

    # ========== Memo Operations ==========

    def add_memo(self, chat_id: int, content: str) -> Memo:
        """Add a memo."""
        now = self._now()
        cursor = self._conn.execute(
            "INSERT INTO memos (chat_id, content, created_at) VALUES (?, ?, ?)",
            (chat_id, content, now)
        )
        self._conn.commit()

        return Memo(
            id=cursor.lastrowid or 0,
            chat_id=chat_id,
            content=content,
            created_at=now
        )

    def get_memo(self, memo_id: int) -> Optional[Memo]:
        """Get memo by ID."""
        cursor = self._conn.execute(
            "SELECT * FROM memos WHERE id = ?", (memo_id,)
        )
        row = cursor.fetchone()
        if not row:
            return None
        return Memo(
            id=row["id"],
            chat_id=row["chat_id"],
            content=row["content"],
            created_at=row["created_at"]
        )

    def delete_memo(self, memo_id: int) -> bool:
        """Delete memo."""
        cursor = self._conn.execute(
            "DELETE FROM memos WHERE id = ?", (memo_id,)
        )
        self._conn.commit()
        return cursor.rowcount > 0

    def list_memos(self, chat_id: int) -> list[Memo]:
        """List memos for chat."""
        cursor = self._conn.execute(
            "SELECT * FROM memos WHERE chat_id = ? ORDER BY created_at DESC",
            (chat_id,)
        )
        return [
            Memo(
                id=row["id"],
                chat_id=row["chat_id"],
                content=row["content"],
                created_at=row["created_at"]
            )
            for row in cursor.fetchall()
        ]

    def clear_memos(self, chat_id: int) -> int:
        """Clear all memos for chat."""
        cursor = self._conn.execute(
            "DELETE FROM memos WHERE chat_id = ?", (chat_id,)
        )
        self._conn.commit()
        return cursor.rowcount

    # ========== Todo Operations ==========

    def add_todo(
        self,
        chat_id: int,
        date: str,
        text: str
    ) -> Todo:
        """Add a todo item."""
        now = self._now()
        cursor = self._conn.execute(
            """INSERT INTO todos (chat_id, date, slot, text, created_at, updated_at)
               VALUES (?, ?, 'default', ?, ?, ?)""",
            (chat_id, date, text, now, now)
        )
        self._conn.commit()

        return Todo(
            id=cursor.lastrowid or 0,
            chat_id=chat_id,
            date=date,
            slot="default",
            text=text,
            done=False,
            created_at=now,
            updated_at=now
        )

    def get_todo(self, todo_id: int) -> Optional[Todo]:
        """Get todo by ID."""
        cursor = self._conn.execute(
            "SELECT * FROM todos WHERE id = ?", (todo_id,)
        )
        row = cursor.fetchone()
        if not row:
            return None
        return self._row_to_todo(row)

    def _row_to_todo(self, row: sqlite3.Row) -> Todo:
        """Convert database row to Todo object."""
        return Todo(
            id=row["id"],
            chat_id=row["chat_id"],
            date=row["date"],
            slot=row["slot"],
            text=row["text"],
            done=bool(row["done"]),
            created_at=row["created_at"],
            updated_at=row["updated_at"]
        )

    def toggle_todo(self, todo_id: int) -> Optional[bool]:
        """Toggle todo done state. Returns new state."""
        todo = self.get_todo(todo_id)
        if not todo:
            return None

        new_state = not todo.done
        self._conn.execute(
            "UPDATE todos SET done = ? WHERE id = ?",
            (int(new_state), todo_id)
        )
        self._conn.commit()
        return new_state

    def delete_todo(self, todo_id: int) -> bool:
        """Delete todo."""
        cursor = self._conn.execute(
            "DELETE FROM todos WHERE id = ?", (todo_id,)
        )
        self._conn.commit()
        return cursor.rowcount > 0

    def list_todos_by_date(self, chat_id: int, date: str) -> list[Todo]:
        """List todos for chat on specific date."""
        cursor = self._conn.execute(
            "SELECT * FROM todos WHERE chat_id = ? AND date = ? ORDER BY id",
            (chat_id, date)
        )
        return [self._row_to_todo(row) for row in cursor.fetchall()]

    def clear_todos_by_date(self, chat_id: int, date: str) -> int:
        """Clear all todos for date."""
        cursor = self._conn.execute(
            "DELETE FROM todos WHERE chat_id = ? AND date = ?",
            (chat_id, date)
        )
        self._conn.commit()
        return cursor.rowcount

    def mark_todo_done(self, todo_id: int, done: bool = True) -> bool:
        """Mark todo as done/undone."""
        cursor = self._conn.execute(
            "UPDATE todos SET done = ? WHERE id = ?",
            (int(done), todo_id)
        )
        self._conn.commit()
        return cursor.rowcount > 0

    def get_pending_todos(self, chat_id: int, date: str) -> list[Todo]:
        """Get incomplete todos for date."""
        cursor = self._conn.execute(
            "SELECT * FROM todos WHERE chat_id = ? AND date = ? AND done = 0 ORDER BY id",
            (chat_id, date)
        )
        return [self._row_to_todo(row) for row in cursor.fetchall()]

    def move_todos_to_date(self, todo_ids: list[int], new_date: str) -> int:
        """Move todos to another date."""
        if not todo_ids:
            return 0
        placeholders = ",".join("?" * len(todo_ids))
        cursor = self._conn.execute(
            f"UPDATE todos SET date = ? WHERE id IN ({placeholders})",
            [new_date] + todo_ids
        )
        self._conn.commit()
        return cursor.rowcount

    def get_todos_by_date_range(
        self, chat_id: int, start_date: str, end_date: str
    ) -> dict[str, list[Todo]]:
        """Get todos for date range, grouped by date."""
        cursor = self._conn.execute(
            """SELECT * FROM todos
               WHERE chat_id = ? AND date >= ? AND date <= ?
               ORDER BY date, id""",
            (chat_id, start_date, end_date)
        )
        result: dict[str, list[Todo]] = {}
        for row in cursor.fetchall():
            todo = self._row_to_todo(row)
            if todo.date not in result:
                result[todo.date] = []
            result[todo.date].append(todo)
        return result

    def get_todo_stats(self, chat_id: int, date: str) -> dict[str, int]:
        """Get todo statistics for date."""
        cursor = self._conn.execute(
            """SELECT
                COUNT(*) as total,
                SUM(CASE WHEN done = 1 THEN 1 ELSE 0 END) as done,
                SUM(CASE WHEN done = 0 THEN 1 ELSE 0 END) as pending
               FROM todos WHERE chat_id = ? AND date = ?""",
            (chat_id, date)
        )
        row = cursor.fetchone()
        return {
            "total": row["total"] or 0,
            "done": row["done"] or 0,
            "pending": row["pending"] or 0
        }

    # ========== Weather Location Operations ==========

    def set_weather_location(
        self,
        chat_id: int,
        name: str,
        lat: float,
        lon: float,
        country: Optional[str] = None
    ) -> WeatherLocation:
        """Set weather location for chat."""
        self._conn.execute(
            """INSERT OR REPLACE INTO weather_locations (chat_id, name, country, lat, lon, updated_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (chat_id, name, country, lat, lon, self._now())
        )
        self._conn.commit()

        return WeatherLocation(
            chat_id=chat_id,
            name=name,
            country=country,
            lat=lat,
            lon=lon
        )

    def get_weather_location(self, chat_id: int) -> Optional[WeatherLocation]:
        """Get weather location for chat."""
        cursor = self._conn.execute(
            "SELECT * FROM weather_locations WHERE chat_id = ?",
            (chat_id,)
        )
        row = cursor.fetchone()
        if not row:
            return None
        return WeatherLocation(
            chat_id=row["chat_id"],
            name=row["name"],
            country=row["country"],
            lat=row["lat"],
            lon=row["lon"]
        )

    def delete_weather_location(self, chat_id: int) -> bool:
        """Delete weather location for chat."""
        cursor = self._conn.execute(
            "DELETE FROM weather_locations WHERE chat_id = ?",
            (chat_id,)
        )
        self._conn.commit()
        return cursor.rowcount > 0

        self._conn.commit()

    # ========== Message Log Operations ==========

    def enqueue_message(
        self,
        chat_id: int,
        session_id: str,
        request: str,
        model: str = "sonnet",
        workspace_path: Optional[str] = None
    ) -> int:
        """Add message to queue. Returns queue entry ID."""
        cursor = self._conn.execute(
            """INSERT INTO message_log (chat_id, session_id, model, workspace_path, request, request_at, processed)
               VALUES (?, ?, ?, ?, ?, ?, 0)""",
            (chat_id, session_id, model, workspace_path, request, self._now())
        )
        self._conn.commit()
        return cursor.lastrowid or 0

    def get_next_pending_message(self, chat_id: int) -> Optional[dict[str, Any]]:
        """Get next unprocessed message for chat. Returns None if queue empty."""
        cursor = self._conn.execute(
            """SELECT * FROM message_log
               WHERE chat_id = ? AND processed = 0
               ORDER BY id ASC LIMIT 1""",
            (chat_id,)
        )
        row = cursor.fetchone()
        if not row:
            return None
        return dict(row)

    def claim_message(self, queue_id: int) -> bool:
        """Mark message as processing (processed=1). Returns True if claimed.
        Also works for retry (processed=1 stays at 1)."""
        cursor = self._conn.execute(
            """UPDATE message_log SET processed = 1
               WHERE id = ? AND processed IN (0, 1)""",
            (queue_id,)
        )
        self._conn.commit()
        return cursor.rowcount > 0

    def complete_message(
        self,
        queue_id: int,
        response: Optional[str] = None,
        error: Optional[str] = None
    ) -> bool:
        """Mark message as completed with response or error."""
        cursor = self._conn.execute(
            """UPDATE message_log
               SET processed = 2, processed_at = ?, response = ?, error = ?
               WHERE id = ?""",
            (self._now(), response, error, queue_id)
        )
        self._conn.commit()
        return cursor.rowcount > 0

    def get_pending_message_count(self, chat_id: int) -> int:
        """Get count of pending messages for chat."""
        cursor = self._conn.execute(
            "SELECT COUNT(*) FROM message_log WHERE chat_id = ? AND processed = 0",
            (chat_id,)
        )
        row = cursor.fetchone()
        return row[0] if row else 0

    def get_processing_message(self, chat_id: int) -> Optional[dict[str, Any]]:
        """Get currently processing message for chat (processed=1)."""
        cursor = self._conn.execute(
            """SELECT * FROM message_log
               WHERE chat_id = ? AND processed = 1
               ORDER BY id ASC LIMIT 1""",
            (chat_id,)
        )
        row = cursor.fetchone()
        if not row:
            return None
        return dict(row)

    def get_unfinished_messages(self, max_age_minutes: int = 30, max_retries: int = 2) -> list[dict[str, Any]]:
        """Get all unfinished messages (processed=0 or 1) within max_age, under retry limit."""
        cutoff = (datetime.now(timezone.utc) - timedelta(minutes=max_age_minutes)).isoformat()
        rows = self._conn.execute(
            """SELECT * FROM message_log
               WHERE processed IN (0, 1) AND request_at > ? AND retry_count < ?
               ORDER BY id ASC""",
            (cutoff, max_retries),
        ).fetchall()
        return [dict(r) for r in rows]

    def increment_retry_count(self, queue_id: int) -> int:
        """Increment retry_count and return new value."""
        self._conn.execute(
            "UPDATE message_log SET retry_count = retry_count + 1 WHERE id = ?",
            (queue_id,),
        )
        self._conn.commit()
        row = self._conn.execute(
            "SELECT retry_count FROM message_log WHERE id = ?", (queue_id,)
        ).fetchone()
        return row[0] if row else 0

    def fail_exceeded_retries(self, max_retries: int = 2) -> int:
        """Mark messages that exceeded retry limit as completed with error."""
        cursor = self._conn.execute(
            """UPDATE message_log SET processed = 2, processed_at = ?, error = 'retry_limit_exceeded'
               WHERE processed IN (0, 1) AND retry_count >= ?""",
            (self._now(), max_retries),
        )
        self._conn.commit()
        return cursor.rowcount

    def reset_stale_processing_messages(self, timeout_minutes: int = 30) -> int:
        """Reset messages stuck in processing state back to pending."""
        cutoff = (datetime.now(timezone.utc) - timedelta(minutes=timeout_minutes)).isoformat()
        cursor = self._conn.execute(
            """UPDATE message_log SET processed = 0
               WHERE processed = 1 AND request_at < ?""",
            (cutoff,)
        )
        self._conn.commit()
        return cursor.rowcount

    def cleanup_old_completed_messages(self, days: int = 7) -> int:
        """Delete completed messages older than N days."""
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        cursor = self._conn.execute(
            """DELETE FROM message_log
               WHERE processed = 2 AND processed_at < ?""",
            (cutoff,)
        )
        self._conn.commit()
        return cursor.rowcount

    # ── auth_sessions ──────────────────────────────────────────

    def save_auth_session(self, user_id: str, authenticated_at: datetime) -> None:
        """인증 세션을 DB에 저장."""
        self._conn.execute(
            "INSERT OR REPLACE INTO auth_sessions (user_id, authenticated_at) VALUES (?, ?)",
            (user_id, authenticated_at.isoformat()),
        )
        self._conn.commit()

    def get_auth_session(self, user_id: str) -> Optional[datetime]:
        """DB에서 인증 세션 조회. 없으면 None."""
        row = self._conn.execute(
            "SELECT authenticated_at FROM auth_sessions WHERE user_id = ?",
            (user_id,),
        ).fetchone()
        if not row:
            return None
        return datetime.fromisoformat(row[0])

    def get_all_auth_sessions(self) -> dict[str, datetime]:
        """모든 인증 세션 반환."""
        rows = self._conn.execute("SELECT user_id, authenticated_at FROM auth_sessions").fetchall()
        return {r[0]: datetime.fromisoformat(r[1]) for r in rows}

    def delete_auth_session(self, user_id: str) -> None:
        """인증 세션 삭제."""
        self._conn.execute("DELETE FROM auth_sessions WHERE user_id = ?", (user_id,))
        self._conn.commit()

    def clear_expired_auth_sessions(self, timeout_minutes: int) -> int:
        """만료된 인증 세션 정리."""
        cutoff = (datetime.now() - timedelta(minutes=timeout_minutes)).isoformat()
        cursor = self._conn.execute(
            "DELETE FROM auth_sessions WHERE authenticated_at < ?",
            (cutoff,),
        )
        self._conn.commit()
        return cursor.rowcount

    # ── pending_messages ───────────────────────────────────────

    def save_pending_message(self, key: str, user_id: str, chat_id: int,
                             message: str, model: str = "", is_new_session: bool = False,
                             workspace_path: str = "", current_session_id: str = "",
                             created_at: float = 0.0) -> None:
        """세션 충돌 시 임시 메시지 저장."""
        self._conn.execute(
            """INSERT OR REPLACE INTO pending_messages
               (pending_key, user_id, chat_id, message, model, is_new_session,
                workspace_path, current_session_id, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (key, user_id, chat_id, message, model, int(is_new_session),
             workspace_path, current_session_id, created_at),
        )
        self._conn.commit()

    def get_pending_message(self, key: str) -> Optional[dict[str, Any]]:
        """pending message 조회."""
        row = self._conn.execute(
            "SELECT * FROM pending_messages WHERE pending_key = ?", (key,)
        ).fetchone()
        if not row:
            return None
        return dict(row)

    def get_all_pending_messages(self) -> dict[str, dict[str, Any]]:
        """모든 pending messages 반환."""
        rows = self._conn.execute("SELECT * FROM pending_messages").fetchall()
        result = {}
        for r in rows:
            d = dict(r)
            key = d.pop("pending_key")
            d["is_new_session"] = bool(d["is_new_session"])
            result[key] = d
        return result

    def delete_pending_message(self, key: str) -> None:
        """pending message 삭제."""
        self._conn.execute("DELETE FROM pending_messages WHERE pending_key = ?", (key,))
        self._conn.commit()

    def clear_expired_pending_messages(self, ttl_seconds: int = 300) -> int:
        """TTL 초과 pending messages 정리."""
        import time
        cutoff = time.time() - ttl_seconds
        cursor = self._conn.execute(
            "DELETE FROM pending_messages WHERE created_at < ?", (cutoff,)
        )
        self._conn.commit()
        return cursor.rowcount

    # ── queued_messages ────────────────────────────────────────

    def save_queued_message(self, session_id: str, user_id: str, chat_id: int,
                            message: str, model: str, is_new_session: bool,
                            workspace_path: str = "", expires_minutes: int = 5) -> int:
        """세션 큐 메시지 저장. 생성된 ID 반환."""
        expires_at = (datetime.now() + timedelta(minutes=expires_minutes)).isoformat()
        cursor = self._conn.execute(
            """INSERT INTO queued_messages
               (session_id, user_id, chat_id, message, model, is_new_session,
                workspace_path, expires_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (session_id, user_id, chat_id, message, model, int(is_new_session),
             workspace_path, expires_at),
        )
        self._conn.commit()
        return cursor.lastrowid

    def get_queued_messages_by_session(self, session_id: str) -> list[dict[str, Any]]:
        """세션의 대기 중인 메시지 목록 (만료되지 않은 것만)."""
        now = datetime.now().isoformat()
        rows = self._conn.execute(
            """SELECT * FROM queued_messages
               WHERE session_id = ? AND expires_at > ?
               ORDER BY id ASC""",
            (session_id, now),
        ).fetchall()
        return [dict(r) for r in rows]

    def delete_queued_message(self, queue_id: int) -> None:
        """큐 메시지 삭제."""
        self._conn.execute("DELETE FROM queued_messages WHERE id = ?", (queue_id,))
        self._conn.commit()

    def clear_expired_queued_messages(self) -> int:
        """만료된 큐 메시지 정리."""
        now = datetime.now().isoformat()
        cursor = self._conn.execute(
            "DELETE FROM queued_messages WHERE expires_at <= ?",
            (now,),
        )
        self._conn.commit()
        return cursor.rowcount
