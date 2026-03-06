"""Main entry point for Telegram Claude Bot."""

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
from src.claude.client import ClaudeClient
from src.bot.handlers import BotHandlers
from src.bot.middleware import AuthManager
from src.plugins.loader import PluginLoader
from src.scheduler_manager import scheduler_manager
from src.repository import init_repository, get_repository, shutdown_repository
from src.repository.migrations import migrate_all
from src.repository.adapters import (
    ScheduleManagerAdapter,
    WorkspaceRegistryAdapter,
)
from src.services.session_service import SessionService

# Todo 스케줄러 (옵션)
_todo_scheduler = None

# 세션 스케줄러 (매니저 compact)
_session_scheduler = None

# 예약 스케줄러 (경로 기반)
_schedule_manager = None


def _setup_session_scheduler(app, session_service, claude_client, settings) -> None:
    """세션 스케줄러 설정 (매니저 세션 자동 compact)."""
    global _session_scheduler

    try:
        from src.scheduler import SessionScheduler

        _session_scheduler = SessionScheduler(
            session_store=session_service,
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


def _setup_todo_scheduler(app, settings) -> None:
    """Todo 스케줄러 설정 (Repository 기반)."""
    global _todo_scheduler

    try:
        from plugins.builtin.todo.scheduler import TodoScheduler

        repository = get_repository()
        if not repository:
            logger.warning("Todo 스케줄러: Repository 없음")
            return

        chat_ids = settings.allowed_chat_ids.copy() if settings.allowed_chat_ids else []
        if settings.admin_chat_id and settings.admin_chat_id not in chat_ids:
            chat_ids.append(settings.admin_chat_id)

        _todo_scheduler = TodoScheduler(
            repository=repository,
            chat_ids=chat_ids,
        )
        _todo_scheduler.setup_jobs(app)

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

    # Initialize Repository (SQLite)
    logger.trace("Repository 초기화 시작")
    repo = init_repository(settings.db_path)
    logger.trace(f"Repository 초기화 완료 - db: {settings.db_path}")

    # Run migrations from JSON if needed
    logger.trace("마이그레이션 확인")
    try:
        migration_result = migrate_all(repo, settings.data_dir)
        if any(v > 0 for v in migration_result.values()):
            logger.info(f"마이그레이션 완료: {migration_result}")
    except Exception as e:
        logger.warning(f"마이그레이션 실패 (기존 JSON 없을 수 있음): {e}")

    # Initialize SessionService
    logger.trace("SessionService 초기화 시작")
    session_service = SessionService(
        repo=repo,
        session_timeout_hours=settings.session_timeout_hours,
    )
    logger.trace("SessionService 초기화 완료")

    logger.trace("ClaudeClient 초기화 시작")
    claude_client = ClaudeClient(
        command=settings.ai_command,
        system_prompt_file=settings.telegram_prompt_file,
        timeout=300,
    )
    logger.trace(f"ClaudeClient 초기화 완료 - command: {settings.ai_command}")

    logger.trace("AuthManager 초기화 시작")
    auth_manager = AuthManager(
        secret_key=settings.auth_secret_key,
        timeout_minutes=settings.auth_timeout_minutes,
    )
    logger.trace(f"AuthManager 초기화 완료 - timeout: {settings.auth_timeout_minutes}분")

    # 플러그인 로더 초기화 (Repository 주입)
    logger.trace("PluginLoader 초기화 시작")
    plugin_loader = PluginLoader(settings.base_dir, repository=repo)
    loaded_plugins = plugin_loader.load_all()
    if loaded_plugins:
        logger.info(f"플러그인 로드됨: {', '.join(loaded_plugins)}")
    else:
        logger.warning("로드된 플러그인 없음")
    logger.trace(f"PluginLoader 초기화 완료 - {len(loaded_plugins)}개 플러그인")

    logger.trace("BotHandlers 초기화 시작")
    handlers = BotHandlers(
        session_service=session_service,
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

    # UpdateQueueManager, UpdateDispatcher 초기화 (Telegram Update 라우팅)
    from src.bot.update_queue import UpdateQueueManager
    from src.bot.update_dispatcher import UpdateDispatcher

    update_queue = UpdateQueueManager(max_queue_size=50, item_timeout=60)
    update_dispatcher = UpdateDispatcher(handlers=handlers)
    update_queue.set_dispatcher(update_dispatcher)
    logger.info("UpdateQueueManager, UpdateDispatcher 초기화 완료")

    # QueueWorker 초기화 (Repository 메시지 큐 처리 - Claude 호출)
    from src.bot.queue_worker import QueueWorker
    queue_worker = QueueWorker(
        repository=repo,
        claude_client=claude_client,
        session_service=session_service,
        bot=app.bot,
    )
    handlers.set_queue_worker(queue_worker)
    logger.info("QueueWorker 초기화 완료")

    # SchedulerManager 초기화 (단일 job_queue 관리)
    scheduler_manager.set_app(app)
    logger.info("SchedulerManager 초기화 완료")

    # Todo 스케줄러 설정 (Repository 기반)
    _setup_todo_scheduler(app, settings)

    # 세션 스케줄러 설정 (매니저 세션 compact)
    _setup_session_scheduler(app, session_service, claude_client, settings)

    # HourlyPing 플러그인 스케줄러 설정 (스케줄러 동작 확인용)
    _setup_hourly_ping_scheduler(app, settings, plugin_loader)

    # 예약 스케줄러 설정 (Repository 어댑터 사용)
    global _schedule_manager
    _schedule_manager = ScheduleManagerAdapter(repo=repo)
    # Executor는 나중에 설정 (bot 필요)
    logger.info("예약 스케줄러 어댑터 초기화 완료")

    # 워크스페이스 레지스트리 설정 (Repository 어댑터 사용)
    workspace_registry = WorkspaceRegistryAdapter(repo=repo)
    logger.info("워크스페이스 레지스트리 어댑터 초기화 완료")

    # Schedule executor 설정 (Claude 호출 로직)
    async def schedule_executor(schedule):
        """Execute scheduled task."""
        from src.repository.repository import Schedule
        try:
            # Create session for schedule
            import uuid
            session_id = f"schedule_{schedule.id}_{uuid.uuid4().hex[:8]}"

            # Determine workspace path
            workspace_path = None
            if schedule.type == "workspace" and schedule.workspace_path:
                workspace_path = schedule.workspace_path

            # Call Claude
            response = await claude_client.chat(
                message=schedule.message,
                session_id=session_id,
                model=schedule.model,
                cwd=workspace_path,
            )

            # Send response to Telegram
            if app.bot and schedule.chat_id:
                # Chunk response if too long
                max_len = 4000
                for i in range(0, len(response), max_len):
                    chunk = response[i:i + max_len]
                    await app.bot.send_message(
                        chat_id=schedule.chat_id,
                        text=f"📅 <b>{schedule.name}</b>\n\n{chunk}",
                        parse_mode="HTML",
                    )

            _schedule_manager.update_run(schedule.id)
            logger.info(f"Schedule {schedule.id} executed successfully")

        except Exception as e:
            _schedule_manager.update_run(schedule.id, last_error=str(e))
            logger.error(f"Schedule {schedule.id} failed: {e}")

    _schedule_manager.set_scheduler_manager(scheduler_manager)
    _schedule_manager.set_executor(schedule_executor)
    _schedule_manager.register_all_to_scheduler()
    logger.info("예약 스케줄러 executor 설정 완료")

    # BotHandlers에 schedule_manager, workspace_registry 설정
    handlers.set_schedule_manager(_schedule_manager)
    handlers.set_workspace_registry(workspace_registry)

    # Update 타입 분류 함수
    from src.bot.update_queue import UpdateType

    def classify_update(update: Update) -> UpdateType:
        """Update 타입 분류."""
        if update.callback_query:
            return UpdateType.CALLBACK
        if update.message:
            if update.message.reply_to_message and update.message.reply_to_message.reply_markup:
                # ForceReply 응답
                return UpdateType.FORCE_REPLY
            if update.message.text and update.message.text.startswith("/"):
                return UpdateType.COMMAND
            return UpdateType.MESSAGE
        return UpdateType.MESSAGE  # fallback

    # 단일 update_router - 모든 update를 큐에 enqueue
    async def update_router(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """모든 Update를 UpdateQueue로 라우팅."""
        chat_id = update.effective_chat.id if update.effective_chat else None
        if not chat_id:
            logger.warning("[UpdateRouter] No chat_id, ignoring update")
            return

        update_type = classify_update(update)
        logger.trace(f"[UpdateRouter] chat_id={chat_id}, type={update_type.value}")

        success = await update_queue.enqueue(
            chat_id=chat_id,
            update=update,
            context=context,
            update_type=update_type,
        )

        if not success:
            logger.warning(f"[UpdateRouter] Enqueue failed - chat_id={chat_id}, queue full")
            if update.message:
                await update.message.reply_text("⚠️ 메시지 큐가 가득 찼습니다. 잠시 후 다시 시도해주세요.")

    # Register single update handler
    logger.trace("핸들러 등록 시작 - 단일 update_router")
    app.add_handler(MessageHandler(filters.ALL, update_router), group=0)
    app.add_handler(CallbackQueryHandler(update_router), group=0)
    app.add_error_handler(handlers.error_handler)
    logger.trace("핸들러 등록 완료")

    # 앱 시작 시 큐 매니저 및 워커 시작
    async def post_init(application):
        update_queue.start()
        queue_worker.start()
        logger.info("UpdateQueueManager, QueueWorker 시작됨")

    async def post_shutdown(application):
        update_queue.stop()
        queue_worker.stop()
        logger.info("UpdateQueueManager, QueueWorker 중지됨")

    app.post_init = post_init
    app.post_shutdown = post_shutdown

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
        print("❌ 봇이 이미 실행 중입니다. 기존 프로세스를 종료하세요.", file=sys.stderr)
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
