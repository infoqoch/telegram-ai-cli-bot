"""Telegram bot command handlers."""

import asyncio
import subprocess
import time
from collections import defaultdict
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Optional

from telegram import Update
from telegram.ext import ContextTypes

from src.logging_config import logger, set_trace_id, set_user_id, set_session_id, clear_context
from .formatters import format_session_quick_list, truncate_message

if TYPE_CHECKING:
    from src.claude.client import ClaudeClient
    from src.claude.session import SessionStore
    from src.plugins.loader import PluginLoader
    from .middleware import AuthManager


@dataclass
class TaskInfo:
    """백그라운드 태스크 메타데이터."""
    user_id: str
    session_id: str
    trace_id: str  # 요청 추적용
    started_at: float = field(default_factory=time.time)
    task: Optional[asyncio.Task] = None


class BotHandlers:
    """Container for all bot command handlers."""

    # 유저별 Lock: 세션 생성 시 race condition 방지
    _user_locks: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)

    # 유저별 Semaphore: 동시 요청 제한
    _user_semaphores: dict[str, asyncio.Semaphore] = defaultdict(
        lambda: asyncio.Semaphore(3)
    )

    # 태스크 추적
    _active_tasks: dict[int, TaskInfo] = {}  # task_id -> TaskInfo
    _watchdog_task: Optional[asyncio.Task] = None

    # 메시지 최대 길이 (DoS 방지)
    MAX_MESSAGE_LENGTH = 4096

    # Watchdog 설정
    WATCHDOG_INTERVAL_SECONDS = 60  # 1분마다 체크
    TASK_TIMEOUT_SECONDS = 30 * 60  # 30분 타임아웃

    def __init__(
        self,
        session_store: "SessionStore",
        claude_client: "ClaudeClient",
        auth_manager: "AuthManager",
        require_auth: bool,
        allowed_chat_ids: list[int],
        response_notify_seconds: int = 60,
        session_list_ai_summary: bool = False,
        plugin_loader: "PluginLoader" = None,
    ):
        logger.trace("BotHandlers.__init__() 시작")
        self.sessions = session_store
        self.claude = claude_client
        self.auth = auth_manager
        self.require_auth = require_auth
        self.allowed_chat_ids = allowed_chat_ids
        self.response_notify_seconds = response_notify_seconds
        self.session_list_ai_summary = session_list_ai_summary
        self.plugins = plugin_loader
        self._watchdog_started = False
        logger.trace(f"BotHandlers 설정 - require_auth={require_auth}, allowed_ids={allowed_chat_ids}")

    def _setup_request_context(self, chat_id: int) -> str:
        """요청 컨텍스트 설정 (trace_id, user_id). trace_id 반환."""
        trace_id = set_trace_id()
        set_user_id(str(chat_id))
        logger.trace(f"요청 컨텍스트 설정 - trace_id={trace_id}, user_id={chat_id}")
        return trace_id

    def _ensure_watchdog(self) -> None:
        """Watchdog 태스크 시작 (지연 초기화)."""
        logger.trace("_ensure_watchdog() 호출")
        if self._watchdog_started:
            logger.trace("Watchdog 이미 시작됨 - 스킵")
            return
        try:
            if self._watchdog_task is None or self._watchdog_task.done():
                self._watchdog_task = asyncio.create_task(self._watchdog_loop())
                self._watchdog_started = True
                logger.info("Watchdog 태스크 시작됨")
        except RuntimeError:
            # 이벤트 루프가 없으면 무시 (테스트 환경)
            logger.trace("Watchdog 시작 실패 - 이벤트 루프 없음")
            pass

    async def _watchdog_loop(self) -> None:
        """주기적으로 장시간 실행 태스크를 체크하고 정리."""
        logger.trace("_watchdog_loop() 시작")
        while True:
            try:
                await asyncio.sleep(self.WATCHDOG_INTERVAL_SECONDS)
                logger.trace(f"Watchdog 체크 - 활성 태스크: {len(self._active_tasks)}개")
                await self._cleanup_zombie_tasks()
            except asyncio.CancelledError:
                logger.info("Watchdog 태스크 종료됨")
                break
            except Exception as e:
                logger.exception(f"Watchdog 오류: {e}")

    async def _cleanup_zombie_tasks(self) -> None:
        """30분 이상 실행 중인 태스크 정리."""
        logger.trace("_cleanup_zombie_tasks() 시작")
        now = time.time()
        zombie_tasks = []

        for task_id, info in list(self._active_tasks.items()):
            elapsed = now - info.started_at
            logger.trace(f"태스크 체크 - id={task_id}, user={info.user_id}, elapsed={elapsed:.0f}s")
            if elapsed > self.TASK_TIMEOUT_SECONDS:
                zombie_tasks.append((task_id, info))

        logger.trace(f"좀비 태스크 발견: {len(zombie_tasks)}개")

        for task_id, info in zombie_tasks:
            elapsed_min = int((now - info.started_at) / 60)
            logger.warning(
                f"좀비 태스크 감지: trace={info.trace_id}, user={info.user_id}, "
                f"elapsed={elapsed_min}분, session={info.session_id[:8]}"
            )

            # 태스크 취소
            if info.task and not info.task.done():
                info.task.cancel()
                logger.info(f"태스크 취소됨 - trace={info.trace_id}")

            # Claude 프로세스 kill (session_id로 찾기)
            await self._kill_claude_process(info.session_id)

            # 추적 목록에서 제거
            self._active_tasks.pop(task_id, None)

    async def _kill_claude_process(self, session_id: str) -> None:
        """특정 세션의 Claude 프로세스 종료."""
        logger.trace(f"_kill_claude_process() - session={session_id[:8]}")
        try:
            # session_id를 포함한 claude 프로세스 찾기
            result = subprocess.run(
                ["pgrep", "-f", f"claude.*{session_id}"],
                capture_output=True,
                text=True,
            )
            pids = result.stdout.strip().split("\n")
            pids = [p for p in pids if p]

            logger.trace(f"Claude 프로세스 PID 목록: {pids}")

            for pid in pids:
                try:
                    subprocess.run(["kill", "-9", pid], check=True)
                    logger.info(f"Claude 프로세스 종료: PID {pid}")
                except subprocess.CalledProcessError:
                    logger.trace(f"프로세스 이미 종료됨: PID {pid}")
        except Exception as e:
            logger.warning(f"Claude 프로세스 종료 실패: {e}")

    def _register_task(self, task: asyncio.Task, user_id: str, session_id: str, trace_id: str) -> int:
        """태스크를 추적 목록에 등록."""
        task_id = id(task)
        self._active_tasks[task_id] = TaskInfo(
            user_id=user_id,
            session_id=session_id,
            trace_id=trace_id,
            task=task,
        )
        task.add_done_callback(lambda t: self._active_tasks.pop(id(t), None))
        logger.trace(f"태스크 등록 - task_id={task_id}, trace={trace_id}, session={session_id[:8]}")
        return task_id

    def get_active_task_count(self, user_id: str = None) -> int:
        """활성 태스크 수 반환. user_id 지정 시 해당 유저만."""
        if user_id is None:
            return len(self._active_tasks)
        return sum(1 for info in self._active_tasks.values() if info.user_id == user_id)

    def _is_authorized(self, chat_id: int) -> bool:
        logger.trace(f"_is_authorized() - chat_id={chat_id}, allowed={self.allowed_chat_ids}")
        if not self.allowed_chat_ids:
            logger.trace("모든 chat_id 허용 (allowed_chat_ids 비어있음)")
            return True
        result = chat_id in self.allowed_chat_ids
        logger.trace(f"권한 체크 결과: {result}")
        return result

    def _is_authenticated(self, user_id: str) -> bool:
        logger.trace(f"_is_authenticated() - user_id={user_id}, require_auth={self.require_auth}")
        if not self.require_auth:
            logger.trace("인증 불필요 (require_auth=False)")
            return True
        result = self.auth.is_authenticated(user_id)
        logger.trace(f"인증 체크 결과: {result}")
        return result

    async def start(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle /start command."""
        chat_id = update.effective_chat.id
        trace_id = self._setup_request_context(chat_id)
        logger.info(f"/start 명령 수신")
        logger.trace(f"update.effective_user={update.effective_user}")

        if not self._is_authorized(chat_id):
            logger.debug(f"/start 거부 - 권한 없음")
            await update.message.reply_text("⛔ 권한이 없습니다.")
            clear_context()
            return

        user_id = str(chat_id)
        logger.trace("현재 세션 조회 중")
        session_id = self.sessions.get_current_session_id(user_id)
        session_info = self.sessions.get_session_info(user_id, session_id)
        history_count = self.sessions.get_history_count(user_id, session_id) if session_id else 0
        logger.trace(f"세션 정보 - session_id={session_id}, info={session_info}, history={history_count}")

        if self.require_auth:
            is_auth = self.auth.is_authenticated(user_id)
            remaining = self.auth.get_remaining_minutes(user_id)
            auth_status = f"✅ 인증됨 ({remaining}분 남음)" if is_auth else "🔒 인증 필요"
            auth_line = f"인증: {auth_status}\n"
            logger.trace(f"인증 상태 - is_auth={is_auth}, remaining={remaining}")
        else:
            auth_line = "🔓 <b>인증 없이 사용 가능</b>\n"

        logger.trace("응답 전송 중")
        await update.message.reply_text(
            f"🤖 <b>Claude Code Bot</b>\n\n"
            f"{auth_line}"
            f"세션: [{session_info}] ({history_count}개 질문)\n\n"
            f"/help 로 명령어 확인",
            parse_mode="HTML"
        )
        logger.trace("/start 완료")
        clear_context()

    async def help_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle /help command."""
        chat_id = update.effective_chat.id
        self._setup_request_context(chat_id)
        logger.info("/help 명령 수신")

        if self.require_auth:
            auth_section = (
                "🔐 인증\n"
                f"/auth &lt;키&gt; - 인증 ({self.auth.timeout_minutes}분 유효)\n"
                "/status - 인증 상태 확인\n\n"
            )
        else:
            auth_section = "🔓 <b>인증 없이 바로 사용 가능</b>\n\n"

        # 플러그인 안내
        plugin_section = ""
        if self.plugins and self.plugins.plugins:
            plugin_section = (
                "\n🔌 플러그인\n"
                "/plugins - 플러그인 목록\n"
                "/ai &lt;질문&gt; - 플러그인 건너뛰고 Claude에게 직접 질문\n"
            )
            logger.trace(f"플러그인 수: {len(self.plugins.plugins)}")

        logger.trace("응답 전송 중")
        await update.message.reply_text(
            "📖 <b>명령어 목록</b>\n\n"
            f"{auth_section}"
            "💬 세션\n"
            "/new [모델] [이름] - 새 세션\n"
            "/new_haiku_speedy - 🚀 Speedy\n"
            "/new_opus_smarty - 🧠 Smarty\n"
            "/model - 현재 세션 모델 변경\n"
            "/rename_MyName - 세션 이름 변경\n"
            "/session - 현재 세션 정보\n"
            "/session_list - 세션 목록\n"
            "/delete_&lt;id&gt; - 세션 삭제\n\n"
            "📋 매니저\n"
            "/m - 매니저 모드 (세션 관리)\n"
            "/m 질문 - 원샷 질문\n"
            f"{plugin_section}\n"
            "ℹ️ 기타\n"
            "/chatid - 내 채팅 ID 확인\n"
            "/help - 이 도움말",
            parse_mode="HTML"
        )
        logger.trace("/help 완료")
        clear_context()

    async def chatid_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle /chatid command - show user's chat ID."""
        chat_id = update.effective_chat.id
        self._setup_request_context(chat_id)
        logger.info("/chatid 명령 수신")

        user = update.effective_user
        logger.trace(f"effective_user={user}")

        user_info = ""
        if user:
            if user.username:
                user_info = f"\n• Username: @{user.username}"
            if user.first_name:
                user_info += f"\n• 이름: {user.first_name}"

        logger.trace("응답 전송 중")
        await update.message.reply_text(
            f"🆔 <b>내 정보</b>\n\n"
            f"• Chat ID: <code>{chat_id}</code>{user_info}\n\n"
            f"💡 이 ID를 <code>ALLOWED_CHAT_IDS</code>에 추가하세요.",
            parse_mode="HTML"
        )
        logger.trace("/chatid 완료")
        clear_context()

    async def plugins_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle /plugins command - show plugin list."""
        chat_id = update.effective_chat.id
        self._setup_request_context(chat_id)
        logger.info("/plugins 명령 수신")

        if not self.plugins or not self.plugins.plugins:
            logger.trace("로드된 플러그인 없음")
            await update.message.reply_text("🔌 로드된 플러그인이 없습니다.")
            clear_context()
            return

        logger.trace(f"플러그인 목록 생성 - {len(self.plugins.plugins)}개")
        lines = ["🔌 <b>플러그인 목록</b>\n"]
        for plugin in self.plugins.plugins:
            lines.append(f"• <b>/{plugin.name}</b> - {plugin.description}")
            logger.trace(f"플러그인: {plugin.name} - {plugin.description}")
        lines.append("\n💡 <code>/플러그인명</code>으로 사용법 확인")

        await update.message.reply_text("\n".join(lines), parse_mode="HTML")
        logger.trace("/plugins 완료")
        clear_context()

    async def plugin_help_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle /plugin_name command - show specific plugin usage."""
        chat_id = update.effective_chat.id
        self._setup_request_context(chat_id)

        if not self.plugins:
            logger.trace("플러그인 로더 없음")
            clear_context()
            return

        # /memo -> "memo"
        text = update.message.text.strip()
        if not text.startswith("/"):
            clear_context()
            return
        plugin_name = text[1:].split()[0]  # /memo arg -> "memo"
        logger.info(f"플러그인 도움말 요청: /{plugin_name}")

        plugin = self.plugins.get_plugin_by_name(plugin_name)
        if plugin:
            logger.trace(f"플러그인 찾음: {plugin.name}")
            await update.message.reply_text(plugin.usage, parse_mode="HTML")
        else:
            logger.trace(f"플러그인 없음: {plugin_name}")

        clear_context()

    async def ai_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle /ai command - force Claude conversation (bypass plugins)."""
        chat_id = update.effective_chat.id
        trace_id = self._setup_request_context(chat_id)
        logger.info("/ai 명령 수신")

        if not self._is_authorized(chat_id):
            logger.debug("/ai 거부 - 권한 없음")
            await update.message.reply_text("⛔ 권한이 없습니다.")
            clear_context()
            return

        user_id = str(chat_id)

        if not self._is_authenticated(user_id):
            logger.debug("/ai 거부 - 인증 필요")
            await update.message.reply_text(
                "🔒 인증이 필요합니다.\n"
                f"/auth <키>로 인증하세요. ({self.auth.timeout_minutes}분간 유효)\n"
                "/help 도움말"
            )
            clear_context()
            return

        # /ai 뒤의 메시지 추출
        if not context.args:
            logger.trace("/ai 인자 없음 - 사용법 표시")
            await update.message.reply_text(
                "🤖 <b>/ai 사용법</b>\n\n"
                "<code>/ai 질문내용</code>\n\n"
                "플러그인을 건너뛰고 Claude에게 직접 질문합니다.",
                parse_mode="HTML"
            )
            clear_context()
            return

        message = " ".join(context.args)
        short_msg = message[:50] + "..." if len(message) > 50 else message
        logger.info(f"/ai 메시지: '{short_msg}'")
        logger.trace(f"전체 메시지 길이: {len(message)}")

        # 메시지 길이 제한
        if len(message) > self.MAX_MESSAGE_LENGTH:
            logger.warning(f"메시지 길이 제한 적용: {len(message)} -> {self.MAX_MESSAGE_LENGTH}")
            message = message[:self.MAX_MESSAGE_LENGTH]

        # 세션 결정
        logger.trace("세션 결정 시작 - Lock 획득 대기")
        async with self._user_locks[user_id]:
            logger.trace("Lock 획득됨")
            session_id = self.sessions.get_current_session_id(user_id)
            logger.trace(f"현재 세션: {session_id[:8] if session_id else 'None'}")

            if not session_id:
                logger.info("새 Claude 세션 생성 중...")
                session_id = await self.claude.create_session()

                if not session_id:
                    logger.error("Claude 세션 생성 실패")
                    await update.message.reply_text("❌ Claude 세션 생성 실패. 다시 시도해주세요.")
                    clear_context()
                    return

                logger.trace(f"세션 저장 중 - session_id={session_id[:8]}")
                self.sessions.create_session(user_id, session_id, message)
                is_new_session = True
            else:
                is_new_session = False

        # 세션 모델 가져오기
        model = self.sessions.get_session_model(user_id, session_id)

        # 세션 ID 컨텍스트 설정
        set_session_id(session_id)
        logger.info(f"세션 결정 완료 - model={model}, new={is_new_session}")

        self._ensure_watchdog()

        # 동시 요청 제한 체크
        semaphore = self._user_semaphores[user_id]
        logger.trace(f"Semaphore 상태 - locked={semaphore.locked()}")
        if semaphore.locked():
            active_count = self.get_active_task_count(user_id)
            logger.warning(f"동시 요청 제한 - 활성 태스크: {active_count}개")
            await update.message.reply_text(
                f"⏳ 현재 {active_count}개 요청 처리 중입니다. 잠시 후 다시 시도해주세요."
            )
            clear_context()
            return

        logger.trace("typing 액션 전송")
        await context.bot.send_chat_action(chat_id=chat_id, action="typing")

        # 백그라운드에서 Claude 호출
        logger.trace(f"백그라운드 태스크 생성 - model={model}")
        task = asyncio.create_task(
            self._process_claude_request_with_semaphore(
                bot=context.bot,
                chat_id=chat_id,
                user_id=user_id,
                session_id=session_id,
                message=message,
                is_new_session=is_new_session,
                trace_id=trace_id,
                model=model,
            )
        )
        self._register_task(task, user_id, session_id, trace_id)
        logger.trace("/ai 핸들러 종료 - 백그라운드 처리 중")
        # 컨텍스트는 백그라운드 태스크에서 정리

    async def auth_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle /auth command."""
        chat_id = update.effective_chat.id
        self._setup_request_context(chat_id)
        logger.info("/auth 명령 수신")

        if not self._is_authorized(chat_id):
            logger.debug("/auth 거부 - 권한 없음")
            await update.message.reply_text("⛔ 권한이 없습니다.")
            clear_context()
            return

        user_id = str(chat_id)

        if not context.args:
            logger.trace("/auth 인자 없음")
            await update.message.reply_text("사용법: /auth <비밀키>")
            clear_context()
            return

        key = context.args[0]
        logger.trace(f"인증 시도 - key_length={len(key)}")

        if self.auth.authenticate(user_id, key):
            logger.info("인증 성공")
            await update.message.reply_text(f"✅ 인증 성공! {self.auth.timeout_minutes}분간 유효합니다.")
        else:
            logger.warning("인증 실패 - 잘못된 키")
            await update.message.reply_text("❌ 인증 실패. 키가 틀렸습니다.")

        clear_context()

    async def status_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle /status command."""
        chat_id = update.effective_chat.id
        self._setup_request_context(chat_id)
        logger.info("/status 명령 수신")

        if not self._is_authorized(chat_id):
            logger.debug("/status 거부 - 권한 없음")
            await update.message.reply_text("⛔ 권한이 없습니다.")
            clear_context()
            return

        user_id = str(chat_id)

        if self.auth.is_authenticated(user_id):
            remaining = self.auth.get_remaining_minutes(user_id)
            logger.trace(f"인증됨 - remaining={remaining}분")
            await update.message.reply_text(f"✅ 인증됨 ({remaining}분 남음)")
        else:
            logger.trace("인증 필요")
            await update.message.reply_text("🔒 인증 필요\n/auth <키>로 인증하세요.")

        clear_context()

    async def new_session(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle /new command.

        Usage:
            /new              - 기본 모델 (sonnet)
            /new opus         - Opus 모델
            /new haiku 이름   - Haiku 모델 + 세션 이름
        """
        from src.claude.session import SUPPORTED_MODELS, DEFAULT_MODEL

        chat_id = update.effective_chat.id
        user_id = str(chat_id)
        self._setup_request_context(chat_id)

        # 모델과 이름 파싱: /new [model] [name...]
        model = DEFAULT_MODEL
        session_name = ""

        if context.args:
            first_arg = context.args[0].lower()
            if first_arg in SUPPORTED_MODELS:
                model = first_arg
                # 나머지는 이름
                if len(context.args) > 1:
                    session_name = " ".join(context.args[1:])
            else:
                # 첫 번째 인자가 모델이 아니면 전체가 이름
                session_name = " ".join(context.args)

        # 이름 길이 제한
        if len(session_name) > 50:
            session_name = session_name[:50]

        logger.info(f"/new 명령 수신 - 새 세션 요청 (model={model}, name={session_name or '(없음)'})")

        if not self._is_authorized(chat_id):
            logger.debug("/new 거부 - 권한 없음")
            await update.message.reply_text("⛔ 권한이 없습니다.")
            clear_context()
            return

        if not self._is_authenticated(user_id):
            logger.debug("/new 거부 - 인증 필요")
            await update.message.reply_text("🔒 먼저 인증이 필요합니다.\n/auth <키>")
            clear_context()
            return

        model_emoji = {"opus": "🧠", "sonnet": "⚡", "haiku": "🚀"}.get(model, "")
        logger.trace("세션 생성 안내 메시지 전송")
        await update.message.reply_text(f"🔄 새 Claude 세션 생성 중... {model_emoji} {model}")

        # 새 Claude 세션 생성
        logger.trace(f"Claude 세션 생성 시작 - model={model}")
        session_id = await self.claude.create_session()
        if not session_id:
            logger.error("Claude 세션 생성 실패")
            await update.message.reply_text("❌ Claude 세션 생성 실패. 다시 시도해주세요.")
            clear_context()
            return

        logger.info(f"새 세션 생성됨: {session_id[:8]}, model={model}")

        # 세션 저장 (모델, 이름 포함)
        logger.trace("세션 저장 중")
        self.sessions.create_session(user_id, session_id, "(새 세션)", model=model, name=session_name)

        name_line = f"\n• 이름: {session_name}" if session_name else ""
        await update.message.reply_text(
            f"✅ 새 세션 시작!\n"
            f"• ID: <code>{session_id[:8]}</code>{name_line}\n"
            f"• 모델: {model_emoji} {model}",
            parse_mode="HTML"
        )
        logger.trace("/new 완료")
        clear_context()

    async def new_session_opus(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle /new_opus command - shortcut for /new opus."""
        context.args = ["opus"]
        await self.new_session(update, context)

    async def new_session_sonnet(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle /new_sonnet command - shortcut for /new sonnet."""
        context.args = ["sonnet"]
        await self.new_session(update, context)

    async def new_session_haiku(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle /new_haiku command - shortcut for /new haiku."""
        context.args = ["haiku"]
        await self.new_session(update, context)

    async def new_session_haiku_speedy(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle /new_haiku_speedy command - quick haiku session with name."""
        context.args = ["haiku", "Speedy"]
        await self.new_session(update, context)

    async def new_session_opus_smarty(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle /new_opus_smarty command - smart opus session with name."""
        context.args = ["opus", "Smarty"]
        await self.new_session(update, context)

    async def model_opus_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle /model_opus command - shortcut for /model opus."""
        context.args = ["opus"]
        await self.model_command(update, context)

    async def model_sonnet_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle /model_sonnet command - shortcut for /model sonnet."""
        context.args = ["sonnet"]
        await self.model_command(update, context)

    async def model_haiku_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle /model_haiku command - shortcut for /model haiku."""
        context.args = ["haiku"]
        await self.model_command(update, context)

    async def model_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle /model command - change current session's model.

        Usage:
            /model         - 현재 모델 확인
            /model opus    - Opus로 변경
            /model sonnet  - Sonnet으로 변경
            /model haiku   - Haiku로 변경
        """
        from src.claude.session import SUPPORTED_MODELS

        chat_id = update.effective_chat.id
        user_id = str(chat_id)
        self._setup_request_context(chat_id)
        logger.info("/model 명령 수신")

        if not self._is_authorized(chat_id):
            logger.debug("/model 거부 - 권한 없음")
            await update.message.reply_text("⛔ 권한이 없습니다.")
            clear_context()
            return

        if not self._is_authenticated(user_id):
            logger.debug("/model 거부 - 인증 필요")
            await update.message.reply_text("🔒 먼저 인증이 필요합니다.\n/auth <키>")
            clear_context()
            return

        # 현재 세션 확인
        session_id = self.sessions.get_current_session_id(user_id)
        if not session_id:
            logger.trace("활성 세션 없음")
            await update.message.reply_text(
                "📭 활성 세션이 없습니다.\n\n"
                "새 세션을 시작하세요:\n"
                "/new_opus - 🧠 Opus\n"
                "/new_sonnet - ⚡ Sonnet\n"
                "/new_haiku - 🚀 Haiku",
                parse_mode="HTML"
            )
            clear_context()
            return

        current_model = self.sessions.get_session_model(user_id, session_id)

        # 인자 없으면 현재 모델 표시
        if not context.args:
            model_emoji = {"opus": "🧠", "sonnet": "⚡", "haiku": "🚀"}.get(current_model, "")
            logger.trace(f"현재 모델 표시: {current_model}")
            await update.message.reply_text(
                f"🔧 <b>현재 모델</b>: {model_emoji} {current_model}\n\n"
                f"변경하려면:\n"
                f"/model opus - 🧠 최고 품질\n"
                f"/model sonnet - ⚡ 균형\n"
                f"/model haiku - 🚀 빠름",
                parse_mode="HTML"
            )
            clear_context()
            return

        # 모델 변경
        new_model = context.args[0].lower()
        if new_model not in SUPPORTED_MODELS:
            await update.message.reply_text(
                f"❌ 지원하지 않는 모델: {new_model}\n\n"
                f"사용 가능: {', '.join(SUPPORTED_MODELS)}",
            )
            clear_context()
            return

        if new_model == current_model:
            model_emoji = {"opus": "🧠", "sonnet": "⚡", "haiku": "🚀"}.get(current_model, "")
            await update.message.reply_text(f"ℹ️ 이미 {model_emoji} {current_model} 모델입니다.")
            clear_context()
            return

        # 세션 데이터에서 모델 변경
        user_data = self.sessions._data.get(user_id)
        if user_data and session_id in user_data.get("sessions", {}):
            user_data["sessions"][session_id]["model"] = new_model
            self.sessions._save()
            logger.info(f"모델 변경: {current_model} -> {new_model}, session={session_id[:8]}")

            model_emoji = {"opus": "🧠", "sonnet": "⚡", "haiku": "🚀"}.get(new_model, "")
            await update.message.reply_text(
                f"✅ 모델 변경 완료!\n\n"
                f"• 이전: {current_model}\n"
                f"• 현재: {model_emoji} {new_model}",
                parse_mode="HTML"
            )
        else:
            await update.message.reply_text("❌ 세션을 찾을 수 없습니다.")

        clear_context()

    async def session_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle /session command - show current session info."""
        chat_id = update.effective_chat.id
        self._setup_request_context(chat_id)
        logger.info("/session 명령 수신")

        if not self._is_authorized(chat_id):
            logger.debug("/session 거부 - 권한 없음")
            await update.message.reply_text("⛔ 권한이 없습니다.")
            clear_context()
            return

        user_id = str(chat_id)

        if not self._is_authenticated(user_id):
            logger.debug("/session 거부 - 인증 필요")
            await update.message.reply_text("🔒 먼저 인증이 필요합니다.\n/auth <키>")
            clear_context()
            return

        logger.trace("현재 세션 조회 중")
        session_id = self.sessions.get_current_session_id(user_id)
        if not session_id:
            logger.trace("활성 세션 없음")
            await update.message.reply_text(
                "📭 활성 세션이 없습니다.\n\n"
                "• /new - 새 세션 시작\n"
                "• /session_list - 저장된 세션 목록",
                parse_mode="HTML"
            )
            clear_context()
            return

        logger.trace(f"세션 히스토리 조회 - session={session_id[:8]}")
        history = self.sessions.get_session_history(user_id, session_id)
        count = len(history)
        model = self.sessions.get_session_model(user_id, session_id)
        model_emoji = {"opus": "🧠", "sonnet": "⚡", "haiku": "🚀"}.get(model, "")
        session_name = self.sessions.get_session_name(user_id, session_id)
        logger.trace(f"히스토리 수: {count}, 모델: {model}, 이름: {session_name or '(없음)'}")

        # Recent 10 messages
        recent = history[-10:]
        history_lines = []
        start_idx = len(history) - len(recent) + 1
        for i, q in enumerate(recent, start=start_idx):
            short_q = truncate_message(q, 40)
            history_lines.append(f"{i}. {short_q}")

        history_text = "\n".join(history_lines) if history_lines else "(없음)"

        name_line = f"• 이름: {session_name}\n" if session_name else ""
        await update.message.reply_text(
            f"📊 <b>현재 세션</b>\n\n"
            f"• ID: <code>{session_id[:8]}</code>\n"
            f"{name_line}"
            f"• 모델: {model_emoji} {model}\n"
            f"• 질문: {count}개\n\n"
            f"<b>대화 내용</b> (최근 10개)\n{history_text}\n\n"
            f"<b>모델 변경</b>\n"
            f"/model_opus 🧠 | /model_sonnet ⚡ | /model_haiku 🚀\n\n"
            f"<b>세션 관리</b>\n"
            f"/history_{session_id[:8]} | /delete_{session_id[:8]}",
            parse_mode="HTML"
        )
        logger.trace("/session 완료")
        clear_context()

    async def session_list_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle /session_list command."""
        chat_id = update.effective_chat.id
        self._setup_request_context(chat_id)
        logger.info("/session_list 명령 수신")

        if not self._is_authorized(chat_id):
            logger.debug("/session_list 거부 - 권한 없음")
            await update.message.reply_text("⛔ 권한이 없습니다.")
            clear_context()
            return

        user_id = str(chat_id)

        if not self._is_authenticated(user_id):
            logger.debug("/session_list 거부 - 인증 필요")
            await update.message.reply_text("🔒 먼저 인증이 필요합니다.\n/auth <키>")
            clear_context()
            return

        logger.trace("세션 목록 조회 중")
        sessions = self.sessions.list_sessions(user_id)
        if not sessions:
            logger.trace("저장된 세션 없음")
            await update.message.reply_text("📭 저장된 세션이 없습니다.")
            clear_context()
            return

        logger.trace(f"세션 수: {len(sessions)}")

        # Get histories for quick list
        logger.trace("히스토리 조회 중")
        histories = {
            s["full_session_id"]: self.sessions.get_session_history(user_id, s["full_session_id"])
            for s in sessions
        }

        # Send quick list
        quick_list = format_session_quick_list(sessions, histories)

        if not self.session_list_ai_summary:
            # AI 요약 비활성화 - 목록만 전송
            logger.trace("AI 요약 비활성화 - 목록만 전송")
            await update.message.reply_text(quick_list, parse_mode="HTML")
            clear_context()
            return

        # AI 요약 활성화
        logger.trace("AI 요약 활성화 - 분석 중 메시지 전송")
        await update.message.reply_text(
            quick_list + "\n\n🔍 AI 분석 중...",
            parse_mode="HTML"
        )

        # Show typing indicator
        await context.bot.send_chat_action(
            chat_id=update.effective_chat.id,
            action="typing"
        )

        # Generate AI summaries
        logger.trace("AI 요약 생성 시작")
        analysis_lines = []
        for s in sessions:
            history = histories.get(s["full_session_id"], [])
            if history:
                logger.trace(f"세션 {s['session_id']} 요약 중")
                summary = await self.claude.summarize(history)
            else:
                summary = "(내용 없음)"

            analysis_lines.append(f"<b>/s_{s['session_id']}</b>\n{summary}")

        logger.trace("AI 요약 전송")
        await update.message.reply_text(
            "📊 <b>AI 분석 결과</b>\n\n" + "\n\n".join(analysis_lines),
            parse_mode="HTML"
        )
        logger.trace("/session_list 완료")
        clear_context()

    async def switch_session_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle /s_<id> command for session switching."""
        chat_id = update.effective_chat.id
        self._setup_request_context(chat_id)

        if not self._is_authorized(chat_id):
            logger.debug("세션 전환 거부 - 권한 없음")
            await update.message.reply_text("⛔ 권한이 없습니다.")
            clear_context()
            return

        user_id = str(chat_id)

        if not self._is_authenticated(user_id):
            logger.debug("세션 전환 거부 - 인증 필요")
            await update.message.reply_text("🔒 먼저 인증이 필요합니다.\n/auth <키>")
            clear_context()
            return

        text = update.message.text
        if not text.startswith("/s_"):
            clear_context()
            return

        target = text[3:]  # Extract session prefix
        logger.info(f"세션 전환 요청: /s_{target}")

        logger.trace(f"세션 검색 중 - prefix={target}")
        target_info = self.sessions.get_session_by_prefix(user_id, target)
        if not target_info:
            logger.debug(f"세션 없음: {target}")
            await update.message.reply_text(f"❌ 세션 '{target}'을 찾을 수 없습니다.")
            clear_context()
            return

        logger.trace(f"세션 전환 시도 - target={target_info['session_id']}")
        if self.sessions.switch_session(user_id, target):
            logger.info(f"세션 전환 성공: {target_info['session_id']}")
            await update.message.reply_text(
                f"✅ 세션 전환 완료!\n\n"
                f"• ID: <code>{target_info['session_id']}</code>\n"
                f"• 질문: {target_info['history_count']}개",
                parse_mode="HTML"
            )
        else:
            logger.error(f"세션 전환 실패: {target}")
            await update.message.reply_text("❌ 세션 전환 실패")

        clear_context()

    async def manager_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle /m command - manager session.

        Usage:
            /m              - 매니저 세션으로 전환
            /m 질문         - 원샷 질문 (현재 세션 유지)
        """
        chat_id = update.effective_chat.id
        user_id = str(chat_id)
        trace_id = self._setup_request_context(chat_id)
        logger.info("/m 명령 수신")

        if not self._is_authorized(chat_id):
            logger.debug("/m 거부 - 권한 없음")
            await update.message.reply_text("⛔ 권한이 없습니다.")
            clear_context()
            return

        if not self._is_authenticated(user_id):
            logger.debug("/m 거부 - 인증 필요")
            await update.message.reply_text("🔒 먼저 인증이 필요합니다.\n/auth <키>")
            clear_context()
            return

        # 매니저 세션 확인/생성
        manager_session_id = self.sessions.get_manager_session_id(user_id)
        if not manager_session_id:
            logger.info("매니저 세션 생성 중...")
            await update.message.reply_text("📋 매니저 세션 생성 중...")
            manager_session_id = await self.claude.create_session()
            if not manager_session_id:
                await update.message.reply_text("❌ 매니저 세션 생성 실패")
                clear_context()
                return
            self.sessions.create_manager_session(user_id, manager_session_id)

        # 원샷 모드: /m 질문
        if context.args:
            message = " ".join(context.args)
            logger.info(f"매니저 원샷 질문: {message[:50]}")

            # 세션 컨텍스트 주입
            sessions_summary = self.sessions.get_all_sessions_summary(user_id)
            full_message = (
                f"[세션 관리자 모드]\n"
                f"사용자의 세션 목록:\n{sessions_summary}\n\n"
                f"질문: {message}"
            )

            await context.bot.send_chat_action(chat_id=chat_id, action="typing")

            response = await self.claude.chat(full_message, manager_session_id, model="sonnet")
            if response.error:
                await update.message.reply_text(f"❌ 오류: {response.error.value}")
            else:
                await update.message.reply_text(
                    f"📋 <b>Manager</b>\n\n{response.text}",
                    parse_mode="HTML"
                )
            clear_context()
            return

        # 전환 모드: /m
        current_session_id = self.sessions.get_current_session_id(user_id)
        if current_session_id and current_session_id != manager_session_id:
            self.sessions.set_previous_session_id(user_id, current_session_id)

        self.sessions.set_current(user_id, manager_session_id)
        set_session_id(manager_session_id)

        await update.message.reply_text(
            "📋 <b>매니저 모드</b>\n\n"
            "세션 관리를 도와드릴게요.\n"
            "• 세션 검색/정리/추천\n"
            "• 작업 요약\n\n"
            "/back - 이전 세션으로\n"
            "/exit - 매니저 종료",
            parse_mode="HTML"
        )
        logger.info(f"매니저 모드 전환됨")
        clear_context()

    async def back_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle /back command - return to previous session."""
        chat_id = update.effective_chat.id
        user_id = str(chat_id)
        self._setup_request_context(chat_id)
        logger.info("/back 명령 수신")

        if not self._is_authorized(chat_id):
            await update.message.reply_text("⛔ 권한이 없습니다.")
            clear_context()
            return

        prev_session_id = self.sessions.get_previous_session_id(user_id)
        if not prev_session_id:
            await update.message.reply_text(
                "📭 돌아갈 세션이 없습니다.\n\n"
                "/session_list 세션 목록 확인"
            )
            clear_context()
            return

        # 이전 세션이 삭제됐는지 확인
        session_info = self.sessions.get_session_by_prefix(user_id, prev_session_id[:8])
        if not session_info:
            await update.message.reply_text("❌ 이전 세션을 찾을 수 없습니다.")
            self.sessions.set_previous_session_id(user_id, None)
            clear_context()
            return

        self.sessions.set_current(user_id, prev_session_id)
        self.sessions.set_previous_session_id(user_id, None)

        name = self.sessions.get_session_name(user_id, prev_session_id)
        name_display = f" ({name})" if name else ""

        await update.message.reply_text(
            f"✅ 세션 복귀!\n\n"
            f"• ID: <code>{prev_session_id[:8]}</code>{name_display}",
            parse_mode="HTML"
        )
        clear_context()

    async def exit_manager_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle /exit command - exit manager mode."""
        chat_id = update.effective_chat.id
        user_id = str(chat_id)
        self._setup_request_context(chat_id)
        logger.info("/exit 명령 수신")

        if not self._is_authorized(chat_id):
            await update.message.reply_text("⛔ 권한이 없습니다.")
            clear_context()
            return

        # 매니저 세션인지 확인
        current_session_id = self.sessions.get_current_session_id(user_id)
        manager_session_id = self.sessions.get_manager_session_id(user_id)

        if current_session_id != manager_session_id:
            await update.message.reply_text("ℹ️ 매니저 모드가 아닙니다.")
            clear_context()
            return

        # 이전 세션 또는 current 해제
        prev_session_id = self.sessions.get_previous_session_id(user_id)
        if prev_session_id:
            self.sessions.set_current(user_id, prev_session_id)
            self.sessions.set_previous_session_id(user_id, None)
            name = self.sessions.get_session_name(user_id, prev_session_id)
            name_display = f" ({name})" if name else ""
            await update.message.reply_text(
                f"✅ 매니저 종료, 세션 복귀!\n\n"
                f"• ID: <code>{prev_session_id[:8]}</code>{name_display}",
                parse_mode="HTML"
            )
        else:
            self.sessions.clear_current(user_id)
            await update.message.reply_text(
                "✅ 매니저 종료!\n\n"
                "/new 새 세션 시작\n"
                "/session_list 세션 목록"
            )

        clear_context()

    async def rename_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle /rename command - rename current session."""
        chat_id = update.effective_chat.id
        user_id = str(chat_id)
        self._setup_request_context(chat_id)
        logger.info("/rename 명령 수신")

        if not self._is_authorized(chat_id):
            logger.debug("/rename 거부 - 권한 없음")
            await update.message.reply_text("⛔ 권한이 없습니다.")
            clear_context()
            return

        if not self._is_authenticated(user_id):
            logger.debug("/rename 거부 - 인증 필요")
            await update.message.reply_text("🔒 먼저 인증이 필요합니다.\n/auth <키>")
            clear_context()
            return

        session_id = self.sessions.get_current_session_id(user_id)
        if not session_id:
            logger.trace("활성 세션 없음")
            await update.message.reply_text("📭 활성 세션이 없습니다.")
            clear_context()
            return

        # /rename_새이름 형태 지원
        text = update.message.text
        if text.startswith("/rename_"):
            new_name = text[8:]  # /rename_ 이후 전체
        elif context.args:
            new_name = " ".join(context.args)
        else:
            current_name = self.sessions.get_session_name(user_id, session_id)
            logger.trace(f"현재 이름: {current_name or '(없음)'}")
            await update.message.reply_text(
                f"✏️ <b>세션 이름 변경</b>\n\n"
                f"• 현재: {current_name or '(이름 없음)'}\n"
                f"• 세션: <code>{session_id[:8]}</code>\n\n"
                f"사용법: <code>/rename_새이름</code>",
                parse_mode="HTML"
            )
            clear_context()
            return
        if len(new_name) > 50:
            await update.message.reply_text("❌ 이름이 너무 깁니다. (최대 50자)")
            clear_context()
            return

        if self.sessions.rename_session(user_id, session_id, new_name):
            await update.message.reply_text(
                f"✅ 세션 이름 변경 완료!\n\n"
                f"• 세션: <code>{session_id[:8]}</code>\n"
                f"• 이름: {new_name}",
                parse_mode="HTML"
            )
        else:
            await update.message.reply_text("❌ 이름 변경 실패")

        clear_context()

    async def delete_session_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle /d_<id> command for deleting a session."""
        chat_id = update.effective_chat.id
        user_id = str(chat_id)
        self._setup_request_context(chat_id)

        if not self._is_authorized(chat_id):
            logger.debug("세션 삭제 거부 - 권한 없음")
            await update.message.reply_text("⛔ 권한이 없습니다.")
            clear_context()
            return

        if not self._is_authenticated(user_id):
            logger.debug("세션 삭제 거부 - 인증 필요")
            await update.message.reply_text("🔒 먼저 인증이 필요합니다.\n/auth <키>")
            clear_context()
            return

        text = update.message.text
        if text.startswith("/delete_"):
            target = text[8:]  # /delete_xxxxx
        elif text.startswith("/d_"):
            target = text[3:]  # /d_xxxxx
        else:
            clear_context()
            return

        logger.info(f"세션 삭제 요청: {target}")

        target_info = self.sessions.get_session_by_prefix(user_id, target)
        if not target_info:
            logger.debug(f"세션 없음: {target}")
            await update.message.reply_text(f"❌ 세션 '{target}'을 찾을 수 없습니다.")
            clear_context()
            return

        full_session_id = target_info["full_session_id"]
        session_name = target_info.get("name", "")

        if self.sessions.delete_session(user_id, full_session_id):
            name_info = f" ({session_name})" if session_name else ""
            await update.message.reply_text(
                f"🗑️ 세션 삭제 완료!\n\n"
                f"• ID: <code>{target_info['session_id']}</code>{name_info}\n"
                f"• 질문: {target_info['history_count']}개",
                parse_mode="HTML"
            )
        else:
            await update.message.reply_text("❌ 세션 삭제 실패")

        clear_context()

    async def history_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle /h_<id> command for viewing session history."""
        chat_id = update.effective_chat.id
        self._setup_request_context(chat_id)

        if not self._is_authorized(chat_id):
            logger.debug("히스토리 조회 거부 - 권한 없음")
            await update.message.reply_text("⛔ 권한이 없습니다.")
            clear_context()
            return

        user_id = str(chat_id)

        if not self._is_authenticated(user_id):
            logger.debug("히스토리 조회 거부 - 인증 필요")
            await update.message.reply_text("🔒 먼저 인증이 필요합니다.\n/auth <키>")
            clear_context()
            return

        text = update.message.text
        if text.startswith("/history_"):
            target = text[9:]  # /history_xxxxx
        elif text.startswith("/h_"):
            target = text[3:]  # /h_xxxxx
        else:
            clear_context()
            return

        logger.info(f"히스토리 조회 요청: {target}")

        logger.trace(f"세션 검색 중 - prefix={target}")
        target_info = self.sessions.get_session_by_prefix(user_id, target)
        if not target_info:
            logger.debug(f"세션 없음: {target}")
            await update.message.reply_text(f"❌ 세션 '{target}'을 찾을 수 없습니다.")
            clear_context()
            return

        # 히스토리 조회
        logger.trace(f"히스토리 조회 - session={target_info['full_session_id'][:8]}")
        history = self.sessions.get_session_history(user_id, target_info["full_session_id"])
        if not history:
            logger.trace("히스토리 없음")
            await update.message.reply_text("📭 히스토리가 없습니다.")
            clear_context()
            return

        logger.trace(f"히스토리 수: {len(history)}")

        # 히스토리 포맷팅
        history_lines = []
        for i, q in enumerate(history, start=1):
            short_q = truncate_message(q, 60)
            history_lines.append(f"{i}. {short_q}")

        history_text = "\n".join(history_lines)

        await update.message.reply_text(
            f"📜 <b>세션 히스토리</b>\n"
            f"• ID: <code>{target_info['session_id']}</code>\n"
            f"• 질문: {len(history)}개\n\n"
            f"{history_text}\n\n"
            f"/s_{target_info['session_id']} 세션이동",
            parse_mode="HTML"
        )
        logger.trace("히스토리 조회 완료")
        clear_context()

    async def handle_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle regular text messages.

        Fire-and-Forget 패턴:
        1. 인증/권한 체크
        2. 세션 결정 (Lock으로 보호)
        3. 백그라운드 태스크로 Claude 호출 + 응답 전송
        4. 핸들러는 즉시 리턴
        """
        chat_id = update.effective_chat.id
        user_id = str(chat_id)
        message = update.message.text
        short_msg = message[:50] + "..." if len(message) > 50 else message

        trace_id = self._setup_request_context(chat_id)
        logger.info(f"메시지 수신: '{short_msg}'")
        logger.trace(f"전체 메시지 길이: {len(message)}")

        if not self._is_authorized(chat_id):
            logger.debug("메시지 거부 - 권한 없음")
            await update.message.reply_text("⛔ 권한이 없습니다.")
            clear_context()
            return

        # 메시지 길이 제한 (DoS 방지)
        if len(message) > self.MAX_MESSAGE_LENGTH:
            original_len = len(message)
            message = message[:self.MAX_MESSAGE_LENGTH]
            logger.warning(f"메시지 길이 제한 적용: {original_len} -> {self.MAX_MESSAGE_LENGTH}")

        # 플러그인 처리 시도 (인증 전에 처리 - 플러그인은 인증 불필요)
        if self.plugins:
            logger.trace(f"플러그인 처리 시도 - 로드된 플러그인: {len(self.plugins.plugins)}개")
            try:
                result = await self.plugins.process_message(message, chat_id)
                if result and result.handled:
                    logger.info(f"플러그인 처리 완료")
                    if result.response:
                        try:
                            await update.message.reply_text(result.response, parse_mode="HTML")
                        except Exception:
                            await update.message.reply_text(result.response)
                    clear_context()
                    return
                logger.trace("플러그인 매칭 없음 → Claude 처리")
            except Exception as e:
                logger.error(f"플러그인 처리 오류: {e}", exc_info=True)
                # 플러그인 오류 시 Claude로 fallback
        else:
            logger.trace("플러그인 로더 없음")

        if not self._is_authenticated(user_id):
            logger.debug("메시지 거부 - 인증 필요")
            await update.message.reply_text(
                "🔒 인증이 필요합니다.\n"
                f"/auth <키>로 인증하세요. ({self.auth.timeout_minutes}분간 유효)\n"
                "/help 도움말"
            )
            clear_context()
            return

        # 유저별 Lock으로 세션 결정 (race condition 방지)
        logger.trace("세션 결정 시작 - Lock 획득 대기")
        async with self._user_locks[user_id]:
            logger.trace("Lock 획득됨")
            session_id = self.sessions.get_current_session_id(user_id)
            logger.trace(f"현재 세션: {session_id[:8] if session_id else 'None'}")

            if not session_id:
                # 새 Claude 세션 생성
                logger.info("새 Claude 세션 생성 중...")
                session_id = await self.claude.create_session()

                if not session_id:
                    logger.error("Claude 세션 생성 실패")
                    await update.message.reply_text("❌ Claude 세션 생성 실패. 다시 시도해주세요.")
                    clear_context()
                    return

                # 세션 저장 (첫 메시지 포함)
                logger.trace(f"세션 저장 중 - session_id={session_id[:8]}")
                self.sessions.create_session(user_id, session_id, message)
                is_new_session = True
            else:
                is_new_session = False

        # 세션 모델 가져오기
        model = self.sessions.get_session_model(user_id, session_id)

        # 세션 ID 컨텍스트 설정
        set_session_id(session_id)

        # 로깅 (session_id 확정 후)
        logger.info(f"메시지 접수: model={model}, new={is_new_session}")

        # Watchdog 지연 시작
        self._ensure_watchdog()

        # 동시 요청 제한 체크
        semaphore = self._user_semaphores[user_id]
        logger.trace(f"Semaphore 상태 - locked={semaphore.locked()}")
        if semaphore.locked():
            active_count = self.get_active_task_count(user_id)
            logger.warning(f"동시 요청 제한 - 활성 태스크: {active_count}개")
            await update.message.reply_text(
                f"⏳ 현재 {active_count}개 요청 처리 중입니다. 잠시 후 다시 시도해주세요."
            )
            clear_context()
            return

        # Show typing indicator
        logger.trace("typing 액션 전송")
        await context.bot.send_chat_action(chat_id=chat_id, action="typing")

        # Fire-and-Forget: 백그라운드에서 Claude 호출 + 응답 전송
        logger.trace(f"백그라운드 태스크 생성 - model={model}")
        task = asyncio.create_task(
            self._process_claude_request_with_semaphore(
                bot=context.bot,
                chat_id=chat_id,
                user_id=user_id,
                session_id=session_id,
                message=message,
                is_new_session=is_new_session,
                trace_id=trace_id,
                model=model,
            )
        )
        # 태스크 추적 등록
        self._register_task(task, user_id, session_id, trace_id)
        logger.trace("handle_message 핸들러 종료 - 백그라운드 처리 중")
        # 핸들러는 즉시 리턴 (Claude 응답을 기다리지 않음)
        # 컨텍스트는 백그라운드 태스크에서 정리

    async def _process_claude_request_with_semaphore(
        self,
        bot,
        chat_id: int,
        user_id: str,
        session_id: str,
        message: str,
        is_new_session: bool,
        trace_id: str,
        model: str = None,
    ) -> None:
        """Semaphore로 동시 요청 제한 후 Claude 호출."""
        # 백그라운드 태스크에서 컨텍스트 재설정
        set_trace_id(trace_id)
        set_user_id(user_id)
        set_session_id(session_id)
        logger.trace(f"_process_claude_request_with_semaphore 시작 - model={model}")

        async with self._user_semaphores[user_id]:
            logger.trace("Semaphore 획득됨")
            await self._process_claude_request(
                bot=bot,
                chat_id=chat_id,
                user_id=user_id,
                session_id=session_id,
                message=message,
                is_new_session=is_new_session,
                model=model,
            )

        logger.trace("_process_claude_request_with_semaphore 완료")
        clear_context()

    async def _process_claude_request(
        self,
        bot,
        chat_id: int,
        user_id: str,
        session_id: str,
        message: str,
        is_new_session: bool,
        model: str = None,
    ) -> None:
        """백그라운드에서 Claude 호출 후 응답 전송.

        Args:
            bot: Telegram Bot instance (응답 전송용)
            chat_id: 응답을 보낼 채팅 ID
            user_id: 사용자 ID (로깅/세션용)
            session_id: Claude 세션 ID
            message: 사용자 메시지
            is_new_session: 새 세션 여부
            model: 사용할 모델 (opus, sonnet, haiku)
        """
        start_time = time.time()
        short_msg = message[:50] + "..." if len(message) > 50 else message

        logger.info(f"Claude 호출 시작 - session={session_id[:8]}, model={model}")
        logger.trace(f"메시지: '{short_msg}'")
        logger.trace(f"새 세션: {is_new_session}")

        try:
            # 매니저 세션인지 확인
            manager_session_id = self.sessions.get_manager_session_id(user_id)
            is_manager = session_id == manager_session_id

            # 매니저 세션이면 세션 정보 주입
            actual_message = message
            if is_manager:
                sessions_summary = self.sessions.get_all_sessions_summary(user_id)
                actual_message = (
                    f"[세션 관리자 모드]\n"
                    f"사용자의 세션 목록:\n{sessions_summary}\n\n"
                    f"사용자 요청: {message}"
                )
                logger.trace("매니저 세션 - 세션 정보 주입됨")

            # Claude 호출
            logger.trace(f"claude.chat() 호출 - model={model}")
            response, error, _ = await self.claude.chat(actual_message, session_id, model=model)

            elapsed = time.time() - start_time
            logger.info(f"Claude 응답 완료 - session={session_id[:8]}, elapsed={elapsed:.1f}s, length={len(response)}")
            logger.debug(f"===== Claude 응답 전체 (START) =====")
            logger.debug(response)
            logger.debug(f"===== Claude 응답 전체 (END) =====")
            logger.trace(f"에러: {error}")

            # 기존 세션이면 메시지 추가 (명시적 session_id 사용)
            if not is_new_session:
                logger.trace("세션 히스토리에 메시지 추가")
                self.sessions.add_message(user_id, session_id, message)

            # 에러 처리
            if error == "TIMEOUT":
                logger.warning("Claude 타임아웃")
                response = "⏱️ 응답 시간 초과. 다시 시도해주세요."
            elif error and error != "SESSION_NOT_FOUND":
                logger.error(f"Claude 오류: {error}")
                response = f"❌ 오류 발생: {error}"

            # 세션 정보 prefix 추가
            session_info = self.sessions.get_session_info(user_id, session_id)
            history_count = self.sessions.get_history_count(user_id, session_id)

            if is_manager:
                prefix = f"📋 <b>[Manager|#{history_count}]</b>\n\n"
                suffix = "\n\n/back 이전세션 | /exit 종료"
            else:
                prefix = f"<b>[{session_info}|#{history_count}]</b>\n\n"
                suffix = (
                    f"\n\n"
                    f"/s_{session_info} 세션이동\n"
                    f"/h_{session_info} 히스토리"
                )

            full_response = prefix + response + suffix
            logger.trace(f"최종 응답 길이: {len(full_response)}")

            # 응답 전송 (chat_id로 직접 전송)
            logger.trace("응답 전송 시작")
            await self._send_message_to_chat(bot, chat_id, full_response)
            logger.trace("응답 전송 완료")

        except Exception as e:
            logger.exception(f"Claude 처리 실패: {e}")
            await bot.send_message(
                chat_id=chat_id,
                text="❌ 오류가 발생했습니다. 잠시 후 다시 시도해주세요."
            )

    async def _send_message_to_chat(
        self,
        bot,
        chat_id: int,
        text: str,
        max_length: int = 4000,
    ) -> None:
        """chat_id로 직접 메시지 전송 (긴 메시지는 분할)."""
        logger.trace(f"_send_message_to_chat - length={len(text)}, max={max_length}")

        if len(text) <= max_length:
            try:
                await bot.send_message(chat_id=chat_id, text=text, parse_mode="HTML")
                logger.trace("메시지 전송 성공 (HTML)")
            except Exception as e:
                logger.trace(f"HTML 전송 실패, plain text로 재시도: {e}")
                await bot.send_message(chat_id=chat_id, text=text)
            return

        # Split into chunks
        chunks = [text[i:i + max_length] for i in range(0, len(text), max_length)]
        logger.trace(f"메시지 분할: {len(chunks)}개 청크")

        for i, chunk in enumerate(chunks):
            logger.trace(f"청크 {i+1}/{len(chunks)} 전송 중")
            try:
                await bot.send_message(chat_id=chat_id, text=chunk, parse_mode="HTML")
            except Exception:
                await bot.send_message(chat_id=chat_id, text=chunk)

    async def _send_long_message(self, update: Update, text: str, max_length: int = 4000) -> None:
        """Send message, splitting if too long. (레거시 - update.reply_text 사용)"""
        logger.trace(f"_send_long_message - length={len(text)}")

        if len(text) <= max_length:
            try:
                await update.message.reply_text(text, parse_mode="HTML")
            except Exception:
                await update.message.reply_text(text)
            return

        # Split into chunks
        chunks = [text[i:i + max_length] for i in range(0, len(text), max_length)]
        logger.trace(f"메시지 분할: {len(chunks)}개 청크")

        for chunk in chunks:
            try:
                await update.message.reply_text(chunk, parse_mode="HTML")
            except Exception:
                await update.message.reply_text(chunk)

    async def unknown_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle unknown commands starting with /."""
        chat_id = update.effective_chat.id
        self._setup_request_context(chat_id)

        text = update.message.text
        command = text.split()[0] if text else ""
        logger.info(f"알 수 없는 명령어: {command}")

        await update.message.reply_text(
            f"❓ 알 수 없는 명령어: <code>{command}</code>\n\n"
            f"/help 명령어 목록 확인",
            parse_mode="HTML"
        )
        clear_context()

    async def error_handler(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle errors."""
        # 내부 로그에는 상세 오류 기록
        chat_id = update.effective_chat.id if update and update.effective_chat else "unknown"
        if chat_id != "unknown":
            self._setup_request_context(chat_id)

        error_type = type(context.error).__name__
        logger.error(f"에러 발생: {error_type}: {context.error}")
        logger.trace(f"에러 상세: {context.error}", exc_info=context.error)

        if update and update.effective_chat:
            # 사용자에게는 일반적인 오류 메시지만 표시 (보안)
            logger.trace("사용자에게 에러 메시지 전송")
            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text="❌ 오류가 발생했습니다. 잠시 후 다시 시도해주세요."
            )

        clear_context()
