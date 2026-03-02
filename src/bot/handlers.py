"""Telegram bot command handlers."""

import asyncio
import logging
import subprocess
import time
from collections import defaultdict
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Optional

from telegram import Update
from telegram.ext import ContextTypes

from .formatters import format_session_quick_list, truncate_message

if TYPE_CHECKING:
    from src.claude.client import ClaudeClient
    from src.claude.session import SessionStore
    from src.plugins.loader import PluginLoader
    from .middleware import AuthManager

logger = logging.getLogger(__name__)


@dataclass
class TaskInfo:
    """백그라운드 태스크 메타데이터."""
    user_id: str
    session_id: str
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
        self.sessions = session_store
        self.claude = claude_client
        self.auth = auth_manager
        self.require_auth = require_auth
        self.allowed_chat_ids = allowed_chat_ids
        self.response_notify_seconds = response_notify_seconds
        self.session_list_ai_summary = session_list_ai_summary
        self.plugins = plugin_loader
        self._watchdog_started = False

    def _ensure_watchdog(self) -> None:
        """Watchdog 태스크 시작 (지연 초기화)."""
        if self._watchdog_started:
            return
        try:
            if self._watchdog_task is None or self._watchdog_task.done():
                self._watchdog_task = asyncio.create_task(self._watchdog_loop())
                self._watchdog_started = True
                logger.info("Watchdog 태스크 시작됨")
        except RuntimeError:
            # 이벤트 루프가 없으면 무시 (테스트 환경)
            pass

    async def _watchdog_loop(self) -> None:
        """주기적으로 장시간 실행 태스크를 체크하고 정리."""
        while True:
            try:
                await asyncio.sleep(self.WATCHDOG_INTERVAL_SECONDS)
                await self._cleanup_zombie_tasks()
            except asyncio.CancelledError:
                logger.info("Watchdog 태스크 종료됨")
                break
            except Exception as e:
                logger.exception(f"Watchdog 오류: {e}")

    async def _cleanup_zombie_tasks(self) -> None:
        """30분 이상 실행 중인 태스크 정리."""
        now = time.time()
        zombie_tasks = []

        for task_id, info in list(self._active_tasks.items()):
            elapsed = now - info.started_at
            if elapsed > self.TASK_TIMEOUT_SECONDS:
                zombie_tasks.append((task_id, info))

        for task_id, info in zombie_tasks:
            elapsed_min = int((now - info.started_at) / 60)
            logger.warning(
                f"[{info.user_id}] 좀비 태스크 감지: {elapsed_min}분 경과, "
                f"session: {info.session_id[:8]}"
            )

            # 태스크 취소
            if info.task and not info.task.done():
                info.task.cancel()
                logger.info(f"[{info.user_id}] 태스크 취소됨")

            # Claude 프로세스 kill (session_id로 찾기)
            await self._kill_claude_process(info.session_id)

            # 추적 목록에서 제거
            self._active_tasks.pop(task_id, None)

    async def _kill_claude_process(self, session_id: str) -> None:
        """특정 세션의 Claude 프로세스 종료."""
        try:
            # session_id를 포함한 claude 프로세스 찾기
            result = subprocess.run(
                ["pgrep", "-f", f"claude.*{session_id}"],
                capture_output=True,
                text=True,
            )
            pids = result.stdout.strip().split("\n")
            pids = [p for p in pids if p]

            for pid in pids:
                try:
                    subprocess.run(["kill", "-9", pid], check=True)
                    logger.info(f"Claude 프로세스 종료: PID {pid}")
                except subprocess.CalledProcessError:
                    pass  # 이미 종료됨
        except Exception as e:
            logger.warning(f"Claude 프로세스 종료 실패: {e}")

    def _register_task(self, task: asyncio.Task, user_id: str, session_id: str) -> int:
        """태스크를 추적 목록에 등록."""
        task_id = id(task)
        self._active_tasks[task_id] = TaskInfo(
            user_id=user_id,
            session_id=session_id,
            task=task,
        )
        task.add_done_callback(lambda t: self._active_tasks.pop(id(t), None))
        return task_id

    def get_active_task_count(self, user_id: str = None) -> int:
        """활성 태스크 수 반환. user_id 지정 시 해당 유저만."""
        if user_id is None:
            return len(self._active_tasks)
        return sum(1 for info in self._active_tasks.values() if info.user_id == user_id)

    def _is_authorized(self, chat_id: int) -> bool:
        if not self.allowed_chat_ids:
            return True
        return chat_id in self.allowed_chat_ids

    def _is_authenticated(self, user_id: str) -> bool:
        if not self.require_auth:
            return True
        return self.auth.is_authenticated(user_id)

    async def start(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle /start command."""
        if not self._is_authorized(update.effective_chat.id):
            await update.message.reply_text("⛔ 권한이 없습니다.")
            return

        user_id = str(update.effective_chat.id)
        session_id = self.sessions.get_current_session_id(user_id)
        session_info = self.sessions.get_session_info(user_id, session_id)
        history_count = self.sessions.get_history_count(user_id, session_id) if session_id else 0

        if self.require_auth:
            is_auth = self.auth.is_authenticated(user_id)
            remaining = self.auth.get_remaining_minutes(user_id)
            auth_status = f"✅ 인증됨 ({remaining}분 남음)" if is_auth else "🔒 인증 필요"
            auth_line = f"인증: {auth_status}\n"
        else:
            auth_line = "🔓 <b>인증 없이 사용 가능</b>\n"

        await update.message.reply_text(
            f"🤖 <b>Claude Code Bot</b>\n\n"
            f"{auth_line}"
            f"세션: [{session_info}] ({history_count}개 질문)\n\n"
            f"/help 로 명령어 확인",
            parse_mode="HTML"
        )

    async def help_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle /help command."""
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

        await update.message.reply_text(
            "📖 <b>명령어 목록</b>\n\n"
            f"{auth_section}"
            "💬 세션\n"
            "/new - 새 Claude 세션 시작\n"
            "/session - 현재 세션 정보 + 대화 내용\n"
            "/session_list - 세션 목록 + AI 요약\n"
            f"{plugin_section}\n"
            "ℹ️ 기타\n"
            "/chatid - 내 채팅 ID 확인\n"
            "/help - 이 도움말",
            parse_mode="HTML"
        )

    async def chatid_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle /chatid command - show user's chat ID."""
        chat_id = update.effective_chat.id
        user = update.effective_user

        user_info = ""
        if user:
            if user.username:
                user_info = f"\n• Username: @{user.username}"
            if user.first_name:
                user_info += f"\n• 이름: {user.first_name}"

        await update.message.reply_text(
            f"🆔 <b>내 정보</b>\n\n"
            f"• Chat ID: <code>{chat_id}</code>{user_info}\n\n"
            f"💡 이 ID를 <code>ALLOWED_CHAT_IDS</code>에 추가하세요.",
            parse_mode="HTML"
        )

    async def plugins_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle /plugins command - show plugin list."""
        if not self.plugins or not self.plugins.plugins:
            await update.message.reply_text("🔌 로드된 플러그인이 없습니다.")
            return

        lines = ["🔌 <b>플러그인 목록</b>\n"]
        for plugin in self.plugins.plugins:
            lines.append(f"• <b>/{plugin.name}</b> - {plugin.description}")
        lines.append("\n💡 <code>/플러그인명</code>으로 사용법 확인")

        await update.message.reply_text("\n".join(lines), parse_mode="HTML")

    async def plugin_help_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle /plugin_name command - show specific plugin usage."""
        if not self.plugins:
            return

        # /memo -> "memo"
        text = update.message.text.strip()
        if not text.startswith("/"):
            return
        plugin_name = text[1:].split()[0]  # /memo arg -> "memo"

        plugin = self.plugins.get_plugin_by_name(plugin_name)
        if plugin:
            await update.message.reply_text(plugin.usage, parse_mode="HTML")

    async def ai_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle /ai command - force Claude conversation (bypass plugins)."""
        if not self._is_authorized(update.effective_chat.id):
            await update.message.reply_text("⛔ 권한이 없습니다.")
            return

        user_id = str(update.effective_chat.id)

        if not self._is_authenticated(user_id):
            await update.message.reply_text(
                "🔒 인증이 필요합니다.\n"
                f"/auth <키>로 인증하세요. ({self.auth.timeout_minutes}분간 유효)\n"
                "/help 도움말"
            )
            return

        # /ai 뒤의 메시지 추출
        if not context.args:
            await update.message.reply_text(
                "🤖 <b>/ai 사용법</b>\n\n"
                "<code>/ai 질문내용</code>\n\n"
                "플러그인을 건너뛰고 Claude에게 직접 질문합니다.",
                parse_mode="HTML"
            )
            return

        message = " ".join(context.args)
        chat_id = update.effective_chat.id

        # 메시지 길이 제한
        if len(message) > self.MAX_MESSAGE_LENGTH:
            message = message[:self.MAX_MESSAGE_LENGTH]

        # 세션 결정
        async with self._user_locks[user_id]:
            session_id = self.sessions.get_current_session_id(user_id)

            if not session_id:
                logger.info(f"[{user_id}] Creating new Claude session...")
                session_id = await self.claude.create_session()

                if not session_id:
                    await update.message.reply_text("❌ Claude 세션 생성 실패. 다시 시도해주세요.")
                    return

                self.sessions.create_session(user_id, session_id, message)
                is_new_session = True
            else:
                is_new_session = False

        logger.info(f"[{user_id}] /ai 메시지: {message[:50]}... (session: {session_id[:8]})")

        self._ensure_watchdog()

        # 동시 요청 제한 체크
        semaphore = self._user_semaphores[user_id]
        if semaphore.locked():
            active_count = self.get_active_task_count(user_id)
            await update.message.reply_text(
                f"⏳ 현재 {active_count}개 요청 처리 중입니다. 잠시 후 다시 시도해주세요."
            )
            return

        await context.bot.send_chat_action(chat_id=chat_id, action="typing")

        # 백그라운드에서 Claude 호출
        task = asyncio.create_task(
            self._process_claude_request_with_semaphore(
                bot=context.bot,
                chat_id=chat_id,
                user_id=user_id,
                session_id=session_id,
                message=message,
                is_new_session=is_new_session,
            )
        )
        self._register_task(task, user_id, session_id)

    async def auth_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle /auth command."""
        if not self._is_authorized(update.effective_chat.id):
            await update.message.reply_text("⛔ 권한이 없습니다.")
            return

        user_id = str(update.effective_chat.id)

        if not context.args:
            await update.message.reply_text("사용법: /auth <비밀키>")
            return

        key = context.args[0]

        if self.auth.authenticate(user_id, key):
            await update.message.reply_text(f"✅ 인증 성공! {self.auth.timeout_minutes}분간 유효합니다.")
            logger.info(f"[{user_id}] 인증 성공")
        else:
            await update.message.reply_text("❌ 인증 실패. 키가 틀렸습니다.")
            logger.warning(f"[{user_id}] 인증 실패")

    async def status_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle /status command."""
        if not self._is_authorized(update.effective_chat.id):
            await update.message.reply_text("⛔ 권한이 없습니다.")
            return

        user_id = str(update.effective_chat.id)

        if self.auth.is_authenticated(user_id):
            remaining = self.auth.get_remaining_minutes(user_id)
            await update.message.reply_text(f"✅ 인증됨 ({remaining}분 남음)")
        else:
            await update.message.reply_text("🔒 인증 필요\n/auth <키>로 인증하세요.")

    async def new_session(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle /new command."""
        if not self._is_authorized(update.effective_chat.id):
            await update.message.reply_text("⛔ 권한이 없습니다.")
            return

        user_id = str(update.effective_chat.id)

        if not self._is_authenticated(user_id):
            await update.message.reply_text("🔒 먼저 인증이 필요합니다.\n/auth <키>")
            return

        await update.message.reply_text("🔄 새 Claude 세션 생성 중...")

        # 새 Claude 세션 생성
        session_id = await self.claude.create_session()
        if not session_id:
            await update.message.reply_text("❌ Claude 세션 생성 실패. 다시 시도해주세요.")
            return

        # 세션 저장
        self.sessions.create_session(user_id, session_id, "(새 세션)")

        await update.message.reply_text(
            f"✅ 새 세션 시작!\n• ID: <code>{session_id[:8]}</code>",
            parse_mode="HTML"
        )

    async def session_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle /session command - show current session info."""
        if not self._is_authorized(update.effective_chat.id):
            await update.message.reply_text("⛔ 권한이 없습니다.")
            return

        user_id = str(update.effective_chat.id)

        if not self._is_authenticated(user_id):
            await update.message.reply_text("🔒 먼저 인증이 필요합니다.\n/auth <키>")
            return

        session_id = self.sessions.get_current_session_id(user_id)
        if not session_id:
            await update.message.reply_text(
                "📭 활성 세션이 없습니다.\n\n"
                "• 메시지를 보내면 새 세션 시작\n"
                "• /session_list - 저장된 세션 목록",
                parse_mode="HTML"
            )
            return

        history = self.sessions.get_session_history(user_id, session_id)
        count = len(history)

        # Recent 10 messages
        recent = history[-10:]
        history_lines = []
        start_idx = len(history) - len(recent) + 1
        for i, q in enumerate(recent, start=start_idx):
            short_q = truncate_message(q, 40)
            history_lines.append(f"{i}. {short_q}")

        history_text = "\n".join(history_lines) if history_lines else "(없음)"

        await update.message.reply_text(
            f"📊 <b>현재 세션</b>\n\n"
            f"• ID: <code>{session_id[:8]}</code>\n"
            f"• 질문: {count}개\n\n"
            f"<b>대화 내용</b> (최근 10개)\n{history_text}",
            parse_mode="HTML"
        )

    async def session_list_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle /session_list command."""
        if not self._is_authorized(update.effective_chat.id):
            await update.message.reply_text("⛔ 권한이 없습니다.")
            return

        user_id = str(update.effective_chat.id)

        if not self._is_authenticated(user_id):
            await update.message.reply_text("🔒 먼저 인증이 필요합니다.\n/auth <키>")
            return

        sessions = self.sessions.list_sessions(user_id)
        if not sessions:
            await update.message.reply_text("📭 저장된 세션이 없습니다.")
            return

        # Get histories for quick list
        histories = {
            s["full_session_id"]: self.sessions.get_session_history(user_id, s["full_session_id"])
            for s in sessions
        }

        # Send quick list
        quick_list = format_session_quick_list(sessions, histories)

        if not self.session_list_ai_summary:
            # AI 요약 비활성화 - 목록만 전송
            await update.message.reply_text(quick_list, parse_mode="HTML")
            return

        # AI 요약 활성화
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
        analysis_lines = []
        for s in sessions:
            history = histories.get(s["full_session_id"], [])
            if history:
                summary = await self.claude.summarize(history)
            else:
                summary = "(내용 없음)"

            analysis_lines.append(f"<b>/s_{s['session_id']}</b>\n{summary}")

        await update.message.reply_text(
            "📊 <b>AI 분석 결과</b>\n\n" + "\n\n".join(analysis_lines),
            parse_mode="HTML"
        )

    async def switch_session_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle /s_<id> command for session switching."""
        if not self._is_authorized(update.effective_chat.id):
            await update.message.reply_text("⛔ 권한이 없습니다.")
            return

        user_id = str(update.effective_chat.id)

        if not self._is_authenticated(user_id):
            await update.message.reply_text("🔒 먼저 인증이 필요합니다.\n/auth <키>")
            return

        text = update.message.text
        if not text.startswith("/s_"):
            return

        target = text[3:]  # Extract session prefix

        target_info = self.sessions.get_session_by_prefix(user_id, target)
        if not target_info:
            await update.message.reply_text(f"❌ 세션 '{target}'을 찾을 수 없습니다.")
            return

        if self.sessions.switch_session(user_id, target):
            await update.message.reply_text(
                f"✅ 세션 전환 완료!\n\n"
                f"• ID: <code>{target_info['session_id']}</code>\n"
                f"• 질문: {target_info['history_count']}개",
                parse_mode="HTML"
            )
        else:
            await update.message.reply_text("❌ 세션 전환 실패")

    async def history_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle /h_<id> command for viewing session history."""
        if not self._is_authorized(update.effective_chat.id):
            await update.message.reply_text("⛔ 권한이 없습니다.")
            return

        user_id = str(update.effective_chat.id)

        if not self._is_authenticated(user_id):
            await update.message.reply_text("🔒 먼저 인증이 필요합니다.\n/auth <키>")
            return

        text = update.message.text
        if not text.startswith("/h_"):
            return

        target = text[3:]  # Extract session prefix

        target_info = self.sessions.get_session_by_prefix(user_id, target)
        if not target_info:
            await update.message.reply_text(f"❌ 세션 '{target}'을 찾을 수 없습니다.")
            return

        # 히스토리 조회
        history = self.sessions.get_session_history(user_id, target_info["full_session_id"])
        if not history:
            await update.message.reply_text("📭 히스토리가 없습니다.")
            return

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

    async def handle_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle regular text messages.

        Fire-and-Forget 패턴:
        1. 인증/권한 체크
        2. 세션 결정 (Lock으로 보호)
        3. 백그라운드 태스크로 Claude 호출 + 응답 전송
        4. 핸들러는 즉시 리턴
        """
        if not self._is_authorized(update.effective_chat.id):
            await update.message.reply_text("⛔ 권한이 없습니다.")
            return

        chat_id = update.effective_chat.id
        user_id = str(chat_id)
        message = update.message.text

        # 메시지 길이 제한 (DoS 방지)
        if len(message) > self.MAX_MESSAGE_LENGTH:
            original_len = len(message)
            message = message[:self.MAX_MESSAGE_LENGTH]
            logger.warning(f"[{user_id}] 메시지 길이 제한 적용: {original_len} -> {self.MAX_MESSAGE_LENGTH}")

        # 플러그인 처리 시도 (인증 전에 처리 - 플러그인은 인증 불필요)
        if self.plugins:
            try:
                result = await self.plugins.process_message(message, chat_id)
                if result and result.handled:
                    if result.response:
                        try:
                            await update.message.reply_text(result.response, parse_mode="HTML")
                        except Exception:
                            await update.message.reply_text(result.response)
                    return
            except Exception as e:
                logger.error(f"[{user_id}] 플러그인 처리 오류: {e}")
                # 플러그인 오류 시 Claude로 fallback

        if not self._is_authenticated(user_id):
            await update.message.reply_text(
                "🔒 인증이 필요합니다.\n"
                f"/auth <키>로 인증하세요. ({self.auth.timeout_minutes}분간 유효)\n"
                "/help 도움말"
            )
            return

        # 유저별 Lock으로 세션 결정 (race condition 방지)
        async with self._user_locks[user_id]:
            session_id = self.sessions.get_current_session_id(user_id)

            if not session_id:
                # 새 Claude 세션 생성
                logger.info(f"[{user_id}] Creating new Claude session...")
                session_id = await self.claude.create_session()

                if not session_id:
                    await update.message.reply_text("❌ Claude 세션 생성 실패. 다시 시도해주세요.")
                    return

                # 세션 저장 (첫 메시지 포함)
                self.sessions.create_session(user_id, session_id, message)
                is_new_session = True
            else:
                is_new_session = False

        # 로깅 (session_id 확정 후)
        logger.info(f"[{user_id}] 메시지 접수: {message[:50]}... (session: {session_id[:8]})")

        # Watchdog 지연 시작
        self._ensure_watchdog()

        # 동시 요청 제한 체크
        semaphore = self._user_semaphores[user_id]
        if semaphore.locked():
            active_count = self.get_active_task_count(user_id)
            await update.message.reply_text(
                f"⏳ 현재 {active_count}개 요청 처리 중입니다. 잠시 후 다시 시도해주세요."
            )
            return

        # Show typing indicator
        await context.bot.send_chat_action(chat_id=chat_id, action="typing")

        # Fire-and-Forget: 백그라운드에서 Claude 호출 + 응답 전송
        task = asyncio.create_task(
            self._process_claude_request_with_semaphore(
                bot=context.bot,
                chat_id=chat_id,
                user_id=user_id,
                session_id=session_id,
                message=message,
                is_new_session=is_new_session,
            )
        )
        # 태스크 추적 등록
        self._register_task(task, user_id, session_id)
        # 핸들러는 즉시 리턴 (Claude 응답을 기다리지 않음)

    async def _process_claude_request_with_semaphore(
        self,
        bot,
        chat_id: int,
        user_id: str,
        session_id: str,
        message: str,
        is_new_session: bool,
    ) -> None:
        """Semaphore로 동시 요청 제한 후 Claude 호출."""
        async with self._user_semaphores[user_id]:
            await self._process_claude_request(
                bot=bot,
                chat_id=chat_id,
                user_id=user_id,
                session_id=session_id,
                message=message,
                is_new_session=is_new_session,
            )

    async def _process_claude_request(
        self,
        bot,
        chat_id: int,
        user_id: str,
        session_id: str,
        message: str,
        is_new_session: bool,
    ) -> None:
        """백그라운드에서 Claude 호출 후 응답 전송.

        Args:
            bot: Telegram Bot instance (응답 전송용)
            chat_id: 응답을 보낼 채팅 ID
            user_id: 사용자 ID (로깅/세션용)
            session_id: Claude 세션 ID
            message: 사용자 메시지
            is_new_session: 새 세션 여부
        """
        try:
            logger.info(f"[{user_id}] Claude 호출 시작 (session: {session_id[:8]})")

            # Claude 호출
            response, error, _ = await self.claude.chat(message, session_id)

            logger.info(f"[{user_id}] Claude 응답 완료 (session: {session_id[:8]})")

            # 기존 세션이면 메시지 추가 (명시적 session_id 사용)
            if not is_new_session:
                self.sessions.add_message(user_id, session_id, message)

            # 에러 처리
            if error == "TIMEOUT":
                response = "⏱️ 응답 시간 초과. 다시 시도해주세요."
            elif error and error != "SESSION_NOT_FOUND":
                response = f"❌ 오류 발생: {error}"

            # 세션 정보 prefix 추가
            session_info = self.sessions.get_session_info(user_id, session_id)
            history_count = self.sessions.get_history_count(user_id, session_id)
            prefix = f"<b>[{session_info}|#{history_count}]</b>\n\n"

            # 세션 커맨드 suffix 추가
            suffix = (
                f"\n\n"
                f"/s_{session_info} 세션이동\n"
                f"/h_{session_info} 히스토리"
            )

            full_response = prefix + response + suffix

            # 응답 전송 (chat_id로 직접 전송)
            await self._send_message_to_chat(bot, chat_id, full_response)

        except Exception as e:
            logger.exception(f"[{user_id}] Claude 처리 실패: {e}")
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
        if len(text) <= max_length:
            try:
                await bot.send_message(chat_id=chat_id, text=text, parse_mode="HTML")
            except Exception:
                await bot.send_message(chat_id=chat_id, text=text)
            return

        # Split into chunks
        chunks = [text[i:i + max_length] for i in range(0, len(text), max_length)]
        for chunk in chunks:
            try:
                await bot.send_message(chat_id=chat_id, text=chunk, parse_mode="HTML")
            except Exception:
                await bot.send_message(chat_id=chat_id, text=chunk)

    async def _send_long_message(self, update: Update, text: str, max_length: int = 4000) -> None:
        """Send message, splitting if too long. (레거시 - update.reply_text 사용)"""
        if len(text) <= max_length:
            try:
                await update.message.reply_text(text, parse_mode="HTML")
            except Exception:
                await update.message.reply_text(text)
            return

        # Split into chunks
        chunks = [text[i:i + max_length] for i in range(0, len(text), max_length)]
        for chunk in chunks:
            try:
                await update.message.reply_text(chunk, parse_mode="HTML")
            except Exception:
                await update.message.reply_text(chunk)

    async def error_handler(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle errors."""
        # 내부 로그에는 상세 오류 기록
        logger.error(f"Error: {context.error}", exc_info=context.error)

        if update and update.effective_chat:
            # 사용자에게는 일반적인 오류 메시지만 표시 (보안)
            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text="❌ 오류가 발생했습니다. 잠시 후 다시 시도해주세요."
            )
