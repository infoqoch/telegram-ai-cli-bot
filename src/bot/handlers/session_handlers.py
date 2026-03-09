"""Session-related command handlers."""

from pathlib import Path

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

from src.ai import (
    get_default_model,
    get_profile_label,
    get_provider_profiles,
    infer_provider_from_model,
    is_supported_model,
    is_supported_provider,
)
from src.logging_config import logger, clear_context
from src.ui_emoji import (
    BUTTON_HISTORY,
    BUTTON_NEW_SESSION,
    BUTTON_RENAME,
    BUTTON_SESSION_LIST,
    BUTTON_SWITCH_AI,
    BUTTON_DELETE,
)
from ..constants import MAX_SESSION_NAME_LENGTH, get_model_emoji
from ..formatters import escape_html, truncate_message
from ..middleware import authorized_only, authenticated_only
from .base import BaseHandler


class SessionHandlers(BaseHandler):
    """Session command handlers."""

    async def _require_claude_shortcut(self, update: Update) -> bool:
        """Return True when Claude-specific shortcut may continue."""
        provider = self._get_selected_ai_provider(str(update.effective_chat.id))
        if provider == "claude":
            return True

        await update.message.reply_text(
            "This shortcut is Claude-only.\n"
            "Use /select_ai to switch to Claude first."
        )
        return False

    def _build_provider_switch_text(self, user_id: str, provider: str) -> str:
        """Build a short provider selection summary."""
        current_session_id = self.sessions.get_current_session_id(user_id, provider)
        current_line = (
            f"Current session: <code>{current_session_id[:8]}</code>"
            if current_session_id else
            "Current session: none"
        )
        return (
            f"<b>Select AI</b>\n\n"
            f"Current AI: <b>{self._format_provider_display(provider)}</b>\n"
            f"{current_line}\n\n"
            f"Choose which provider `/new`, `/sl`, `/session`, `/model`, `/ai`, and normal chat should use."
        )

    @authorized_only
    @authenticated_only
    async def select_ai_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle /select_ai command."""
        chat_id = update.effective_chat.id
        user_id = str(chat_id)
        self._setup_request_context(chat_id)

        current_provider = self._get_selected_ai_provider(user_id)

        if context.args:
            provider = context.args[0].lower()
            if not is_supported_provider(provider):
                await update.message.reply_text(
                    "❌ Unsupported AI provider.\n\n"
                    "Available: claude, codex"
                )
                clear_context()
                return

            self._set_selected_ai_provider(user_id, provider)
            await update.message.reply_text(
                f"✅ Current AI switched to <b>{self._format_provider_display(provider)}</b>.\n\n"
                f"{self._build_provider_switch_text(user_id, provider)}",
                parse_mode="HTML",
            )
            clear_context()
            return

        keyboard = self._build_ai_selector_keyboard(current_provider)
        keyboard.append([
            InlineKeyboardButton(BUTTON_SESSION_LIST, callback_data="sess:list"),
            InlineKeyboardButton(BUTTON_NEW_SESSION, callback_data="sess:new"),
        ])

        await update.message.reply_text(
            self._build_provider_switch_text(user_id, current_provider),
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="HTML",
        )
        clear_context()

    @authorized_only
    @authenticated_only
    async def new_session(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle /new command.

        Usage:
            /new              - Show model selection buttons
            /new opus         - Opus model
            /new haiku name   - Haiku model + session name
        """
        chat_id = update.effective_chat.id
        user_id = str(chat_id)
        self._setup_request_context(chat_id)
        provider = self._get_selected_ai_provider(user_id)
        provider_label = self._format_provider_display(provider)

        if not context.args:
            keyboard = [
                *self._build_new_session_picker_keyboard(),
                [
                    InlineKeyboardButton(BUTTON_SESSION_LIST, callback_data="sess:list"),
                    InlineKeyboardButton(BUTTON_SWITCH_AI, callback_data="ai:open"),
                ]
            ]
            await update.message.reply_text(
                self._build_new_session_picker_text(user_id),
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode="HTML"
            )
            clear_context()
            return

        model = get_default_model(provider)
        session_name = ""

        first_arg = context.args[0].lower()
        target_provider = infer_provider_from_model(first_arg)
        if is_supported_model(target_provider, first_arg):
            provider = target_provider
            model = first_arg
            provider_label = self._format_provider_display(provider)
            if len(context.args) > 1:
                session_name = " ".join(context.args[1:])
        else:
            session_name = " ".join(context.args)

        if len(session_name) > MAX_SESSION_NAME_LENGTH:
            session_name = session_name[:MAX_SESSION_NAME_LENGTH]

        logger.info(
            f"/new command - provider={provider}, model={model}, name={session_name or '(none)'}"
        )

        model_emoji = get_model_emoji(model)
        logger.trace("Sending session creation message")
        await update.message.reply_text(
            f"Creating new {provider_label} session... "
            f"{model_emoji} {get_profile_label(provider, model)}"
        )

        logger.trace("Saving session")
        self._set_selected_ai_provider(user_id, provider)
        session_id = self.sessions.create_session(
            user_id=user_id,
            ai_provider=provider,
            model=model,
            name=session_name,
            first_message="(new session)",
        )
        logger.info(f"New session created: {session_id[:8]}, provider={provider}, model={model}")

        name_line = f"\n- Name: {escape_html(session_name)}" if session_name else ""
        await update.message.reply_text(
            f"✅ New session created!\n"
            f"- ID: <code>{session_id[:8]}</code>{name_line}\n"
            f"- AI: {provider_label}\n"
            f"- Model: {model_emoji} {get_profile_label(provider, model)}",
            parse_mode="HTML"
        )
        logger.trace("/new complete")
        clear_context()

    async def new_session_opus(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle /new_opus command - shortcut for /new opus."""
        if not await self._require_claude_shortcut(update):
            return
        context.args = ["opus"]
        await self.new_session(update, context)

    async def new_session_sonnet(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle /new_sonnet command - shortcut for /new sonnet."""
        if not await self._require_claude_shortcut(update):
            return
        context.args = ["sonnet"]
        await self.new_session(update, context)

    async def new_session_haiku(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle /new_haiku command - shortcut for /new haiku."""
        if not await self._require_claude_shortcut(update):
            return
        context.args = ["haiku"]
        await self.new_session(update, context)

    async def new_session_haiku_speedy(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle /new_haiku_speedy command - quick haiku session with name."""
        if not await self._require_claude_shortcut(update):
            return
        context.args = ["haiku", "Speedy"]
        await self.new_session(update, context)

    async def new_session_opus_smarty(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle /new_opus_smarty command - smart opus session with name."""
        if not await self._require_claude_shortcut(update):
            return
        context.args = ["opus", "Smarty"]
        await self.new_session(update, context)

    @authorized_only
    @authenticated_only
    async def new_workspace_session(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle /new_workspace command - create workspace-bound session.

        Usage:
            /new_workspace /path/to/workspace           - Default model (sonnet)
            /new_workspace /path/to/workspace opus      - Opus model
            /new_workspace /path/to/workspace haiku name - Haiku model + session name
        """
        from src.config import get_settings

        chat_id = update.effective_chat.id
        user_id = str(chat_id)
        args = context.args or []
        provider = self._get_selected_ai_provider(user_id)
        provider_label = self._format_provider_display(provider)
        provider_profiles = get_provider_profiles(provider)

        if not args:
            await update.message.reply_text(
                "📁 <b>Workspace Session Usage</b>\n\n"
                f"Current AI: <b>{provider_label}</b>\n\n"
                "<code>/new_workspace path [model] [name]</code>\n\n"
                "Examples:\n"
                "• <code>/new_workspace ~/Projects/my-app</code>\n"
                f"• <code>/new_workspace ~/AiSandbox/bot {get_default_model(provider)}</code>\n"
                f"• <code>/new_workspace ~/work/api {provider_profiles[-1].key} MyBot</code>",
                parse_mode="HTML"
            )
            return

        workspace_path = args[0]

        settings = get_settings()
        is_valid, error_msg = settings.validate_project_path(workspace_path)
        if not is_valid:
            await update.message.reply_text(f"{error_msg}", parse_mode="HTML")
            return

        model = None
        session_name = ""
        if len(args) > 1:
            potential_model = args[1].lower()
            if potential_model in {profile.key for profile in provider_profiles}:
                model = potential_model
                if len(args) > 2:
                    session_name = " ".join(args[2:])
            else:
                session_name = " ".join(args[1:])

        expanded_path = str(Path(workspace_path).expanduser().resolve())
        workspace_name = Path(expanded_path).name
        display_name = session_name or f"[ws]{workspace_name}"

        logger.info(f"/new_workspace - path={expanded_path}, model={model}, name={display_name}")
        session_id = self.sessions.create_session(
            user_id=user_id,
            ai_provider=provider,
            model=model or get_default_model(provider),
            name=display_name,
            workspace_path=expanded_path,
            first_message=f"(workspace: {workspace_name})",
        )

        model_label = get_profile_label(provider, model or get_default_model(provider))

        claude_md_exists = (Path(expanded_path) / "CLAUDE.md").exists()
        claude_dir_exists = (Path(expanded_path) / ".claude").exists()
        config_status = "CLAUDE.md" if claude_md_exists else (".claude/" if claude_dir_exists else "No config")

        await update.message.reply_text(
            f"📁 <b>Workspace Session Created</b>\n\n"
            f"- Path: <code>{expanded_path}</code>\n"
            f"- AI: {provider_label}\n"
            f"- Model: {model_label}\n"
            f"- Name: {escape_html(display_name)}\n"
            f"- Config: {config_status}\n\n"
            f"This session follows the workspace instructions in that project.",
            parse_mode="HTML"
        )

    async def model_opus_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle /model_opus command - shortcut for /model opus."""
        if not await self._require_claude_shortcut(update):
            return
        context.args = ["opus"]
        await self.model_command(update, context)

    async def model_sonnet_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle /model_sonnet command - shortcut for /model sonnet."""
        if not await self._require_claude_shortcut(update):
            return
        context.args = ["sonnet"]
        await self.model_command(update, context)

    async def model_haiku_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle /model_haiku command - shortcut for /model haiku."""
        if not await self._require_claude_shortcut(update):
            return
        context.args = ["haiku"]
        await self.model_command(update, context)

    @authorized_only
    @authenticated_only
    async def model_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle /model command - redirect to /session or change model.

        Usage:
            /model         - Redirect to /session (shows full session info with model buttons)
            /model opus    - Change to Opus
            /model sonnet  - Change to Sonnet
            /model haiku   - Change to Haiku
        """
        chat_id = update.effective_chat.id
        user_id = str(chat_id)
        self._setup_request_context(chat_id)
        logger.info("/model command received")
        provider = self._get_selected_ai_provider(user_id)

        # No args → redirect to /session (has model change buttons)
        if not context.args:
            logger.trace("/model without args → redirect to /session")
            await self.session_command(update, context)
            return

        session_id = self.sessions.get_current_session_id(user_id)
        if not session_id:
            logger.trace("No active session")
            await update.message.reply_text(
                "❌ No active session.\n\n"
                f"Current AI: <b>{self._format_provider_display(provider)}</b>\n\n"
                "Create one with:\n"
                "<code>/new</code>\n"
                f"Or pick a profile directly: <code>/new {get_default_model(provider)}</code>",
                parse_mode="HTML"
            )
            clear_context()
            return

        current_model = self.sessions.get_session_model(session_id)

        new_model = context.args[0].lower()
        if not is_supported_model(provider, new_model):
            await update.message.reply_text(
                f"❌ Unsupported model: {new_model}\n\n"
                f"Available: {', '.join(profile.key for profile in get_provider_profiles(provider))}",
            )
            clear_context()
            return

        if new_model == current_model:
            model_emoji = get_model_emoji(current_model)
            await update.message.reply_text(
                f"Already using {model_emoji} {get_profile_label(provider, current_model)}."
            )
            clear_context()
            return

        if self.sessions.update_session_model(session_id, new_model):
            logger.info(f"Model changed: {current_model} -> {new_model}, session={session_id[:8]}")

            model_emoji = get_model_emoji(new_model)
            await update.message.reply_text(
                f"✅ Model changed!\n\n"
                f"- Previous: {get_profile_label(provider, current_model)}\n"
                f"- Current: {model_emoji} {get_profile_label(provider, new_model)}",
                parse_mode="HTML"
            )
        else:
            await update.message.reply_text("❌ Session not found.")

        clear_context()

    @authorized_only
    @authenticated_only
    async def session_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle /session command - show current session info with buttons."""
        chat_id = update.effective_chat.id
        self._setup_request_context(chat_id)
        logger.info("/session command received")

        user_id = str(chat_id)
        provider = self._get_selected_ai_provider(user_id)
        provider_label = self._format_provider_display(provider)

        logger.trace("Getting current session")
        session_id = self.sessions.get_current_session_id(user_id)
        if not session_id:
            logger.trace("No active session")
            keyboard = [
                *self._build_new_session_picker_keyboard(),
                [
                    InlineKeyboardButton(BUTTON_SESSION_LIST, callback_data="sess:list"),
                    InlineKeyboardButton(BUTTON_SWITCH_AI, callback_data="ai:open"),
                ]
            ]
            await update.message.reply_text(
                f"❌ No active session.\n\n"
                f"Current AI: <b>{provider_label}</b>\n"
                f"Select a model. Choosing one also switches the current AI:",
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode="HTML"
            )
            clear_context()
            return

        logger.trace(f"Getting session history - session={session_id[:8]}")
        history_entries = self.sessions.get_session_history_entries(session_id)
        count = len(history_entries)
        model = self.sessions.get_session_model(session_id)
        session_provider = self.sessions.get_session_ai_provider(session_id) or provider
        model_emoji = get_model_emoji(model)
        session_name = self.sessions.get_session_name(session_id)
        logger.trace(f"History count: {count}, model: {model}, name: {session_name or '(none)'}")

        recent = history_entries[-10:]
        history_lines = []
        start_idx = len(history_entries) - len(recent) + 1

        processor_emoji = {
            "claude": "",
            "command": "[cmd]",
            "rejected": "[x]",
        }

        for i, entry in enumerate(recent, start=start_idx):
            msg = entry.get("message", "") if isinstance(entry, dict) else str(entry)
            processor = entry.get("processor", "claude") if isinstance(entry, dict) else "claude"

            if processor.startswith("plugin:"):
                emoji = "[plugin]"
            else:
                emoji = processor_emoji.get(processor, "")

            short_q = truncate_message(msg, 35)
            history_lines.append(f"{i}. {emoji} {escape_html(short_q)}")

        history_text = "\n".join(history_lines) if history_lines else "(empty)"

        name_line = f"- Name: {escape_html(session_name)}\n" if session_name else ""

        model_buttons = self._build_model_buttons(
            session_provider,
            "sess:model:",
            callback_suffix=f":{session_id}",
        )
        keyboard = [
            model_buttons,
            [
                InlineKeyboardButton(BUTTON_RENAME, callback_data=f"sess:rename:{session_id}"),
                InlineKeyboardButton(BUTTON_HISTORY, callback_data=f"sess:history:{session_id}"),
                InlineKeyboardButton(BUTTON_DELETE, callback_data=f"sess:delete:{session_id}"),
            ],
            [
                InlineKeyboardButton(BUTTON_SESSION_LIST, callback_data="sess:list"),
                InlineKeyboardButton(BUTTON_SWITCH_AI, callback_data="ai:open"),
            ]
        ]

        await update.message.reply_text(
            f"<b>Current Session</b>\n\n"
            f"- AI: {self._format_provider_display(session_provider)}\n"
            f"- ID: <code>{session_id[:8]}</code>\n"
            f"{name_line}"
            f"- Model: {model_emoji} {get_profile_label(session_provider, model)}\n"
            f"- Messages: {count}\n\n"
            f"<b>History</b> (last 10)\n{history_text}",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="HTML"
        )
        logger.trace("/session complete")
        clear_context()

    @authorized_only
    @authenticated_only
    async def session_list_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle /session_list command - button-based session list."""
        chat_id = update.effective_chat.id
        self._setup_request_context(chat_id)
        logger.info("/session_list command received")

        user_id = str(chat_id)
        text, buttons = self._build_session_list_view(user_id)

        await update.message.reply_text(
            text,
            reply_markup=InlineKeyboardMarkup(buttons),
            parse_mode="HTML"
        )
        logger.trace("/session_list complete")
        clear_context()

    @authorized_only
    @authenticated_only
    async def switch_session_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle /s_<id> command for session switching."""
        chat_id = update.effective_chat.id
        self._setup_request_context(chat_id)

        user_id = str(chat_id)

        text = update.message.text
        if not text.startswith("/s_"):
            clear_context()
            return

        target = text[3:]
        logger.info(f"Session switch request: /s_{target}")

        logger.trace(f"Searching session - prefix={target}")
        target_info = self.sessions.get_session_by_prefix(user_id, target)
        if not target_info:
            logger.debug(f"Session not found: {target}")
            await update.message.reply_text(f"Session '{target}' not found.")
            clear_context()
            return

        logger.trace(f"Switching session - target={target_info['session_id']}")
        full_session_id = target_info["full_session_id"]
        if self.sessions.switch_session(user_id, full_session_id):
            logger.info(f"Session switch successful: {target_info['session_id']}")
            model_emoji = get_model_emoji(target_info["model"])
            await update.message.reply_text(
                f"Session switched!\n\n"
                f"- AI: {self._format_provider_display(target_info['ai_provider'])}\n"
                f"- ID: <code>{target_info['session_id']}</code>\n"
                f"- Model: {model_emoji} {get_profile_label(target_info['ai_provider'], target_info['model'])}\n"
                f"- Messages: {target_info['history_count']}",
                parse_mode="HTML"
            )
        else:
            logger.error(f"Session switch failed: {target}")
            await update.message.reply_text("Session switch failed")

        clear_context()

    @authorized_only
    @authenticated_only
    async def rename_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle /rename command - rename current session or specific session."""
        chat_id = update.effective_chat.id
        user_id = str(chat_id)
        self._setup_request_context(chat_id)
        logger.info("/rename command received")

        text = update.message.text

        # /r_sessionID_newname format support
        if text.startswith("/r_") and "_" in text[3:]:
            parts = text[3:].split("_", 1)
            if len(parts) == 2:
                target_prefix = parts[0]
                new_name = parts[1]

                target_info = self.sessions.get_session_by_prefix(user_id, target_prefix)
                if not target_info:
                    logger.debug(f"Session not found: {target_prefix}")
                    await update.message.reply_text(f"Session <code>{target_prefix}</code> not found.", parse_mode="HTML")
                    clear_context()
                    return

                session_id = target_info["full_session_id"]

                if len(new_name) > 50:
                    await update.message.reply_text("Name too long. (max 50 chars)")
                    clear_context()
                    return

                if self.sessions.rename_session(session_id, new_name):
                    logger.info(f"Session renamed: {session_id[:8]} -> {new_name}")
                    await update.message.reply_text(
                        f"Session renamed!\n\n"
                        f"- Session: <code>{session_id[:8]}</code>\n"
                        f"- Name: {escape_html(new_name)}",
                        parse_mode="HTML"
                    )
                else:
                    await update.message.reply_text("Rename failed")

                clear_context()
                return
            else:
                await update.message.reply_text(
                    "Usage: <code>/r_sessionID_newname</code>\n"
                    "Example: <code>/r_a1b2c3d4_MyBot</code>",
                    parse_mode="HTML"
                )
                clear_context()
                return

        # Current session rename (/rename or /rename_newname)
        session_id = self.sessions.get_current_session_id(user_id)
        if not session_id:
            logger.trace("No active session")
            await update.message.reply_text("❌ No active session.")
            clear_context()
            return

        # /rename_newname format support
        if text.startswith("/rename_"):
            new_name = text[8:]
        elif context.args:
            new_name = " ".join(context.args)
        else:
            current_name = self.sessions.get_session_name(session_id)
            logger.trace(f"Current name: {current_name or '(none)'}")
            await update.message.reply_text(
                f"<b>Rename Session</b>\n\n"
                f"- Current: {current_name or '(unnamed)'}\n"
                f"- Session: <code>{session_id[:8]}</code>\n\n"
                f"Usage: <code>/rename_newname</code>\n"
                f"Or: <code>/r_sessionID_newname</code>",
                parse_mode="HTML"
            )
            clear_context()
            return

        if len(new_name) > 50:
            await update.message.reply_text("❌ Name too long. (max 50 chars)")
            clear_context()
            return

        if self.sessions.rename_session(session_id, new_name):
            await update.message.reply_text(
                f"✅ Session renamed!\n\n"
                f"- Session: <code>{session_id[:8]}</code>\n"
                f"- Name: {escape_html(new_name)}",
                parse_mode="HTML"
            )
        else:
            await update.message.reply_text("❌ Rename failed.")

        clear_context()

    @authorized_only
    @authenticated_only
    async def delete_session_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle /d_<id> command for deleting a session."""
        chat_id = update.effective_chat.id
        user_id = str(chat_id)
        self._setup_request_context(chat_id)

        text = update.message.text
        if text.startswith("/delete_"):
            target = text[8:]
        elif text.startswith("/d_"):
            target = text[3:]
        else:
            clear_context()
            return

        logger.info(f"Session delete request: {target}")

        target_info = self.sessions.get_session_by_prefix(user_id, target)
        if not target_info:
            logger.debug(f"Session not found: {target}")
            await update.message.reply_text(f"❌ Session '{target}' not found.")
            clear_context()
            return

        full_session_id = target_info["full_session_id"]
        session_name = target_info.get("name", "")

        current_session_id = self.sessions.get_current_session_id(user_id)
        if current_session_id == full_session_id:
            name_info = f" ({session_name})" if session_name else ""
            await update.message.reply_text(
                f"❌ Cannot delete the current session.\n\n"
                f"- ID: <code>{target_info['session_id']}</code>{name_info}\n\n"
                f"Switch to another session or create a new one first.",
                parse_mode="HTML"
            )
            clear_context()
            return

        if self.sessions.delete_session(user_id, full_session_id):
            name_info = f" ({session_name})" if session_name else ""
            await update.message.reply_text(
                f"🗑️ Session deleted!\n\n"
                f"- ID: <code>{target_info['session_id']}</code>{name_info}\n"
                f"- Messages: {target_info['history_count']}",
                parse_mode="HTML"
            )
        else:
            await update.message.reply_text("❌ Session delete failed.")

        clear_context()

    @authorized_only
    @authenticated_only
    async def history_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle /h_<id> command for viewing session history."""
        chat_id = update.effective_chat.id
        self._setup_request_context(chat_id)

        user_id = str(chat_id)

        text = update.message.text
        if text.startswith("/history_"):
            target = text[9:]
        elif text.startswith("/h_"):
            target = text[3:]
        else:
            clear_context()
            return

        logger.info(f"History request: {target}")

        logger.trace(f"Searching session - prefix={target}")
        target_info = self.sessions.get_session_by_prefix(user_id, target)
        if not target_info:
            logger.debug(f"Session not found: {target}")
            await update.message.reply_text(f"❌ Session '{target}' not found.")
            clear_context()
            return

        logger.trace(f"History lookup - session={target_info['full_session_id'][:8]}")
        history = self.sessions.get_session_history(target_info["full_session_id"])
        if not history:
            logger.trace("No history")
            await update.message.reply_text("📭 No history.")
            clear_context()
            return

        logger.trace(f"History count: {len(history)}")

        history_lines = []
        for i, q in enumerate(history, start=1):
            short_q = truncate_message(q, 60)
            history_lines.append(f"{i}. {escape_html(short_q)}")

        history_text = "\n".join(history_lines)

        await update.message.reply_text(
            f"<b>Session History</b>\n"
            f"- ID: <code>{target_info['session_id']}</code>\n"
            f"- Messages: {len(history)}\n\n"
            f"{history_text}\n\n"
            f"/s_{target_info['session_id']} Switch to this session",
            parse_mode="HTML"
        )
        logger.trace("History lookup complete")
        clear_context()

    @authorized_only
    async def back_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle /back command - return to previous session."""
        chat_id = update.effective_chat.id
        user_id = str(chat_id)
        self._setup_request_context(chat_id)
        logger.info("/back command received")

        prev_session_id = self.sessions.get_previous_session_id(user_id)
        if not prev_session_id:
            await update.message.reply_text(
                "❌ No previous session.\n\n"
                "Use /sl to see sessions across both AIs."
            )
            clear_context()
            return

        session_info = self.sessions.get_session_by_prefix(user_id, prev_session_id[:8])
        if not session_info:
            await update.message.reply_text("❌ Previous session not found.")
            self.sessions.set_previous_session_id(user_id, None)
            clear_context()
            return

        self.sessions.set_current(user_id, prev_session_id)
        self.sessions.set_previous_session_id(user_id, None)

        name = self.sessions.get_session_name(prev_session_id)
        name_display = f" ({escape_html(name)})" if name else ""

        await update.message.reply_text(
            f"✅ Switched back!\n\n"
            f"- AI: {self._format_provider_display(self.sessions.get_session_ai_provider(prev_session_id) or self._get_selected_ai_provider(user_id))}\n"
            f"- ID: <code>{prev_session_id[:8]}</code>{name_display}",
            parse_mode="HTML"
        )
        clear_context()
