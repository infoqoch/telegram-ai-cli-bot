"""Migration utilities for JSON to SQLite conversion."""

import json
import logging
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .repository import Repository

logger = logging.getLogger(__name__)


class MigrationError(Exception):
    """Migration error."""
    pass


def migrate_all(repo: Repository, data_dir: Path) -> dict[str, int]:
    """Run all migrations from JSON files to SQLite.

    Args:
        repo: Repository instance
        data_dir: Path to .data directory

    Returns:
        Dict with migration counts per type
    """
    results = {
        "users": 0,
        "sessions": 0,
        "history": 0,
        "schedules": 0,
        "workspaces": 0,
        "memos": 0,
        "todos": 0,
        "weather": 0,
    }

    if repo.is_migration_applied("json_to_sqlite_v1"):
        logger.info("Migration already applied, skipping")
        return results

    try:
        # Migrate sessions (includes users and history)
        sessions_file = data_dir / "sessions.json"
        if sessions_file.exists():
            counts = _migrate_sessions(repo, sessions_file)
            results["users"] = counts["users"]
            results["sessions"] = counts["sessions"]
            results["history"] = counts["history"]
            _backup_file(sessions_file)

        # Migrate schedules
        schedules_file = data_dir / "schedules.json"
        if schedules_file.exists():
            results["schedules"] = _migrate_schedules(repo, schedules_file)
            _backup_file(schedules_file)

        # Migrate workspaces
        workspaces_file = data_dir / "workspaces.json"
        if workspaces_file.exists():
            results["workspaces"] = _migrate_workspaces(repo, workspaces_file)
            _backup_file(workspaces_file)

        # Migrate plugin data
        memo_dir = data_dir / "memo"
        if memo_dir.exists():
            results["memos"] = _migrate_memos(repo, memo_dir)

        todo_dir = data_dir / "todo"
        if todo_dir.exists():
            results["todos"] = _migrate_todos(repo, todo_dir)

        weather_dir = data_dir / "weather"
        if weather_dir.exists():
            results["weather"] = _migrate_weather(repo, weather_dir)

        # Mark migration as complete
        repo.mark_migration_applied("json_to_sqlite_v1")
        logger.info(f"Migration complete: {results}")

    except Exception as e:
        logger.error(f"Migration failed: {e}")
        raise MigrationError(f"Migration failed: {e}") from e

    return results


def _backup_file(file_path: Path) -> None:
    """Create backup of JSON file."""
    backup_path = file_path.with_suffix(".json.bak")
    shutil.copy2(file_path, backup_path)
    logger.info(f"Backed up {file_path} to {backup_path}")


def _migrate_sessions(repo: Repository, sessions_file: Path) -> dict[str, int]:
    """Migrate sessions.json to SQLite.

    JSON format:
    {
        "user_id": {
            "current": "session_id",
            "previous_session": "session_id",
            "sessions": {
                "session_id": {
                    "created_at": "...",
                    "last_used": "...",
                    "history": [...],
                    "model": "...",
                    "name": "...",
                    "workspace_path": "...",
                    "deleted": false
                }
            }
        }
    }
    """
    data = json.loads(sessions_file.read_text(encoding="utf-8"))

    users: list[dict[str, Any]] = []
    sessions: list[dict[str, Any]] = []
    history: list[dict[str, Any]] = []

    for user_id, user_data in data.items():
        # User record
        users.append({
            "id": user_id,
            "current_session_id": user_data.get("current"),
            "previous_session_id": user_data.get("previous_session"),
        })

        # Sessions
        for session_id, session_data in user_data.get("sessions", {}).items():
            sessions.append({
                "id": session_id,
                "user_id": user_id,
                "model": session_data.get("model", "sonnet"),
                "name": session_data.get("name"),
                "workspace_path": session_data.get("workspace_path"),
                "created_at": session_data.get("created_at", datetime.now(timezone.utc).isoformat()),
                "last_used": session_data.get("last_used", datetime.now(timezone.utc).isoformat()),
                "deleted": 1 if session_data.get("deleted", False) else 0,
            })

            # History entries
            for entry in session_data.get("history", []):
                if isinstance(entry, str):
                    # Legacy format: just message string
                    history.append({
                        "session_id": session_id,
                        "message": entry,
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                        "processed": 0,
                        "processor": None,
                    })
                elif isinstance(entry, dict):
                    # New format: HistoryEntry dict
                    history.append({
                        "session_id": session_id,
                        "message": entry.get("message", ""),
                        "timestamp": entry.get("timestamp", datetime.now(timezone.utc).isoformat()),
                        "processed": 1 if entry.get("processed", False) else 0,
                        "processor": entry.get("processor"),
                    })

    # Bulk insert
    if users:
        repo.bulk_insert_users(users)
    if sessions:
        repo.bulk_insert_sessions(sessions)
    if history:
        repo.bulk_insert_history(history)

    return {
        "users": len(users),
        "sessions": len(sessions),
        "history": len(history),
    }


def _migrate_schedules(repo: Repository, schedules_file: Path) -> int:
    """Migrate schedules.json to SQLite.

    JSON format:
    {
        "schedules": [
            {
                "id": "...",
                "user_id": "...",
                "chat_id": 123,
                "hour": 9,
                "minute": 0,
                "message": "...",
                "name": "...",
                "type": "claude",
                "model": "sonnet",
                "workspace_path": null,
                "enabled": true,
                "created_at": "...",
                "last_run": null,
                "last_error": null,
                "run_count": 0
            }
        ]
    }
    """
    data = json.loads(schedules_file.read_text(encoding="utf-8"))
    schedules = data.get("schedules", [])

    if not schedules:
        return 0

    # Ensure users exist
    user_ids = set(s["user_id"] for s in schedules)
    for user_id in user_ids:
        repo.get_or_create_user(user_id)

    # Prepare for bulk insert
    schedule_records = []
    for s in schedules:
        schedule_records.append({
            "id": s["id"],
            "user_id": s["user_id"],
            "chat_id": s["chat_id"],
            "hour": s["hour"],
            "minute": s["minute"],
            "message": s["message"],
            "name": s["name"],
            "type": s.get("type", "claude"),
            "model": s.get("model", "sonnet"),
            "workspace_path": s.get("workspace_path"),
            "enabled": 1 if s.get("enabled", True) else 0,
            "created_at": s.get("created_at", datetime.now(timezone.utc).isoformat()),
            "last_run": s.get("last_run"),
            "last_error": s.get("last_error"),
            "run_count": s.get("run_count", 0),
        })

    repo.bulk_insert_schedules(schedule_records)
    return len(schedule_records)


def _migrate_workspaces(repo: Repository, workspaces_file: Path) -> int:
    """Migrate workspaces.json to SQLite.

    JSON format:
    {
        "workspaces": [
            {
                "id": "...",
                "user_id": "...",
                "path": "...",
                "name": "...",
                "description": "...",
                "keywords": [...],
                "created_at": "...",
                "last_used": null,
                "use_count": 0
            }
        ]
    }
    """
    data = json.loads(workspaces_file.read_text(encoding="utf-8"))
    workspaces = data.get("workspaces", [])

    if not workspaces:
        return 0

    # Ensure users exist
    user_ids = set(w["user_id"] for w in workspaces)
    for user_id in user_ids:
        repo.get_or_create_user(user_id)

    # Prepare for bulk insert
    workspace_records = []
    for w in workspaces:
        workspace_records.append({
            "id": w["id"],
            "user_id": w["user_id"],
            "path": w["path"],
            "name": w["name"],
            "description": w.get("description", ""),
            "keywords": w.get("keywords", []),
            "created_at": w.get("created_at", datetime.now(timezone.utc).isoformat()),
            "last_used": w.get("last_used"),
            "use_count": w.get("use_count", 0),
        })

    repo.bulk_insert_workspaces(workspace_records)
    return len(workspace_records)


def _migrate_memos(repo: Repository, memo_dir: Path) -> int:
    """Migrate memo plugin data to SQLite.

    File format: .data/memo/{chat_id}.json
    [
        {"id": 1, "content": "...", "created_at": "..."},
        ...
    ]
    """
    count = 0
    memo_records = []

    for memo_file in memo_dir.glob("*.json"):
        try:
            chat_id = int(memo_file.stem)
            memos = json.loads(memo_file.read_text(encoding="utf-8"))

            for memo in memos:
                memo_records.append({
                    "chat_id": chat_id,
                    "content": memo.get("content", ""),
                    "created_at": memo.get("created_at", datetime.now(timezone.utc).isoformat()),
                })
                count += 1

            _backup_file(memo_file)
        except (ValueError, json.JSONDecodeError) as e:
            logger.warning(f"Failed to migrate {memo_file}: {e}")

    if memo_records:
        repo.bulk_insert_memos(memo_records)

    return count


def _migrate_todos(repo: Repository, todo_dir: Path) -> int:
    """Migrate todo plugin data to SQLite.

    File format: .data/todo/{chat_id}.json
    {
        "date": "2024-01-15",
        "tasks": {
            "morning": [{"text": "...", "done": false, "created_at": "..."}, ...],
            "afternoon": [...],
            "evening": [...]
        },
        "pending_input": false,
        ...
    }
    """
    count = 0
    todo_records = []
    now = datetime.now(timezone.utc).isoformat()

    for todo_file in todo_dir.glob("*.json"):
        try:
            chat_id = int(todo_file.stem)
            data = json.loads(todo_file.read_text(encoding="utf-8"))

            # New format: {date, tasks: {morning, afternoon, evening}, ...}
            date = data.get("date")
            tasks = data.get("tasks", {})

            if not date or not tasks:
                logger.warning(f"Skipping {todo_file}: invalid format")
                continue

            for slot, items in tasks.items():
                if slot not in ("morning", "afternoon", "evening"):
                    continue
                for item in items:
                    if isinstance(item, dict):
                        todo_records.append({
                            "chat_id": chat_id,
                            "date": date,
                            "slot": slot,
                            "text": item.get("text", ""),
                            "done": 1 if item.get("done", False) else 0,
                            "created_at": item.get("created_at", now),
                            "updated_at": item.get("updated_at", now),
                        })
                        count += 1
                    elif isinstance(item, str):
                        # Legacy format: just text
                        todo_records.append({
                            "chat_id": chat_id,
                            "date": date,
                            "slot": slot,
                            "text": item,
                            "done": 0,
                            "created_at": now,
                            "updated_at": now,
                        })
                        count += 1

            _backup_file(todo_file)
        except (ValueError, json.JSONDecodeError) as e:
            logger.warning(f"Failed to migrate {todo_file}: {e}")

    if todo_records:
        repo.bulk_insert_todos(todo_records)

    return count


def _migrate_weather(repo: Repository, weather_dir: Path) -> int:
    """Migrate weather plugin data to SQLite.

    File format: .data/weather/{chat_id}.json
    {
        "name": "Seoul",
        "country": "South Korea",
        "lat": 37.5665,
        "lon": 126.9780
    }
    """
    count = 0
    location_records = []

    for weather_file in weather_dir.glob("*.json"):
        try:
            chat_id = int(weather_file.stem)
            data = json.loads(weather_file.read_text(encoding="utf-8"))

            location_records.append({
                "chat_id": chat_id,
                "name": data.get("name", "Unknown"),
                "country": data.get("country"),
                "lat": data.get("lat", 0.0),
                "lon": data.get("lon", 0.0),
            })
            count += 1

            _backup_file(weather_file)
        except (ValueError, json.JSONDecodeError) as e:
            logger.warning(f"Failed to migrate {weather_file}: {e}")

    if location_records:
        repo.bulk_insert_weather_locations(location_records)

    return count


def rollback_migration(data_dir: Path) -> dict[str, int]:
    """Rollback migration by restoring .bak files.

    Args:
        data_dir: Path to .data directory

    Returns:
        Dict with rollback counts per type
    """
    results = {"restored": 0, "failed": 0}

    # Find all .bak files
    for bak_file in data_dir.rglob("*.json.bak"):
        try:
            original = bak_file.with_suffix("")  # Remove .bak
            shutil.copy2(bak_file, original)
            results["restored"] += 1
            logger.info(f"Restored {original} from {bak_file}")
        except Exception as e:
            results["failed"] += 1
            logger.error(f"Failed to restore {bak_file}: {e}")

    return results
