"""Session-related callback handlers."""

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, ForceReply
from telegram.error import BadRequest

from src.ai import (
    get_default_model,
    get_profile_label,
    infer_provider_from_model,
    is_supported_model,
)
from src.logging_config import logger
from src.ui_emoji import (
    BUTTON_BACK,
    BUTTON_CANCEL,
    BUTTON_DELETE,
    BUTTON_HISTORY,
    BUTTON_LIST,
    BUTTON_RENAME,
    BUTTON_SESSION,
    BUTTON_SESSION_LIST,
    BUTTON_SWITCH,
    BUTTON_SWITCH_AI,
)
from ..constants import get_model_emoji
from ..formatters import escape_html, truncate_message
from .base import BaseHandler


class SessionCallbackHandlers(BaseHandler):
    """Session callback handlers (sess: prefix)."""

    def _resolve_user_session(self, user_id: str, session_id: str):
        """Resolve a user-visible session id/prefix to the stored session payload."""
        session = self.sessions.get_session_by_prefix(user_id, session_id[:8])
        if not session:
            return None, None
        return session, session.get("full_session_id", session_id)

    def _build_session_detail_message(self, user_id: str, session: dict, full_session_id: str) -> tuple[str, InlineKeyboardMarkup]:
        """Build the current-session detail card used by both edit and follow-up flows."""
        short_id = full_session_id[:8]
        session_name = session.get("name") or ""
        model = session.get("model", "sonnet")
        provider = session.get("ai_provider", self._get_selected_ai_provider(user_id))
        model_emoji = get_model_emoji(model)

        history_entries = self.sessions.get_session_history_entries(full_session_id)
        count = len(history_entries)

        recent = history_entries[-10:]
        history_lines = []
        start_idx = len(history_entries) - len(recent) + 1

        for i, entry in enumerate(recent, start=start_idx):
            msg = entry.get("message", "") if isinstance(entry, dict) else str(entry)
            processor = entry.get("processor", "claude") if isinstance(entry, dict) else "claude"
            emoji = "[plugin]" if processor.startswith("plugin:") else {"command": "[cmd]", "rejected": "[x]"}.get(processor, "")
            short_q = truncate_message(msg, 35)
            history_lines.append(f"{i}. {emoji} {escape_html(short_q)}")

        history_text = "\n".join(history_lines) if history_lines else "(empty)"
        name_line = f"- Name: {escape_html(session_name)}\n" if session_name else ""

        model_buttons = self._build_model_buttons(
            provider,
            "sess:model:",
            callback_suffix=f":{full_session_id}",
        )
        keyboard = InlineKeyboardMarkup([
            model_buttons,
            [
                InlineKeyboardButton(BUTTON_RENAME, callback_data=f"sess:rename:{full_session_id}"),
                InlineKeyboardButton(BUTTON_HISTORY, callback_data=f"sess:history:{full_session_id}"),
                InlineKeyboardButton(BUTTON_DELETE, callback_data=f"sess:delete:{full_session_id}"),
            ],
            [
                InlineKeyboardButton(BUTTON_SESSION_LIST, callback_data="sess:list"),
                InlineKeyboardButton(BUTTON_SWITCH_AI, callback_data="ai:open"),
            ]
        ])

        text = (
            f"✅ <b>Session switched!</b>\n\n"
            f"- AI: {self._format_provider_display(provider)}\n"
            f"- ID: <code>{short_id}</code>\n"
            f"{name_line}"
            f"- Model: {model_emoji} {get_profile_label(provider, model)}\n"
            f"- Messages: {count}\n\n"
            f"<b>History</b> (last 10)\n{history_text}"
        )
        return text, keyboard

    def _build_history_message(self, user_id: str, session: dict, full_session_id: str) -> tuple[str, InlineKeyboardMarkup]:
        """Build the session history card used by both edit and follow-up flows."""
        del user_id
        short_id = full_session_id[:8]
        name = session.get("name") or f"Session {short_id}"
        history = self.sessions.get_session_history_entries(full_session_id)

        lines = [f"<b>{escape_html(name)}</b> History\n"]

        if not history:
            lines.append("(no history)")
        else:
            for i, entry in enumerate(history[-10:], 1):
                msg = entry.get("message", "")[:50] if isinstance(entry, dict) else str(entry)[:50]
                if len(entry.get("message", "") if isinstance(entry, dict) else str(entry)) > 50:
                    msg += "..."
                lines.append(f"{i}. {escape_html(msg)}")

        keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton(BUTTON_SWITCH, callback_data=f"sess:switch:{full_session_id}"),
                InlineKeyboardButton(BUTTON_LIST, callback_data="sess:list"),
            ]
        ])
        return "\n".join(lines), keyboard

    async def _reply_to_callback_origin(self, query, *, text: str, reply_markup=None, parse_mode: str = "HTML") -> None:
        """Send a follow-up message for callbacks triggered from immutable AI responses."""
        if query.message:
            await query.message.reply_text(
                text=text,
                reply_markup=reply_markup,
                parse_mode=parse_mode,
            )
            return

        await query.edit_message_text(
            text=text,
            reply_markup=reply_markup,
            parse_mode=parse_mode,
        )

    async def _handle_session_callback(self, query, chat_id: int, callback_data: str) -> None:
        """Handle session callbacks."""
        try:
            parts = callback_data.split(":")
            if len(parts) < 2:
                await query.edit_message_text("Invalid request")
                return

            action = parts[1]
            user_id = str(chat_id)
            selected_provider = self._get_selected_ai_provider(user_id)

            if action == "new":
                if len(parts) > 2:
                    model = parts[2]
                    await self._handle_new_session_name_prompt(query, chat_id, model)
                else:
                    await self._handle_new_session_menu_callback(query, chat_id)

            elif action == "new_confirm":
                model = parts[2] if len(parts) > 2 else get_default_model(selected_provider)
                await self._handle_new_session_callback(query, chat_id, model, "")

            elif action == "switch":
                session_id = parts[2] if len(parts) > 2 else ""
                await self._handle_switch_session_callback(query, chat_id, session_id)

            elif action == "delete":
                session_id = parts[2] if len(parts) > 2 else ""
                await self._handle_delete_session_confirm(query, chat_id, session_id)

            elif action == "confirm_del":
                session_id = parts[2] if len(parts) > 2 else ""
                await self._handle_delete_session_execute(query, chat_id, session_id)

            elif action == "history":
                session_id = parts[2] if len(parts) > 2 else ""
                await self._handle_history_callback(query, chat_id, session_id)

            elif action == "list":
                await self._handle_session_list_callback(query, chat_id)

            elif action == "rename":
                session_id = parts[2] if len(parts) > 2 else ""
                await self._handle_rename_prompt_callback(query, chat_id, session_id)

            elif action == "model":
                model = parts[2] if len(parts) > 2 else "sonnet"
                session_id = parts[3] if len(parts) > 3 else ""
                await self._handle_model_change_callback(query, chat_id, model, session_id)

            elif action == "cancel":
                await self._handle_session_list_callback(query, chat_id)

            else:
                await query.edit_message_text("Unknown command")

        except BadRequest as e:
            if "Message is not modified" in str(e):
                pass
            else:
                logger.warning(f"Session callback BadRequest: {e}")
        except Exception as e:
            logger.exception(f"Session callback error: {e}")
            try:
                await query.edit_message_text(
                    text=f"Error occurred.\n\n<code>{escape_html(str(e))}</code>",
                    parse_mode="HTML"
                )
            except:
                pass

    async def _handle_response_session_callback(self, query, chat_id: int, callback_data: str) -> None:
        """Handle AI-response shortcut callbacks without overwriting the original answer."""
        try:
            parts = callback_data.split(":")
            if len(parts) < 2:
                await self._reply_to_callback_origin(query, text="Invalid request")
                return

            action = parts[1]
            user_id = str(chat_id)

            if action == "switch":
                session_id = parts[2] if len(parts) > 2 else ""
                session, full_session_id = self._resolve_user_session(user_id, session_id)
                if not session:
                    await self._reply_to_callback_origin(query, text="❌ Session not found.")
                    return

                self.sessions.switch_session(user_id, full_session_id)
                text, reply_markup = self._build_session_detail_message(user_id, session, full_session_id)
                await self._reply_to_callback_origin(
                    query,
                    text=text,
                    reply_markup=reply_markup,
                )
                return

            if action == "history":
                session_id = parts[2] if len(parts) > 2 else ""
                session, full_session_id = self._resolve_user_session(user_id, session_id)
                if not session:
                    await self._reply_to_callback_origin(query, text="❌ Session not found.")
                    return

                text, reply_markup = self._build_history_message(user_id, session, full_session_id)
                await self._reply_to_callback_origin(
                    query,
                    text=text,
                    reply_markup=reply_markup,
                )
                return

            if action == "list":
                text, buttons = self._build_session_list_view(
                    user_id,
                    include_timestamp=True,
                )
                await self._reply_to_callback_origin(
                    query,
                    text=text,
                    reply_markup=InlineKeyboardMarkup(buttons),
                )
                return

            await self._reply_to_callback_origin(query, text="Unknown command")

        except BadRequest as e:
            if "Message is not modified" in str(e):
                pass
            else:
                logger.warning(f"Response session callback BadRequest: {e}")
        except Exception as e:
            logger.exception(f"Response session callback error: {e}")
            try:
                await self._reply_to_callback_origin(
                    query,
                    text=f"Error occurred.\n\n<code>{escape_html(str(e))}</code>",
                )
            except Exception:
                pass

    async def _handle_new_session_name_prompt(self, query, chat_id: int, model: str) -> None:
        """Prompt for new session name."""
        selected_provider = self._get_selected_ai_provider(str(chat_id))
        provider = infer_provider_from_model(model)
        if not is_supported_model(provider, model):
            provider = selected_provider
        normalized_model = model if is_supported_model(provider, model) else get_default_model(provider)
        model_emoji = get_model_emoji(normalized_model)

        await query.edit_message_text(
            text=f"{model_emoji} <b>{get_profile_label(provider, normalized_model)}</b> session creation\n\n"
                 f"AI: <b>{self._format_provider_display(provider)}</b>\n\n"
                 f"Enter session name.\n"
                 f"Creating this session will also switch the current AI:",
            parse_mode="HTML"
        )

        await query.message.reply_text(
            text=f"Enter session name (sess_name:{normalized_model})",
            reply_markup=ForceReply(selective=True, input_field_placeholder="Session name...")
        )

    async def _handle_new_session_menu_callback(self, query, chat_id: int) -> None:
        """Show a compact new-session model picker for the current provider."""
        keyboard = [
            *self._build_new_session_picker_keyboard(),
            [
                InlineKeyboardButton(BUTTON_SESSION_LIST, callback_data="sess:list"),
                InlineKeyboardButton(BUTTON_SWITCH_AI, callback_data="ai:open"),
            ],
        ]

        await query.edit_message_text(
            text=self._build_new_session_picker_text(str(chat_id)),
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="HTML",
        )

    async def _handle_rename_prompt_callback(self, query, chat_id: int, session_id: str) -> None:
        """Handle rename button - prompt for new name via ForceReply."""
        session_name = self.sessions.get_session_name(session_id) or "(unnamed)"

        await query.edit_message_text(
            text=f"✏️ <b>Rename Session</b>\n\n"
            f"- Current: {escape_html(session_name)}\n"
            f"- ID: <code>{session_id[:8]}</code>\n\n"
            f"Enter new name below:",
            parse_mode="HTML"
        )

        await query.message.reply_text(
            text=f"Enter new name (sess_rename:{session_id})",
            reply_markup=ForceReply(selective=True, input_field_placeholder="New session name...")
        )

    async def _handle_new_session_callback(self, query, chat_id: int, model: str, name: str = "") -> None:
        """Handle new session creation callback."""
        user_id = str(chat_id)
        selected_provider = self._get_selected_ai_provider(user_id)
        provider = infer_provider_from_model(model)
        if not is_supported_model(provider, model):
            provider = selected_provider
        model_name = model if is_supported_model(provider, model) else get_default_model(provider)

        self._set_selected_ai_provider(user_id, provider)
        session_id = self.sessions.create_session(
            user_id=user_id,
            ai_provider=provider,
            model=model_name,
            name=name,
            first_message="(new session)",
        )
        short_id = session_id[:8]

        model_emoji = get_model_emoji(model_name)

        keyboard = [
            [
                InlineKeyboardButton(BUTTON_SESSION, callback_data=f"sess:switch:{session_id}"),
                InlineKeyboardButton(BUTTON_SESSION_LIST, callback_data="sess:list"),
            ]
        ]

        await query.edit_message_text(
            text=f"New session created!\n\n"
                 f"<b>AI:</b> {self._format_provider_display(provider)}\n"
                 f"{model_emoji} <b>Model:</b> {get_profile_label(provider, model_name)}\n"
                 f"<b>ID:</b> <code>{short_id}</code>",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="HTML"
        )

    async def _handle_switch_session_callback(self, query, chat_id: int, session_id: str) -> None:
        """Handle session switch callback - shows full session info like /session."""
        user_id = str(chat_id)
        session, full_session_id = self._resolve_user_session(user_id, session_id)
        if not session:
            await query.edit_message_text("❌ Session not found.")
            return

        self.sessions.switch_session(user_id, full_session_id)
        text, reply_markup = self._build_session_detail_message(user_id, session, full_session_id)

        await query.edit_message_text(
            text=text,
            reply_markup=reply_markup,
            parse_mode="HTML"
        )

    async def _handle_delete_session_confirm(self, query, chat_id: int, session_id: str) -> None:
        """Handle session delete confirmation."""
        user_id = str(chat_id)
        session = self.sessions.get_session_by_prefix(user_id, session_id[:8])
        if not session:
            await query.edit_message_text("❌ Session not found.")
            return

        full_session_id = session.get("full_session_id", session_id)
        short_id = full_session_id[:8]
        name = session.get("name") or f"Session {short_id}"

        current_session_id = self.sessions.get_current_session_id(user_id)
        if current_session_id == full_session_id:
            keyboard = [[InlineKeyboardButton(BUTTON_BACK, callback_data="sess:list")]]
            await query.edit_message_text(
                text=f"<b>Cannot Delete</b>\n\n"
                     f"<b>{escape_html(name)}</b> is currently in use.\n\n"
                     f"Switch to another session before deleting.",
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode="HTML"
            )
            return

        keyboard = [
            [
                InlineKeyboardButton(BUTTON_DELETE, callback_data=f"sess:confirm_del:{full_session_id}"),
                InlineKeyboardButton(BUTTON_CANCEL, callback_data="sess:cancel"),
            ]
        ]

        await query.edit_message_text(
            text=f"<b>Delete Session Confirmation</b>\n\n"
                 f"<b>{escape_html(name)}</b>\n"
                 f"ID: <code>{short_id}</code>\n\n"
                 f"Are you sure?",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="HTML"
        )

    async def _handle_delete_session_execute(self, query, chat_id: int, session_id: str) -> None:
        """Execute session deletion."""
        user_id = str(chat_id)
        session = self.sessions.get_session_by_prefix(user_id, session_id[:8])
        if not session:
            await query.edit_message_text("❌ Session not found.")
            return

        full_session_id = session.get("full_session_id", session_id)
        short_id = full_session_id[:8]
        name = session.get("name") or f"Session {short_id}"

        current_session_id = self.sessions.get_current_session_id(user_id)
        if current_session_id == full_session_id:
            keyboard = [[InlineKeyboardButton(BUTTON_BACK, callback_data="sess:list")]]
            await query.edit_message_text(
                text=f"<b>Cannot Delete</b>\n\n"
                     f"<b>{escape_html(name)}</b> is currently in use.\n\n"
                     f"Switch to another session before deleting.",
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode="HTML"
            )
            return

        self.sessions.delete_session(user_id, full_session_id)

        await self._handle_session_list_callback(query, chat_id, f"<s>{escape_html(name)}</s> deleted!\n\n")

    async def _handle_history_callback(self, query, chat_id: int, session_id: str) -> None:
        """Handle session history callback."""
        user_id = str(chat_id)
        session, full_session_id = self._resolve_user_session(user_id, session_id)
        if not session:
            await query.edit_message_text("❌ Session not found.")
            return

        text, reply_markup = self._build_history_message(user_id, session, full_session_id)

        await query.edit_message_text(
            text=text,
            reply_markup=reply_markup,
            parse_mode="HTML"
        )

    async def _handle_session_list_callback(self, query, chat_id: int, prefix: str = "") -> None:
        """Handle session list callback."""
        user_id = str(chat_id)
        text, buttons = self._build_session_list_view(
            user_id,
            prefix=prefix,
            include_timestamp=True,
        )

        await query.edit_message_text(
            text=text,
            reply_markup=InlineKeyboardMarkup(buttons),
            parse_mode="HTML"
        )

    async def _handle_model_change_callback(self, query, chat_id: int, model: str, session_id: str) -> None:
        """Handle model change callback."""
        user_id = str(chat_id)
        session = self.sessions.get_session_by_prefix(user_id, session_id[:8])
        if not session:
            await query.edit_message_text("❌ Session not found.")
            return

        full_session_id = session.get("full_session_id", session_id)
        provider = session.get("ai_provider", self._get_selected_ai_provider(user_id))
        if not is_supported_model(provider, model):
            await query.edit_message_text("❌ Unsupported model for this AI.")
            return

        self.sessions.update_session_model(full_session_id, model)

        short_id = full_session_id[:8]
        name = session.get("name") or f"Session {short_id}"
        model_emoji = get_model_emoji(model)

        model_buttons = self._build_model_buttons(
            provider,
            "sess:model:",
            callback_suffix=f":{full_session_id}",
        )
        keyboard = [
            model_buttons,
            [
                InlineKeyboardButton(BUTTON_SESSION_LIST, callback_data="sess:list"),
            ]
        ]

        await query.edit_message_text(
            text=f"Model changed!\n\n"
                 f"<b>{escape_html(name)}</b>\n"
                 f"AI: <b>{self._format_provider_display(provider)}</b>\n"
                 f"{model_emoji} Model: <b>{get_profile_label(provider, model)}</b>\n"
                 f"ID: <code>{short_id}</code>",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="HTML"
        )
