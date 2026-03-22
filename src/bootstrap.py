"""Application runtime bootstrap for the Telegram bot."""

from __future__ import annotations

from dataclasses import dataclass

from src.ai import AIRegistry, build_default_registry
from src.bot.handlers import BotHandlers
from src.bot.middleware import AuthManager
from src.claude.client import ClaudeClient
from src.logging_config import logger
from src.plugins.loader import PluginLoader
from src.repository import Repository, init_repository
from src.repository.adapters import ScheduleManagerAdapter, WorkspaceRegistryAdapter
from src.services.session_service import SessionService


@dataclass
class BotRuntime:
    """Resolved runtime collaborators for app startup."""

    repo: Repository
    session_service: SessionService
    ai_registry: AIRegistry
    auth_manager: AuthManager
    plugin_loader: PluginLoader
    handlers: BotHandlers
    schedule_manager: ScheduleManagerAdapter
    workspace_registry: WorkspaceRegistryAdapter


def build_bot_runtime(settings) -> BotRuntime:
    """Build the runtime dependency graph for the Telegram application."""
    logger.trace("Initializing Repository")
    repo = init_repository(settings.db_path)
    logger.trace(f"Repository initialized - db: {settings.db_path}")

    logger.trace("Initializing SessionService")
    session_service = SessionService(
        repo=repo,
        session_timeout_hours=settings.session_timeout_hours,
        session_purge_days=settings.session_purge_days,
    )
    logger.trace("SessionService initialized")

    logger.trace("Initializing AIRegistry")
    ai_registry = build_default_registry(settings)
    claude_client = ai_registry.get_client("claude")
    logger.trace("AIRegistry initialized")

    logger.trace("Initializing AuthManager")
    auth_manager = AuthManager(
        secret_key=settings.auth_secret_key,
        timeout_minutes=settings.auth_timeout_minutes,
        repository=repo,
    )
    auth_manager.restore_from_db()
    logger.trace(f"AuthManager initialized - timeout: {settings.auth_timeout_minutes}min")

    logger.trace("Initializing PluginLoader")
    plugin_loader = PluginLoader(settings.base_dir, repository=repo)
    loaded_plugins = plugin_loader.load_all()
    if loaded_plugins:
        logger.info(f"Plugins loaded: {', '.join(loaded_plugins)}")
    else:
        logger.warning("No plugins loaded")
    logger.trace(f"PluginLoader initialized - {len(loaded_plugins)} plugins")

    logger.trace("Initializing BotHandlers")
    handlers = BotHandlers(
        session_service=session_service,
        claude_client=claude_client,
        ai_registry=ai_registry,
        auth_manager=auth_manager,
        require_auth=settings.require_auth,
        allowed_chat_ids=settings.allowed_chat_ids,
        plugin_loader=plugin_loader,
    )
    handlers.restore_pending_requests()
    logger.trace("BotHandlers initialized")

    schedule_manager = ScheduleManagerAdapter(repo=repo)
    logger.info("Schedule manager adapter initialized")

    workspace_registry = WorkspaceRegistryAdapter(
        repo=repo,
        recommendation_client=ClaudeClient(
            command=settings.ai_command,
            system_prompt_file=None,
            timeout=30,
        ),
    )
    logger.info("Workspace registry adapter initialized")

    return BotRuntime(
        repo=repo,
        session_service=session_service,
        ai_registry=ai_registry,
        auth_manager=auth_manager,
        plugin_loader=plugin_loader,
        handlers=handlers,
        schedule_manager=schedule_manager,
        workspace_registry=workspace_registry,
    )
