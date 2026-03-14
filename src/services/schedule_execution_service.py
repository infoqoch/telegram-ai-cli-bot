"""Execution service for scheduled chat/workspace/plugin jobs."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Optional

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from src.bot.formatters import escape_html
from src.logging_config import logger
from src.schedule_utils import normalize_schedule_type, resolve_provider, resolve_schedule_type

if TYPE_CHECKING:
    from src.ai import AIRegistry
    from src.plugins.loader import PluginLoader
    from src.repository import Repository


class ScheduleExecutionService:
    """Run scheduled jobs and deliver the output to Telegram."""

    def __init__(
        self,
        bot,
        ai_registry: "AIRegistry",
        plugin_loader: "PluginLoader",
        schedule_manager,
        repo: Optional["Repository"] = None,
        execution_timeout_seconds: float | None = 300,
    ):
        self._bot = bot
        self._ai_registry = ai_registry
        self._plugin_loader = plugin_loader
        self._schedule_manager = schedule_manager
        self._repo = repo
        self._execution_timeout_seconds = execution_timeout_seconds

    async def execute(self, schedule) -> None:
        """Execute one schedule and persist the outcome."""
        try:
            result = await self._run_with_timeout(schedule)
            response = result[0] if isinstance(result, tuple) else result
            provider_session_id = result[1] if isinstance(result, tuple) else None
            is_ai = isinstance(result, tuple)

            if self._bot and schedule.chat_id and not response:
                logger.warning(
                    f"Schedule {schedule.id} ({schedule.name}) returned empty response, sending fallback"
                )
                response = "(응답 내용 없음)"

            if self._bot and schedule.chat_id and response:
                log_id = None
                if is_ai and self._repo:
                    log_id = self._repo.insert_schedule_message_log(
                        chat_id=schedule.chat_id,
                        schedule_id=schedule.id,
                        request=schedule.message,
                        response=response,
                        model=getattr(schedule, "model", "sonnet"),
                        workspace_path=getattr(schedule, "workspace_path", None),
                        provider_session_id=provider_session_id,
                    )
                reply_markup = self._build_session_button(log_id) if log_id else None
                await self._send_response(schedule.chat_id, schedule.name, response, reply_markup=reply_markup)

            self._schedule_manager.update_run(schedule.id)
            logger.info(f"Schedule {schedule.id} executed successfully")
        except asyncio.TimeoutError:
            timeout_text = self._format_timeout_error()
            self._schedule_manager.update_run(schedule.id, last_error=timeout_text)
            logger.error(f"Schedule {schedule.id} failed: {timeout_text}")
        except Exception as exc:
            self._schedule_manager.update_run(schedule.id, last_error=str(exc))
            logger.error(f"Schedule {schedule.id} failed: {exc}")

    async def _run_with_timeout(self, schedule) -> str | tuple[str, Optional[str]]:
        """Execute one schedule response build with a hard timeout."""
        if not self._execution_timeout_seconds:
            return await self._build_response(schedule)
        return await asyncio.wait_for(
            self._build_response(schedule),
            timeout=self._execution_timeout_seconds,
        )

    async def _build_response(self, schedule) -> str | tuple[str, Optional[str]]:
        """Generate the response body for one scheduled execution.

        Returns str for plugin schedules, tuple(response, provider_session_id) for AI schedules.
        """
        schedule_type = resolve_schedule_type(schedule)

        if schedule_type == "plugin" and schedule.plugin_name and schedule.action_name:
            plugin = self._plugin_loader.get_plugin_by_name(schedule.plugin_name)
            if not plugin:
                raise RuntimeError(f"Plugin '{schedule.plugin_name}' not found")
            return await plugin.execute_scheduled_action(schedule.action_name, schedule.chat_id)

        workspace_path = schedule.workspace_path if schedule_type == "workspace" and schedule.workspace_path else None
        provider = resolve_provider(schedule)
        client = self._ai_registry.get_client(provider)
        text, error, provider_session_id = await client.chat(
            message=schedule.message,
            session_id=None,
            model=schedule.model,
            workspace_path=workspace_path,
        )
        return (text or error or "(no response)", provider_session_id)

    async def _send_response(
        self,
        chat_id: int,
        schedule_name: str,
        response: str,
        *,
        reply_markup: Optional[InlineKeyboardMarkup] = None,
    ) -> None:
        """Send a possibly long response with HTML fallback."""
        header_html = f"📅 <b>{escape_html(schedule_name)}</b>\n\n"
        header_plain = f"📅 {schedule_name}\n\n"
        max_len = 4000
        chunks = [response[offset:offset + max_len] for offset in range(0, len(response), max_len)]

        for i, chunk in enumerate(chunks):
            is_last = i == len(chunks) - 1
            chunk_markup = reply_markup if is_last else None
            try:
                await self._bot.send_message(
                    chat_id=chat_id,
                    text=f"{header_html}{chunk}",
                    parse_mode="HTML",
                    reply_markup=chunk_markup,
                )
            except Exception:
                await self._bot.send_message(
                    chat_id=chat_id,
                    text=f"{header_plain}{chunk}",
                    reply_markup=chunk_markup,
                )

    @staticmethod
    def _build_session_button(log_id: int) -> InlineKeyboardMarkup:
        """Build inline button to create a session from this schedule result."""
        return InlineKeyboardMarkup([[
            InlineKeyboardButton("💬 Session", callback_data=f"resp:sched:{log_id}"),
        ]])

    def _format_timeout_error(self) -> str:
        """Return a stable timeout error string."""
        if self._execution_timeout_seconds:
            return f"Schedule execution timed out after {int(self._execution_timeout_seconds)}s"
        return "Schedule execution timed out"
