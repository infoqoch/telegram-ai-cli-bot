"""Main entry point for Telegram Claude Bot."""

import logging
import sys
from pathlib import Path

from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    filters,
)

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.config import get_settings
from src.claude.client import ClaudeClient
from src.claude.session import SessionStore
from src.bot.handlers import BotHandlers
from src.bot.middleware import AuthManager
from src.plugins.loader import PluginLoader

# Configure logging
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)


def create_app() -> Application:
    """Create and configure the Telegram application."""
    settings = get_settings()
    
    # Initialize components
    session_store = SessionStore(
        file_path=settings.sessions_file,
        timeout_hours=settings.session_timeout_hours,
    )
    
    claude_client = ClaudeClient(
        command=settings.effective_ai_command,
        system_prompt_file=settings.telegram_prompt_file,
        timeout=300,
    )
    
    auth_manager = AuthManager(
        secret_key=settings.auth_secret_key,
        timeout_minutes=settings.auth_timeout_minutes,
    )

    # 플러그인 로더 초기화
    plugin_loader = PluginLoader(settings.base_dir)
    loaded_plugins = plugin_loader.load_all()
    if loaded_plugins:
        logger.info(f"플러그인 로드됨: {', '.join(loaded_plugins)}")

    handlers = BotHandlers(
        session_store=session_store,
        claude_client=claude_client,
        auth_manager=auth_manager,
        require_auth=settings.require_auth,
        allowed_chat_ids=settings.allowed_chat_ids,
        response_notify_seconds=settings.response_notify_seconds,
        session_list_ai_summary=settings.session_list_ai_summary,
        plugin_loader=plugin_loader,
    )
    
    # Create application (concurrent_updates=True로 동시 메시지 처리 활성화)
    app = Application.builder().token(settings.telegram_token).concurrent_updates(True).build()
    
    # Register handlers
    app.add_handler(CommandHandler("start", handlers.start))
    app.add_handler(CommandHandler("help", handlers.help_command))
    app.add_handler(CommandHandler("auth", handlers.auth_command))
    app.add_handler(CommandHandler("status", handlers.status_command))
    app.add_handler(CommandHandler("new", handlers.new_session))
    app.add_handler(CommandHandler("session", handlers.session_command))
    app.add_handler(CommandHandler("session_list", handlers.session_list_command))
    app.add_handler(CommandHandler("chatid", handlers.chatid_command))
    app.add_handler(CommandHandler("plugins", handlers.plugins_command))

    # 동적 플러그인 명령어 (예: /memo)
    if plugin_loader.plugins:
        plugin_names = [p.name for p in plugin_loader.plugins]
        for name in plugin_names:
            app.add_handler(CommandHandler(name, handlers.plugin_help_command))

    app.add_handler(MessageHandler(filters.Regex(r'^/s_'), handlers.switch_session_command))
    app.add_handler(MessageHandler(filters.Regex(r'^/h_'), handlers.history_command))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handlers.handle_message))
    
    app.add_error_handler(handlers.error_handler)
    
    return app


def main() -> None:
    """Run the bot."""
    settings = get_settings()
    
    if not settings.telegram_token:
        logger.error("TELEGRAM_TOKEN is not set")
        sys.exit(1)
    
    logger.info("Starting Telegram Claude Bot...")
    
    app = create_app()
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
