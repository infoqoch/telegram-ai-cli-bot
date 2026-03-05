"""Telegram bot command handlers - composed from separate handler modules."""

from typing import TYPE_CHECKING

from .base import BaseHandler, TaskInfo, PendingMessage
from .session_handlers import SessionHandlers
from .message_handlers import MessageHandlers
from .admin_handlers import AdminHandlers
from .workspace_handlers import WorkspaceHandlers
from .callback_handlers import CallbackHandlers

if TYPE_CHECKING:
    from src.claude.client import ClaudeClient
    from src.repository.adapters import SessionStoreAdapter
    from src.plugins.loader import PluginLoader
    from ..middleware import AuthManager

__all__ = ["BotHandlers", "TaskInfo", "PendingMessage"]


class BotHandlers(
    SessionHandlers,
    MessageHandlers,
    AdminHandlers,
    WorkspaceHandlers,
    CallbackHandlers,
):
    """Container for all bot command handlers.

    Composed from:
    - BaseHandler: Common utilities, watchdog, task tracking
    - SessionHandlers: Session commands (new, switch, delete, etc.)
    - MessageHandlers: Message processing, Claude requests
    - AdminHandlers: Admin commands (lock, jobs, auth, etc.)
    - WorkspaceHandlers: Workspace management
    - CallbackHandlers: Inline button callbacks
    """

    def __init__(
        self,
        session_store: "SessionStoreAdapter",
        claude_client: "ClaudeClient",
        auth_manager: "AuthManager",
        require_auth: bool,
        allowed_chat_ids: list[int],
        response_notify_seconds: int = 60,
        session_list_ai_summary: bool = False,
        plugin_loader: "PluginLoader" = None,
    ):
        # Initialize base class (all mixins share same base)
        super().__init__(
            session_store=session_store,
            claude_client=claude_client,
            auth_manager=auth_manager,
            require_auth=require_auth,
            allowed_chat_ids=allowed_chat_ids,
            response_notify_seconds=response_notify_seconds,
            session_list_ai_summary=session_list_ai_summary,
            plugin_loader=plugin_loader,
        )
