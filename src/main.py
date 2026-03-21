"""Main entry point for Telegram Agent Relay."""

import atexit
import os
import signal
import sys
from pathlib import Path

from telegram import Update
from telegram import BotCommandScopeChat
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    MessageHandler,
    filters,
)

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.lock import ProcessLock

# 싱글톤 락
_process_lock = ProcessLock(Path("/tmp/telegram-bot.lock"))


from src.config import get_settings
from src.logging_config import logger, setup_logging
from src.runtime_exit_codes import RuntimeExitCode
from src.bootstrap import build_bot_runtime
from src.bot.command_catalog import build_bot_commands
from src.scheduler_manager import scheduler_manager
from src.repository import shutdown_repository
from src.time_utils import configure_app_timezone
from src.services.delivery_retry_service import DeliveryRetryService
from src.services.schedule_execution_service import ScheduleExecutionService


_schedule_manager = None


async def _sync_bot_commands(bot, settings, runtime) -> None:
    """Publish a compact slash-command list to Telegram."""
    has_plugins = bool(runtime.plugin_loader and runtime.plugin_loader.plugins)
    default_commands = build_bot_commands(has_plugins=has_plugins)
    await bot.set_my_commands(default_commands)
    logger.info(f"Telegram commands synced: {[cmd.command for cmd in default_commands]}")

    if settings.admin_chat_id:
        admin_commands = build_bot_commands(has_plugins=has_plugins, is_admin=True)
        await bot.set_my_commands(
            admin_commands,
            scope=BotCommandScopeChat(chat_id=settings.admin_chat_id),
        )
        logger.info(f"Telegram admin commands synced for chat_id={settings.admin_chat_id}")


def create_app(settings) -> Application:
    """Create and configure the Telegram application."""
    log_level = os.getenv("LOG_LEVEL", "INFO").upper()
    app_timezone = getattr(settings, "app_timezone", "Asia/Seoul")
    configure_app_timezone(app_timezone)

    logger.info("=" * 60)
    logger.info("Telegram Agent Relay initialization started")
    logger.info(f"  LOG_LEVEL: {log_level}")
    logger.info(f"  base_dir: {settings.base_dir}")
    logger.info(f"  working_dir: {settings.effective_working_dir}")
    logger.info(f"  app_timezone: {app_timezone}")
    logger.info(f"  require_auth: {settings.require_auth}")
    logger.info(f"  allowed_chat_ids: {settings.allowed_chat_ids or '(all allowed)'}")
    logger.info("=" * 60)

    runtime = build_bot_runtime(settings)
    handlers = runtime.handlers

    # Delivery retry service
    delivery_retry_service = DeliveryRetryService(repo=runtime.repo)

    # 봇 시작 후 미완료 메시지 재처리 콜백
    async def post_init(application):
        count = await handlers.cleanup_detached_jobs(application.bot)
        if count:
            logger.info(f"Detached job cleanup count: {count}")
        # Immediate retry of failed deliveries from previous session
        retried = await delivery_retry_service.retry_failed_deliveries(application.bot)
        if retried:
            logger.info(f"Startup delivery retry count: {retried}")
        try:
            await _sync_bot_commands(application.bot, settings, runtime)
        except Exception as exc:
            logger.warning(f"Telegram command sync skipped: {exc}")

    # Create application (concurrent_updates=True로 동시 메시지 처리 활성화)
    logger.trace("Application build started")
    app = (
        Application.builder()
        .token(settings.telegram_token)
        .concurrent_updates(True)
        .post_init(post_init)
        .read_timeout(15)
        .write_timeout(15)
        .connect_timeout(10)
        .pool_timeout(5)
        .build()
    )
    logger.trace("Application build complete - concurrent_updates=True")

    scheduler_manager.set_app(app)
    logger.info("SchedulerManager initialized")

    # Register delivery retry repeating job (every 60s, first run after 30s)
    async def _delivery_retry_callback(context):
        """Scheduled callback for retrying failed Telegram deliveries."""
        await delivery_retry_service.retry_failed_deliveries(context.bot)

    scheduler_manager.register_repeating(
        name="delivery_retry",
        callback=_delivery_retry_callback,
        interval=60,
        owner="system",
        first=30,
        metadata={"description": "Retry failed Telegram message deliveries"},
    )

    if hasattr(runtime.plugin_loader, "register_system_jobs"):
        runtime.plugin_loader.register_system_jobs(app, settings.admin_chat_id)

    global _schedule_manager
    _schedule_manager = runtime.schedule_manager

    schedule_execution_service = ScheduleExecutionService(
        bot=app.bot,
        ai_registry=runtime.ai_registry,
        plugin_loader=runtime.plugin_loader,
        schedule_manager=_schedule_manager,
        repo=runtime.repo,
    )

    _schedule_manager.set_scheduler_manager(scheduler_manager)
    _schedule_manager.set_executor(schedule_execution_service.execute)
    _schedule_manager.register_all_to_scheduler()
    logger.info("Schedule executor configured")

    handlers.set_schedule_manager(_schedule_manager)
    handlers.set_workspace_registry(runtime.workspace_registry)

    # Register handlers
    logger.trace("Registering handlers")
    app.add_handler(CommandHandler("start", handlers.start))
    app.add_handler(CommandHandler("menu", handlers.menu_command))
    app.add_handler(CommandHandler("help", handlers.help_command))
    app.add_handler(MessageHandler(filters.Regex(r'^/help_'), handlers.help_topic_command))
    app.add_handler(CommandHandler("auth", handlers.auth_command))
    app.add_handler(CommandHandler("status", handlers.status_command))
    app.add_handler(CommandHandler("select_ai", handlers.select_ai_command))
    app.add_handler(CommandHandler("new", handlers.new_session))
    app.add_handler(CommandHandler("new_opus", handlers.new_session_opus))
    app.add_handler(CommandHandler("new_sonnet", handlers.new_session_sonnet))
    app.add_handler(CommandHandler("new_haiku", handlers.new_session_haiku))
    app.add_handler(CommandHandler("new_workspace", handlers.new_workspace_session))
    app.add_handler(CommandHandler("model", handlers.model_command))
    app.add_handler(CommandHandler("model_opus", handlers.model_opus_command))
    app.add_handler(CommandHandler("model_sonnet", handlers.model_sonnet_command))
    app.add_handler(CommandHandler("model_haiku", handlers.model_haiku_command))
    app.add_handler(CommandHandler("session", handlers.session_command))
    app.add_handler(CommandHandler("session_list", handlers.session_list_command))
    app.add_handler(CommandHandler("sl", handlers.session_list_command))  # 단축 명령어
    app.add_handler(CommandHandler("back", handlers.back_command))
    app.add_handler(CommandHandler("chatid", handlers.chatid_command))
    app.add_handler(CommandHandler("tasks", handlers.tasks_command))
    app.add_handler(CommandHandler("scheduler", handlers.scheduler_command))
    app.add_handler(CommandHandler("workspace", handlers.workspace_command))
    app.add_handler(CommandHandler("ws", handlers.workspace_command))  # 단축 명령어
    app.add_handler(CommandHandler("plugins", handlers.plugins_command))
    app.add_handler(CommandHandler("reload", handlers.reload_command))
    app.add_handler(CommandHandler("ai", handlers.ai_command))

    # 동적 플러그인 명령어
    if runtime.plugin_loader.plugins:
        plugin_names = [p.name for p in runtime.plugin_loader.plugins]
        for name in plugin_names:
            app.add_handler(CommandHandler(name, handlers.plugin_help_command))
        logger.trace(f"Plugin commands registered: {plugin_names}")

    app.add_handler(CommandHandler("rename", handlers.rename_command))
    app.add_handler(MessageHandler(filters.Regex(r'^/rename_'), handlers.rename_command))
    app.add_handler(MessageHandler(filters.Regex(r'^/r_'), handlers.rename_command))
    app.add_handler(MessageHandler(filters.Regex(r'^/s_'), handlers.switch_session_command))
    app.add_handler(MessageHandler(filters.Regex(r'^/h_'), handlers.history_command))
    app.add_handler(MessageHandler(filters.Regex(r'^/history_'), handlers.history_command))
    app.add_handler(MessageHandler(filters.Regex(r'^/d_'), handlers.delete_session_command))
    app.add_handler(MessageHandler(filters.Regex(r'^/delete_'), handlers.delete_session_command))

    # Callback Query 핸들러 (인라인 버튼)
    app.add_handler(CallbackQueryHandler(handlers.callback_query_handler))

    # 알 수 없는 명령어 처리
    app.add_handler(MessageHandler(filters.COMMAND, handlers.unknown_command))

    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handlers.handle_message))

    app.add_error_handler(handlers.error_handler)
    logger.trace("Handlers registered")

    return app


def _load_settings_or_exit():
    """Load validated settings or exit with one unrecoverable config code."""
    try:
        return get_settings()
    except Exception as exc:
        logger.error(f"Startup settings invalid: {exc}")
        raise SystemExit(int(RuntimeExitCode.CONFIG_ERROR))


def main() -> None:
    """Run the bot."""
    # 로깅 초기화 (가장 먼저!)
    log_level = os.getenv("LOG_LEVEL", "INFO").upper()
    log_file = os.getenv("LOG_FILE")  # 옵션: 파일 로깅
    setup_logging(level=log_level, log_file=log_file)

    logger.trace("main() started")

    # 싱글톤 락 획득 (다른 인스턴스 실행 방지)
    logger.trace("Acquiring singleton lock")
    if not _process_lock.acquire():
        print("❌ Bot is already running.", file=sys.stderr)
        print("   ./run.sh stop && ./run.sh start", file=sys.stderr)
        sys.exit(int(RuntimeExitCode.LOCK_HELD))
    logger.trace("Singleton lock acquired")

    # 종료 시 락 해제 및 Repository 정리
    def cleanup():
        _process_lock.release()
        try:
            shutdown_repository()
        except Exception:
            pass

    atexit.register(cleanup)
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(int(RuntimeExitCode.OK)))
    signal.signal(signal.SIGINT, lambda *_: sys.exit(int(RuntimeExitCode.OK)))
    logger.trace("Shutdown handlers registered")

    settings = _load_settings_or_exit()

    if not settings.telegram_token:
        logger.error("TELEGRAM_TOKEN is not set")
        sys.exit(int(RuntimeExitCode.CONFIG_ERROR))

    logger.info("Starting Telegram Agent Relay...")

    app = create_app(settings)

    # SIGUSR1 → DB에서 스케줄 핫리로드
    def _handle_schedule_reload(*_):
        if _schedule_manager:
            removed, added = _schedule_manager.sync_from_db()
            logger.info(f"SIGUSR1: schedule reload - {removed} removed, {added} added")

    signal.signal(signal.SIGUSR1, _handle_schedule_reload)

    logger.info("=" * 60)
    logger.info("Bot started - polling mode")
    logger.info("  Press Ctrl+C to stop")
    logger.info("=" * 60)

    logger.trace("run_polling() called")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
