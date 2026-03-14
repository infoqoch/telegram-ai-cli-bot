"""Shared test helpers for callback/handler tests."""

from unittest.mock import AsyncMock, MagicMock


def make_query():
    """재사용 가능한 query mock."""
    query = MagicMock()
    query.answer = AsyncMock()
    query.edit_message_text = AsyncMock()
    query.from_user = MagicMock()
    query.from_user.id = 12345
    query.message = MagicMock()
    query.message.reply_text = AsyncMock(return_value=MagicMock(message_id=987))
    query.message.chat_id = 12345
    query.get_bot = MagicMock(return_value=MagicMock())
    return query


def get_text(query):
    """edit_message_text의 첫 인자."""
    call = query.edit_message_text.call_args
    if not call:
        return ""
    return call[0][0] if call[0] else call[1].get("text", "")


def get_buttons(query):
    """edit_message_text의 버튼 텍스트 목록."""
    call = query.edit_message_text.call_args
    if not call:
        return []
    markup = call[1].get("reply_markup")
    if not markup:
        return []
    return [btn.text for row in markup.inline_keyboard for btn in row]


def get_callback_data(query):
    """edit_message_text의 callback_data 목록."""
    call = query.edit_message_text.call_args
    if not call:
        return []
    markup = call[1].get("reply_markup")
    if not markup:
        return []
    return [btn.callback_data for row in markup.inline_keyboard for btn in row]


def make_handlers(**overrides):
    """BotHandlers mock 생성."""
    from src.bot.handlers import BotHandlers

    h = BotHandlers(
        session_service=overrides.get("session_service", MagicMock()),
        claude_client=overrides.get("claude_client", MagicMock()),
        auth_manager=overrides.get("auth_manager", MagicMock()),
        require_auth=False,
        allowed_chat_ids=[],
    )
    return h
