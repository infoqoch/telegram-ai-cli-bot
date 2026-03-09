"""Execution service for scheduled chat/workspace/plugin jobs."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from src.bot.formatters import escape_html
from src.logging_config import logger
from src.schedule_utils import normalize_schedule_type

if TYPE_CHECKING:
    from src.ai import AIRegistry
    from src.plugins.loader import PluginLoader


class ScheduleExecutionService:
    """Run scheduled jobs and deliver the output to Telegram."""

    def __init__(
        self,
        bot,
        ai_registry: "AIRegistry",
        plugin_loader: "PluginLoader",
        schedule_manager,
        execution_timeout_seconds: float | None = 300,
    ):
        self._bot = bot
        self._ai_registry = ai_registry
        self._plugin_loader = plugin_loader
        self._schedule_manager = schedule_manager
        self._execution_timeout_seconds = execution_timeout_seconds

    async def execute(self, schedule) -> None:
        """Execute one schedule and persist the outcome."""
        try:
            response = await self._run_with_timeout(schedule)
            if self._bot and schedule.chat_id and response:
                await self._send_response(schedule.chat_id, schedule.name, response)

            self._schedule_manager.update_run(schedule.id)
            logger.info(f"Schedule {schedule.id} executed successfully")
        except asyncio.TimeoutError:
            timeout_text = self._format_timeout_error()
            self._schedule_manager.update_run(schedule.id, last_error=timeout_text)
            logger.error(f"Schedule {schedule.id} failed: {timeout_text}")
        except Exception as exc:
            self._schedule_manager.update_run(schedule.id, last_error=str(exc))
            logger.error(f"Schedule {schedule.id} failed: {exc}")

    async def _run_with_timeout(self, schedule) -> str:
        """Execute one schedule response build with a hard timeout."""
        if not self._execution_timeout_seconds:
            return await self._build_response(schedule)
        return await asyncio.wait_for(
            self._build_response(schedule),
            timeout=self._execution_timeout_seconds,
        )

    async def _build_response(self, schedule) -> str:
        """Generate the response body for one scheduled execution."""
        schedule_type = self._resolve_schedule_type(schedule)

        if schedule_type == "plugin" and schedule.plugin_name and schedule.action_name:
            plugin = self._plugin_loader.get_plugin_by_name(schedule.plugin_name)
            if not plugin:
                raise RuntimeError(f"Plugin '{schedule.plugin_name}' not found")
            return await plugin.execute_scheduled_action(schedule.action_name, schedule.chat_id)

        workspace_path = schedule.workspace_path if schedule_type == "workspace" and schedule.workspace_path else None
        provider = self._resolve_provider(schedule)
        client = self._ai_registry.get_client(provider)
        text, error, _ = await client.chat(
            message=schedule.message,
            session_id=None,
            model=schedule.model,
            workspace_path=workspace_path,
        )
        return text or error or "(no response)"

    async def _send_response(self, chat_id: int, schedule_name: str, response: str) -> None:
        """Send a possibly long response with HTML fallback."""
        header_html = f"📅 <b>{escape_html(schedule_name)}</b>\n\n"
        header_plain = f"📅 {schedule_name}\n\n"
        max_len = 4000

        for offset in range(0, len(response), max_len):
            chunk = response[offset:offset + max_len]
            try:
                await self._bot.send_message(
                    chat_id=chat_id,
                    text=f"{header_html}{chunk}",
                    parse_mode="HTML",
                )
            except Exception:
                await self._bot.send_message(
                    chat_id=chat_id,
                    text=f"{header_plain}{chunk}",
                )

    @staticmethod
    def _resolve_schedule_type(schedule) -> str:
        """Resolve schedule type while tolerating MagicMock attributes in tests."""
        schedule_type = getattr(schedule, "schedule_type", None)
        if not isinstance(schedule_type, str) or not schedule_type:
            schedule_type = getattr(schedule, "type", None)
        if not isinstance(schedule_type, str) or not schedule_type:
            schedule_type = "chat"
        return normalize_schedule_type(schedule_type)

    @staticmethod
    def _resolve_provider(schedule) -> str:
        """Resolve provider while tolerating MagicMock attributes in tests."""
        provider = getattr(schedule, "ai_provider", None)
        if not isinstance(provider, str) or provider not in {"claude", "codex"}:
            return "claude"
        return provider

    def _format_timeout_error(self) -> str:
        """Return a stable timeout error string."""
        if self._execution_timeout_seconds:
            return f"Schedule execution timed out after {int(self._execution_timeout_seconds)}s"
        return "Schedule execution timed out"
