"""Unified Repository class for all data operations."""

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
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
    type: str
    model: str
    workspace_path: Optional[str]
    enabled: bool
    created_at: str
    last_run: Optional[str]
    last_error: Optional[str]
    run_count: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "user_id": self.user_id,
            "chat_id": self.chat_id,
            "hour": self.hour,
            "minute": self.minute,
            "message": self.message,
            "name": self.name,
            "type": self.type,
            "model": self.model,
            "workspace_path": self.workspace_path,
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
        workspace_path: Optional[str] = None
    ) -> Schedule:
        """Add a new schedule."""
        schedule_id = uuid4().hex[:8]
        now = self._now()
        self.get_or_create_user(user_id)

        self._conn.execute(
            """INSERT INTO schedules
               (id, user_id, chat_id, hour, minute, message, name, type, model, workspace_path, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (schedule_id, user_id, chat_id, hour, minute, message, name,
             schedule_type, model, workspace_path, now)
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
            type=schedule_type,
            model=model,
            workspace_path=workspace_path,
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
            type=row["type"],
            model=row["model"],
            workspace_path=row["workspace_path"],
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
        slot: str,
        text: str
    ) -> Todo:
        """Add a todo item."""
        now = self._now()
        cursor = self._conn.execute(
            """INSERT INTO todos (chat_id, date, slot, text, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (chat_id, date, slot, text, now, now)
        )
        self._conn.commit()

        return Todo(
            id=cursor.lastrowid or 0,
            chat_id=chat_id,
            date=date,
            slot=slot,
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
            "SELECT * FROM todos WHERE chat_id = ? AND date = ? ORDER BY slot, id",
            (chat_id, date)
        )
        return [self._row_to_todo(row) for row in cursor.fetchall()]

    def list_todos_by_slot(self, chat_id: int, date: str, slot: str) -> list[Todo]:
        """List todos for specific slot."""
        cursor = self._conn.execute(
            "SELECT * FROM todos WHERE chat_id = ? AND date = ? AND slot = ? ORDER BY id",
            (chat_id, date, slot)
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

    # ========== Migration Tracking ==========

    def is_migration_applied(self, name: str) -> bool:
        """Check if migration has been applied."""
        cursor = self._conn.execute(
            "SELECT 1 FROM migrations WHERE name = ?", (name,)
        )
        return cursor.fetchone() is not None

    def mark_migration_applied(self, name: str) -> None:
        """Mark migration as applied."""
        self._conn.execute(
            "INSERT INTO migrations (name) VALUES (?)", (name,)
        )
        self._conn.commit()

    # ========== Bulk Operations for Migration ==========

    def bulk_insert_users(self, users: list[dict[str, Any]]) -> None:
        """Bulk insert users."""
        self._conn.executemany(
            """INSERT OR IGNORE INTO users (id, current_session_id, previous_session_id)
               VALUES (:id, :current_session_id, :previous_session_id)""",
            users
        )
        self._conn.commit()

    def bulk_insert_sessions(self, sessions: list[dict[str, Any]]) -> None:
        """Bulk insert sessions."""
        self._conn.executemany(
            """INSERT OR IGNORE INTO sessions
               (id, user_id, model, name, workspace_path, created_at, last_used, deleted)
               VALUES (:id, :user_id, :model, :name, :workspace_path, :created_at, :last_used, :deleted)""",
            sessions
        )
        self._conn.commit()

    def bulk_insert_history(self, history: list[dict[str, Any]]) -> None:
        """Bulk insert history entries."""
        self._conn.executemany(
            """INSERT INTO session_history (session_id, message, timestamp, processed, processor)
               VALUES (:session_id, :message, :timestamp, :processed, :processor)""",
            history
        )
        self._conn.commit()

    def bulk_insert_schedules(self, schedules: list[dict[str, Any]]) -> None:
        """Bulk insert schedules."""
        self._conn.executemany(
            """INSERT OR IGNORE INTO schedules
               (id, user_id, chat_id, hour, minute, message, name, type, model,
                workspace_path, enabled, created_at, last_run, last_error, run_count)
               VALUES (:id, :user_id, :chat_id, :hour, :minute, :message, :name, :type, :model,
                       :workspace_path, :enabled, :created_at, :last_run, :last_error, :run_count)""",
            schedules
        )
        self._conn.commit()

    def bulk_insert_workspaces(self, workspaces: list[dict[str, Any]]) -> None:
        """Bulk insert workspaces."""
        for ws in workspaces:
            if isinstance(ws.get("keywords"), list):
                ws["keywords"] = json.dumps(ws["keywords"])
        self._conn.executemany(
            """INSERT OR IGNORE INTO workspaces
               (id, user_id, path, name, description, keywords, created_at, last_used, use_count)
               VALUES (:id, :user_id, :path, :name, :description, :keywords, :created_at, :last_used, :use_count)""",
            workspaces
        )
        self._conn.commit()

    def bulk_insert_memos(self, memos: list[dict[str, Any]]) -> None:
        """Bulk insert memos."""
        self._conn.executemany(
            """INSERT INTO memos (chat_id, content, created_at)
               VALUES (:chat_id, :content, :created_at)""",
            memos
        )
        self._conn.commit()

    def bulk_insert_todos(self, todos: list[dict[str, Any]]) -> None:
        """Bulk insert todos."""
        self._conn.executemany(
            """INSERT INTO todos (chat_id, date, slot, text, done, created_at, updated_at)
               VALUES (:chat_id, :date, :slot, :text, :done, :created_at, :updated_at)""",
            todos
        )
        self._conn.commit()

    def bulk_insert_weather_locations(self, locations: list[dict[str, Any]]) -> None:
        """Bulk insert weather locations."""
        self._conn.executemany(
            """INSERT OR REPLACE INTO weather_locations (chat_id, name, country, lat, lon)
               VALUES (:chat_id, :name, :country, :lat, :lon)""",
            locations
        )
        self._conn.commit()
