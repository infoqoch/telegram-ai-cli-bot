"""Session storage - Claude session_id as primary key."""

import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional, TypedDict

from src.logging_config import logger


class SessionData(TypedDict):
    """Type definition for session data structure."""

    created_at: str
    last_used: str
    history: list[str]
    model: str  # opus, sonnet, haiku
    name: str  # 사용자 지정 세션 이름 (선택)
    deleted: bool  # soft delete 상태
    is_manager: bool  # 매니저 세션 여부


# 지원하는 모델 목록
SUPPORTED_MODELS = ["opus", "sonnet", "haiku"]
DEFAULT_MODEL = "sonnet"


class SessionStore:
    """
    Session storage using Claude's session_id as primary key.

    Data structure:
    {
        "user_id": {
            "current": "claude_session_id",
            "sessions": {
                "claude_session_id": {
                    "created_at": "...",
                    "last_used": "...",
                    "history": [...]
                }
            }
        }
    }
    """

    def __init__(self, file_path: Path, timeout_hours: int = 24):
        logger.trace(f"SessionStore.__init__() - file={file_path}, timeout={timeout_hours}h")
        self.file_path = file_path
        self.timeout_hours = timeout_hours
        self._data: dict = self._load()

    def _load(self) -> dict:
        logger.trace(f"_load() - file={self.file_path}")

        if self.file_path.exists():
            try:
                with open(self.file_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    user_count = len(data)
                    session_count = sum(len(u.get("sessions", {})) for u in data.values())
                    logger.trace(f"세션 파일 로드됨 - users={user_count}, sessions={session_count}")
                    logger.info(f"세션 로드: {user_count}명, {session_count}개 세션")
                    return data
            except Exception as e:
                logger.error(f"세션 파일 로드 실패: {e}")
        else:
            logger.trace("세션 파일 없음 - 새로 시작")

        return {}

    def _save(self) -> bool:
        """Save session data. Returns True on success."""
        logger.trace(f"_save() - file={self.file_path}")

        try:
            self.file_path.parent.mkdir(parents=True, exist_ok=True)
            logger.trace("디렉토리 확인됨")

            # atomic write: 임시 파일에 쓴 후 이동
            temp_file = self.file_path.with_suffix('.tmp')
            with open(temp_file, "w", encoding="utf-8") as f:
                json.dump(self._data, f, indent=2, ensure_ascii=False, default=str)
            temp_file.replace(self.file_path)

            logger.trace("세션 저장 완료")
            return True
        except Exception as e:
            logger.error(f"세션 저장 실패: {e}")
            return False

    def _ensure_user(self, user_id: str) -> dict:
        """Ensure user data structure exists."""
        logger.trace(f"_ensure_user() - user_id={user_id}")

        if user_id not in self._data:
            self._data[user_id] = {"current": None, "sessions": {}}
            logger.trace("새 사용자 데이터 구조 생성됨")

        return self._data[user_id]

    def get_current_session_id(self, user_id: str) -> Optional[str]:
        """Get current session_id for user (None if expired or not exists)."""
        logger.trace(f"get_current_session_id() - user_id={user_id}")

        user_data = self._data.get(user_id)
        if not user_data:
            logger.trace("사용자 데이터 없음")
            return None

        session_id = user_data.get("current")
        if not session_id:
            logger.trace("현재 세션 미설정")
            return None

        logger.trace(f"현재 세션: {session_id[:8]}")

        session = user_data.get("sessions", {}).get(session_id)
        if not session:
            logger.trace("세션 데이터 없음")
            return None

        # Check expiration
        try:
            last_used = datetime.fromisoformat(session["last_used"])
            logger.trace(f"last_used={last_used}")
        except (ValueError, KeyError, TypeError) as e:
            logger.warning(f"잘못된 타임스탬프: {e}")
            return None

        elapsed = datetime.now() - last_used
        logger.trace(f"경과 시간: {elapsed}")

        if elapsed > timedelta(hours=self.timeout_hours):
            logger.info(f"세션 만료 - session={session_id[:8]}, elapsed={elapsed}")
            return None

        logger.trace(f"유효한 세션 반환 - {session_id[:8]}")
        return session_id

    def create_session(self, user_id: str, session_id: str, first_message: str, model: str = None, name: str = "") -> None:
        """Create a new session with Claude's session_id."""
        model = model or DEFAULT_MODEL
        logger.trace(f"create_session() - user={user_id}, session={session_id[:8]}, model={model}, name={name or '(없음)'}")
        logger.trace(f"first_message length={len(first_message)}")

        user_data = self._ensure_user(user_id)
        now = datetime.now().isoformat()

        user_data["current"] = session_id
        user_data["sessions"][session_id] = {
            "created_at": now,
            "last_used": now,
            "history": [first_message],
            "model": model,
            "name": name,
        }

        self._save()
        logger.info(f"세션 생성됨 - user={user_id}, session={session_id[:8]}, model={model}, name={name or '(없음)'}")

    def add_message(self, user_id: str, session_id: str, message: str) -> None:
        """Add a message to specific session (not current!)."""
        short_msg = message[:30] + "..." if len(message) > 30 else message
        logger.trace(f"add_message() - user={user_id}, session={session_id[:8]}")
        logger.trace(f"message='{short_msg}'")

        user_data = self._data.get(user_id)
        if not user_data:
            logger.warning("메시지 추가 실패 - 사용자 없음")
            return

        session = user_data.get("sessions", {}).get(session_id)
        if not session:
            logger.warning(f"메시지 추가 실패 - 세션 없음: {session_id[:8]}")
            return

        session["last_used"] = datetime.now().isoformat()
        session["history"].append(message)
        history_count = len(session["history"])

        logger.trace(f"메시지 추가됨 - 총 {history_count}개")
        self._save()

    def set_current(self, user_id: str, session_id: str) -> None:
        """Set current session for user."""
        logger.trace(f"set_current() - user={user_id}, session={session_id[:8]}")

        user_data = self._ensure_user(user_id)
        if session_id in user_data.get("sessions", {}):
            user_data["current"] = session_id
            user_data["sessions"][session_id]["last_used"] = datetime.now().isoformat()
            self._save()
            logger.trace("현재 세션 설정됨")
        else:
            logger.trace("세션을 찾을 수 없음")

    def clear_current(self, user_id: str) -> None:
        """Clear current session selection."""
        logger.trace(f"clear_current() - user={user_id}")

        if user_id in self._data:
            self._data[user_id]["current"] = None
            self._save()
            logger.trace("현재 세션 클리어됨")

    def get_session_info(self, user_id: str, session_id: str) -> str:
        """Return short session ID (first 8 chars)."""
        result = session_id[:8] if session_id else "없음"
        logger.trace(f"get_session_info() - user={user_id} -> {result}")
        return result

    def get_history_count(self, user_id: str, session_id: str) -> int:
        """Get history count for specific session."""
        logger.trace(f"get_history_count() - user={user_id}, session={session_id[:8] if session_id else 'None'}")

        user_data = self._data.get(user_id)
        if not user_data:
            logger.trace("사용자 없음 -> 0")
            return 0

        session = user_data.get("sessions", {}).get(session_id)
        count = len(session.get("history", [])) if session else 0
        logger.trace(f"히스토리 수: {count}")
        return count

    def get_session_model(self, user_id: str, session_id: str) -> str:
        """Get model for specific session."""
        logger.trace(f"get_session_model() - user={user_id}, session={session_id[:8] if session_id else 'None'}")

        user_data = self._data.get(user_id)
        if not user_data:
            logger.trace(f"사용자 없음 -> {DEFAULT_MODEL}")
            return DEFAULT_MODEL

        session = user_data.get("sessions", {}).get(session_id)
        model = session.get("model", DEFAULT_MODEL) if session else DEFAULT_MODEL
        logger.trace(f"모델: {model}")
        return model

    def list_sessions(self, user_id: str) -> list[dict]:
        """List all sessions for a user."""
        logger.trace(f"list_sessions() - user={user_id}")

        user_data = self._data.get(user_id)
        if not user_data:
            logger.trace("사용자 없음 -> []")
            return []

        current_id = user_data.get("current")
        sessions = []

        for session_id, data in user_data.get("sessions", {}).items():
            # soft deleted 세션 제외
            if data.get("deleted", False):
                continue
            sessions.append({
                "session_id": session_id[:8],
                "full_session_id": session_id,
                "created_at": data.get("created_at", "")[:19],
                "last_used": data.get("last_used", "")[:19],
                "history_count": len(data.get("history", [])),
                "is_current": session_id == current_id,
                "model": data.get("model", DEFAULT_MODEL),
                "name": data.get("name", ""),
                "is_manager": data.get("is_manager", False),
            })

        sessions.sort(key=lambda x: x["last_used"], reverse=True)
        logger.trace(f"세션 목록: {len(sessions)}개")
        return sessions

    def switch_session(self, user_id: str, session_prefix: str) -> bool:
        """Switch to a session by ID prefix."""
        logger.trace(f"switch_session() - user={user_id}, prefix={session_prefix}")

        user_data = self._data.get(user_id)
        if not user_data:
            logger.trace("사용자 없음 -> False")
            return False

        for session_id, data in user_data.get("sessions", {}).items():
            if data.get("deleted", False):
                continue
            if session_id.startswith(session_prefix):
                self.set_current(user_id, session_id)
                logger.info(f"세션 전환됨 - user={user_id}, session={session_id[:8]}")
                return True

        logger.trace("매칭 세션 없음 -> False")
        return False

    def get_session_by_prefix(self, user_id: str, prefix: str) -> Optional[dict]:
        """Find session info by ID prefix."""
        logger.trace(f"get_session_by_prefix() - user={user_id}, prefix={prefix}")

        user_data = self._data.get(user_id)
        if not user_data:
            logger.trace("사용자 없음 -> None")
            return None

        for session_id, data in user_data.get("sessions", {}).items():
            if data.get("deleted", False):
                continue
            if session_id.startswith(prefix):
                result = {
                    "session_id": session_id[:8],
                    "full_session_id": session_id,
                    "created_at": data.get("created_at", "")[:19],
                    "last_used": data.get("last_used", "")[:19],
                    "history_count": len(data.get("history", [])),
                    "name": data.get("name", ""),
                }
                logger.trace(f"세션 찾음: {result['session_id']}")
                return result

        logger.trace("매칭 세션 없음 -> None")
        return None

    def get_session_history(self, user_id: str, session_id: str) -> list[str]:
        """Get history for a specific session."""
        logger.trace(f"get_session_history() - user={user_id}, session={session_id[:8] if session_id else 'None'}")

        user_data = self._data.get(user_id)
        if not user_data:
            logger.trace("사용자 없음 -> []")
            return []

        session = user_data.get("sessions", {}).get(session_id)
        history = session.get("history", []) if session else []
        logger.trace(f"히스토리 반환: {len(history)}개")
        return history

    def rename_session(self, user_id: str, session_id: str, name: str) -> bool:
        """Rename a session."""
        logger.trace(f"rename_session() - user={user_id}, session={session_id[:8]}, name={name}")

        user_data = self._data.get(user_id)
        if not user_data:
            logger.trace("사용자 없음 -> False")
            return False

        session = user_data.get("sessions", {}).get(session_id)
        if not session or session.get("deleted", False):
            logger.trace("세션 없거나 삭제됨 -> False")
            return False

        session["name"] = name
        self._save()
        logger.info(f"세션 이름 변경됨 - session={session_id[:8]}, name={name}")
        return True

    def delete_session(self, user_id: str, session_id: str) -> bool:
        """Soft delete a session."""
        logger.trace(f"delete_session() - user={user_id}, session={session_id[:8]}")

        user_data = self._data.get(user_id)
        if not user_data:
            logger.trace("사용자 없음 -> False")
            return False

        session = user_data.get("sessions", {}).get(session_id)
        if not session:
            logger.trace("세션 없음 -> False")
            return False

        session["deleted"] = True

        # 현재 세션이면 current 해제
        if user_data.get("current") == session_id:
            user_data["current"] = None
            logger.trace("current 세션 해제됨")

        self._save()
        logger.info(f"세션 삭제됨 (soft) - session={session_id[:8]}")
        return True

    def get_session_name(self, user_id: str, session_id: str) -> str:
        """Get session name."""
        logger.trace(f"get_session_name() - user={user_id}, session={session_id[:8] if session_id else 'None'}")

        user_data = self._data.get(user_id)
        if not user_data:
            return ""

        session = user_data.get("sessions", {}).get(session_id)
        name = session.get("name", "") if session else ""
        logger.trace(f"세션 이름: {name or '(없음)'}")
        return name

    # ===== Manager Session Methods =====

    def get_manager_session_id(self, user_id: str) -> Optional[str]:
        """Get manager session ID for user (None if not exists)."""
        logger.trace(f"get_manager_session_id() - user={user_id}")

        user_data = self._data.get(user_id)
        if not user_data:
            return None

        for session_id, data in user_data.get("sessions", {}).items():
            if data.get("deleted", False):
                continue
            if data.get("is_manager", False):
                logger.trace(f"매니저 세션 찾음: {session_id[:8]}")
                return session_id

        logger.trace("매니저 세션 없음")
        return None

    def create_manager_session(self, user_id: str, session_id: str, model: str = "sonnet") -> None:
        """Create a manager session."""
        logger.trace(f"create_manager_session() - user={user_id}, session={session_id[:8]}, model={model}")

        user_data = self._ensure_user(user_id)
        now = datetime.now().isoformat()

        user_data["sessions"][session_id] = {
            "created_at": now,
            "last_used": now,
            "history": ["(매니저 세션 시작)"],
            "model": model,
            "name": "📋 Manager",
            "is_manager": True,
        }

        self._save()
        logger.info(f"매니저 세션 생성됨 - user={user_id}, session={session_id[:8]}")

    def get_previous_session_id(self, user_id: str) -> Optional[str]:
        """Get previous session ID (stored when switching to manager)."""
        logger.trace(f"get_previous_session_id() - user={user_id}")

        user_data = self._data.get(user_id)
        if not user_data:
            return None

        prev_id = user_data.get("previous_session")
        logger.trace(f"이전 세션: {prev_id[:8] if prev_id else 'None'}")
        return prev_id

    def set_previous_session_id(self, user_id: str, session_id: Optional[str]) -> None:
        """Store previous session ID for /back command."""
        logger.trace(f"set_previous_session_id() - user={user_id}, session={session_id[:8] if session_id else 'None'}")

        user_data = self._ensure_user(user_id)
        user_data["previous_session"] = session_id
        self._save()

    def get_all_sessions_summary(self, user_id: str) -> str:
        """Get summary of all sessions for manager context."""
        logger.trace(f"get_all_sessions_summary() - user={user_id}")

        sessions = self.list_sessions(user_id)
        if not sessions:
            return "(세션 없음)"

        lines = []
        for s in sessions:
            if s.get("is_manager"):
                continue  # 매니저 세션 제외
            name = s.get("name", "") or "(이름없음)"
            model_emoji = {"opus": "🧠", "sonnet": "⚡", "haiku": "🚀"}.get(s.get("model", ""), "")
            history = self.get_session_history(user_id, s["full_session_id"])
            last_msg = history[-1][:50] if history else "-"
            lines.append(
                f"- {s['session_id']} {name} {model_emoji}{s.get('model', 'sonnet')} "
                f"({s['history_count']}개, {s['last_used'][:10]})\n  최근: {last_msg}"
            )

        return "\n".join(lines)
