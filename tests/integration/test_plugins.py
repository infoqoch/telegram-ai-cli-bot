"""Plugin integration tests.

플러그인 통합 테스트 - 실제 Repository 사용.
"""

import pytest

from tests.integration.conftest import (
    create_message_update,
    get_reply_text,
    MockTelegram,
    wait_for_handlers,
)


class TestMemoPlugin:
    """메모 플러그인 테스트."""

    @pytest.mark.asyncio
    async def test_memo_trigger(self, handlers, repository):
        """메모 키워드로 메인 화면 표시."""
        # 정확한 키워드 "메모"로 트리거
        update, context = create_message_update("메모")

        await handlers.handle_message(update, context)

        reply = await get_reply_text(update)
        # 메모 메인 화면 (저장된 메모 개수 표시)
        assert reply or context.bot.send_message.called

    @pytest.mark.asyncio
    async def test_memo_list(self, handlers, repository):
        """메모 목록 조회."""
        # 먼저 메모 추가
        repository.add_memo(12345, "테스트 메모 1")
        repository.add_memo(12345, "테스트 메모 2")

        # 정확한 키워드 "메모"로 트리거
        update, context = create_message_update("메모")

        await handlers.handle_message(update, context)

        reply = await get_reply_text(update)
        assert reply or context.bot.send_message.called

    @pytest.mark.asyncio
    async def test_memo_command_redirects_to_help_topic(self, handlers):
        """`/memo`는 canonical help topic으로 안내한다."""
        update, context = create_message_update("/memo")
        update.message.text = "/memo"

        await handlers.plugin_help_command(update, context)

        reply = await get_reply_text(update)
        assert "/help_memo" in reply
        assert "/plugins" in reply

    @pytest.mark.asyncio
    async def test_help_memo_command_shows_usage(self, handlers):
        """`/help_memo`는 메모 플러그인 도움말을 보여준다."""
        update, context = create_message_update("/help_memo")
        update.message.text = "/help_memo"

        await handlers.help_topic_command(update, context)

        reply = await get_reply_text(update)
        assert "Memo" in reply or "메모" in reply
        assert "/memo" in reply


class TestTodoPlugin:
    """할일 플러그인 테스트."""

    @pytest.mark.asyncio
    async def test_todo_trigger(self, handlers, repository):
        """할일 키워드로 리스트 표시."""
        # "할일"로 시작하면 트리거됨
        update, context = create_message_update("할일")

        await handlers.handle_message(update, context)

        reply = await get_reply_text(update)
        assert reply or context.bot.send_message.called

    @pytest.mark.asyncio
    async def test_todo_trigger_with_text(self, handlers, repository):
        """할일 키워드 + 추가 텍스트."""
        # "할일"로 시작하면 트리거됨
        update, context = create_message_update("할일 보여줘")

        await handlers.handle_message(update, context)

        reply = await get_reply_text(update)
        assert reply or context.bot.send_message.called

    @pytest.mark.asyncio
    async def test_todo_english_trigger(self, handlers, repository):
        """todo 키워드 트리거."""
        update, context = create_message_update("todo")

        await handlers.handle_message(update, context)

        reply = await get_reply_text(update)
        assert reply or context.bot.send_message.called

    @pytest.mark.asyncio
    async def test_todo_list_with_items(self, handlers, repository):
        """할일이 있을 때 리스트 표시."""
        from datetime import date
        repository.add_todo(
            chat_id=12345,
            date=date.today().isoformat(),
            text="테스트 할일"
        )

        update, context = create_message_update("할일")

        await handlers.handle_message(update, context)

        reply = await get_reply_text(update)
        assert reply or context.bot.send_message.called


class TestWeatherPlugin:
    """날씨 플러그인 테스트."""

    @pytest.mark.asyncio
    async def test_weather_trigger(self, handlers, repository):
        """날씨 키워드로 트리거."""
        # "날씨"만 입력 (exclude pattern인 "알려줘"는 제외)
        update, context = create_message_update("날씨")

        await handlers.handle_message(update, context)

        reply = await get_reply_text(update)
        # 날씨 정보 또는 도시 선택 화면
        assert reply or context.bot.send_message.called

    @pytest.mark.asyncio
    async def test_weather_city_query_not_handled(self, handlers, mock_claude):
        """'도시+날씨' 자연어 패턴은 플러그인이 처리하지 않음 (AI로 넘김)."""
        update, context = create_message_update("경주 날씨")

        await handlers.handle_message(update, context)
        await wait_for_handlers(handlers)

        assert handlers._spawn_detached_worker.called


class TestPluginExcludePatterns:
    """플러그인 제외 패턴 테스트.

    "X란 뭐야" 같은 질문은 플러그인이 아닌 Claude가 처리해야 함.
    """

    @pytest.mark.asyncio
    async def test_memo_question_goes_to_claude(self, handlers, mock_claude):
        """메모란 뭐야 → Claude 처리."""
        update, context = create_message_update("메모란 뭐야?")

        await handlers.handle_message(update, context)
        await wait_for_handlers(handlers)

        assert handlers._spawn_detached_worker.called

    @pytest.mark.asyncio
    async def test_todo_translation_goes_to_claude(self, handlers, mock_claude):
        """할일 영어로 번역 → Claude 처리."""
        update, context = create_message_update("할일을 영어로 번역해줘")

        await handlers.handle_message(update, context)
        await wait_for_handlers(handlers)

        assert handlers._spawn_detached_worker.called


class TestPluginLoader:
    """플러그인 로더 테스트."""

    def test_builtin_plugins_loaded(self, plugin_loader):
        """내장 플러그인 로드 확인."""
        plugin_names = [p.name for p in plugin_loader.plugins]

        # builtin 플러그인들이 로드되어야 함
        assert len(plugin_names) >= 1  # 최소 1개 이상

    def test_plugin_has_required_attributes(self, plugin_loader):
        """플러그인 필수 속성 확인."""
        for plugin in plugin_loader.plugins:
            assert hasattr(plugin, "name")
            assert hasattr(plugin, "description")
            assert hasattr(plugin, "can_handle")
            assert hasattr(plugin, "handle")
