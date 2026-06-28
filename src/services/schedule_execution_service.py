"""Execution service for scheduled chat/workspace/plugin jobs."""

from __future__ import annotations

from typing import TYPE_CHECKING, Optional

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from src.ai import normalize_model
from src.bot.formatters import escape_html
from src.logging_config import logger
from src.network_guard import NetworkUnavailable, network_guard
from src.schedule_utils import resolve_provider, resolve_schedule_type

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
    ):
        self._bot = bot
        self._ai_registry = ai_registry
        self._plugin_loader = plugin_loader
        self._schedule_manager = schedule_manager
        self._repo = repo

    async def execute(self, schedule) -> None:
        """Execute one schedule and persist the outcome."""
        try:
            result = await self._run(schedule)
            response = result[0] if isinstance(result, tuple) else result
            provider_session_id = result[1] if isinstance(result, tuple) else None
            is_ai = isinstance(result, tuple)

            # Plugin rich responses are already sent directly
            if response == "__plugin_rich_sent__":
                self._schedule_manager.update_run(schedule.id)
                logger.info(f"Schedule {schedule.id} executed successfully (plugin rich response)")
                return

            # None = intentional silence (e.g., reminder with no upcoming events)
            if response is None:
                self._schedule_manager.update_run(schedule.id)
                logger.info(f"Schedule {schedule.id} executed (no notification needed)")
                return

            if self._bot and schedule.chat_id and not response:
                logger.warning(
                    f"Schedule {schedule.id} ({schedule.name}) returned empty response, sending fallback"
                )
                response = "(No response content)"

            if self._bot and schedule.chat_id and response:
                log_id = None
                if is_ai and self._repo:
                    provider = resolve_provider(schedule)
                    model = self._resolve_schedule_model(provider, schedule)
                    log_id = self._repo.insert_schedule_message_log(
                        chat_id=schedule.chat_id,
                        schedule_id=schedule.id,
                        request=schedule.message,
                        response=response,
                        model=model,
                        workspace_path=getattr(schedule, "workspace_path", None),
                        provider_session_id=provider_session_id,
                    )
                reply_markup = self._build_session_button(log_id) if log_id else None
                await self._send_response(
                    schedule.chat_id,
                    schedule.name,
                    response,
                    reply_markup=reply_markup,
                )

            self._schedule_manager.update_run(schedule.id)
            logger.info(f"Schedule {schedule.id} executed successfully")
        except Exception as exc:
            self._schedule_manager.update_run(schedule.id, last_error=str(exc))
            logger.error(f"Schedule {schedule.id} failed: {exc}")

    async def _run(self, schedule) -> str | tuple[str, Optional[str]]:
        """Execute one schedule response build."""
        return await self._build_response(schedule)

    async def _build_response(self, schedule) -> str | tuple[str, Optional[str]]:
        """Generate the response body for one scheduled execution.

        Returns str for plugin schedules, tuple(response, provider_session_id) for AI schedules.
        """
        schedule_type = resolve_schedule_type(schedule)

        if schedule_type == "plugin" and schedule.plugin_name and schedule.action_name:
            plugin = self._plugin_loader.get_plugin_by_name(schedule.plugin_name)
            if not plugin:
                raise RuntimeError(f"Plugin '{schedule.plugin_name}' not found")
            result = await plugin.execute_scheduled_action(
                schedule.action_name,
                schedule.chat_id,
                schedule=schedule,
            )
            if isinstance(result, dict):
                # Plugin returned rich response (text + reply_markup)
                await self._send_plugin_rich_response(schedule.chat_id, schedule.name, result)
                return "__plugin_rich_sent__"
            return result

        if schedule_type == "command":
            import asyncio
            try:
                # message 필드에 명령어/스크립트 경로가 저장되어 있다고 가정
                process = await asyncio.create_subprocess_shell(
                    schedule.message,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE
                )
                stdout, stderr = await process.communicate()
                out_text = stdout.decode('utf-8').strip()
                err_text = stderr.decode('utf-8').strip()
                
                # 출력이 없으면 조용히 종료할 수 있도록 None 반환 (또는 설정에 따라 빈 문자열)
                if not out_text and not err_text:
                    return None
                    
                result = out_text
                if err_text:
                    result += f"\n\n[Errors]\n{err_text}"
                return result
            except Exception as e:
                return f"Command execution failed: {e}"

        workspace_path = (
            schedule.workspace_path
            if schedule_type == "workspace" and schedule.workspace_path
            else None
        )
        provider = resolve_provider(schedule)
        model = self._resolve_schedule_model(provider, schedule)
        client = self._ai_registry.get_client(provider)
        text, error, provider_session_id = await client.chat(
            message=schedule.message,
            session_id=None,
            model=model,
            workspace_path=workspace_path,
        )
        return (text or error or "(no response)", provider_session_id)

    @staticmethod
    def _resolve_schedule_model(provider: str, schedule) -> str:
        """Return a provider-compatible model key for a persisted schedule."""
        model = getattr(schedule, "model", None)
        return normalize_model(provider, model if isinstance(model, str) and model else None)

    async def _send_response(
        self,
        chat_id: int,
        schedule_name: str,
        response: str,
        *,
        reply_markup: Optional[InlineKeyboardMarkup] = None,
    ) -> None:
        """Send a possibly long response with HTML fallback."""
        header_html = f"⏰ <b>{escape_html(schedule_name)}</b>\n\n"
        header_plain = f"⏰ {schedule_name}\n\n"
        
        from src.bot.formatters import markdown_to_telegram_html
        response_html = markdown_to_telegram_html(response)
        
        max_len = 4000
        chunks = [response_html[offset:offset + max_len] for offset in range(0, len(response_html), max_len)]

        for i, chunk in enumerate(chunks):
            is_last = i == len(chunks) - 1
            chunk_markup = reply_markup if is_last else None
            try:
                await network_guard.run_async(
                    "telegram",
                    self._bot.send_message,
                    chat_id=chat_id,
                    text=f"{header_html}{chunk}",
                    parse_mode="HTML",
                    reply_markup=chunk_markup,
                )
            except Exception as exc:
                if isinstance(exc, NetworkUnavailable):
                    raise
                await network_guard.run_async(
                    "telegram",
                    self._bot.send_message,
                    chat_id=chat_id,
                    text=f"{header_plain}{chunk}",
                    reply_markup=chunk_markup,
                )

    async def _send_plugin_rich_response(
        self,
        chat_id: int,
        schedule_name: str,
        result: dict,
    ) -> None:
        """Send a plugin rich response (text + reply_markup) with schedule header."""
        header = f"⏰ <b>{escape_html(schedule_name)}</b>\n\n"
        raw_text = result.get("text", "")
        from src.bot.formatters import markdown_to_telegram_html
        text = markdown_to_telegram_html(raw_text)
        reply_markup = result.get("reply_markup")

        try:
            await network_guard.run_async(
                "telegram",
                self._bot.send_message,
                chat_id=chat_id,
                text=f"{header}{text}",
                parse_mode="HTML",
                reply_markup=reply_markup,
            )
        except Exception as exc:
            if isinstance(exc, NetworkUnavailable):
                raise
            await network_guard.run_async(
                "telegram",
                self._bot.send_message,
                chat_id=chat_id,
                text=f"⏰ {schedule_name}\n\n{text}",
                reply_markup=reply_markup,
            )

    @staticmethod
    def _build_session_button(log_id: int) -> InlineKeyboardMarkup:
        """Build inline button to create a session from this schedule result."""
        return InlineKeyboardMarkup([[
            InlineKeyboardButton("💬 Session", callback_data=f"resp:sched:{log_id}"),
        ]])
