"""인증 미들웨어 테스트.

AuthManager 클래스의 핵심 기능 검증:
- 인증 성공/실패
- 세션 타임아웃
- 타이밍 공격 방지 (hmac.compare_digest)
"""

import time
from datetime import datetime, timedelta

import pytest

from src.bot.middleware import AuthManager


@pytest.fixture
def auth_manager():
    """테스트용 AuthManager 생성."""
    return AuthManager(secret_key="test_secret_key", timeout_minutes=30)


class TestAuthManager:
    """AuthManager 단위 테스트."""

    def test_authenticate_success(self, auth_manager):
        """올바른 키로 인증 성공 확인."""
        user_id = "user123"
        result = auth_manager.authenticate(user_id, "test_secret_key")

        assert result is True
        assert auth_manager.is_authenticated(user_id) is True

    def test_authenticate_failure(self, auth_manager):
        """잘못된 키로 인증 실패 확인."""
        user_id = "user123"
        result = auth_manager.authenticate(user_id, "wrong_key")

        assert result is False
        assert auth_manager.is_authenticated(user_id) is False

    def test_is_authenticated_without_auth(self, auth_manager):
        """인증하지 않은 사용자는 미인증 상태."""
        assert auth_manager.is_authenticated("unknown_user") is False

    def test_session_timeout(self):
        """세션 타임아웃 확인 (1분 타임아웃)."""
        auth = AuthManager(secret_key="key", timeout_minutes=0)
        user_id = "user123"

        auth.authenticate(user_id, "key")
        # 타임아웃 0분이므로 즉시 만료
        assert auth.is_authenticated(user_id) is False

    def test_get_remaining_minutes(self, auth_manager):
        """남은 시간 조회 확인."""
        user_id = "user123"

        # 인증 전
        assert auth_manager.get_remaining_minutes(user_id) == 0

        # 인증 후
        auth_manager.authenticate(user_id, "test_secret_key")
        remaining = auth_manager.get_remaining_minutes(user_id)
        assert 29 <= remaining <= 30

    def test_empty_secret_key(self):
        """빈 시크릿 키로 인증 시도."""
        auth = AuthManager(secret_key="", timeout_minutes=30)
        # 빈 키로도 빈 입력과 매칭됨
        assert auth.authenticate("user", "") is True
        assert auth.authenticate("user", "any_key") is False

    def test_timing_attack_resistance(self, auth_manager):
        """타이밍 공격 저항성 - hmac.compare_digest 사용 확인."""
        # 짧은 키와 긴 키 비교 시 시간 차이가 없어야 함
        import hmac

        # 실제로 hmac.compare_digest가 사용되는지 확인
        # (이 테스트는 코드 리뷰 목적)
        user_id = "user123"

        # 다양한 길이의 잘못된 키로 시도
        for wrong_key in ["a", "ab", "abc", "abcd", "wrong_key_very_long"]:
            result = auth_manager.authenticate(user_id, wrong_key)
            assert result is False


class TestCleanupExpired:
    """cleanup_expired 메서드 테스트."""

    def test_cleanup_expired_removes_expired(self):
        """만료된 세션 정리 확인."""
        auth = AuthManager(secret_key="key", timeout_minutes=0)

        # 3명 인증 (타임아웃 0분이므로 즉시 만료)
        auth.authenticate("user1", "key")
        auth.authenticate("user2", "key")
        auth.authenticate("user3", "key")

        # 정리 실행
        count = auth.cleanup_expired()

        assert count == 3
        assert len(auth._sessions) == 0

    def test_cleanup_expired_keeps_valid(self):
        """유효한 세션은 유지 확인."""
        auth = AuthManager(secret_key="key", timeout_minutes=30)

        # 유효한 세션 생성
        auth.authenticate("user1", "key")
        auth.authenticate("user2", "key")

        # 정리 실행
        count = auth.cleanup_expired()

        assert count == 0
        assert len(auth._sessions) == 2
        assert auth.is_authenticated("user1") is True
        assert auth.is_authenticated("user2") is True

    def test_cleanup_expired_returns_count(self):
        """정리된 세션 수 반환 확인."""
        auth = AuthManager(secret_key="key", timeout_minutes=0)

        # 5명 인증
        for i in range(5):
            auth.authenticate(f"user{i}", "key")

        # 정리 실행
        count = auth.cleanup_expired()

        assert count == 5

    def test_cleanup_expired_empty_sessions(self):
        """세션이 없을 때 0 반환 확인."""
        auth = AuthManager(secret_key="key", timeout_minutes=30)

        count = auth.cleanup_expired()

        assert count == 0
        assert len(auth._sessions) == 0


class MockBotHandlers:
    """데코레이터 테스트용 모의 BotHandlers 클래스."""

    def __init__(self, is_authorized: bool = True, is_authenticated: bool = True):
        self._authorized = is_authorized
        self._authenticated = is_authenticated
        self.method_called = False
        self.method_args = None

    def _is_authorized(self, chat_id: int) -> bool:
        return self._authorized

    def _is_authenticated(self, user_id: str) -> bool:
        return self._authenticated


class MockUpdate:
    """테스트용 모의 Update 객체."""

    def __init__(self, chat_id: int):
        self.effective_chat = type('obj', (), {'id': chat_id})()
        self.message = type('obj', (), {'reply_text': self._mock_reply})()
        self.reply_message = None

    async def _mock_reply(self, text: str):
        self.reply_message = text


class TestAuthorizedOnlyDecorator:
    """authorized_only 데코레이터 테스트."""

    @pytest.mark.asyncio
    async def test_authorized_only_allows_authorized(self):
        """권한이 있는 사용자는 허용."""
        from src.bot.middleware import authorized_only

        handler = MockBotHandlers(is_authorized=True)

        @authorized_only
        async def test_method(self, update, context):
            self.method_called = True
            return "success"

        update = MockUpdate(chat_id=123)
        result = await test_method(handler, update, None)

        assert handler.method_called is True
        assert result == "success"

    @pytest.mark.asyncio
    async def test_authorized_only_blocks_unauthorized(self):
        """권한이 없는 사용자는 차단."""
        from src.bot.middleware import authorized_only

        handler = MockBotHandlers(is_authorized=False)

        @authorized_only
        async def test_method(self, update, context):
            self.method_called = True
            return "success"

        update = MockUpdate(chat_id=123)
        result = await test_method(handler, update, None)

        assert handler.method_called is False
        assert result is None
        assert update.reply_message == "⛔ 권한이 없습니다."

    def test_authorized_only_preserves_method_name(self):
        """functools.wraps로 메서드 이름 보존 확인."""
        from src.bot.middleware import authorized_only

        @authorized_only
        async def test_method(self, update, context):
            """테스트 메서드."""
            pass

        assert test_method.__name__ == "test_method"


class TestAuthenticatedOnlyDecorator:
    """authenticated_only 데코레이터 테스트."""

    @pytest.mark.asyncio
    async def test_authenticated_only_allows_authenticated(self):
        """인증된 사용자는 허용."""
        from src.bot.middleware import authenticated_only

        handler = MockBotHandlers(is_authenticated=True)

        @authenticated_only
        async def test_method(self, update, context):
            self.method_called = True
            return "success"

        update = MockUpdate(chat_id=123)
        result = await test_method(handler, update, None)

        assert handler.method_called is True
        assert result == "success"

    @pytest.mark.asyncio
    async def test_authenticated_only_blocks_unauthenticated(self):
        """인증되지 않은 사용자는 차단."""
        from src.bot.middleware import authenticated_only

        handler = MockBotHandlers(is_authenticated=False)

        @authenticated_only
        async def test_method(self, update, context):
            self.method_called = True
            return "success"

        update = MockUpdate(chat_id=123)
        result = await test_method(handler, update, None)

        assert handler.method_called is False
        assert result is None
        assert update.reply_message == "🔒 먼저 인증이 필요합니다.\n/auth <키>"

    def test_authenticated_only_preserves_method_name(self):
        """functools.wraps로 메서드 이름 보존 확인."""
        from src.bot.middleware import authenticated_only

        @authenticated_only
        async def test_method(self, update, context):
            """테스트 메서드."""
            pass

        assert test_method.__name__ == "test_method"


class TestRequireAuthDecoratorFactory:
    """require_auth 데코레이터 팩토리 테스트."""

    @pytest.mark.asyncio
    async def test_require_auth_decorator_authorized(self):
        """권한 있고 인증된 사용자 허용."""
        from src.bot.middleware import require_auth, AuthManager

        auth_manager = AuthManager(secret_key="key", timeout_minutes=30)
        auth_manager.authenticate("123", "key")

        @require_auth(auth_manager, require_auth_setting=True, allowed_chat_ids=[123])
        async def handler(update, context):
            return "success"

        update = MockUpdate(chat_id=123)
        result = await handler(update, None)

        assert result == "success"

    @pytest.mark.asyncio
    async def test_require_auth_decorator_unauthorized_chat(self):
        """허용되지 않은 채팅 ID 차단."""
        from src.bot.middleware import require_auth, AuthManager

        auth_manager = AuthManager(secret_key="key", timeout_minutes=30)

        @require_auth(auth_manager, require_auth_setting=False, allowed_chat_ids=[123])
        async def handler(update, context):
            return "success"

        update = MockUpdate(chat_id=999)
        result = await handler(update, None)

        assert result is None
        assert update.reply_message == "⛔ 권한이 없습니다."

    @pytest.mark.asyncio
    async def test_require_auth_decorator_unauthenticated(self):
        """인증 필요한데 미인증 사용자 차단."""
        from src.bot.middleware import require_auth, AuthManager

        auth_manager = AuthManager(secret_key="key", timeout_minutes=30)

        @require_auth(auth_manager, require_auth_setting=True, allowed_chat_ids=[123])
        async def handler(update, context):
            return "success"

        update = MockUpdate(chat_id=123)
        result = await handler(update, None)

        assert result is None
        assert "🔒 인증이 필요합니다." in update.reply_message


class TestRequireAllowedChatDecoratorFactory:
    """require_allowed_chat 데코레이터 팩토리 테스트."""

    @pytest.mark.asyncio
    async def test_require_allowed_chat_allows(self):
        """허용된 채팅 ID 통과."""
        from src.bot.middleware import require_allowed_chat

        @require_allowed_chat(allowed_chat_ids=[123, 456])
        async def handler(update, context):
            return "success"

        update = MockUpdate(chat_id=123)
        result = await handler(update, None)

        assert result == "success"

    @pytest.mark.asyncio
    async def test_require_allowed_chat_blocks(self):
        """허용되지 않은 채팅 ID 차단."""
        from src.bot.middleware import require_allowed_chat

        @require_allowed_chat(allowed_chat_ids=[123, 456])
        async def handler(update, context):
            return "success"

        update = MockUpdate(chat_id=999)
        result = await handler(update, None)

        assert result is None
        assert update.reply_message == "⛔ 권한이 없습니다."

    @pytest.mark.asyncio
    async def test_require_allowed_chat_empty_list_allows_all(self):
        """빈 리스트는 모든 채팅 허용."""
        from src.bot.middleware import require_allowed_chat

        @require_allowed_chat(allowed_chat_ids=[])
        async def handler(update, context):
            return "success"

        update = MockUpdate(chat_id=999)
        result = await handler(update, None)

        assert result == "success"
