"""Execution service for scheduled chat/workspace/plugin jobs."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Optional

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from src.ai import normalize_model
from src.bot.formatters import escape_html, markdown_to_telegram_html, split_message
from src.logging_config import logger
from src.network_guard import CircuitOpen, NetworkUnavailable, network_guard
from src.schedule_utils import resolve_provider, resolve_schedule_type
from src.services.command_execution_service import CommandExecutionService

if TYPE_CHECKING:
    from src.ai import AIRegistry
    from src.plugins.loader import PluginLoader
    from src.repository import Repository


@dataclass(frozen=True)
class _ScheduleRunResult:
    """Normalized schedule execution output before Telegram delivery."""

    response: Optional[str]
    provider_session_id: Optional[str] = None
    is_ai: bool = False
    response_is_html: bool = False
    reply_markup: Optional[InlineKeyboardMarkup] = None
    run_status: Optional[str] = None
    run_summary: Optional[str] = None
    run_error: Optional[str] = None


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
        self._command_runner = CommandExecutionService(default_cwd=self._project_root())

    async def execute(self, schedule) -> None:
        """Execute one schedule and persist the outcome."""
        started_at = self._now()
        run_recorded = False
        schedule_type = resolve_schedule_type(schedule)
        result_type = self._schedule_result_type(schedule_type)
        log_id = None
        try:
            run_result = await self._run(schedule)
            response = run_result.response
            result_type = self._schedule_result_type(schedule_type, run_result)

            # None = intentional silence (e.g., reminder with no upcoming events)
            if response is None:
                self._schedule_manager.update_run(schedule.id)
                run_status = run_result.run_status or "no_output"
                self._record_schedule_run(
                    schedule.id,
                    started_at=started_at,
                    status=run_status,
                    result_type=result_type if run_result.run_status else "none",
                    error=run_result.run_error,
                    summary=run_result.run_summary or ("no notification needed" if run_status == "no_output" else None),
                )
                run_recorded = True
                logger.info(f"Schedule {schedule.id} executed (no notification needed)")
                return

            if self._bot and schedule.chat_id and not response:
                logger.warning(
                    f"Schedule {schedule.id} ({schedule.name}) returned empty response, sending fallback"
                )
                response = "(No response content)"

            if self._bot and schedule.chat_id and response:
                reply_markup = run_result.reply_markup
                delivery_markup_json = self._serialize_reply_markup(reply_markup)
                delivery_text = self._build_delivery_text(
                    schedule.name,
                    response,
                    response_is_html=run_result.response_is_html,
                )
                if self._repo:
                    provider = resolve_provider(schedule) if run_result.is_ai else None
                    model = (
                        self._resolve_schedule_model(provider, schedule)
                        if provider
                        else schedule_type
                    )
                    log_id = self._repo.insert_schedule_delivery_log(
                        chat_id=schedule.chat_id,
                        schedule_id=schedule.id,
                        request=self._schedule_request_text(schedule),
                        response=response,
                        delivery_text=delivery_text,
                        model=model,
                        workspace_path=getattr(schedule, "workspace_path", None),
                        provider_session_id=run_result.provider_session_id,
                        delivery_markup_json=delivery_markup_json,
                    )
                    if run_result.is_ai:
                        reply_markup = self._build_session_button(log_id)
                        delivery_markup_json = self._serialize_reply_markup(reply_markup)
                        self._repo.set_message_delivery_markup(log_id, json.loads(delivery_markup_json))

                try:
                    await self._send_delivery_text(
                        schedule.chat_id,
                        delivery_text,
                        reply_markup=reply_markup,
                        log_id=log_id,
                    )
                except Exception as exc:
                    if self._repo and log_id:
                        self._repo.mark_message_delivery_failed(log_id, str(exc))
                    self._record_schedule_run(
                        schedule.id,
                        started_at=started_at,
                        status="delivery_failed",
                        result_type=result_type,
                        message_log_id=log_id,
                        error=str(exc),
                    )
                    run_recorded = True
                    raise

                if self._repo and log_id:
                    self._repo.mark_message_delivered(log_id)

            self._schedule_manager.update_run(schedule.id)
            self._record_schedule_run(
                schedule.id,
                started_at=started_at,
                status=run_result.run_status or "success",
                result_type=result_type,
                message_log_id=log_id,
                error=run_result.run_error,
                summary=run_result.run_summary,
            )
            run_recorded = True
            logger.info(f"Schedule {schedule.id} executed successfully")
        except Exception as exc:
            self._schedule_manager.update_run(schedule.id, last_error=str(exc))
            if not run_recorded:
                self._record_schedule_run(
                    schedule.id,
                    started_at=started_at,
                    status="failed",
                    result_type=result_type,
                    message_log_id=log_id,
                    error=str(exc),
                )
            logger.error(f"Schedule {schedule.id} failed: {exc}")

    async def _run(self, schedule) -> _ScheduleRunResult:
        """Execute one schedule response build."""
        return await self._build_response(schedule)

    async def _build_response(self, schedule) -> _ScheduleRunResult:
        """Generate the response body for one scheduled execution.

        Returns a normalized result. response=None means intentional silence.
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
                return _ScheduleRunResult(
                    response=result.get("text", ""),
                    response_is_html=True,
                    reply_markup=result.get("reply_markup"),
                    run_status=self._normalize_run_status(result.get("run_status")),
                    run_summary=self._optional_string(result.get("summary")),
                    run_error=self._optional_string(result.get("error")),
                )
            return _ScheduleRunResult(response=result)

        if schedule_type == "command":
            result = await self._command_runner.run(
                schedule.message,
                cwd=getattr(schedule, "workspace_path", None) or self._project_root(),
            )
            return _ScheduleRunResult(
                response=self._command_runner.build_telegram_body(result),
                response_is_html=True,
            )

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
        return _ScheduleRunResult(
            response=text or error or "(no response)",
            provider_session_id=provider_session_id,
            is_ai=True,
        )

    @staticmethod
    def _resolve_schedule_model(provider: str, schedule) -> str:
        """Return a provider-compatible model key for a persisted schedule."""
        model = getattr(schedule, "model", None)
        return normalize_model(provider, model if isinstance(model, str) and model else None)

    @staticmethod
    def _now() -> str:
        """Return current UTC timestamp."""
        return datetime.now(timezone.utc).isoformat()

    @staticmethod
    def _normalize_run_status(value) -> Optional[str]:
        """Return a supported plugin-provided run status."""
        if not isinstance(value, str):
            return None
        status = value.strip().lower()
        return status if status in {"success", "no_output", "warning", "delivery_failed", "failed"} else None

    @staticmethod
    def _optional_string(value) -> Optional[str]:
        """Return a non-empty string or None."""
        return value if isinstance(value, str) and value.strip() else None

    @staticmethod
    def _schedule_result_type(schedule_type: str, run_result: Optional[_ScheduleRunResult] = None) -> str:
        """Return the schedule run result category."""
        if run_result and run_result.is_ai:
            return "ai"
        if schedule_type == "plugin":
            return "plugin"
        if schedule_type == "command":
            return "command"
        return "message"

    def _record_schedule_run(
        self,
        schedule_id: str,
        *,
        started_at: str,
        status: str,
        result_type: str,
        message_log_id: Optional[int] = None,
        error: Optional[str] = None,
        summary: Optional[str] = None,
    ) -> None:
        """Persist one schedule run without affecting execution behavior."""
        if not self._repo or not hasattr(self._repo, "insert_schedule_run"):
            return
        try:
            self._repo.insert_schedule_run(
                schedule_id=schedule_id,
                started_at=started_at,
                finished_at=self._now(),
                status=status,
                result_type=result_type,
                message_log_id=message_log_id,
                error=error,
                summary=summary,
            )
        except Exception as exc:
            logger.warning(f"Schedule run record failed: schedule_id={schedule_id}, error={exc}")

    def _build_delivery_text(
        self,
        schedule_name: str,
        response: str,
        *,
        response_is_html: bool = False,
    ) -> str:
        """Build the exact HTML body to persist and deliver."""
        header_html = f"⏰ <b>{escape_html(schedule_name)}</b>\n\n"
        response_html = response if response_is_html else markdown_to_telegram_html(response)
        return f"{header_html}{response_html}"

    async def _send_delivery_text(
        self,
        chat_id: int,
        delivery_text: str,
        *,
        reply_markup: Optional[InlineKeyboardMarkup] = None,
        log_id: Optional[int] = None,
    ) -> None:
        """Send a persisted delivery body with HTML fallback."""
        chunks = split_message(delivery_text)

        for i, chunk in enumerate(chunks):
            is_last = i == len(chunks) - 1
            chunk_markup = reply_markup if is_last else None
            try:
                await self._send_telegram_with_attempt_count(
                    log_id=log_id,
                    chat_id=chat_id,
                    text=chunk,
                    parse_mode="HTML",
                    reply_markup=chunk_markup,
                )
            except Exception as exc:
                if isinstance(exc, NetworkUnavailable):
                    raise
                await self._send_telegram_with_attempt_count(
                    log_id=log_id,
                    chat_id=chat_id,
                    text=chunk,
                    reply_markup=chunk_markup,
                )

    async def _send_telegram_with_attempt_count(
        self,
        *,
        log_id: Optional[int],
        **kwargs,
    ) -> None:
        """Send one Telegram message and count actual delivery attempts."""
        try:
            await network_guard.run_async("telegram", self._bot.send_message, **kwargs)
        except CircuitOpen:
            raise
        except Exception:
            if self._repo and log_id:
                self._repo.increment_delivery_attempts(log_id)
            raise
        else:
            if self._repo and log_id:
                self._repo.increment_delivery_attempts(log_id)

    @staticmethod
    def _project_root() -> str:
        return str(Path(__file__).resolve().parents[2])

    @staticmethod
    def _build_session_button(log_id: int) -> InlineKeyboardMarkup:
        """Build inline button to create a session from this schedule result."""
        return InlineKeyboardMarkup([[
            InlineKeyboardButton("💬 Session", callback_data=f"resp:sched:{log_id}"),
        ]])

    @staticmethod
    def _schedule_request_text(schedule) -> str:
        """Return a safe persisted request label for one schedule run."""
        message = getattr(schedule, "message", "")
        return message if isinstance(message, str) else ""

    @staticmethod
    def _serialize_reply_markup(reply_markup: Optional[InlineKeyboardMarkup]) -> Optional[str]:
        """Serialize retry-safe inline buttons from Telegram markup."""
        if not reply_markup:
            return None
        rows: list[list[dict[str, str]]] = []
        for row in reply_markup.inline_keyboard:
            serialized_row: list[dict[str, str]] = []
            for button in row:
                item = {"text": button.text}
                if button.callback_data:
                    item["callback_data"] = button.callback_data
                elif button.url:
                    item["url"] = button.url
                else:
                    continue
                serialized_row.append(item)
            if serialized_row:
                rows.append(serialized_row)
        return json.dumps(rows, ensure_ascii=False) if rows else None
