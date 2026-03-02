"""Authentication and authorization middleware."""

import hmac
from datetime import datetime, timedelta
from functools import wraps
from typing import Callable, Optional, TypeVar

from telegram import Update
from telegram.ext import ContextTypes

F = TypeVar('F', bound=Callable)


class AuthManager:
    """Manages user authentication sessions."""
    
    def __init__(self, secret_key: str, timeout_minutes: int = 30):
        self.secret_key = secret_key
        self.timeout_minutes = timeout_minutes
        self._sessions: dict[str, datetime] = {}
    
    def is_authenticated(self, user_id: str) -> bool:
        if user_id not in self._sessions:
            return False
        
        last_auth = self._sessions[user_id]
        return datetime.now() - last_auth < timedelta(minutes=self.timeout_minutes)
    
    def authenticate(self, user_id: str, key: str) -> bool:
        # 타이밍 공격 방지를 위해 상수 시간 비교 사용
        if hmac.compare_digest(key, self.secret_key):
            self._sessions[user_id] = datetime.now()
            return True
        return False
    
    def get_remaining_minutes(self, user_id: str) -> int:
        if user_id not in self._sessions:
            return 0

        elapsed = datetime.now() - self._sessions[user_id]
        remaining = self.timeout_minutes - int(elapsed.total_seconds() / 60)
        return max(0, remaining)

    def cleanup_expired(self) -> int:
        """만료된 인증 세션 정리. 정리된 수 반환."""
        now = datetime.now()
        expired = [
            uid for uid, last_auth in self._sessions.items()
            if now - last_auth >= timedelta(minutes=self.timeout_minutes)
        ]
        for uid in expired:
            del self._sessions[uid]
        return len(expired)


def require_auth(
    auth_manager: AuthManager,
    require_auth_setting: bool,
    allowed_chat_ids: list[int],
):
    """Decorator factory for auth-protected handlers."""
    
    def decorator(func: Callable):
        @wraps(func)
        async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE, *args, **kwargs):
            chat_id = update.effective_chat.id
            user_id = str(chat_id)
            
            # Check allowed chat IDs
            if allowed_chat_ids and chat_id not in allowed_chat_ids:
                await update.message.reply_text("⛔ 권한이 없습니다.")
                return
            
            # Check authentication if required
            if require_auth_setting and not auth_manager.is_authenticated(user_id):
                await update.message.reply_text(
                    "🔒 인증이 필요합니다.\n/auth <키>로 인증하세요. (30분간 유효)"
                )
                return
            
            return await func(update, context, *args, **kwargs)
        
        return wrapper
    return decorator


def require_allowed_chat(allowed_chat_ids: list[int]):
    """Decorator factory for chat ID restriction."""

    def decorator(func: Callable):
        @wraps(func)
        async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE, *args, **kwargs):
            chat_id = update.effective_chat.id

            if allowed_chat_ids and chat_id not in allowed_chat_ids:
                await update.message.reply_text("⛔ 권한이 없습니다.")
                return

            return await func(update, context, *args, **kwargs)

        return wrapper
    return decorator


def authorized_only(method: F) -> F:
    """권한 검사 데코레이터 (BotHandlers 메서드용)."""
    @wraps(method)
    async def wrapper(self, update, context, *args, **kwargs):
        if not self._is_authorized(update.effective_chat.id):
            await update.message.reply_text("⛔ 권한이 없습니다.")
            return
        return await method(self, update, context, *args, **kwargs)
    return wrapper


def authenticated_only(method: F) -> F:
    """인증 검사 데코레이터 (BotHandlers 메서드용).

    Note: authorized_only와 함께 사용 시 authorized_only를 먼저 적용해야 함.
    """
    @wraps(method)
    async def wrapper(self, update, context, *args, **kwargs):
        user_id = str(update.effective_chat.id)
        if not self._is_authenticated(user_id):
            await update.message.reply_text(
                "🔒 먼저 인증이 필요합니다.\n/auth <키>"
            )
            return
        return await method(self, update, context, *args, **kwargs)
    return wrapper
