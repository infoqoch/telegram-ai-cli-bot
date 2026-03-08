"""Telegram bot command handlers - composed from separate handler modules."""

from typing import TYPE_CHECKING

from .base import BaseHandler
from .session_handlers import SessionHandlers
from .message_handlers import MessageHandlers
from .admin_handlers import AdminHandlers
from .workspace_handlers import WorkspaceHandlers
from .callback_handlers import CallbackHandlers

if TYPE_CHECKING:
    from src.claude.client import ClaudeClient
    from src.services.session_service import SessionService
    from src.plugins.loader import PluginLoader
    from ..middleware import AuthManager

__all__ = ["BotHandlers"]


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
    - MessageHandlers: Message intake and detached worker spawning
    - AdminHandlers: Admin commands (tasks, jobs, auth, etc.)
    - WorkspaceHandlers: Workspace management
    - CallbackHandlers: Inline button callbacks
    """

    def __init__(
        self,
        session_service: "SessionService",
        claude_client: "ClaudeClient",
        auth_manager: "AuthManager",
        require_auth: bool,
        allowed_chat_ids: list[int],
        plugin_loader: "PluginLoader" = None,
        ai_registry=None,
    ):
        # Initialize base class (all mixins share same base)
        super().__init__(
            session_service=session_service,
            claude_client=claude_client,
            auth_manager=auth_manager,
            require_auth=require_auth,
            allowed_chat_ids=allowed_chat_ids,
            plugin_loader=plugin_loader,
            ai_registry=ai_registry,
        )
