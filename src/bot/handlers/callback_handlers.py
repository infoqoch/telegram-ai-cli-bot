"""Callback query handlers - router and small utility callbacks."""
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ForceReply
from telegram.error import BadRequest
from telegram.ext import ContextTypes

from src.ai import (
    get_default_model,
    get_profile_label,
    infer_provider_from_model,
    is_supported_model,
)
from src.logging_config import logger, clear_context
from src.ui_emoji import BUTTON_BACK, BUTTON_NEW_SESSION, BUTTON_REFRESH, BUTTON_SESSION, BUTTON_SESSION_LIST, BUTTON_SWITCH_AI
from ..constants import get_model_emoji
from ..formatters import escape_html
from .base import BaseHandler


class CallbackHandlers(BaseHandler):
    """Callback query handlers - router and small utility callbacks."""

    async def callback_query_handler(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle inline button callbacks."""
        query = update.callback_query
        if not query:
            return

        chat_id = query.message.chat_id if query.message else None
        if not chat_id:
            return

        self._setup_request_context(chat_id)
        callback_data = query.data or ""
        logger.info(f"Callback query: {callback_data} (chat_id={chat_id})")

        if not self._is_authorized(chat_id):
            logger.debug("Callback denied - unauthorized")
            await query.answer("⛔ Access denied.", show_alert=True)
            clear_context()
            return

        allow_unauthenticated_menu = callback_data in {"menu:open", "menu:help"}
        if not allow_unauthenticated_menu and not self._is_authenticated(str(chat_id)):
            logger.debug("Callback denied - auth required")
            await query.answer("🔒 Authentication required.\n/auth <key>", show_alert=True)
            clear_context()
            return

        await query.answer()

        if callback_data.startswith("menu:"):
            await self._handle_menu_callback(query, chat_id, callback_data)
            return

        if callback_data.startswith("plug:"):
            await self._handle_plugin_hub_callback(query, chat_id, callback_data)
            return

        # Plugin auto-routing (CALLBACK_PREFIX 기반)
        if self.plugins:
            plugin = self.plugins.get_plugin_for_callback(callback_data)
            if plugin:
                await self._handle_plugin_callback(query, chat_id, callback_data, plugin)
                return

        if callback_data.startswith("ai:"):
            await self._handle_ai_callback(query, chat_id, callback_data)
            return

        if callback_data.startswith("resp:"):
            await self._handle_response_session_callback(query, chat_id, callback_data)
            return

        # Session callback
        if callback_data.startswith("sess:"):
            await self._handle_session_callback(query, chat_id, callback_data)
            return

        # Tasks callback
        if callback_data.startswith("tasks:"):
            await self._handle_tasks_callback(query, chat_id)
            return

        # Scheduler callback
        if callback_data.startswith("sched:"):
            await self._handle_scheduler_callback(query, chat_id, callback_data)
            return

        # Workspace callback
        if callback_data.startswith("ws:"):
            await self._handle_workspace_callback(query, chat_id, callback_data)
            return

        # Session queue callback (new method)
        if callback_data.startswith("sq:"):
            await self._handle_session_queue_callback(query, chat_id, callback_data)
            return

        logger.warning(f"Unknown callback: {callback_data}")

    async def _handle_menu_callback(self, query, chat_id: int, callback_data: str) -> None:
        """Handle `/menu` launcher callbacks."""
        action = callback_data.split(":", 1)[1] if ":" in callback_data else "open"
        user_id = str(chat_id)

        if action == "open":
            await query.edit_message_text(
                self._build_menu_text(chat_id),
                reply_markup=self._build_menu_keyboard(chat_id),
                parse_mode="HTML",
            )
            return

        if action == "help":
            await query.edit_message_text(
                self._build_main_help_text(),
                reply_markup=self._build_menu_back_markup(),
                parse_mode="HTML",
            )
            return

        if action == "sessions":
            text, buttons = self._build_session_list_view(
                user_id,
                include_timestamp=True,
                launcher_context="menu",
            )
            await query.edit_message_text(
                text,
                reply_markup=InlineKeyboardMarkup(buttons),
                parse_mode="HTML",
            )
            return

        if action == "new":
            keyboard = [
                *self._build_new_session_picker_keyboard(),
                [
                    InlineKeyboardButton(BUTTON_SESSION_LIST, callback_data="menu:sessions"),
                    InlineKeyboardButton(BUTTON_SWITCH_AI, callback_data="menu:ai"),
                ],
                [InlineKeyboardButton(BUTTON_BACK, callback_data="menu:open")],
            ]
            await query.edit_message_text(
                self._build_new_session_picker_text(user_id),
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode="HTML",
            )
            return

        if action == "ai":
            provider = self._get_selected_ai_provider(user_id)
            keyboard = self._build_ai_selector_keyboard(provider, launcher_context="menu")
            keyboard.insert(1, [
                InlineKeyboardButton(BUTTON_SESSION_LIST, callback_data="menu:sessions"),
                InlineKeyboardButton(BUTTON_NEW_SESSION, callback_data="menu:new"),
            ])
            await query.edit_message_text(
                self._build_provider_switch_text(user_id, provider),
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode="HTML",
            )
            return

        if action == "tasks":
            text, _ = self._build_tasks_status(user_id)
            await query.edit_message_text(
                text,
                reply_markup=InlineKeyboardMarkup([
                    [
                        InlineKeyboardButton(BUTTON_REFRESH, callback_data="menu:tasks"),
                        InlineKeyboardButton(BUTTON_SESSION_LIST, callback_data="menu:sessions"),
                    ],
                    [InlineKeyboardButton(BUTTON_BACK, callback_data="menu:open")],
                ]),
                parse_mode="HTML",
            )
            return

        if action == "workspace":
            if not self._workspace_registry:
                await query.edit_message_text(
                    "Workspace feature not initialized.",
                    reply_markup=self._build_menu_back_markup(),
                )
                return

            keyboard = self._build_workspace_keyboard(user_id)
            keyboard.append([InlineKeyboardButton(BUTTON_BACK, callback_data="menu:open")])
            await query.edit_message_text(
                self._workspace_registry.get_status_text(user_id),
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode="HTML",
            )
            return

        if action == "scheduler":
            if not self._schedule_manager:
                await query.edit_message_text(
                    "Schedule feature not initialized.",
                    reply_markup=self._build_menu_back_markup(),
                )
                return

            keyboard = self._build_scheduler_keyboard(user_id)
            keyboard.append([InlineKeyboardButton(BUTTON_BACK, callback_data="menu:open")])
            await query.edit_message_text(
                self._build_scheduler_screen_text(user_id),
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode="HTML",
            )
            return

        if action == "plugins":
            await query.edit_message_text(
                self._build_plugins_text(),
                reply_markup=self._build_plugins_markup(launcher_context="menu"),
                parse_mode="HTML",
            )
            return

        if action == "claude_usage":
            usage_text = "<b>Claude Usage</b>\n\nPlan: <b>unknown</b>\n5h / wk: unavailable right now"
            usage = None
            if hasattr(self.claude, "get_usage_snapshot"):
                usage = await self.claude.get_usage_snapshot()

            if usage:
                plan = escape_html(usage.get("subscription_type", "unknown"))
                lines = [
                    "<b>Claude Usage</b>",
                    "",
                    f"Plan: <b>{plan}</b>",
                ]
                if {"five_hour_percent", "five_hour_reset", "weekly_percent", "weekly_reset"} <= usage.keys():
                    lines.extend([
                        f"5h: <b>{escape_html(usage['five_hour_percent'])}%</b> "
                        f"({escape_html(usage['five_hour_reset'])})",
                        f"wk: <b>{escape_html(usage['weekly_percent'])}%</b> "
                        f"({escape_html(usage['weekly_reset'])})",
                    ])
                else:
                    lines.append("5h / wk: unavailable right now")
                    reason = usage.get("unavailable_reason")
                    if reason:
                        lines.append(f"Reason: {escape_html(reason)}")
                    checked_at = usage.get("checked_at")
                    if checked_at:
                        lines.append(f"Checked: <code>{escape_html(checked_at)}</code>")
                usage_text = "\n".join(lines)

            await query.edit_message_text(
                usage_text,
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton(BUTTON_REFRESH, callback_data="menu:claude_usage"),
                    InlineKeyboardButton(BUTTON_BACK, callback_data="menu:open"),
                ]]),
                parse_mode="HTML",
            )
            return

        await query.edit_message_text(
            "Unknown menu action.",
            reply_markup=self._build_menu_back_markup(),
        )

    async def _handle_plugin_hub_callback(self, query, chat_id: int, callback_data: str) -> None:
        """Handle plugin launcher callbacks shared by `/plugins` and `/menu`."""
        parts = callback_data.split(":")
        action = parts[1] if len(parts) > 1 else "list"

        if action == "list":
            origin = parts[2] if len(parts) > 2 else None
            launcher_context = origin if origin == "menu" else None
            await query.edit_message_text(
                self._build_plugins_text(),
                reply_markup=self._build_plugins_markup(launcher_context=launcher_context),
                parse_mode="HTML",
            )
            return

        if action == "open" and len(parts) > 2:
            plugin_name = parts[2]
            origin = parts[3] if len(parts) > 3 else "standalone"
            await self._open_plugin_launcher(query, chat_id, plugin_name, origin)
            return

        await query.edit_message_text(
            "Unknown plugin launcher action.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(BUTTON_BACK, callback_data="plug:list:standalone")]]),
        )

    async def _open_plugin_launcher(self, query, chat_id: int, plugin_name: str, origin: str) -> None:
        """Open one plugin root screen from the plugin launcher."""
        if not self.plugins:
            await query.edit_message_text("No plugins loaded.")
            return

        plugin = self.plugins.get_plugin_by_name(plugin_name)
        if not plugin:
            await query.edit_message_text(
                f"Plugin not found: <code>{escape_html(plugin_name)}</code>",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton(BUTTON_BACK, callback_data=f"plug:list:{origin}"),
                ]]),
                parse_mode="HTML",
            )
            return

        try:
            result = await plugin.handle(plugin.name, chat_id)
        except Exception as exc:
            logger.exception(f"Plugin launcher open failed ({plugin_name}): {exc}")
            await query.edit_message_text(
                f"Error opening <code>/{escape_html(plugin.name)}</code>.\n\n"
                f"<code>{escape_html(str(exc))}</code>",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton(BUTTON_BACK, callback_data=f"plug:list:{origin}"),
                ]]),
                parse_mode="HTML",
            )
            return

        if result.handled:
            reply_markup = self._append_plugin_launcher_back(
                getattr(result, "reply_markup", None),
                origin,
            )
            await query.edit_message_text(
                text=result.response or plugin.usage,
                reply_markup=reply_markup,
                parse_mode="HTML",
            )
            return

        await query.edit_message_text(
            text=(
                f"<b>/{escape_html(plugin.name)}</b>\n\n"
                "This plugin has no interactive menu.\n"
                f"Help: <code>/help_{escape_html(plugin.name)}</code>"
            ),
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton(BUTTON_BACK, callback_data=f"plug:list:{origin}"),
            ]]),
            parse_mode="HTML",
        )

    @staticmethod
    def _find_plugin_launcher_return_callback(reply_markup) -> str | None:
        """Return the active plugin-launcher back callback, if present."""
        if not reply_markup or not getattr(reply_markup, "inline_keyboard", None):
            return None

        for row in reply_markup.inline_keyboard:
            for button in row:
                callback_data = getattr(button, "callback_data", "") or ""
                if callback_data.startswith("plug:list:"):
                    return callback_data
        return None

    def _append_plugin_launcher_back(self, reply_markup, origin: str):
        """Append one launcher back row while preserving the plugin's own buttons."""
        back_callback = f"plug:list:{origin}"
        existing_back = self._find_plugin_launcher_return_callback(reply_markup)
        if existing_back == back_callback:
            return reply_markup

        keyboard = []
        if reply_markup and getattr(reply_markup, "inline_keyboard", None):
            keyboard = [list(row) for row in reply_markup.inline_keyboard]

        keyboard.append([InlineKeyboardButton(BUTTON_BACK, callback_data=back_callback)])
        return InlineKeyboardMarkup(keyboard)

    async def _handle_new_session_force_reply(self, update: Update, chat_id: int, name: str, model: str) -> None:
        """Handle session creation ForceReply response."""
        logger.info(f"Session creation ForceReply processing: model={model}, name={name}")

        user_id = str(chat_id)
        selected_provider = self._get_selected_ai_provider(user_id)
        provider = infer_provider_from_model(model)
        if not is_supported_model(provider, model):
            provider = selected_provider
        model_name = model if is_supported_model(provider, model) else get_default_model(provider)

        session_name = name.strip()[:50] if name.strip() else ""

        self._set_selected_ai_provider(user_id, provider)
        session_id = self.sessions.create_session(
            user_id=user_id,
            ai_provider=provider,
            model=model_name,
            name=session_name,
            first_message="(new session)",
        )
        short_id = session_id[:8]

        model_emoji = get_model_emoji(model_name)
        name_line = f"\n<b>Name:</b> {escape_html(session_name)}" if session_name else ""

        keyboard = [[
            InlineKeyboardButton(BUTTON_SESSION, callback_data=f"sess:switch:{session_id}"),
            InlineKeyboardButton(BUTTON_SESSION_LIST, callback_data="sess:list"),
        ]]

        await update.message.reply_text(
            text=f"New session created!\n\n"
                 f"<b>AI:</b> {self._format_provider_display(provider)}\n"
                 f"{model_emoji} <b>Model:</b> {get_profile_label(provider, model_name)}\n"
                 f"<b>ID:</b> <code>{short_id}</code>{name_line}",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="HTML"
        )

    async def _handle_rename_force_reply(self, update: Update, chat_id: int, new_name: str, session_id: str) -> None:
        """Handle session rename ForceReply response."""
        logger.info(f"Rename ForceReply processing: session={session_id[:8]}, name={new_name}")

        new_name = new_name.strip()
        if not new_name:
            await update.message.reply_text("❌ Name cannot be empty.")
            return

        if len(new_name) > 50:
            await update.message.reply_text("❌ Name too long. (max 50 chars)")
            return

        if self.sessions.rename_session(session_id, new_name):
            logger.info(f"Session renamed: {session_id[:8]} -> {new_name}")
            await update.message.reply_text(
                f"✅ Session renamed!\n\n"
                f"- Session: <code>{session_id[:8]}</code>\n"
                f"- Name: {escape_html(new_name)}",
                parse_mode="HTML"
            )
        else:
            await update.message.reply_text("❌ Rename failed.")

    async def _handle_plugin_callback(self, query, chat_id: int, callback_data: str, plugin) -> None:
        """Handle plugin callback with auto-routing."""
        try:
            plugin_launcher_back = self._find_plugin_launcher_return_callback(
                getattr(query.message, "reply_markup", None)
            )
            result = await plugin.handle_callback_async(callback_data, chat_id)

            # ForceReply 처리
            if result.get("force_reply"):
                await query.edit_message_text(
                    text=result.get("text", "Enter input"),
                    parse_mode="HTML"
                )
                prompt_message = await query.message.reply_text(
                    text=result.get("force_reply_prompt", "Reply below."),
                    reply_markup=result["force_reply"],
                    parse_mode="HTML"
                )
                self._register_plugin_interaction(
                    prompt_message_id=getattr(prompt_message, "message_id", None),
                    chat_id=chat_id,
                    plugin_name=plugin.name,
                    action=result.get("interaction_action", "force_reply"),
                    state=result.get("interaction_state"),
                )
                return

            # 메시지 편집/전송
            reply_markup = result.get("reply_markup")
            if plugin_launcher_back:
                origin = plugin_launcher_back.split(":")[-1]
                reply_markup = self._append_plugin_launcher_back(reply_markup, origin)

            if result.get("edit", True) and query.message:
                await query.edit_message_text(
                    text=result.get("text", ""),
                    reply_markup=reply_markup,
                    parse_mode="HTML"
                )
            else:
                await query.message.reply_text(
                    text=result.get("text", ""),
                    reply_markup=reply_markup,
                    parse_mode="HTML"
                )
        except BadRequest as e:
            if "Message is not modified" in str(e):
                pass
            else:
                logger.warning(f"{plugin.name} callback BadRequest: {e}")
        except Exception as e:
            logger.exception(f"{plugin.name} callback error: {e}")
            try:
                await query.edit_message_text(
                    text=f"Error occurred.\n\n<code>{escape_html(str(e))}</code>",
                    parse_mode="HTML"
                )
            except:
                pass

    async def _handle_ai_callback(self, query, chat_id: int, callback_data: str) -> None:
        """Handle provider selection callbacks."""
        user_id = str(chat_id)
        parts = callback_data.split(":")
        action = parts[1] if len(parts) > 1 else ""
        origin = parts[3] if action == "select" and len(parts) > 3 else parts[2] if action == "open" and len(parts) > 2 else ""

        if action == "cancel":
            await query.edit_message_text("Provider selection cancelled.")
            return

        if action == "open":
            provider = self._get_selected_ai_provider(user_id)
            keyboard = self._build_ai_selector_keyboard(
                provider,
                launcher_context="menu" if origin == "menu" else None,
            )
            keyboard.insert(1, [
                InlineKeyboardButton(
                    BUTTON_SESSION_LIST,
                    callback_data="menu:sessions" if origin == "menu" else "sess:list",
                ),
                InlineKeyboardButton(
                    BUTTON_NEW_SESSION,
                    callback_data="menu:new" if origin == "menu" else "sess:new",
                ),
            ])
            await query.edit_message_text(
                self._build_provider_switch_text(user_id, provider),
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode="HTML",
            )
            return

        if action == "select" and len(parts) > 2:
            provider = parts[2]
            self._set_selected_ai_provider(user_id, provider)
            current_session_id = self.sessions.get_current_session_id(user_id, provider)
            current_line = (
                f"Current session: <code>{current_session_id[:8]}</code>"
                if current_session_id else
                "Current session: none"
            )
            keyboard = [[
                InlineKeyboardButton(
                    BUTTON_SESSION_LIST,
                    callback_data="menu:sessions" if origin == "menu" else "sess:list",
                ),
                InlineKeyboardButton(
                    BUTTON_NEW_SESSION,
                    callback_data="menu:new" if origin == "menu" else "sess:new",
                ),
            ]]
            if origin == "menu":
                keyboard.append([InlineKeyboardButton(BUTTON_BACK, callback_data="menu:open")])
            await query.edit_message_text(
                f"✅ Current AI switched to <b>{self._format_provider_display(provider)}</b>.\n\n"
                f"{current_line}",
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode="HTML",
            )
            return

        await query.edit_message_text("Unknown AI selection request.")

    async def _handle_tasks_callback(self, query, chat_id: int) -> None:
        """Handle task status callback - same as /tasks."""
        user_id = str(chat_id)
        text, keyboard = self._build_tasks_status(user_id)

        await query.edit_message_text(
            text=text,
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="HTML"
        )
