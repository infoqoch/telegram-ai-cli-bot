"""Base handler class with common utilities."""

import asyncio
from collections import defaultdict
from datetime import datetime
from typing import TYPE_CHECKING, Optional

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

from src.ai import (
    AIRegistry,
    DEFAULT_PROVIDER,
    get_default_model,
    get_profile_label,
    get_provider_button,
    get_provider_icon,
    get_provider_label,
    get_provider_profiles,
    is_supported_provider,
    normalize_model,
)
from src.logging_config import logger, set_trace_id, set_user_id, clear_context
from src.ui_emoji import (
    BUTTON_BACK,
    BUTTON_CANCEL,
    BUTTON_DELETE,
    BUTTON_HISTORY,
    BUTTON_NEW_SESSION,
    BUTTON_REFRESH,
    BUTTON_SESSION,
    BUTTON_SWITCH_AI,
    BUTTON_TASKS,
    ENTITY_BOT,
    ENTITY_SESSION_CURRENT,
)
from ..command_catalog import build_menu_specs
from ..constants import get_model_badge
from ..formatters import escape_html
from ..runtime import DetachedJobManager, PendingRequestStore

if TYPE_CHECKING:
    from src.claude.client import ClaudeClient
    from src.repository import Repository
    from src.services.session_service import SessionService
    from src.plugins.loader import PluginLoader
    from ..middleware import AuthManager


class BaseHandler:
    """Base class with common handler utilities."""

    def __init__(
        self,
        session_service: "SessionService",
        claude_client: "ClaudeClient",
        auth_manager: "AuthManager",
        require_auth: bool,
        allowed_chat_ids: list[int],
        plugin_loader: "PluginLoader" = None,
        ai_registry: Optional[AIRegistry] = None,
    ):
        logger.trace("BaseHandler.__init__() start")
        self.sessions = session_service
        self.ai = ai_registry or AIRegistry({"claude": claude_client})
        self.claude = self.ai.get_client("claude") if "claude" in self.ai.supported_providers() else claude_client
        self.auth = auth_manager
        self.require_auth = require_auth
        self.allowed_chat_ids = allowed_chat_ids
        self.plugins = plugin_loader

        # Instance variables (previously class variables - fixed bug where all instances shared state)
        self._user_locks: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)
        self._creating_sessions: set[str] = set()
        self._sched_pending: dict[str, dict] = {}
        self._schedule_manager = None
        self._workspace_registry = None
        self._ws_pending: dict[str, dict] = {}
        self._pending_requests = PendingRequestStore(self._repository)
        self._detached_jobs = DetachedJobManager(self._repository)

        logger.trace(f"BaseHandler config - require_auth={require_auth}, allowed_ids={allowed_chat_ids}")

    @property
    def _repository(self) -> Optional["Repository"]:
        """Access repository via SessionService."""
        return getattr(self.sessions, '_repo', None)

    @property
    def _temp_pending(self) -> dict[str, dict]:
        """Compatibility accessor for temp pending state."""
        return self._pending_requests.data

    @_temp_pending.setter
    def _temp_pending(self, value: dict[str, dict]) -> None:
        """Compatibility setter for tests and transitional code."""
        self._pending_requests.data = value

    def _get_selected_ai_provider(self, user_id: str) -> str:
        """Return the currently selected provider for a user."""
        provider = self.sessions.get_selected_ai_provider(user_id)
        return provider if is_supported_provider(provider) else DEFAULT_PROVIDER

    def _set_selected_ai_provider(self, user_id: str, provider: str) -> None:
        """Switch the selected provider."""
        if not is_supported_provider(provider):
            raise ValueError(f"Unsupported provider: {provider}")
        self.sessions.select_ai_provider(user_id, provider)

    def _get_ai_client(self, provider: str):
        """Return a provider-specific client."""
        return self.ai.get_client(provider)

    def _get_selected_ai_client(self, user_id: str):
        """Return the active provider client for a user."""
        return self._get_ai_client(self._get_selected_ai_provider(user_id))

    def _get_session_provider(self, session_id: str) -> str:
        """Return provider owning the session."""
        provider = self.sessions.get_session_ai_provider(session_id)
        return provider if is_supported_provider(provider) else DEFAULT_PROVIDER

    def _normalize_model(self, provider: str, model: Optional[str]) -> str:
        """Normalize one provider model/profile key."""
        return normalize_model(provider, model or get_default_model(provider))

    def _get_model_label(self, provider: str, model: Optional[str]) -> str:
        """Return display label for one provider model/profile."""
        return get_profile_label(provider, model)

    def _get_provider_label(self, provider: str) -> str:
        """Return display label for a provider."""
        return get_provider_label(provider)

    def _get_provider_icon(self, provider: str) -> str:
        """Return icon for a provider."""
        return get_provider_icon(provider)

    def _format_provider_display(self, provider: str) -> str:
        """Return provider label with icon for chat-facing UI."""
        return f"{self._get_provider_icon(provider)} {get_provider_label(provider)}"

    def _format_model_button_label(self, provider: str, profile, include_provider_icon: bool = False) -> str:
        """Return one compact model button label."""
        parts: list[str] = []
        if include_provider_icon:
            parts.append(self._get_provider_icon(provider))
        if profile.badge:
            parts.append(profile.badge)
        parts.append(profile.button_label)
        return " ".join(parts)

    def _build_model_buttons(
        self,
        provider: str,
        callback_prefix: str,
        *,
        include_provider_icon: bool = False,
        callback_suffix: str = "",
    ) -> list[InlineKeyboardButton]:
        """Build one row of model/profile buttons for a provider."""
        return [
            InlineKeyboardButton(
                self._format_model_button_label(
                    provider,
                    profile,
                    include_provider_icon=include_provider_icon,
                ),
                callback_data=f"{callback_prefix}{profile.key}{callback_suffix}",
            )
            for profile in get_provider_profiles(provider)
        ]

    def _build_session_list_view(
        self,
        user_id: str,
        *,
        prefix: str = "",
        include_timestamp: bool = False,
        launcher_context: Optional[str] = None,
    ) -> tuple[str, list[list[InlineKeyboardButton]]]:
        """Build the mixed-provider `/sl` view."""
        provider = self._get_selected_ai_provider(user_id)
        sessions = self.sessions.list_sessions_for_all_providers(user_id, limit=10)
        selected_current_id = self.sessions.get_current_session_id(user_id, provider)

        header = f"{prefix}<b>Session List</b>"
        if include_timestamp:
            header += f" <i>({datetime.now().strftime('%H:%M:%S')})</i>"

        lines = [
            header,
            f"Current AI: <b>{self._format_provider_display(provider)}</b>",
            "",
        ]
        buttons: list[list[InlineKeyboardButton]] = []

        if not sessions:
            lines.append("No sessions.")
        else:
            for session in sessions:
                sid = session["full_session_id"]
                short_id = session["session_id"]
                name = session.get("name") or f"Session {short_id}"
                session_provider = session.get("ai_provider", provider)
                model = session.get("model", get_default_model(session_provider))
                provider_icon = self._get_provider_icon(session_provider)
                model_badge = get_model_badge(model)
                lock_indicator = " 🔒" if self._is_session_locked(sid) else ""
                pin_indicator = f" {ENTITY_SESSION_CURRENT}" if sid == selected_current_id else ""

                lines.append(
                    f"{provider_icon} {model_badge} "
                    f"<b>{escape_html(name)}</b>{lock_indicator}{pin_indicator}"
                )

                buttons.append([
                    InlineKeyboardButton(
                        f"{provider_icon} {name[:10]}",
                        callback_data=f"sess:switch:{sid}",
                    ),
                    InlineKeyboardButton(BUTTON_HISTORY, callback_data=f"sess:history:{sid}"),
                    InlineKeyboardButton(BUTTON_DELETE, callback_data=f"sess:delete:{sid}"),
                ])

        if launcher_context == "menu":
            buttons.append([
                InlineKeyboardButton(BUTTON_NEW_SESSION, callback_data="menu:new"),
                InlineKeyboardButton(BUTTON_REFRESH, callback_data="menu:sessions"),
                InlineKeyboardButton(BUTTON_TASKS, callback_data="menu:tasks"),
            ])
            buttons.append([
                InlineKeyboardButton(BUTTON_SWITCH_AI, callback_data="menu:ai"),
            ])
            buttons.append([
                InlineKeyboardButton(BUTTON_BACK, callback_data="menu:open"),
            ])
        else:
            buttons.append([
                InlineKeyboardButton(BUTTON_NEW_SESSION, callback_data="sess:new"),
                InlineKeyboardButton(BUTTON_REFRESH, callback_data="sess:list"),
                InlineKeyboardButton(BUTTON_TASKS, callback_data="tasks:refresh"),
            ])
            buttons.append([
                InlineKeyboardButton(BUTTON_SWITCH_AI, callback_data="ai:open"),
            ])

        return "\n".join(lines), buttons

    def _build_new_session_picker_keyboard(self) -> list[list[InlineKeyboardButton]]:
        """Build the unified 3x2 provider/model picker used by `/new`."""
        return [
            self._build_model_buttons("claude", "sess:new:", include_provider_icon=True),
            self._build_model_buttons("codex", "sess:new:", include_provider_icon=True),
        ]

    def _build_new_session_picker_text(self, user_id: str) -> str:
        """Build the shared `/new` picker body."""
        provider = self._get_selected_ai_provider(user_id)
        return (
            f"🆕 <b>New Session</b>\n\n"
            f"Current AI: <b>{self._format_provider_display(provider)}</b>\n"
            f"Select a model. Choosing one also switches the current AI:"
        )

    def _build_session_action_keyboard(self, session_id: str) -> list[list[InlineKeyboardButton]]:
        """Build compact session actions used after AI responses."""
        return [[InlineKeyboardButton(BUTTON_SESSION, callback_data=f"sess:switch:{session_id}")]]

    def _build_auth_status_text(self, user_id: str) -> str:
        """Return a compact auth status line for launcher screens."""
        if not self.require_auth:
            return "Auth: <b>Open</b>"

        if self.auth.is_authenticated(user_id):
            remaining = self.auth.get_remaining_minutes(user_id)
            return f"Auth: <b>✅ Authenticated</b> ({remaining}m left)"
        return "Auth: <b>🔒 Authentication required</b>"

    def _build_menu_text(self, chat_id: int) -> str:
        """Render the `/menu` launcher body."""
        user_id = str(chat_id)
        provider = self._get_selected_ai_provider(user_id)
        has_plugins = bool(self.plugins and self.plugins.plugins)

        lines = [
            "<b>Main Menu</b>",
            self._build_auth_status_text(user_id),
            f"Current AI: <b>{self._format_provider_display(provider)}</b>",
            "",
            "Choose a service:",
            "• Sessions and AI controls",
            "• Workspace and scheduler hubs",
        ]
        if has_plugins:
            lines.append("• Plugin catalog")
        return "\n".join(lines)

    def _find_menu_spec(self, name: str, *, chat_id: int):
        """Return one launcher spec by name."""
        specs = build_menu_specs(
            has_plugins=bool(self.plugins and self.plugins.plugins),
            is_admin=self._is_admin_chat(chat_id),
        )
        return next((spec for spec in specs if spec.name == name), None)

    def _build_menu_keyboard(self, chat_id: int) -> InlineKeyboardMarkup:
        """Build the `/menu` launcher keyboard."""
        buttons: list[list[InlineKeyboardButton]] = []

        def add_row(*names: str) -> None:
            row: list[InlineKeyboardButton] = []
            for name in names:
                spec = self._find_menu_spec(name, chat_id=chat_id)
                if spec and spec.menu_label and spec.callback_data:
                    row.append(InlineKeyboardButton(spec.menu_label, callback_data=spec.callback_data))
            if row:
                buttons.append(row)

        add_row("new", "sl")
        add_row("workspace", "scheduler")
        add_row("plugins", "tasks")
        add_row("select_ai")
        buttons.append([InlineKeyboardButton("❓ Help", callback_data="menu:help")])

        return InlineKeyboardMarkup(buttons)

    @staticmethod
    def _build_menu_back_markup() -> InlineKeyboardMarkup:
        """Return a simple back-to-menu keyboard."""
        return InlineKeyboardMarkup(
            [[InlineKeyboardButton(BUTTON_BACK, callback_data="menu:open")]]
        )

    def _build_ai_selector_keyboard(
        self,
        current_provider: str,
        *,
        launcher_context: Optional[str] = None,
    ) -> list[list[InlineKeyboardButton]]:
        """Build provider switch buttons."""
        buttons = []
        row = []
        for provider in ("claude", "codex"):
            label = get_provider_button(provider)
            if provider == current_provider:
                label = f"• {label}"
            callback_data = f"ai:select:{provider}"
            if launcher_context == "menu":
                callback_data += ":menu"
            row.append(InlineKeyboardButton(label, callback_data=callback_data))
        buttons.append(row)
        if launcher_context == "menu":
            buttons.append([InlineKeyboardButton(BUTTON_BACK, callback_data="menu:open")])
        else:
            buttons.append([InlineKeyboardButton(BUTTON_CANCEL, callback_data="ai:cancel")])
        return buttons

    def _save_temp_pending(self, key: str, data: dict) -> None:
        """Save pending data to memory and DB."""
        self._pending_requests.save(key, data)

    def _delete_temp_pending(self, key: str) -> None:
        """Delete pending data from memory and DB."""
        self._pending_requests.delete(key)

    def _restore_temp_pending(self) -> int:
        """Restore non-expired pending messages from DB. Returns count restored."""
        return self._pending_requests.restore()

    def _get_live_session_lock(self, session_id: str) -> Optional[dict]:
        """Return a live detached-worker lock or clean it up if stale."""
        return self._detached_jobs.get_live_session_lock(session_id)

    def _is_session_locked(self, session_id: str) -> bool:
        """Check if a session is actively locked by a detached worker."""
        return self._detached_jobs.is_session_locked(session_id)

    def _start_detached_job(
        self,
        chat_id: int,
        session_id: str,
        message: str,
        model: str,
        workspace_path: Optional[str] = None,
    ) -> tuple[Optional[int], Optional[str]]:
        """Create a message_log job, reserve the session lock, and spawn a worker."""
        job_id, error = self._detached_jobs.prepare_job(
            chat_id=chat_id,
            session_id=session_id,
            message=message,
            model=model or "sonnet",
            workspace_path=workspace_path,
        )
        if error:
            return None, error

        try:
            worker_pid = self._spawn_detached_worker(job_id)
            self._detached_jobs.attach_worker(session_id, job_id, worker_pid)
        except Exception as exc:
            self._detached_jobs.fail_job_spawn(session_id, job_id, exc)
            raise

        return job_id, None

    def _spawn_detached_worker(self, job_id: int) -> int:
        """Spawn one detached worker for a prepared job."""
        return self._detached_jobs.spawn_worker(job_id)

    async def _cleanup_detached_jobs(self, bot) -> int:
        """Cleanup stale lock reservations or dead detached workers after bot startup."""
        return await self._detached_jobs.cleanup_orphaned_jobs(bot)

    def restore_pending_requests(self) -> int:
        """Restore persisted temp pending state after startup."""
        return self._restore_temp_pending()

    async def cleanup_detached_jobs(self, bot) -> int:
        """Cleanup orphaned detached jobs after startup."""
        return await self._cleanup_detached_jobs(bot)

    def set_schedule_manager(self, manager) -> None:
        """Set schedule manager."""
        self._schedule_manager = manager
        logger.debug("ScheduleManager connected to handlers")

    def set_workspace_registry(self, registry) -> None:
        """Set workspace registry."""
        self._workspace_registry = registry
        logger.debug("WorkspaceRegistry connected to handlers")

    def _setup_request_context(self, chat_id: int) -> str:
        """Setup request context (trace_id, user_id). Returns trace_id."""
        trace_id = set_trace_id()
        set_user_id(str(chat_id))
        logger.trace(f"Request context setup - trace_id={trace_id}, user_id={chat_id}")
        return trace_id

    def _is_authorized(self, chat_id: int) -> bool:
        """Check if chat_id is authorized."""
        logger.trace(f"_is_authorized() - chat_id={chat_id}, allowed={self.allowed_chat_ids}")
        if not self.allowed_chat_ids:
            logger.trace("All chat_ids allowed (allowed_chat_ids empty)")
            return True
        result = chat_id in self.allowed_chat_ids
        logger.trace(f"Authorization check result: {result}")
        return result

    def _is_authenticated(self, user_id: str) -> bool:
        """Check if user is authenticated."""
        logger.trace(f"_is_authenticated() - user_id={user_id}, require_auth={self.require_auth}")
        if not self.require_auth:
            logger.trace("Authentication not required (require_auth=False)")
            return True
        result = self.auth.is_authenticated(user_id)
        logger.trace(f"Authentication check result: {result}")
        return result

    @staticmethod
    def _split_message(text: str, max_length: int = 4000) -> list[str]:
        """Split text into chunks no longer than max_length.

        Splits preferably at the last newline within the max_length window.
        Falls back to hard character split if no newline is found.
        Empty chunks are never returned.
        """
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

    async def _send_message_to_chat(
        self,
        bot,
        chat_id: int,
        text: str,
        max_length: int = 4000,
    ) -> None:
        """Send message directly to chat_id (split if too long)."""
        logger.trace(f"_send_message_to_chat - length={len(text)}, max={max_length}")

        chunks = self._split_message(text, max_length)
        logger.trace(f"Message split: {len(chunks)} chunks")

        for i, chunk in enumerate(chunks):
            logger.trace(f"Sending chunk {i+1}/{len(chunks)}")
            try:
                await bot.send_message(chat_id=chat_id, text=chunk, parse_mode="HTML")
                if i == 0:
                    logger.trace("Message sent successfully (HTML)")
            except Exception as e:
                if i == 0:
                    logger.trace(f"HTML send failed, retrying as plain text: {e}")
                await bot.send_message(chat_id=chat_id, text=chunk)

    def _get_plugin_source_group(self, plugin) -> str:
        """Return plugin origin group for UI grouping."""
        group = getattr(plugin, "_source_group", "")
        return group if group in {"builtin", "custom"} else "custom"

    def _build_help_auth_section(self) -> str:
        """Return auth-related help lines."""
        if self.require_auth:
            return (
                "Authentication\n"
                f"/auth &lt;key&gt; - Authenticate ({self.auth.timeout_minutes}min valid)\n"
                "/status - Check auth status\n\n"
            )
        return "<b>No authentication required</b>\n\n"

    def _build_main_help_text(self) -> str:
        """Return the concise main help screen."""
        lines = [
            "<b>Commands</b>\n",
            self._build_help_auth_section().rstrip(),
            "Core",
            "/menu - Main service launcher",
            "/select_ai - Choose Claude or Codex",
            "/new [model] [name] - New session",
            "/session - Current session info",
            "/sl - Session list",
            "/workspace - Workspace hub",
            "/scheduler - Scheduler hub",
        ]

        if self.plugins and self.plugins.plugins:
            lines.append("/plugins - Plugin list")

        lines.extend([
            "",
            "Utility",
            "/tasks - Active tasks/queue",
            "/chatid - My chat ID",
        ])

        if self.plugins and self.plugins.plugins:
            lines.append("/ai &lt;question&gt; - Ask current AI directly")

        lines.extend([
            "",
            "More",
            "/help_extend - Extended guides",
        ])

        return "\n".join(lines)

    def _is_admin_chat(self, chat_id: int) -> bool:
        """Return True when the current chat is the configured admin chat."""
        try:
            from src.config import get_settings

            admin_chat_id = get_settings().admin_chat_id
            return bool(admin_chat_id) and chat_id == admin_chat_id
        except Exception:
            return False

    def _build_extended_help_text(self, *, is_admin: bool = False) -> str:
        """Return the extended help index."""
        lines = [
            "<b>Extended Help</b>\n",
            "Guides",
            "/help_session - Session workflow",
            "/help_workspace - Workspace workflow",
            "/help_plugins - Plugin usage",
        ]

        if is_admin:
            lines.append("/help_admin - Admin operations")

        if self.plugins and self.plugins.plugins:
            lines.append("")
            lines.append("Plugin Topics")
            for plugin in sorted(self.plugins.plugins, key=lambda item: (self._get_plugin_source_group(item), item.name)):
                lines.append(f"/help_{plugin.name}")

        return "\n".join(lines)

    def _build_session_help_text(self) -> str:
        """Return session guide text."""
        return (
            "<b>Session Guide</b>\n\n"
            "/new [model] [name] - Create a session\n"
            "/session - View the current session\n"
            "/sl - Browse, switch, rename, or delete sessions\n\n"
            "Rename, delete, and model changes should be done from the session UI."
        )

    def _build_workspace_help_text(self) -> str:
        """Return workspace guide text."""
        return (
            "<b>Workspace Guide</b>\n\n"
            "/workspace - Open the workspace hub\n\n"
            "Recommended flow\n"
            "1. Add or select a workspace\n"
            "2. Choose Chat or Schedule from that workspace\n\n"
            "Use the workspace UI as the primary path instead of raw shortcut commands."
        )

    def _build_scheduler_help_text(self) -> str:
        """Return scheduler guide text."""
        return (
            "<b>Scheduler Guide</b>\n\n"
            "/scheduler - Open the scheduler hub\n\n"
            "Types\n"
            "• 💬 Chat - Uses the current AI/provider\n"
            "• 📂 Workspace - Runs with workspace context\n"
            "• 🔌 Plugin - Runs a plugin action\n\n"
            "Flow\n"
            "• Pick a time, then choose Daily or One-time\n"
            "• Complex cron changes can be handled later through AI/admin updates"
        )

    def _build_plugins_help_text(self) -> str:
        """Return plugin guide text."""
        lines = [
            "<b>Plugin Guide</b>\n",
            "/plugins - Open the plugin launcher",
            "Use plugin buttons for normal interaction",
            "/help_memo, /help_todo ... - Show detailed plugin usage",
        ]

        if self.plugins and self.plugins.plugins:
            lines.append("")
            lines.append("Available")
            for plugin in sorted(self.plugins.plugins, key=lambda item: (self._get_plugin_source_group(item), item.name)):
                lines.append(f"• /help_{plugin.name}")

        return "\n".join(lines)

    def _build_admin_help_text(self) -> str:
        """Return admin-only operations help."""
        return (
            "<b>Admin Guide</b>\n\n"
            "/reload [name] - Reload all plugins or one plugin\n\n"
            "This command is intentionally hidden from the main help."
        )

    async def help_topic_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle /help_* topic commands."""
        chat_id = update.effective_chat.id
        self._setup_request_context(chat_id)
        is_admin = self._is_admin_chat(chat_id)

        text = update.message.text.strip() if update.message and update.message.text else ""
        if not text.startswith("/help_"):
            await update.message.reply_text(self._build_extended_help_text(is_admin=is_admin), parse_mode="HTML")
            clear_context()
            return

        topic = text[6:].split()[0].lower()
        logger.info(f"/help topic request: {topic}")

        topic_builders = {
            "extend": self._build_extended_help_text,
            "session": self._build_session_help_text,
            "workspace": self._build_workspace_help_text,
            "scheduler": self._build_scheduler_help_text,
            "plugins": self._build_plugins_help_text,
        }

        if topic == "admin" and is_admin:
            await update.message.reply_text(self._build_admin_help_text(), parse_mode="HTML")
            clear_context()
            return

        if topic in topic_builders:
            if topic == "extend":
                await update.message.reply_text(self._build_extended_help_text(is_admin=is_admin), parse_mode="HTML")
            else:
                await update.message.reply_text(topic_builders[topic](), parse_mode="HTML")
            clear_context()
            return

        if self.plugins:
            plugin = self.plugins.get_plugin_by_name(topic)
            if plugin:
                await update.message.reply_text(
                    f"{plugin.usage}\n\nOpen: <code>/{plugin.name}</code>",
                    parse_mode="HTML",
                )
                clear_context()
                return

        await update.message.reply_text(
            f"Unknown help topic: <code>/help_{escape_html(topic)}</code>\n\n"
            "Use /help_extend to see available guides.",
            parse_mode="HTML",
        )
        clear_context()

    async def start(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle /start command."""
        chat_id = update.effective_chat.id
        trace_id = self._setup_request_context(chat_id)
        logger.info("/start command received")
        logger.trace(f"update.effective_user={update.effective_user}")

        if not self._is_authorized(chat_id):
            logger.debug("/start denied - unauthorized")
            await update.message.reply_text("⛔ Access denied.")
            clear_context()
            return

        user_id = str(chat_id)
        provider = self._get_selected_ai_provider(user_id)
        logger.trace("Getting current session")
        session_id = self.sessions.get_current_session_id(user_id)
        session_info = self.sessions.get_session_info(session_id)
        history_count = self.sessions.get_history_count(session_id) if session_id else 0
        logger.trace(f"Session info - session_id={session_id}, info={session_info}, history={history_count}")

        if self.require_auth:
            is_auth = self.auth.is_authenticated(user_id)
            remaining = self.auth.get_remaining_minutes(user_id)
            auth_status = f"✅ Authenticated ({remaining}m remaining)" if is_auth else "🔒 Authentication required"
            auth_line = f"Auth: {auth_status}\n"
            logger.trace(f"Auth status - is_auth={is_auth}, remaining={remaining}")
        else:
            auth_line = "🔓 <b>No authentication required</b>\n"

        logger.trace("Sending response")
        await update.message.reply_text(
            f"{ENTITY_BOT} <b>CLI AI Bot</b>\n\n"
            f"{auth_line}"
            f"Current AI: <b>{self._format_provider_display(provider)}</b>\n"
            f"Session: [{session_info}] ({history_count} messages)\n\n"
            f"/menu or /help",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("📋 Menu", callback_data="menu:open"),
                InlineKeyboardButton("❓ Help", callback_data="menu:help"),
            ]]),
        )
        logger.trace("/start complete")
        clear_context()

    async def menu_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle /menu command."""
        chat_id = update.effective_chat.id
        self._setup_request_context(chat_id)
        logger.info("/menu command received")

        if not self._is_authorized(chat_id):
            logger.debug("/menu denied - unauthorized")
            await update.message.reply_text("⛔ Access denied.")
            clear_context()
            return

        await update.message.reply_text(
            self._build_menu_text(chat_id),
            reply_markup=self._build_menu_keyboard(chat_id),
            parse_mode="HTML",
        )
        logger.trace("/menu complete")
        clear_context()

    async def help_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle /help command."""
        chat_id = update.effective_chat.id
        self._setup_request_context(chat_id)
        logger.info("/help command received")

        logger.trace("Sending response")
        await update.message.reply_text(
            self._build_main_help_text(),
            parse_mode="HTML",
            reply_markup=self._build_menu_back_markup(),
        )
        logger.trace("/help complete")
        clear_context()

    async def unknown_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle unknown commands starting with /."""
        chat_id = update.effective_chat.id
        self._setup_request_context(chat_id)

        text = update.message.text
        command = text.split()[0] if text else ""
        logger.info(f"Unknown command: {command}")

        from ..formatters import escape_html as _esc
        await update.message.reply_text(
            f"Unknown command: <code>{_esc(command)}</code>\n\n"
            f"/menu or /help",
            parse_mode="HTML"
        )
        clear_context()

    async def error_handler(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle errors."""
        chat_id = update.effective_chat.id if update and update.effective_chat else "unknown"
        if chat_id != "unknown":
            self._setup_request_context(chat_id)

        error_type = type(context.error).__name__
        error_msg = str(context.error)

        friendly_errors = {
            "Query is too old": "⏰ Button expired. Please try again.",
            "Message is not modified": None,
            "message to edit not found": "🗑️ Message deleted, cannot edit.",
        }

        for pattern, friendly_msg in friendly_errors.items():
            if pattern in error_msg:
                logger.debug(f"Known error: {error_type}: {error_msg}")
                if friendly_msg and update and update.effective_chat:
                    await context.bot.send_message(
                        chat_id=update.effective_chat.id,
                        text=friendly_msg
                    )
                clear_context()
                return

        logger.error(f"Error: {error_type}: {context.error}")
        logger.trace(f"Error detail: {context.error}", exc_info=context.error)

        if update and update.effective_chat:
            logger.trace("Sending error message to user")
            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text="❌ An error occurred. Please try again later."
            )

        clear_context()
