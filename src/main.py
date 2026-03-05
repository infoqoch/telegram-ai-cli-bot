"""Main entry point for Telegram Claude Bot."""

import atexit
import fcntl
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

# 싱글톤 보장을 위한 락 파일
LOCK_FILE = Path("/tmp/telegram-bot.lock")
_lock_fd = None


def acquire_singleton_lock() -> bool:
    """프로세스 싱글톤 락 획득. 실패 시 False 반환."""
    global _lock_fd
    try:
        _lock_fd = open(LOCK_FILE, "w")
        fcntl.flock(_lock_fd.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        _lock_fd.write(str(os.getpid()))
        _lock_fd.flush()
        return True
    except (IOError, OSError):
        if _lock_fd:
            _lock_fd.close()
        return False


def release_singleton_lock():
    """싱글톤 락 해제."""
    global _lock_fd
    if _lock_fd:
        try:
            fcntl.flock(_lock_fd.fileno(), fcntl.LOCK_UN)
            _lock_fd.close()
            LOCK_FILE.unlink(missing_ok=True)
        except Exception:
            pass
        _lock_fd = None


from src.config import get_settings
from src.logging_config import logger, setup_logging
from src.claude.client import ClaudeClient
from src.claude.session import SessionStore
from src.bot.handlers import BotHandlers
from src.bot.middleware import AuthManager
from src.plugins.loader import PluginLoader
from src.scheduler_manager import scheduler_manager
from src.schedule import init_schedule_manager, get_schedule_manager

# Todo 스케줄러 (옵션)
_todo_scheduler = None

# 세션 스케줄러 (매니저 compact)
_session_scheduler = None

# 예약 스케줄러 (경로 기반)
_schedule_manager = None


def _setup_session_scheduler(app, session_store, claude_client, settings) -> None:
    """세션 스케줄러 설정 (매니저 세션 자동 compact)."""
    global _session_scheduler

    try:
        from src.scheduler import SessionScheduler

        _session_scheduler = SessionScheduler(
            session_store=session_store,
            claude_client=claude_client,
            admin_chat_id=settings.admin_chat_id,
        )
        _session_scheduler.setup_jobs(app)

        logger.info(f"세션 스케줄러 활성화 - 21:00 매니저 compact, 보고: {settings.admin_chat_id or '(없음)'}")

    except ImportError as e:
        logger.debug(f"세션 스케줄러 비활성화 (모듈 없음): {e}")
    except Exception as e:
        logger.warning(f"세션 스케줄러 초기화 실패: {e}")


def _setup_hourly_ping_scheduler(app, settings, plugin_loader) -> None:
    """HourlyPing 플러그인 스케줄러 설정 (스케줄러 동작 확인용)."""
    try:
        hourly_ping_plugin = plugin_loader.get_plugin_by_name("hourly_ping")
        if hourly_ping_plugin and hasattr(hourly_ping_plugin, "setup_scheduler"):
            hourly_ping_plugin.setup_scheduler(app, settings.admin_chat_id)
            logger.info("HourlyPing 스케줄러 활성화 (08:00~19:00 매 정시)")
    except Exception as e:
        logger.debug(f"HourlyPing 스케줄러 비활성화: {e}")


def _setup_todo_scheduler(app, settings, plugin_loader) -> None:
    """Todo 스케줄러 설정."""
    global _todo_scheduler

    try:
        from plugins.builtin.todo.scheduler import TodoScheduler
        from plugins.builtin.todo.manager import TodoManager

        # 데이터 디렉토리
        data_dir = settings.base_dir / ".data" / "todo"

        # TodoManager 생성
        todo_manager = TodoManager(data_dir)

        # 알림 받을 chat_ids (allowed_chat_ids 또는 admin_chat_id)
        chat_ids = settings.allowed_chat_ids.copy() if settings.allowed_chat_ids else []
        if settings.admin_chat_id and settings.admin_chat_id not in chat_ids:
            chat_ids.append(settings.admin_chat_id)

        # 스케줄러 생성 및 설정
        _todo_scheduler = TodoScheduler(
            todo_manager=todo_manager,
            chat_ids=chat_ids,
        )
        _todo_scheduler.setup_jobs(app)

        # TodoPlugin에 manager 공유 (플러그인이 로드된 경우)
        if plugin_loader:
            todo_plugin = plugin_loader.get_plugin_by_name("todo")
            if todo_plugin:
                todo_plugin._manager = todo_manager
                logger.info("Todo 플러그인에 공유 매니저 연결됨")

        logger.info(f"Todo 스케줄러 활성화 - chat_ids: {chat_ids}")

    except ImportError as e:
        logger.debug(f"Todo 스케줄러 비활성화 (모듈 없음): {e}")
    except Exception as e:
        logger.warning(f"Todo 스케줄러 초기화 실패: {e}")


def create_app() -> Application:
    """Create and configure the Telegram application."""
    settings = get_settings()
    log_level = os.getenv("LOG_LEVEL", "INFO").upper()

    logger.info("=" * 60)
    logger.info("Telegram Claude Bot 초기화 시작")
    logger.info(f"  LOG_LEVEL: {log_level}")
    logger.info(f"  base_dir: {settings.base_dir}")
    logger.info(f"  working_dir: {settings.effective_working_dir}")
    logger.info(f"  require_auth: {settings.require_auth}")
    logger.info(f"  allowed_chat_ids: {settings.allowed_chat_ids or '(모두 허용)'}")
    logger.info("=" * 60)

    # Initialize components
    logger.trace("SessionStore 초기화 시작")
    session_store = SessionStore(
        file_path=settings.sessions_file,
        timeout_hours=settings.session_timeout_hours,
    )
    logger.trace(f"SessionStore 초기화 완료 - file: {settings.sessions_file}")

    logger.trace("ClaudeClient 초기화 시작")
    claude_client = ClaudeClient(
        command=settings.effective_ai_command,
        system_prompt_file=settings.telegram_prompt_file,
        timeout=300,
    )
    logger.trace(f"ClaudeClient 초기화 완료 - command: {settings.effective_ai_command}")

    logger.trace("AuthManager 초기화 시작")
    auth_manager = AuthManager(
        secret_key=settings.auth_secret_key,
        timeout_minutes=settings.auth_timeout_minutes,
    )
    logger.trace(f"AuthManager 초기화 완료 - timeout: {settings.auth_timeout_minutes}분")

    # 플러그인 로더 초기화
    logger.trace("PluginLoader 초기화 시작")
    plugin_loader = PluginLoader(settings.base_dir)
    loaded_plugins = plugin_loader.load_all()
    if loaded_plugins:
        logger.info(f"플러그인 로드됨: {', '.join(loaded_plugins)}")
    else:
        logger.warning("로드된 플러그인 없음")
    logger.trace(f"PluginLoader 초기화 완료 - {len(loaded_plugins)}개 플러그인")

    logger.trace("BotHandlers 초기화 시작")
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
    logger.trace("BotHandlers 초기화 완료")

    # Create application (concurrent_updates=True로 동시 메시지 처리 활성화)
    logger.trace("Application 빌드 시작")
    app = Application.builder().token(settings.telegram_token).concurrent_updates(True).build()
    logger.trace("Application 빌드 완료 - concurrent_updates=True")

    # SchedulerManager 초기화 (단일 job_queue 관리)
    scheduler_manager.set_app(app)
    logger.info("SchedulerManager 초기화 완료")

    # Todo 스케줄러 설정 (SchedulerManager 사용)
    _setup_todo_scheduler(app, settings, plugin_loader)

    # 세션 스케줄러 설정 (매니저 세션 compact)
    _setup_session_scheduler(app, session_store, claude_client, settings)

    # HourlyPing 플러그인 스케줄러 설정 (스케줄러 동작 확인용)
    _setup_hourly_ping_scheduler(app, settings, plugin_loader)

    # 예약 스케줄러 설정 (경로 기반)
    global _schedule_manager
    _schedule_manager = init_schedule_manager(
        data_dir=settings.data_dir,
        claude_client=claude_client,
    )
    _schedule_manager.set_bot(app.bot)
    _schedule_manager.register_all_to_scheduler()
    logger.info("예약 스케줄러 초기화 완료")

    # BotHandlers에 schedule_manager 설정
    handlers.set_schedule_manager(_schedule_manager)

    # Register handlers
    logger.trace("핸들러 등록 시작")
    app.add_handler(CommandHandler("start", handlers.start))
    app.add_handler(CommandHandler("help", handlers.help_command))
    app.add_handler(CommandHandler("auth", handlers.auth_command))
    app.add_handler(CommandHandler("status", handlers.status_command))
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
    app.add_handler(CommandHandler("chatid", handlers.chatid_command))
    app.add_handler(CommandHandler("lock", handlers.lock_command))
    app.add_handler(CommandHandler("jobs", handlers.jobs_command))
    app.add_handler(CommandHandler("scheduler", handlers.scheduler_command))
    app.add_handler(CommandHandler("plugins", handlers.plugins_command))
    app.add_handler(CommandHandler("ai", handlers.ai_command))

    # 동적 플러그인 명령어 (예: /memo)
    if plugin_loader.plugins:
        plugin_names = [p.name for p in plugin_loader.plugins]
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

    # 알 수 없는 명령어 처리 (/ai 제외 - 이미 등록됨)
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
    if not acquire_singleton_lock():
        print("❌ 봇이 이미 실행 중입니다. 기존 프로세스를 종료하세요.", file=sys.stderr)
        print("   ./run.sh stop && ./run.sh start", file=sys.stderr)
        sys.exit(1)
    logger.trace("싱글톤 락 획득 성공")

    # 종료 시 락 해제
    atexit.register(release_singleton_lock)
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    signal.signal(signal.SIGINT, lambda *_: sys.exit(0))
    logger.trace("종료 핸들러 등록 완료")

    settings = get_settings()

    if not settings.telegram_token:
        logger.error("TELEGRAM_TOKEN is not set")
        sys.exit(1)

    logger.info("Starting Telegram Claude Bot...")

    app = create_app()

    logger.info("=" * 60)
    logger.info("봇 시작 완료 - polling 모드")
    logger.info("  Ctrl+C로 종료")
    logger.info("=" * 60)

    logger.trace("run_polling() 호출")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
