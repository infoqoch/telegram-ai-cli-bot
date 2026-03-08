"""Execution service for scheduled AI/plugin jobs."""

from __future__ import annotations

from typing import TYPE_CHECKING

from src.logging_config import logger

if TYPE_CHECKING:
    from src.ai import AIRegistry
    from src.plugins.loader import PluginLoader


class ScheduleExecutionService:
    """Run scheduled jobs and deliver results to Telegram."""

    def __init__(self, bot, ai_registry: "AIRegistry", plugin_loader: "PluginLoader", schedule_manager):
        self._bot = bot
        self._ai_registry = ai_registry
        self._plugin_loader = plugin_loader
        self._schedule_manager = schedule_manager

    async def execute(self, schedule) -> None:
        """Execute one scheduled task and persist the run result."""
        try:
            response = await self._build_response(schedule)
            if self._bot and schedule.chat_id and response:
                await self._send_response(schedule.chat_id, schedule.name, response)

            self._schedule_manager.update_run(schedule.id)
            logger.info(f"Schedule {schedule.id} executed successfully")
        except Exception as exc:
            self._schedule_manager.update_run(schedule.id, last_error=str(exc))
            logger.error(f"Schedule {schedule.id} failed: {exc}")

    async def _build_response(self, schedule) -> str:
        """Build the text response for one schedule execution."""
        if schedule.type == "plugin" and schedule.plugin_name and schedule.action_name:
            plugin = self._plugin_loader.get_plugin_by_name(schedule.plugin_name)
            if not plugin:
                raise RuntimeError(f"Plugin '{schedule.plugin_name}' not found")

            return await plugin.execute_scheduled_action(schedule.action_name, schedule.chat_id)

        workspace_path = schedule.workspace_path if schedule.type == "workspace" and schedule.workspace_path else None
        client = self._ai_registry.get_client(schedule.ai_provider or "claude")
        text, error, _ = await client.chat(
            message=schedule.message,
            session_id=None,
            model=schedule.model,
            workspace_path=workspace_path,
        )
        return text or error or "(no response)"

    async def _send_response(self, chat_id: int, schedule_name: str, response: str) -> None:
        """Send a schedule response with HTML fallback and chunking."""
        max_len = 4000
        for index in range(0, len(response), max_len):
            chunk = response[index:index + max_len]
            try:
                await self._bot.send_message(
                    chat_id=chat_id,
                    text=f"📅 <b>{schedule_name}</b>\n\n{chunk}",
                    parse_mode="HTML",
                )
            except Exception:
                await self._bot.send_message(
                    chat_id=chat_id,
                    text=f"📅 {schedule_name}\n\n{chunk}",
                )
