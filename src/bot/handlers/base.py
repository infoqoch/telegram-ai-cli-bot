"""Base handler class with common utilities."""

import asyncio
import subprocess
import time
from collections import defaultdict
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Optional

from telegram import Update
from telegram.ext import ContextTypes

from src.logging_config import logger, set_trace_id, set_user_id, clear_context
from ..session_queue import session_queue_manager
from ..constants import (
    WATCHDOG_INTERVAL_SECONDS,
    TASK_TIMEOUT_SECONDS,
    MAX_TASK_MESSAGE_PREVIEW,
)

if TYPE_CHECKING:
    from src.claude.client import ClaudeClient
    from src.services.session_service import SessionService
    from src.plugins.loader import PluginLoader
    from ..middleware import AuthManager


@dataclass
class TaskInfo:
    """Background task metadata."""
    user_id: str
    session_id: str
    trace_id: str
    message: str = ""
    started_at: float = field(default_factory=time.time)
    task: Optional[asyncio.Task] = None


@dataclass
class PendingMessage:
    """Message pending during session lock conflict."""
    user_id: str
    message: str
    created_at: float = field(default_factory=time.time)
    expires_at: float = field(default_factory=lambda: time.time() + 300)


class BaseHandler:
    """Base class with common handler utilities."""

    def __init__(
        self,
        session_service: "SessionService",
        claude_client: "ClaudeClient",
        auth_manager: "AuthManager",
        require_auth: bool,
        allowed_chat_ids: list[int],
        response_notify_seconds: int = 60,
        session_list_ai_summary: bool = False,
        plugin_loader: "PluginLoader" = None,
    ):
        logger.trace("BaseHandler.__init__() start")
        self.sessions = session_service
        self.claude = claude_client
        self.auth = auth_manager
        self.require_auth = require_auth
        self.allowed_chat_ids = allowed_chat_ids
        self.response_notify_seconds = response_notify_seconds
        self.session_list_ai_summary = session_list_ai_summary
        self.plugins = plugin_loader

        # Instance variables (previously class variables - fixed bug where all instances shared state)
        self._user_locks: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)
        self._user_semaphores: dict[str, asyncio.Semaphore] = defaultdict(
            lambda: asyncio.Semaphore(3)
        )
        self._active_tasks: dict[int, TaskInfo] = {}
        self._watchdog_task: Optional[asyncio.Task] = None
        self._creating_sessions: set[str] = set()
        self._sched_pending: dict[str, dict] = {}
        self._schedule_manager = None
        self._workspace_registry = None
        self._ws_pending: dict[str, dict] = {}
        self._watchdog_started = False
        # Temporary pending for session queue callbacks (keyed by pending_key)
        self._temp_pending: dict[str, dict] = {}

        logger.trace(f"BaseHandler config - require_auth={require_auth}, allowed_ids={allowed_chat_ids}")

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

    def _ensure_watchdog(self) -> None:
        """Start watchdog task (lazy initialization)."""
        logger.trace("_ensure_watchdog() called")
        if self._watchdog_started:
            logger.trace("Watchdog already started - skip")
            return
        try:
            if self._watchdog_task is None or self._watchdog_task.done():
                self._watchdog_task = asyncio.create_task(self._watchdog_loop())
                self._watchdog_started = True
                logger.info("Watchdog task started")
        except RuntimeError:
            logger.trace("Watchdog start failed - no event loop")
            pass

    async def _watchdog_loop(self) -> None:
        """Periodically check and cleanup long-running tasks."""
        logger.trace("_watchdog_loop() started")
        while True:
            try:
                await asyncio.sleep(WATCHDOG_INTERVAL_SECONDS)
                logger.trace(f"Watchdog check - active tasks: {len(self._active_tasks)}")
                await self._cleanup_zombie_tasks()
            except asyncio.CancelledError:
                logger.info("Watchdog task cancelled")
                break
            except Exception as e:
                logger.exception(f"Watchdog error: {e}")

    async def _cleanup_zombie_tasks(self) -> None:
        """Cleanup tasks running for more than 30 minutes."""
        logger.trace("_cleanup_zombie_tasks() started")
        now = time.time()
        zombie_tasks = []

        for task_id, info in list(self._active_tasks.items()):
            elapsed = now - info.started_at
            logger.trace(f"Task check - id={task_id}, user={info.user_id}, elapsed={elapsed:.0f}s")
            if elapsed > TASK_TIMEOUT_SECONDS:
                zombie_tasks.append((task_id, info))

        logger.trace(f"Zombie tasks found: {len(zombie_tasks)}")

        for task_id, info in zombie_tasks:
            elapsed_min = int((now - info.started_at) / 60)
            logger.warning(
                f"Zombie task detected: trace={info.trace_id}, user={info.user_id}, "
                f"elapsed={elapsed_min}min, session={info.session_id[:8]}"
            )

            if info.task and not info.task.done():
                info.task.cancel()
                logger.info(f"Task cancelled - trace={info.trace_id}")

            await self._kill_claude_process(info.session_id)
            await session_queue_manager.force_unlock(info.session_id)
            self._active_tasks.pop(task_id, None)

    async def _kill_claude_process(self, session_id: str) -> None:
        """Kill Claude process for a specific session."""
        logger.trace(f"_kill_claude_process() - session={session_id[:8]}")
        try:
            result = subprocess.run(
                ["pgrep", "-f", f"claude.*{session_id}"],
                capture_output=True,
                text=True,
            )
            pids = result.stdout.strip().split("\n")
            pids = [p for p in pids if p]

            logger.trace(f"Claude process PIDs: {pids}")

            for pid in pids:
                try:
                    subprocess.run(["kill", "-9", pid], check=True)
                    logger.info(f"Claude process killed: PID {pid}")
                except subprocess.CalledProcessError:
                    logger.trace(f"Process already terminated: PID {pid}")
        except Exception as e:
            logger.warning(f"Failed to kill Claude process: {e}")

    def _register_task(self, task: asyncio.Task, user_id: str, session_id: str, trace_id: str, message: str = "") -> int:
        """Register task for tracking."""
        task_id = id(task)
        self._active_tasks[task_id] = TaskInfo(
            user_id=user_id,
            session_id=session_id,
            trace_id=trace_id,
            message=message[:MAX_TASK_MESSAGE_PREVIEW],
            task=task,
        )
        task.add_done_callback(lambda t: self._active_tasks.pop(id(t), None))
        logger.trace(f"Task registered - task_id={task_id}, trace={trace_id}, session={session_id[:8]}")
        return task_id

    def get_active_task_count(self, user_id: str = None) -> int:
        """Return active task count. If user_id specified, only that user."""
        if user_id is None:
            return len(self._active_tasks)
        return sum(1 for info in self._active_tasks.values() if info.user_id == user_id)

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

    async def _send_message_to_chat(
        self,
        bot,
        chat_id: int,
        text: str,
        max_length: int = 4000,
    ) -> None:
        """Send message directly to chat_id (split if too long)."""
        logger.trace(f"_send_message_to_chat - length={len(text)}, max={max_length}")

        if len(text) <= max_length:
            try:
                await bot.send_message(chat_id=chat_id, text=text, parse_mode="HTML")
                logger.trace("Message sent successfully (HTML)")
            except Exception as e:
                logger.trace(f"HTML send failed, retrying as plain text: {e}")
                await bot.send_message(chat_id=chat_id, text=text)
            return

        chunks = [text[i:i + max_length] for i in range(0, len(text), max_length)]
        logger.trace(f"Message split: {len(chunks)} chunks")

        for i, chunk in enumerate(chunks):
            logger.trace(f"Sending chunk {i+1}/{len(chunks)}")
            try:
                await bot.send_message(chat_id=chat_id, text=chunk, parse_mode="HTML")
            except Exception:
                await bot.send_message(chat_id=chat_id, text=chunk)

    async def _send_long_message(self, update: Update, text: str, max_length: int = 4000) -> None:
        """Send message, splitting if too long. (Legacy - uses update.reply_text)"""
        logger.trace(f"_send_long_message - length={len(text)}")

        if len(text) <= max_length:
            try:
                await update.message.reply_text(text, parse_mode="HTML")
            except Exception:
                await update.message.reply_text(text)
            return

        chunks = [text[i:i + max_length] for i in range(0, len(text), max_length)]
        logger.trace(f"Message split: {len(chunks)} chunks")

        for chunk in chunks:
            try:
                await update.message.reply_text(chunk, parse_mode="HTML")
            except Exception:
                await update.message.reply_text(chunk)

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
            f"🤖 <b>Claude Code Bot</b>\n\n"
            f"{auth_line}"
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
                "/ai &lt;question&gt; - Ask Claude directly (bypass plugins)\n"
            )
            logger.trace(f"Plugin count: {len(self.plugins.plugins)}")

        logger.trace("Sending response")
        await update.message.reply_text(
            "<b>Commands</b>\n\n"
            f"{auth_section}"
            "Sessions\n"
            "/new [model] [name] - New session\n"
            "/nw path [model] [name] - Workspace session\n"
            "/new_haiku_speedy - Speedy\n"
            "/new_opus_smarty - Smarty\n"
            "/rename_MyName - Rename session\n"
            "/session - Current session info\n"
            "/sl - Session list\n"
            "/delete_&lt;id&gt; - Delete session\n\n"
            f"{plugin_section}\n"
            "Workspace\n"
            "/workspace - Workspace management\n\n"
            "Schedule\n"
            "/scheduler - Schedule management\n\n"
            "Other\n"
            "/tasks - Active tasks/queue\n"
            "/chatid - My chat ID\n"
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
