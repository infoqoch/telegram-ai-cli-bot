"""Base handler class with common utilities."""

import asyncio
from collections import defaultdict
from typing import TYPE_CHECKING, Optional

from telegram import Update, InlineKeyboardButton
from telegram.ext import ContextTypes

from src.ai import (
    AIRegistry,
    DEFAULT_PROVIDER,
    get_default_model,
    get_profile_label,
    get_provider_button,
    get_provider_label,
    get_provider_profiles,
    is_supported_provider,
    normalize_model,
)
from src.logging_config import logger, set_trace_id, set_user_id, clear_context
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

    def _build_model_buttons(self, provider: str, callback_prefix: str) -> list[InlineKeyboardButton]:
        """Build one row of model/profile buttons for a provider."""
        return [
            InlineKeyboardButton(profile.button_label, callback_data=f"{callback_prefix}{profile.key}")
            for profile in get_provider_profiles(provider)
        ]

    def _build_ai_selector_keyboard(self, current_provider: str) -> list[list[InlineKeyboardButton]]:
        """Build provider switch buttons."""
        buttons = []
        row = []
        for provider in ("claude", "codex"):
            label = get_provider_button(provider)
            if provider == current_provider:
                label = f"• {label}"
            row.append(InlineKeyboardButton(label, callback_data=f"ai:select:{provider}"))
        buttons.append(row)
        buttons.append([InlineKeyboardButton("Cancel", callback_data="ai:cancel")])
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
            f"🤖 <b>CLI AI Bot</b>\n\n"
            f"{auth_line}"
            f"Current AI: <b>{get_provider_label(provider)}</b>\n"
            f"Session: [{session_info}] ({history_count} messages)\n\n"
            f"/help for commands",
            parse_mode="HTML"
        )
        logger.trace("/start complete")
        clear_context()

    async def help_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle /help command."""
        chat_id = update.effective_chat.id
        self._setup_request_context(chat_id)
        logger.info("/help command received")

        if self.require_auth:
            auth_section = (
                "Authentication\n"
                f"/auth &lt;key&gt; - Authenticate ({self.auth.timeout_minutes}min valid)\n"
                "/status - Check auth status\n\n"
            )
        else:
            auth_section = "<b>No authentication required</b>\n\n"

        plugin_section = ""
        if self.plugins and self.plugins.plugins:
            plugin_section = (
                "\nPlugins\n"
                "/plugins - Plugin list\n"
                "/ai &lt;question&gt; - Ask current AI directly (bypass plugins)\n"
            )
            logger.trace(f"Plugin count: {len(self.plugins.plugins)}")

        logger.trace("Sending response")
        await update.message.reply_text(
            "<b>Commands</b>\n\n"
            f"{auth_section}"
            "Sessions\n"
            "/select_ai - Choose Claude or Codex\n"
            "/new [model] [name] - New session\n"
            "/nw path [model] [name] - Workspace session\n"
            "/new_haiku_speedy - Claude shortcut\n"
            "/new_opus_smarty - Claude shortcut\n"
            "/rename_MyName - Rename session\n"
            "/session - Current session info\n"
            "/sl - Session list\n"
            "/back - Return to previous session\n"
            "/delete_&lt;id&gt; - Delete session\n\n"
            f"{plugin_section}\n"
            "Workspace\n"
            "/workspace - Workspace management\n\n"
            "Schedule\n"
            "/scheduler - Schedule management\n\n"
            "Other\n"
            "/tasks - Active tasks/queue\n"
            "/chatid - My chat ID\n"
            "/reload [name] - Reload plugins\n"
            "/help - This help",
            parse_mode="HTML"
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

        await update.message.reply_text(
            f"Unknown command: <code>{command}</code>\n\n"
            f"/help for command list",
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
