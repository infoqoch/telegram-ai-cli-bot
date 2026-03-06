"""Message processing handlers."""

import asyncio
import time

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

from src.logging_config import logger, set_trace_id, set_user_id, set_session_id, clear_context
from ..constants import (
    MAX_MESSAGE_LENGTH,
    LONG_TASK_THRESHOLD_SECONDS,
)
from ..formatters import truncate_message
from ..middleware import authorized_only, authenticated_only
from ..session_queue import session_queue_manager, QueuedMessage
from .base import BaseHandler


class MessageHandlers(BaseHandler):
    """Message processing handlers."""

    @authorized_only
    @authenticated_only
    async def ai_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle /ai command - force Claude conversation (bypass plugins)."""
        chat_id = update.effective_chat.id
        trace_id = self._setup_request_context(chat_id)
        logger.info("/ai command received")

        user_id = str(chat_id)

        if not context.args:
            logger.trace("/ai no args - show usage")
            await update.message.reply_text(
                "<b>/ai Usage</b>\n\n"
                "<code>/ai question</code>\n\n"
                "Bypass plugins and ask Claude directly.",
                parse_mode="HTML"
            )
            clear_context()
            return

        message = " ".join(context.args)
        short_msg = message[:50] + "..." if len(message) > 50 else message
        logger.info(f"/ai message: '{short_msg}'")
        logger.trace(f"Full message length: {len(message)}")

        if len(message) > MAX_MESSAGE_LENGTH:
            logger.warning(f"Message length limited: {len(message)} -> {MAX_MESSAGE_LENGTH}")
            message = message[:MAX_MESSAGE_LENGTH]

        if user_id in self._creating_sessions:
            logger.info(f"Session creation in progress - /ai blocked: user={user_id}")
            await update.message.reply_text(
                "<b>세션 준비 중...</b>\n\n"
                "잠시 후 다시 시도해주세요!",
                parse_mode="HTML"
            )
            clear_context()
            return

        logger.trace("Session decision start - waiting for Lock")
        async with self._user_locks[user_id]:
            logger.trace("Lock acquired")
            session_id = self.sessions.get_current_session_id(user_id)
            logger.trace(f"Current session: {session_id[:8] if session_id else 'None'}")

            if not session_id:
                logger.info("Creating new Claude session...")
                self._creating_sessions.add(user_id)
                try:
                    session_id = await self.claude.create_session()

                    if not session_id:
                        logger.error("Claude session creation failed")
                        await update.message.reply_text("❌ Claude 세션 생성 실패. 다시 시도해주세요.")
                        clear_context()
                        return

                    logger.trace(f"Saving session - session_id={session_id[:8]}")
                    self.sessions.create_session(user_id, session_id, first_message=message)
                    is_new_session = True
                finally:
                    self._creating_sessions.discard(user_id)
            else:
                is_new_session = False

        model = self.sessions.get_session_model(session_id)
        workspace_path = self.sessions.get_workspace_path(session_id)

        set_session_id(session_id)
        logger.info(f"Session decided - model={model}, new={is_new_session}, workspace={workspace_path or '(none)'}")

        # Fire-and-forget: 백그라운드에서 Claude 호출
        asyncio.create_task(
            self._process_claude_request_with_semaphore(
                bot=context.bot,
                chat_id=chat_id,
                user_id=user_id,
                session_id=session_id,
                message=message,
                is_new_session=is_new_session,
                trace_id=trace_id,
                model=model,
            )
        )
        logger.trace("/ai handler complete - background task created")

    async def handle_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle regular text messages.

        Fire-and-Forget pattern:
        1. Auth/authorization check
        2. Session decision (Lock protected)
        3. Background task for Claude call + response
        4. Handler returns immediately
        """
        chat_id = update.effective_chat.id
        user_id = str(chat_id)
        message = update.message.text
        short_msg = message[:50] + "..." if len(message) > 50 else message

        trace_id = self._setup_request_context(chat_id)
        logger.info(f"Message received: '{short_msg}'")
        logger.trace(f"Full message length: {len(message)}")

        if not self._is_authorized(chat_id):
            logger.debug("메시지 거부 - 권한 없음")
            await update.message.reply_text("⛔ 권한이 없습니다.")
            clear_context()
            return

        if len(message) > MAX_MESSAGE_LENGTH:
            original_len = len(message)
            message = message[:MAX_MESSAGE_LENGTH]
            logger.warning(f"Message length limited: {original_len} -> {MAX_MESSAGE_LENGTH}")

        # ForceReply response handling
        if update.message.reply_to_message:
            reply_text = update.message.reply_to_message.text or ""
            import re

            if "td:add" in reply_text:
                await self._handle_todo_force_reply(update, chat_id, message)
                clear_context()
                return

            if "sess_name:" in reply_text:
                sess_match = re.search(r"sess_name:(\w+)", reply_text)
                if sess_match:
                    model = sess_match.group(1)
                    await self._handle_new_session_force_reply(update, chat_id, message, model)
                    clear_context()
                    return

            if "memo_add" in reply_text:
                await self._handle_memo_force_reply(update, chat_id, message)
                clear_context()
                return

            if "schedule_input" in reply_text and user_id in self._pending_schedule_input:
                await self._handle_schedule_force_reply(update, chat_id, message)
                clear_context()
                return

            if user_id in self._pending_workspace_input:
                await self._handle_workspace_force_reply(update, chat_id, message)
                clear_context()
                return

        # Plugin processing attempt
        if self.plugins:
            logger.debug(f"[PLUGIN] Processing - loaded plugins: {len(self.plugins.plugins)}")
            logger.debug(f"[PLUGIN] Message: {message[:100]}")
            try:
                result = await self.plugins.process_message(message, chat_id)
                logger.debug(f"[PLUGIN] result={result}, handled={result.handled if result else 'N/A'}")
                if result and result.handled:
                    plugin_name = result.plugin_name if hasattr(result, 'plugin_name') else "plugin"
                    logger.info(f"[PLUGIN] Processed: {plugin_name}")
                    logger.debug(f"[PLUGIN] response length={len(result.response) if result.response else 0}")
                    session_id = self.sessions.get_current_session_id(user_id)
                    if session_id:
                        self.sessions.add_message(session_id, message, processor=f"plugin:{plugin_name}")
                    if result.response:
                        logger.debug(f"[PLUGIN] Sending response")
                        try:
                            await update.message.reply_text(
                                result.response,
                                parse_mode="HTML",
                                reply_markup=result.reply_markup if hasattr(result, 'reply_markup') else None
                            )
                        except Exception as e:
                            logger.warning(f"[PLUGIN] HTML parse failed, retrying: {e}")
                            await update.message.reply_text(result.response)
                        logger.debug(f"[PLUGIN] Response sent")
                    else:
                        logger.warning(f"[PLUGIN] handled=True but response empty!")
                    clear_context()
                    return
                logger.debug("[PLUGIN] No match -> proceeding to Claude")
            except Exception as e:
                logger.error(f"[PLUGIN] Error: {e}", exc_info=True)
        else:
            logger.debug("[PLUGIN] No plugin loader")

        if not self._is_authenticated(user_id):
            logger.debug("메시지 거부 - 인증 필요")
            await update.message.reply_text(
                "🔒 인증이 필요합니다.\n"
                f"/auth <키>로 인증하세요. ({self.auth.timeout_minutes}분간 유효)\n"
                "/help 도움말"
            )
            clear_context()
            return

        if user_id in self._creating_sessions:
            logger.info(f"Session creation in progress - message blocked: user={user_id}")
            await update.message.reply_text(
                "<b>세션 준비 중...</b>\n\n"
                "잠시 후 다시 시도해주세요!",
                parse_mode="HTML"
            )
            clear_context()
            return

        logger.trace("Session decision start - waiting for Lock")
        async with self._user_locks[user_id]:
            logger.trace("Lock acquired")
            session_id = self.sessions.get_current_session_id(user_id)
            logger.trace(f"Current session: {session_id[:8] if session_id else 'None'}")

            if not session_id:
                logger.info("Creating new Claude session...")
                self._creating_sessions.add(user_id)
                try:
                    session_id = await self.claude.create_session()

                    if not session_id:
                        logger.error("Claude session creation failed")
                        await update.message.reply_text("❌ Claude 세션 생성 실패. 다시 시도해주세요.")
                        clear_context()
                        return

                    logger.trace(f"Saving session - session_id={session_id[:8]}")
                    self.sessions.create_session(user_id, session_id, first_message=message)
                    is_new_session = True
                finally:
                    self._creating_sessions.discard(user_id)
            else:
                is_new_session = False

        model = self.sessions.get_session_model(session_id)
        workspace_path = self.sessions.get_workspace_path(session_id)

        set_session_id(session_id)

        logger.info(f"Message accepted: model={model}, new={is_new_session}, workspace={workspace_path or '(none)'}")

        # Fire-and-forget: 백그라운드에서 Claude 호출
        asyncio.create_task(
            self._process_claude_request_with_semaphore(
                bot=context.bot,
                chat_id=chat_id,
                user_id=user_id,
                session_id=session_id,
                message=message,
                is_new_session=is_new_session,
                trace_id=trace_id,
                model=model,
            )
        )
        logger.trace("handle_message complete - background task created")

    async def _process_claude_request_with_semaphore(
        self,
        bot,
        chat_id: int,
        user_id: str,
        session_id: str,
        message: str,
        is_new_session: bool,
        trace_id: str,
        model: str = None,
    ) -> None:
        """Semaphore + session lock for concurrent request limiting then Claude call."""
        set_trace_id(trace_id)
        set_user_id(user_id)
        set_session_id(session_id)
        logger.trace(f"_process_claude_request_with_semaphore start - model={model}")

        locked = await session_queue_manager.try_lock(session_id, user_id, message)
        if not locked:
            logger.warning(f"Session lock acquisition failed - session={session_id[:8]}")
            workspace_path = self.sessions.get_workspace_path(session_id) or ""
            await self._show_session_selection_ui(
                update=None,
                user_id=user_id,
                message=message,
                current_session_id=session_id,
                model=model or "sonnet",
                is_new_session=is_new_session,
                workspace_path=workspace_path,
                bot=bot,
                chat_id=chat_id,
            )
            logger.info(f"Session selection UI shown - session={session_id[:8]}")
            clear_context()
            return

        try:
            logger.trace(f"Session lock acquired - session={session_id[:8]}")
            async with self._user_semaphores[user_id]:
                logger.trace("Semaphore acquired")
                await self._process_claude_request(
                    bot=bot,
                    chat_id=chat_id,
                    user_id=user_id,
                    session_id=session_id,
                    message=message,
                    is_new_session=is_new_session,
                    model=model,
                )
        finally:
            next_msg = await session_queue_manager.unlock(session_id)
            if next_msg:
                logger.info(f"Processing next message from queue - session={session_id[:8]}, user={next_msg.user_id}")
                asyncio.create_task(
                    self._process_queued_message(bot, next_msg)
                )

        logger.trace("_process_claude_request_with_semaphore complete")
        clear_context()

    async def _process_claude_request(
        self,
        bot,
        chat_id: int,
        user_id: str,
        session_id: str,
        message: str,
        is_new_session: bool,
        model: str = None,
    ) -> None:
        """Background Claude call and response sending.

        Args:
            bot: Telegram Bot instance (for response)
            chat_id: Chat ID to send response
            user_id: User ID (for logging/session)
            session_id: Claude session ID
            message: User message
            is_new_session: Whether new session
            model: Model to use (opus, sonnet, haiku)
        """
        from ..constants import get_model_emoji

        start_time = time.time()

        logger.info(f"Claude call start - session={session_id[:8]}, model={model}")
        logger.info(f"===== User Question (START) =====")
        logger.info(message)
        logger.info(f"===== User Question (END) =====")

        try:
            workspace_path = self.sessions.get_workspace_path(session_id)
            if workspace_path:
                logger.trace(f"Workspace session - workspace_path={workspace_path}")

            long_task_notified = False
            short_message = truncate_message(message, 30)

            async def notify_long_task():
                nonlocal long_task_notified
                await asyncio.sleep(LONG_TASK_THRESHOLD_SECONDS)
                if not long_task_notified:
                    long_task_notified = True
                    elapsed_min = LONG_TASK_THRESHOLD_SECONDS // 60
                    logger.info(f"Long task notification - {elapsed_min}min elapsed")
                    await bot.send_message(
                        chat_id=chat_id,
                        text=f"<code>{short_message}</code>\nTask taking {elapsed_min}+ minutes. Will notify on completion!",
                        parse_mode="HTML"
                    )

            notify_task = asyncio.create_task(notify_long_task())

            logger.trace(f"claude.chat() call - model={model}")
            try:
                response, error, _ = await self.claude.chat(message, session_id, model=model, workspace_path=workspace_path or None)
            finally:
                notify_task.cancel()
                try:
                    await notify_task
                except asyncio.CancelledError:
                    pass

            elapsed = time.time() - start_time
            logger.info(f"Claude response complete - session={session_id[:8]}, elapsed={elapsed:.1f}s, length={len(response) if response else 0}")
            logger.info(f"===== Claude Response (START) =====")
            logger.info(response if response else "(None or empty)")
            logger.info(f"===== Claude Response (END) =====")

            logger.debug(f"[DEBUG] response type: {type(response)}")
            logger.debug(f"[DEBUG] response repr: {repr(response)[:200] if response else 'None'}")
            logger.debug(f"[DEBUG] error: {error}")

            if error:
                logger.warning(f"Claude error: {error}")

            if not is_new_session:
                logger.trace("Adding message to session history")
                self.sessions.add_message(session_id, message, processor="claude")

            if error == "TIMEOUT":
                logger.warning("Claude 타임아웃")
                response = "⏱️ 응답 시간 초과. 다시 시도해주세요."
            elif error and error != "SESSION_NOT_FOUND":
                logger.error(f"Claude 오류: {error}")
                response = f"❌ 오류 발생: {error}"
            elif not response or not response.strip():
                logger.error(f"[EMPTY RESPONSE] Claude 빈 응답 감지!")
                logger.error(f"  response type: {type(response)}")
                logger.error(f"  response repr: {repr(response)}")
                logger.error(f"  error: {error}")
                logger.error(f"  session_id: {session_id[:8]}")
                logger.error(f"  model: {model}")
                logger.error(f"  is_new_session: {is_new_session}")
                logger.error(f"  message preview: {message[:200]}")
                logger.error(f"  workspace_path: {workspace_path}")
                response = f"⚠️ <code>{short_message}</code>\n응답이 비어있습니다. 다시 시도해주세요."

            session_info = self.sessions.get_session_info(session_id)
            session_short_id = session_id[:8]
            history_count = self.sessions.get_history_count(session_id)

            question_preview = truncate_message(message, 30)

            prefix = f"<b>[{session_info}|#{history_count}]</b>\n<code>{question_preview}</code>\n\n"
            suffix = (
                f"\n\n"
                f"/s_{session_short_id} switch\n"
                f"/h_{session_short_id} history"
            )

            full_response = prefix + response + suffix
            logger.trace(f"Final response length: {len(full_response)}")

            if long_task_notified:
                elapsed_min = int(elapsed // 60)
                elapsed_sec = int(elapsed % 60)
                await bot.send_message(
                    chat_id=chat_id,
                    text=f"<code>{short_message}</code>\nTask complete! ({elapsed_min}m {elapsed_sec}s)",
                    parse_mode="HTML"
                )

            logger.trace("Sending response")
            await self._send_message_to_chat(bot, chat_id, full_response)
            logger.trace("Response sent")

        except Exception as e:
            logger.exception(f"Claude 처리 실패: {e}")
            await bot.send_message(
                chat_id=chat_id,
                text="❌ 오류가 발생했습니다. 잠시 후 다시 시도해주세요."
            )

    async def _show_session_selection_ui(
        self,
        update: Update,
        user_id: str,
        message: str,
        current_session_id: str,
        model: str,
        is_new_session: bool,
        workspace_path: str,
        *,
        bot=None,
        chat_id: int = None,
    ) -> None:
        """Show session selection UI on session lock conflict (improved version).

        Options:
        1. Wait in this session (recommended) - auto process after current completes
        2. Select other session - list of available sessions
        3. Create new session
        4. Cancel

        Args:
            update: Telegram Update (None then use bot/chat_id)
            bot: Telegram Bot object (when update is None)
            chat_id: Chat ID (when update is None)
        """
        message_preview = truncate_message(message, 40)
        if update:
            chat_id = update.effective_chat.id

        current_state = session_queue_manager.get_status(current_session_id)
        queue_size = current_state.get_queue_size() if current_state else 0

        all_sessions = self.sessions.list_sessions(user_id)
        available_sessions = []

        for s in all_sessions:
            sid = s["full_session_id"]
            if sid == current_session_id:
                continue
            if not session_queue_manager.is_locked(sid):
                history = self.sessions.get_session_history(sid)
                recent = history[-2:] if history else []
                available_sessions.append({
                    **s,
                    "recent": recent,
                })

        lines = [
            f"<b>Current session is processing</b>",
            f"",
            f"<code>{message_preview}</code>",
            f"",
        ]

        buttons = []

        wait_label = f"Wait in this session"
        if queue_size > 0:
            wait_label += f" ({queue_size} waiting)"
        buttons.append([
            InlineKeyboardButton(
                wait_label + " (recommended)",
                callback_data=f"sq:wait:{current_session_id[:16]}"
            )
        ])
        lines.append(f"<b>Wait in this session</b>: Auto process after completion")

        if available_sessions:
            lines.append(f"")
            lines.append(f"<b>Available sessions:</b>")
            for s in available_sessions[:4]:
                sid = s["full_session_id"]
                short_id = s["session_id"]
                name = s.get("name") or f"Session {short_id}"
                sess_model = s.get("model", "sonnet")
                model_emoji = {"opus": "[O]", "sonnet": "[S]", "haiku": "[H]"}.get(sess_model, "[S]")

                recent_msgs = s.get("recent", [])
                if recent_msgs:
                    recent_preview = " / ".join(truncate_message(m, 12) for m in recent_msgs[-2:])
                    lines.append(f"- {model_emoji} <b>{name[:10]}</b>: {recent_preview}")
                else:
                    lines.append(f"- {model_emoji} <b>{name[:10]}</b>")

                buttons.append([
                    InlineKeyboardButton(
                        f"{model_emoji} {name[:15]}",
                        callback_data=f"sq:switch:{sid[:16]}"
                    )
                ])

        lines.append(f"")
        lines.append(f"<b>Create new session:</b>")
        buttons.append([
            InlineKeyboardButton("Opus", callback_data="sq:new:opus"),
            InlineKeyboardButton("Sonnet", callback_data="sq:new:sonnet"),
            InlineKeyboardButton("Haiku", callback_data="sq:new:haiku"),
        ])

        buttons.append([
            InlineKeyboardButton("Cancel", callback_data="sq:cancel"),
        ])

        self._temp_pending = {
            "user_id": user_id,
            "chat_id": chat_id,
            "message": message,
            "model": model,
            "is_new_session": is_new_session,
            "workspace_path": workspace_path,
            "current_session_id": current_session_id,
        }

        if update:
            await update.message.reply_text(
                "\n".join(lines),
                reply_markup=InlineKeyboardMarkup(buttons),
                parse_mode="HTML"
            )
        else:
            await bot.send_message(
                chat_id=chat_id,
                text="\n".join(lines),
                reply_markup=InlineKeyboardMarkup(buttons),
                parse_mode="HTML"
            )

    async def _process_queued_message(self, bot, queued_msg: QueuedMessage) -> None:
        """Process message from queue."""
        trace_id = self._setup_request_context(queued_msg.chat_id)
        set_user_id(queued_msg.user_id)
        set_session_id(queued_msg.session_id)

        logger.info(f"Queue message processing start - session={queued_msg.session_id[:8]}, user={queued_msg.user_id}")

        session_info = self.sessions.get_session_info(queued_msg.session_id)
        model_emoji = {"opus": "[O]", "sonnet": "[S]", "haiku": "[H]"}.get(queued_msg.model, "[S]")
        try:
            await bot.send_message(
                chat_id=queued_msg.chat_id,
                text=(
                    f"<b>Queue complete!</b>\n\n"
                    f"<code>{truncate_message(queued_msg.message, 30)}</code>\n\n"
                    f"Session: {model_emoji} <b>{session_info}</b>\n"
                    f"Starting processing..."
                ),
                parse_mode="HTML"
            )
        except Exception as e:
            logger.warning(f"Queue complete notification failed: {e}")

        await self._process_claude_request_with_semaphore(
            bot=bot,
            chat_id=queued_msg.chat_id,
            user_id=queued_msg.user_id,
            session_id=queued_msg.session_id,
            message=queued_msg.message,
            is_new_session=queued_msg.is_new_session,
            trace_id=trace_id,
            model=queued_msg.model,
        )

    async def _process_alternative_session_request(
        self,
        bot,
        chat_id: int,
        user_id: str,
        session_id: str,
        message: str,
        model: str,
        is_new_session: bool = False,
    ) -> None:
        """Process message in alternative session (background)."""
        trace_id = self._setup_request_context(chat_id)
        set_session_id(session_id)

        logger.info(f"Alternative session processing start - session={session_id[:8]}, model={model}")

        await self._process_claude_request_with_semaphore(
            bot=bot,
            chat_id=chat_id,
            user_id=user_id,
            session_id=session_id,
            message=message,
            is_new_session=is_new_session,
            trace_id=trace_id,
            model=model,
        )

        clear_context()
