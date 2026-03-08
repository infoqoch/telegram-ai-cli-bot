"""Detached provider job execution service."""

import asyncio
import html
import os
import time
from typing import Optional

from telegram import Bot

from src.ai import AIRegistry, get_profile_label, get_provider_label
from src.bot.constants import LONG_TASK_THRESHOLD_SECONDS, TASK_TIMEOUT_SECONDS
from src.bot.formatters import truncate_message
from src.logging_config import clear_context, logger, set_session_id, set_trace_id, set_user_id
from src.repository import Repository
from src.services.session_service import SessionService


class JobService:
    """Run detached provider jobs and deliver responses directly to Telegram."""

    def __init__(
        self,
        repo: Repository,
        session_service: SessionService,
        telegram_token: str,
        ai_registry: Optional[AIRegistry] = None,
        claude_client=None,
    ):
        self._repo = repo
        self._sessions = session_service
        if ai_registry is None:
            if claude_client is None:
                raise ValueError("Either ai_registry or claude_client must be provided")
            ai_registry = AIRegistry({"claude": claude_client})
        self._ai_registry = ai_registry
        self._telegram_token = telegram_token

    async def run_job(self, job_id: int) -> bool:
        """Run one detached job and drain the persistent queue for the same session."""
        job = self._repo.get_message_log(job_id)
        if not job or job["processed"] == 2:
            logger.warning(f"Detached job missing or already completed: id={job_id}")
            return False

        session_id = job["session_id"]
        worker_pid = os.getpid()
        active_lock_job_id = job_id
        request_preview = truncate_message(job["request"], 60)

        logger.info(
            f"Detached job loaded - job_id={job_id}, session={session_id[:8]}, "
            f"processed={job['processed']}, worker_pid={worker_pid}, request={request_preview!r}"
        )

        if not self._attach_or_acquire_lock(session_id, job_id, worker_pid):
            logger.warning(f"Detached job lock attach failed: job={job_id}, session={session_id[:8]}")
            return False

        logger.info(
            f"Detached job lock ready - job_id={job_id}, session={session_id[:8]}, worker_pid={worker_pid}"
        )

        bot = Bot(token=self._telegram_token)

        try:
            current_job = job

            while current_job:
                if current_job["processed"] == 0 and not self._repo.claim_pending_message(current_job["id"]):
                    logger.warning(f"Detached job claim failed: id={current_job['id']}")
                    break
                if current_job["processed"] == 0:
                    logger.info(
                        f"Detached job claimed - job_id={current_job['id']}, "
                        f"session={current_job['session_id'][:8]}"
                    )

                await self._execute_job(bot, current_job)

                next_queued = self._repo.pop_next_queued_message(session_id)
                if not next_queued:
                    logger.info(f"Detached worker queue drained - job_id={current_job['id']}, session={session_id[:8]}")
                    current_job = None
                    continue

                next_job_id = self._repo.enqueue_message(
                    chat_id=next_queued["chat_id"],
                    session_id=next_queued["session_id"],
                    request=next_queued["message"],
                    model=next_queued["model"],
                    workspace_path=next_queued.get("workspace_path"),
                )
                previous_job_id = current_job["id"]
                if not self._repo.rebind_session_lock(session_id, previous_job_id, next_job_id, worker_pid):
                    logger.error(
                        f"Detached worker lock rebind failed: previous={previous_job_id}, "
                        f"next={next_job_id}, session={session_id[:8]}, worker_pid={worker_pid}"
                    )
                    self._repo.complete_message(next_job_id, error="lock_rebind_failed")
                    current_job = None
                    continue

                active_lock_job_id = next_job_id
                current_job = self._repo.get_message_log(next_job_id)
                logger.info(
                    f"Detached worker continuing queued job: previous={previous_job_id}, next={next_job_id}, "
                    f"session={session_id[:8]}, request={truncate_message(next_queued['message'], 60)!r}"
                )

            return True
        finally:
            logger.info(f"Detached job releasing lock - job_id={active_lock_job_id}, session={session_id[:8]}")
            self._repo.release_session_lock(session_id, active_lock_job_id)
            clear_context()

    def _attach_or_acquire_lock(self, session_id: str, job_id: int, worker_pid: int) -> bool:
        """Attach this worker to a reserved lock, or acquire it directly as fallback."""
        if self._repo.attach_worker_to_session_lock(session_id, job_id, worker_pid):
            logger.info(
                f"Detached lock attached to reserved slot - job_id={job_id}, "
                f"session={session_id[:8]}, worker_pid={worker_pid}"
            )
            return True

        existing = self._repo.get_session_lock(session_id)
        if existing and existing["job_id"] == job_id and existing.get("worker_pid") == worker_pid:
            logger.info(
                f"Detached lock already attached - job_id={job_id}, "
                f"session={session_id[:8]}, worker_pid={worker_pid}"
            )
            return True

        if existing:
            logger.warning(
                f"Detached lock already owned - job_id={job_id}, session={session_id[:8]}, "
                f"existing_job={existing['job_id']}, existing_worker_pid={existing.get('worker_pid')}"
            )
            return False

        if not self._repo.reserve_session_lock(session_id, job_id):
            logger.warning(f"Detached lock reserve failed - job_id={job_id}, session={session_id[:8]}")
            return False

        attached = self._repo.attach_worker_to_session_lock(session_id, job_id, worker_pid)
        if attached:
            logger.info(
                f"Detached lock reserved and attached - job_id={job_id}, "
                f"session={session_id[:8]}, worker_pid={worker_pid}"
            )
        else:
            logger.warning(
                f"Detached lock attach after reserve failed - job_id={job_id}, session={session_id[:8]}"
            )
        return attached

    @staticmethod
    def _format_watchdog_limit(seconds: int) -> str:
        """Format watchdog timeout for user-facing status text."""
        if seconds % 60 == 0 and seconds >= 60:
            minutes = seconds // 60
            return f"{minutes} minutes"
        if seconds == 1:
            return "1 second"
        return f"{seconds} seconds"

    @staticmethod
    def _escape_html(text: str) -> str:
        """Escape one user-controlled string for Telegram HTML."""
        return html.escape(text or "")

    async def _call_provider(
        self,
        *,
        bot: Bot,
        client,
        job_id: int,
        chat_id: int,
        session_id: str,
        message: str,
        provider: str,
        provider_session_id: Optional[str],
        model: str,
        workspace_path: Optional[str],
        short_message: str,
    ) -> tuple[str, Optional[str], Optional[str], bool, float]:
        """Run one provider call with long-task notice and detached watchdog."""
        start_time = time.time()
        long_task_notified = False
        escaped_short_message = self._escape_html(short_message)

        async def notify_long_task() -> None:
            nonlocal long_task_notified
            await asyncio.sleep(LONG_TASK_THRESHOLD_SECONDS)
            long_task_notified = True
            elapsed_min = LONG_TASK_THRESHOLD_SECONDS // 60
            logger.info(
                f"Detached provider job long-task notice - job_id={job_id}, "
                f"session={session_id[:8]}, threshold={LONG_TASK_THRESHOLD_SECONDS}s"
            )
            await bot.send_message(
                chat_id=chat_id,
                text=(
                    f"<code>{escaped_short_message}</code>\n"
                    f"Task taking {elapsed_min}+ minutes. Still running. "
                    f"I will notify you when it finishes."
                ),
                parse_mode="HTML",
            )

        notify_task = asyncio.create_task(notify_long_task())

        try:
            logger.info(
                f"Detached provider CLI call - job_id={job_id}, session={session_id[:8]}, "
                f"provider={provider}, model={model}"
            )
            try:
                chat_response = await asyncio.wait_for(
                    client.chat(
                        message,
                        provider_session_id,
                        model=model,
                        workspace_path=workspace_path or None,
                    ),
                    timeout=TASK_TIMEOUT_SECONDS,
                )
                response, error, next_provider_session_id = chat_response
            except asyncio.TimeoutError:
                logger.warning(
                    f"Detached provider watchdog timeout - job_id={job_id}, "
                    f"session={session_id[:8]}, timeout={TASK_TIMEOUT_SECONDS}s"
                )
                response = ""
                error = "WATCHDOG_TIMEOUT"
                next_provider_session_id = provider_session_id
        finally:
            notify_task.cancel()
            try:
                await notify_task
            except asyncio.CancelledError:
                pass

        elapsed = time.time() - start_time
        logger.info(
            f"Detached provider CLI returned - job_id={job_id}, session={session_id[:8]}, "
            f"error={error or '-'}, response_chars={len(response or '')}"
        )
        return response, error, next_provider_session_id, long_task_notified, elapsed

    def _normalize_provider_result(
        self,
        *,
        response: str,
        error: Optional[str],
        short_message: str,
    ) -> tuple[str, Optional[str]]:
        """Map provider output to one user-facing response body and stored error."""
        escaped_short_message = self._escape_html(short_message)

        if error == "WATCHDOG_TIMEOUT":
            timeout_label = self._format_watchdog_limit(TASK_TIMEOUT_SECONDS)
            return f"⏱️ Task exceeded {timeout_label} and was stopped. Please try again.", "watchdog_timeout"
        if error == "TIMEOUT":
            return "⏱️ Response timed out. Please try again.", "provider_timeout"
        if error and error != "SESSION_NOT_FOUND":
            return f"❌ Error: {self._escape_html(error)}", error
        if not response or not response.strip():
            return f"⚠️ <code>{escaped_short_message}</code>\nResponse is empty. Please try again.", "empty_response"
        return response, None

    def _build_full_response(
        self,
        *,
        provider_label: str,
        model_label: str,
        session_info: str,
        history_count: int,
        question_preview: str,
        response: str,
        session_short_id: str,
    ) -> str:
        """Render the final Telegram response envelope."""
        return (
            f"<b>[{self._escape_html(provider_label)} · {self._escape_html(model_label)} · "
            f"{self._escape_html(session_info)}|#{history_count}]</b>\n"
            f"<code>{self._escape_html(question_preview)}</code>\n\n"
            f"{response}\n\n"
            f"/s_{session_short_id} switch\n"
            f"/h_{session_short_id} history"
        )

    async def _send_completion_notice(
        self,
        *,
        bot: Bot,
        chat_id: int,
        short_message: str,
        elapsed: float,
    ) -> None:
        """Send the post-completion notice for long-running successful jobs."""
        elapsed_min = int(elapsed // 60)
        elapsed_sec = int(elapsed % 60)
        await bot.send_message(
            chat_id=chat_id,
            text=(
                f"<code>{self._escape_html(short_message)}</code>\n"
                f"Task complete! ({elapsed_min}m {elapsed_sec}s)"
            ),
            parse_mode="HTML",
        )

    async def _execute_job(self, bot: Bot, job: dict) -> None:
        """Execute one provider job and send the final response to Telegram."""
        job_id = job["id"]
        chat_id = job["chat_id"]
        session_id = job["session_id"]
        message = job["request"]
        session = self._sessions.get_session(session_id) or {}
        provider = session.get("ai_provider") or "claude"
        provider_session_id = session.get("provider_session_id")
        model = session.get("model") or job["model"]
        workspace_path = job.get("workspace_path") or session.get("workspace_path")
        client = self._ai_registry.get_client(provider)
        provider_label = get_provider_label(provider)
        model_label = get_profile_label(provider, model)

        trace_id = set_trace_id()
        set_user_id(str(chat_id))
        set_session_id(session_id)

        short_message = truncate_message(message, 30)

        logger.info(
            f"Detached provider job start - job_id={job_id}, session={session_id[:8]}, "
            f"provider={provider}, model={model}, provider_session="
            f"{provider_session_id[:8] if provider_session_id else '-'}, "
            f"workspace={workspace_path or '(none)'}, request={short_message!r}"
        )

        try:
            response, error, next_provider_session_id, long_task_notified, elapsed = await self._call_provider(
                bot=bot,
                client=client,
                job_id=job_id,
                chat_id=chat_id,
                session_id=session_id,
                message=message,
                provider=provider,
                provider_session_id=provider_session_id,
                model=model,
                workspace_path=workspace_path,
                short_message=short_message,
            )
            if next_provider_session_id and next_provider_session_id != provider_session_id:
                logger.info(
                    f"Detached provider session updated - job_id={job_id}, session={session_id[:8]}, "
                    f"provider_session={provider_session_id[:8] if provider_session_id else '-'}"
                    f"->{next_provider_session_id[:8]}"
                )
                self._sessions.update_session_provider_session_id(session_id, next_provider_session_id)

            logger.info(
                f"Detached provider job complete - job_id={job_id}, session={session_id[:8]}, "
                f"elapsed={elapsed:.1f}s, error={error or '-'}"
            )

            self._sessions.add_message(session_id, message, processor=provider)
            response, stored_error = self._normalize_provider_result(
                response=response,
                error=error,
                short_message=short_message,
            )

            session_info = self._sessions.get_session_info(session_id)
            history_count = self._sessions.get_history_count(session_id)
            question_preview = truncate_message(message, 30)
            session_short_id = session_id[:8]

            full_response = self._build_full_response(
                provider_label=provider_label,
                model_label=model_label,
                session_info=session_info,
                history_count=history_count,
                question_preview=question_preview,
                response=response,
                session_short_id=session_short_id,
            )

            if long_task_notified and not stored_error:
                await self._send_completion_notice(
                    bot=bot,
                    chat_id=chat_id,
                    short_message=short_message,
                    elapsed=elapsed,
                )

            logger.info(
                f"Detached provider sending Telegram response - job_id={job_id}, "
                f"session={session_id[:8]}, history_count={history_count}, response_chars={len(full_response)}"
            )
            await self._send_message_to_chat(bot, chat_id, full_response)
            self._repo.complete_message(job_id, response=response, error=stored_error)
            logger.info(
                f"Detached provider persisted completion - job_id={job_id}, "
                f"session={session_id[:8]}, stored_response_chars={len(response or '')}"
            )

        except Exception as e:
            logger.exception(f"Detached provider job failed: job_id={job_id}, trace={trace_id}, error={e}")
            self._repo.complete_message(job_id, error=str(e))
            try:
                await bot.send_message(
                    chat_id=chat_id,
                    text="❌ An error occurred. Please try again later.",
                )
            except Exception:
                logger.exception(f"Detached Claude job error delivery failed: job_id={job_id}")

    @staticmethod
    def _split_message(text: str, max_length: int = 4000) -> list[str]:
        """Split long Telegram messages on newline boundaries when possible."""
        if len(text) <= max_length:
            return [text]

        chunks: list[str] = []
        remaining = text

        while len(remaining) > max_length:
            window = remaining[:max_length]
            split_pos = window.rfind("\n")
            if split_pos > 0:
                chunk = remaining[:split_pos]
                remaining = remaining[split_pos + 1:]
            else:
                chunk = window
                remaining = remaining[max_length:]
            if chunk:
                chunks.append(chunk)

        if remaining:
            chunks.append(remaining)

        return chunks

    async def _send_message_to_chat(self, bot: Bot, chat_id: int, text: str) -> None:
        """Send a split-safe Telegram message with HTML fallback."""
        chunks = self._split_message(text)
        logger.info(f"Detached provider Telegram chunks - chat_id={chat_id}, chunks={len(chunks)}")

        for index, chunk in enumerate(chunks, start=1):
            try:
                logger.info(
                    f"Detached provider Telegram send - chat_id={chat_id}, chunk={index}/{len(chunks)}, "
                    f"chars={len(chunk)}, parse_mode=HTML"
                )
                await bot.send_message(chat_id=chat_id, text=chunk, parse_mode="HTML")
            except Exception:
                logger.warning(
                    f"Detached provider Telegram HTML send failed, retrying plain text - "
                    f"chat_id={chat_id}, chunk={index}/{len(chunks)}"
                )
                await bot.send_message(chat_id=chat_id, text=chunk)
