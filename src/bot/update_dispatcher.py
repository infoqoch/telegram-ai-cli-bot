"""Update Dispatcher - Update 유형별 핸들러 라우팅."""

import re
from typing import Callable, Awaitable

from telegram import Update
from telegram.ext import ContextTypes

from src.logging_config import logger
from .update_queue import UpdateType

# TYPE_CHECKING import to avoid circular dependency
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from .handlers import BotHandlers


class UpdateDispatcher:
    """Update를 유형별로 적절한 핸들러에 디스패치."""

    def __init__(self, handlers: "BotHandlers"):
        """초기화.

        Args:
            handlers: BotHandlers 인스턴스
        """
        self._handlers = handlers
        self._command_map: dict[str, Callable[[Update, ContextTypes.DEFAULT_TYPE], Awaitable[None]]] = {}
        self._setup_command_map()

    def _setup_command_map(self) -> None:
        """명령어 매핑 설정."""
        # Basic commands
        self._command_map["start"] = self._handlers.start
        self._command_map["help"] = self._handlers.help_command
        self._command_map["auth"] = self._handlers.auth_command
        self._command_map["status"] = self._handlers.status_command

        # Session commands
        self._command_map["new"] = self._handlers.new_session
        self._command_map["new_opus"] = self._handlers.new_session_opus
        self._command_map["new_sonnet"] = self._handlers.new_session_sonnet
        self._command_map["new_haiku"] = self._handlers.new_session_haiku
        self._command_map["new_haiku_speedy"] = self._handlers.new_session_haiku_speedy
        self._command_map["new_opus_smarty"] = self._handlers.new_session_opus_smarty
        self._command_map["model"] = self._handlers.model_command
        self._command_map["model_opus"] = self._handlers.model_opus_command
        self._command_map["model_sonnet"] = self._handlers.model_sonnet_command
        self._command_map["model_haiku"] = self._handlers.model_haiku_command
        self._command_map["session"] = self._handlers.session_command
        self._command_map["session_list"] = self._handlers.session_list_command
        self._command_map["sl"] = self._handlers.session_list_command
        self._command_map["chatid"] = self._handlers.chatid_command
        self._command_map["lock"] = self._handlers.lock_command

        # Workspace commands
        self._command_map["new_workspace"] = self._handlers.new_workspace_session
        self._command_map["nw"] = self._handlers.new_workspace_session
        self._command_map["workspace"] = self._handlers.workspace_command
        self._command_map["ws"] = self._handlers.workspace_command

        # Admin commands
        self._command_map["jobs"] = self._handlers.jobs_command
        self._command_map["scheduler"] = self._handlers.scheduler_command

        # Plugin commands
        self._command_map["plugins"] = self._handlers.plugins_command

        # AI command
        self._command_map["ai"] = self._handlers.ai_command

        # Rename commands
        self._command_map["rename"] = self._handlers.rename_command

        logger.debug(f"[UpdateDispatcher] Command map 설정 완료 ({len(self._command_map)} commands)")

    async def dispatch(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
        update_type: UpdateType,
    ) -> None:
        """Update를 적절한 핸들러로 디스패치.

        Args:
            update: 텔레그램 Update
            context: 텔레그램 Context
            update_type: Update 유형
        """
        try:
            if update_type == UpdateType.COMMAND:
                await self._dispatch_command(update, context)
            elif update_type == UpdateType.MESSAGE:
                await self._dispatch_message(update, context)
            elif update_type == UpdateType.CALLBACK:
                await self._dispatch_callback(update, context)
            elif update_type == UpdateType.FORCE_REPLY:
                await self._dispatch_force_reply(update, context)
            else:
                logger.warning(f"[UpdateDispatcher] Unknown update type: {update_type}")
        except Exception as e:
            logger.exception(f"[UpdateDispatcher] Dispatch error: {e}")
            # 에러 핸들러 호출
            await self._handlers.error_handler(update, context)

    async def _dispatch_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """명령어 처리."""
        if not update.message or not update.message.text:
            logger.warning("[UpdateDispatcher] Command update but no message text")
            return

        message_text = update.message.text

        # 동적 패턴 명령어 처리 (regex)
        if message_text.startswith("/rename_") or message_text.startswith("/r_"):
            await self._handlers.rename_command(update, context)
            return
        elif message_text.startswith("/s_"):
            await self._handlers.switch_session_command(update, context)
            return
        elif message_text.startswith("/h_") or message_text.startswith("/history_"):
            await self._handlers.history_command(update, context)
            return
        elif message_text.startswith("/d_") or message_text.startswith("/delete_"):
            await self._handlers.delete_session_command(update, context)
            return

        # 명령어 파싱
        command = message_text.split()[0][1:]  # Remove leading '/'

        # 동적 플러그인 명령어 처리
        if self._handlers.plugin_loader and self._handlers.plugin_loader.plugins:
            plugin_names = [p.name for p in self._handlers.plugin_loader.plugins]
            if command in plugin_names:
                await self._handlers.plugin_help_command(update, context)
                return

        # 정적 명령어 매핑
        handler = self._command_map.get(command)
        if handler:
            await handler(update, context)
        else:
            # 알 수 없는 명령어
            await self._handlers.unknown_command(update, context)

    async def _dispatch_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """일반 메시지 처리."""
        await self._handlers.handle_message(update, context)

    async def _dispatch_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """콜백 쿼리 처리."""
        await self._handlers.callback_query_handler(update, context)

    async def _dispatch_force_reply(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """ForceReply 메시지 처리 (메시지 핸들러로 전달)."""
        await self._handlers.handle_message(update, context)
