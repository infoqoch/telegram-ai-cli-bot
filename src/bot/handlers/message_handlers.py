"""Message processing handlers."""

import hashlib
import time

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

from src.ai import get_default_model, get_provider_label
from src.logging_config import logger, set_trace_id, set_user_id, set_session_id, clear_context
from ..constants import (
    MAX_MESSAGE_LENGTH,
    get_model_badge,
)
from ..formatters import escape_html, truncate_message
from ..middleware import authorized_only, authenticated_only
from .base import BaseHandler


class MessageHandlers(BaseHandler):
    """Message processing handlers."""

    @authorized_only
    @authenticated_only
    async def ai_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle /ai command - force current AI conversation (bypass plugins)."""
        chat_id = update.effective_chat.id
        trace_id = self._setup_request_context(chat_id)
        logger.info("/ai command received")

        user_id = str(chat_id)
        provider = self._get_selected_ai_provider(user_id)
        provider_label = get_provider_label(provider)

        if not context.args:
            logger.trace("/ai no args - show usage")
            await update.message.reply_text(
                "<b>/ai Usage</b>\n\n"
                "<code>/ai question</code>\n\n"
                f"Bypass plugins and ask <b>{provider_label}</b> directly.",
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

        await self._dispatch_to_ai(update, chat_id, user_id, message)

    async def handle_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle regular text messages.

        Fire-and-Forget pattern:
        1. Auth/authorization check
        2. Session decision (Lock protected)
        3. Spawn detached worker for provider CLI call + Telegram response
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
            logger.debug("Message denied - unauthorized")
            await update.message.reply_text("⛔ Access denied.")
            clear_context()
            return

        if len(message) > MAX_MESSAGE_LENGTH:
            original_len = len(message)
            message = message[:MAX_MESSAGE_LENGTH]
            logger.warning(f"Message length limited: {original_len} -> {MAX_MESSAGE_LENGTH}")

        if not self._is_authenticated(user_id):
            logger.debug("Message denied - auth required")
            await update.message.reply_text(
                "🔒 Authentication required.\n"
                f"Use /auth <key> to authenticate. (Valid for {self.auth.timeout_minutes}m)\n"
                "/help for commands"
            )
            clear_context()
            return

        # ForceReply response handling
        if update.message.reply_to_message:
            reply_to_message = update.message.reply_to_message
            interaction = self._pop_plugin_interaction(
                prompt_message_id=getattr(reply_to_message, "message_id", None),
                chat_id=chat_id,
            )
            if interaction:
                await self._handle_plugin_interaction_reply(update, chat_id, message, interaction)
                clear_context()
                return

            reply_text = reply_to_message.text or ""
            import re

            if "sess_name:" in reply_text:
                sess_match = re.search(r"sess_name:(\w+)", reply_text)
                if sess_match:
                    model = sess_match.group(1)
                    await self._handle_new_session_force_reply(update, chat_id, message, model)
                    clear_context()
                    return

            if "sess_rename:" in reply_text:
                rename_match = re.search(r"sess_rename:([\w-]+)", reply_text)
                if rename_match:
                    session_id = rename_match.group(1)
                    await self._handle_rename_force_reply(update, chat_id, message, session_id)
                    clear_context()
                    return

            if "schedule_input" in reply_text and user_id in self._sched_pending:
                await self._handle_schedule_force_reply(update, chat_id, message)
                clear_context()
                return

            if user_id in self._ws_pending:
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

        await self._dispatch_to_ai(update, chat_id, user_id, message)

    async def _dispatch_to_ai(
        self,
        update: Update,
        chat_id: int,
        user_id: str,
        message: str,
    ) -> None:
        """Common AI dispatch: session decision → detached job spawn.

        Shared by ai_command and handle_message.
        """
        if user_id in self._creating_sessions:
            logger.info(f"Session creation in progress - message blocked: user={user_id}")
            await update.message.reply_text(
                "<b>Session initializing...</b>\n\n"
                "Please try again shortly!",
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
                provider = self._get_selected_ai_provider(user_id)
                default_model = get_default_model(provider)
                logger.info(f"Creating new {provider} session envelope...")
                session_id = self.sessions.create_session(
                    user_id=user_id,
                    ai_provider=provider,
                    model=default_model,
                    first_message="(new session)",
                )

            session_provider = self.sessions.get_session_ai_provider(session_id) or self._get_selected_ai_provider(user_id)
            model = self.sessions.get_session_model(session_id) or get_default_model(session_provider)
            workspace_path = self.sessions.get_workspace_path(session_id)

            if self._is_session_locked(session_id):
                await self._show_session_selection_ui(
                    update=update,
                    user_id=user_id,
                    message=message,
                    current_session_id=session_id,
                    model=model,
                    is_new_session=False,
                    workspace_path=workspace_path or "",
                )
                clear_context()
                return

            try:
                _, start_error = self._start_detached_job(
                    chat_id=chat_id,
                    session_id=session_id,
                    message=message,
                    model=model,
                    workspace_path=workspace_path,
                )
            except Exception:
                await update.message.reply_text("❌ Failed to start detached worker. Please try again.")
                clear_context()
                return

            if start_error == "session_locked":
                await self._show_session_selection_ui(
                    update=update,
                    user_id=user_id,
                    message=message,
                    current_session_id=session_id,
                    model=model,
                    is_new_session=False,
                    workspace_path=workspace_path or "",
                )
                clear_context()
                return

        set_session_id(session_id)
        logger.info(f"Detached job started: model={model}, workspace={workspace_path or '(none)'}")
        logger.trace("Dispatch complete - detached worker spawned")

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
        3. Create new session for the same AI
        4. Cancel

        Args:
            update: Telegram Update (None then use bot/chat_id)
            bot: Telegram Bot object (when update is None)
            chat_id: Chat ID (when update is None)
        """
        message_preview = truncate_message(message, 40)
        if update:
            chat_id = update.effective_chat.id

        repo = self._repository
        queue_size = len(repo.get_queued_messages_by_session(current_session_id)) if repo else 0
        provider = self.sessions.get_session_ai_provider(current_session_id) or self._get_selected_ai_provider(user_id)
        provider_label = get_provider_label(provider)

        all_sessions = self.sessions.list_sessions(user_id, ai_provider=provider)
        available_sessions = []

        for s in all_sessions:
            sid = s["full_session_id"]
            if sid == current_session_id:
                continue
            if not self._is_session_locked(sid):
                history = self.sessions.get_session_history(sid)
                recent = history[-2:] if history else []
                available_sessions.append({
                    **s,
                    "recent": recent,
                })

        lines = [
            f"<b>Current session is processing</b>",
            f"",
            f"AI: <b>{provider_label}</b>",
            f"",
            f"<code>{escape_html(message_preview)}</code>",
            f"",
        ]

        buttons = []

        # Generate unique key for this pending request
        pending_key = hashlib.md5(
            f"{current_session_id}:{message}:{time.time()}".encode()
        ).hexdigest()[:8]

        # Expire entries older than 5 minutes
        now = time.time()
        expired_keys = [k for k, v in self._temp_pending.items() if now - v.get("created_at", 0) > 300]
        for k in expired_keys:
            self._delete_temp_pending(k)

        wait_label = f"Wait in this session"
        if queue_size > 0:
            wait_label += f" ({queue_size} waiting)"
        buttons.append([
            InlineKeyboardButton(
                wait_label + " (recommended)",
                callback_data=f"sq:wait:{pending_key}:{current_session_id[:16]}"
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
                model_emoji = get_model_badge(sess_model)

                recent_msgs = s.get("recent", [])
                if recent_msgs:
                    recent_preview = " / ".join(truncate_message(m, 12) for m in recent_msgs[-2:])
                    lines.append(f"- {model_emoji} <b>{escape_html(name[:10])}</b>: {escape_html(recent_preview)}")
                else:
                    lines.append(f"- {model_emoji} <b>{escape_html(name[:10])}</b>")

                buttons.append([
                    InlineKeyboardButton(
                        f"{model_emoji} {name[:15]}",
                        callback_data=f"sq:switch:{pending_key}:{sid[:16]}"
                    )
                ])

        lines.append(f"")
        lines.append(f"<b>Create new {provider_label} session:</b>")
        buttons.append(self._build_model_buttons(provider, f"sq:new:{pending_key}:"))

        buttons.append([
            InlineKeyboardButton("Cancel", callback_data=f"sq:cancel:{pending_key}"),
        ])

        self._save_temp_pending(pending_key, {
            "user_id": user_id,
            "chat_id": chat_id,
            "message": message,
            "model": model,
            "is_new_session": is_new_session,
            "workspace_path": workspace_path,
            "current_session_id": current_session_id,
            "created_at": time.time(),
        })

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
