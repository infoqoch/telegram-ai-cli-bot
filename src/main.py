"""Main entry point for the Telegram CLI AI bot."""

import atexit
import os
import signal
import sys
from pathlib import Path

from telegram import Update
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
from src.bootstrap import build_bot_runtime
from src.scheduler_manager import scheduler_manager
from src.repository import shutdown_repository
from src.services.schedule_execution_service import ScheduleExecutionService

# 예약 스케줄러 (경로 기반)
_schedule_manager = None


def _setup_hourly_ping_scheduler(app, settings, plugin_loader) -> None:
    """HourlyPing 플러그인 스케줄러 설정 (스케줄러 동작 확인용)."""
    try:
        hourly_ping_plugin = plugin_loader.get_plugin_by_name("hourly_ping")
        if hourly_ping_plugin and hasattr(hourly_ping_plugin, "setup_scheduler"):
            hourly_ping_plugin.setup_scheduler(app, settings.admin_chat_id)
            logger.info("HourlyPing 스케줄러 활성화 (08:00~19:00 매 정시)")
    except Exception as e:
        logger.debug(f"HourlyPing 스케줄러 비활성화: {e}")



def create_app() -> Application:
    """Create and configure the Telegram application."""
    settings = get_settings()
    log_level = os.getenv("LOG_LEVEL", "INFO").upper()

    logger.info("=" * 60)
    logger.info("Telegram CLI AI Bot 초기화 시작")
    logger.info(f"  LOG_LEVEL: {log_level}")
    logger.info(f"  base_dir: {settings.base_dir}")
    logger.info(f"  working_dir: {settings.effective_working_dir}")
    logger.info(f"  require_auth: {settings.require_auth}")
    logger.info(f"  allowed_chat_ids: {settings.allowed_chat_ids or '(모두 허용)'}")
    logger.info("=" * 60)

    runtime = build_bot_runtime(settings)
    handlers = runtime.handlers

    # 봇 시작 후 미완료 메시지 재처리 콜백
    async def post_init(application):
        count = await handlers.cleanup_detached_jobs(application.bot)
        if count:
            logger.info(f"Detached job cleanup count: {count}")

    # Create application (concurrent_updates=True로 동시 메시지 처리 활성화)
    logger.trace("Application 빌드 시작")
    app = (
        Application.builder()
        .token(settings.telegram_token)
        .concurrent_updates(True)
        .post_init(post_init)
        .build()
    )
    logger.trace("Application 빌드 완료 - concurrent_updates=True")

    # SchedulerManager 초기화 (단일 job_queue 관리)
    scheduler_manager.set_app(app)
    logger.info("SchedulerManager 초기화 완료")

    # HourlyPing 플러그인 스케줄러 설정 (스케줄러 동작 확인용)
    _setup_hourly_ping_scheduler(app, settings, runtime.plugin_loader)

    # 예약 스케줄러 설정 (Repository 어댑터 사용)
    global _schedule_manager
    _schedule_manager = runtime.schedule_manager

    schedule_execution_service = ScheduleExecutionService(
        bot=app.bot,
        ai_registry=runtime.ai_registry,
        plugin_loader=runtime.plugin_loader,
        schedule_manager=_schedule_manager,
    )

    _schedule_manager.set_scheduler_manager(scheduler_manager)
    _schedule_manager.set_executor(schedule_execution_service.execute)
    _schedule_manager.register_all_to_scheduler()
    logger.info("예약 스케줄러 executor 설정 완료")

    # BotHandlers에 schedule_manager, workspace_registry 설정
    handlers.set_schedule_manager(_schedule_manager)
    handlers.set_workspace_registry(runtime.workspace_registry)

    # Register handlers
    logger.trace("핸들러 등록 시작")
    app.add_handler(CommandHandler("start", handlers.start))
    app.add_handler(CommandHandler("help", handlers.help_command))
    app.add_handler(CommandHandler("auth", handlers.auth_command))
    app.add_handler(CommandHandler("status", handlers.status_command))
    app.add_handler(CommandHandler("select_ai", handlers.select_ai_command))
    app.add_handler(CommandHandler("new", handlers.new_session))
    app.add_handler(CommandHandler("new_opus", handlers.new_session_opus))
    app.add_handler(CommandHandler("new_sonnet", handlers.new_session_sonnet))
    app.add_handler(CommandHandler("new_haiku", handlers.new_session_haiku))
    app.add_handler(CommandHandler("new_haiku_speedy", handlers.new_session_haiku_speedy))
    app.add_handler(CommandHandler("new_opus_smarty", handlers.new_session_opus_smarty))
    app.add_handler(CommandHandler("new_workspace", handlers.new_workspace_session))
    app.add_handler(CommandHandler("nw", handlers.new_workspace_session))  # 단축 명령어
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

    # 동적 플러그인 명령어 (예: /memo)
    if runtime.plugin_loader.plugins:
        plugin_names = [p.name for p in runtime.plugin_loader.plugins]
        for name in plugin_names:
            app.add_handler(CommandHandler(name, handlers.plugin_help_command))
        logger.trace(f"플러그인 명령어 등록: {plugin_names}")

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
    logger.trace("핸들러 등록 완료")

    return app


def main() -> None:
    """Run the bot."""
    # 로깅 초기화 (가장 먼저!)
    log_level = os.getenv("LOG_LEVEL", "INFO").upper()
    log_file = os.getenv("LOG_FILE")  # 옵션: 파일 로깅
    setup_logging(level=log_level, log_file=log_file)

    logger.trace("main() 시작")

    # 싱글톤 락 획득 (다른 인스턴스 실행 방지)
    logger.trace("싱글톤 락 획득 시도")
    if not _process_lock.acquire():
        print("❌ Bot is already running.", file=sys.stderr)
        print("   ./run.sh stop && ./run.sh start", file=sys.stderr)
        sys.exit(1)
    logger.trace("싱글톤 락 획득 성공")

    # 종료 시 락 해제 및 Repository 정리
    def cleanup():
        _process_lock.release()
        try:
            shutdown_repository()
        except Exception:
            pass

    atexit.register(cleanup)
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    signal.signal(signal.SIGINT, lambda *_: sys.exit(0))
    logger.trace("종료 핸들러 등록 완료")

    settings = get_settings()

    if not settings.telegram_token:
        logger.error("TELEGRAM_TOKEN is not set")
        sys.exit(1)

    logger.info("Starting Telegram CLI AI Bot...")

    app = create_app()

    logger.info("=" * 60)
    logger.info("봇 시작 완료 - polling 모드")
    logger.info("  Ctrl+C로 종료")
    logger.info("=" * 60)

    logger.trace("run_polling() 호출")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
