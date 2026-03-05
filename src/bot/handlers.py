"""Telegram bot command handlers."""

import asyncio
import html
import subprocess
import time
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Optional

from telegram import Update
from telegram.ext import ContextTypes

from src.logging_config import logger, set_trace_id, set_user_id, set_session_id, clear_context
from .constants import (
    MAX_MESSAGE_LENGTH,
    WATCHDOG_INTERVAL_SECONDS,
    TASK_TIMEOUT_SECONDS,
    LONG_TASK_THRESHOLD_SECONDS,
    get_model_emoji,
)
from .formatters import format_session_quick_list, truncate_message
from .middleware import authorized_only, authenticated_only
from .session_queue import session_queue_manager, QueuedMessage

__all__ = ["BotHandlers"]

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
    message: str = ""  # 요청 메시지 (미리보기용)
    started_at: float = field(default_factory=time.time)
    task: Optional[asyncio.Task] = None


@dataclass
class PendingMessage:
    """세션 락 충돌 시 대기 중인 메시지."""
    user_id: str
    message: str
    created_at: float = field(default_factory=time.time)
    expires_at: float = field(default_factory=lambda: time.time() + 300)  # 5분 후 만료


class BotHandlers:
    """Container for all bot command handlers."""

    # 유저별 Lock: 세션 생성 시 race condition 방지
    _user_locks: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)

    # 유저별 Semaphore: 동시 요청 제한 (전체 부하 제한)
    _user_semaphores: dict[str, asyncio.Semaphore] = defaultdict(
        lambda: asyncio.Semaphore(3)
    )

    # 태스크 추적 (watchdog용 - 세션 락은 session_queue_manager가 관리)
    _active_tasks: dict[int, TaskInfo] = {}  # task_id -> TaskInfo
    _watchdog_task: Optional[asyncio.Task] = None

    # 세션 생성 중인 유저 추적 (메시지 블로킹용)
    _creating_sessions: set[str] = set()  # user_id set

    # NOTE: _session_locks, _pending_messages 제거됨
    # 세션 락 및 대기열은 session_queue_manager가 단일 진실 소스로 관리

    # 스케줄 입력 대기 상태 (ForceReply용)
    _pending_schedule_input: dict[str, dict] = {}  # user_id -> {type, hour, minute, ...}

    # 예약 스케줄러 (경로 기반)
    _schedule_manager = None

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

    # ==================== 스케줄 매니저 ====================

    @classmethod
    def set_schedule_manager(cls, manager) -> None:
        """스케줄 매니저 설정."""
        cls._schedule_manager = manager
        logger.debug("BotHandlers에 ScheduleManager 연결됨")

    # ==================== 유틸리티 메서드 ====================

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
                await asyncio.sleep(WATCHDOG_INTERVAL_SECONDS)
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
            if elapsed > TASK_TIMEOUT_SECONDS:
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

    def _register_task(self, task: asyncio.Task, user_id: str, session_id: str, trace_id: str, message: str = "") -> int:
        """태스크를 추적 목록에 등록."""
        task_id = id(task)
        self._active_tasks[task_id] = TaskInfo(
            user_id=user_id,
            session_id=session_id,
            trace_id=trace_id,
            message=message[:100],  # 최대 100자
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

    # ==================== 정보 명령어 ====================

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
            "/np 경로 [모델] [이름] - 프로젝트 세션\n"
            "/new_haiku_speedy - 🚀 Speedy\n"
            "/new_opus_smarty - 🧠 Smarty\n"
            "/model - 현재 세션 모델 변경\n"
            "/rename_MyName - 세션 이름 변경\n"
            "/session - 현재 세션 정보\n"
            "/sl - 세션 목록\n"
            "/delete_&lt;id&gt; - 세션 삭제\n\n"
            "📋 매니저\n"
            "/m - 매니저 모드 (세션 관리)\n"
            "/m 질문 - 원샷 질문\n"
            f"{plugin_section}\n"
            "⏰ 스케줄\n"
            "/scheduler - 세션 스케줄 관리\n"
            "/jobs - 등록된 스케줄 작업\n\n"
            "ℹ️ 기타\n"
            "/lock - 처리 중인 작업/대기열\n"
            "/chatid - 내 채팅 ID 확인\n"
            "/help - 이 도움말",
            parse_mode="HTML"
        )
        logger.trace("/help 완료")
        clear_context()

    async def lock_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle /lock command - show active tasks with buttons."""
        from telegram import InlineKeyboardButton, InlineKeyboardMarkup

        chat_id = update.effective_chat.id
        self._setup_request_context(chat_id)
        user_id = str(chat_id)
        logger.info("/lock 명령 수신")

        text, keyboard = self._build_lock_status(user_id)

        await update.message.reply_text(
            text,
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="HTML"
        )
        logger.trace("/lock 완료")
        clear_context()

    async def jobs_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle /jobs command - show scheduled jobs."""
        from telegram import InlineKeyboardButton, InlineKeyboardMarkup
        from src.scheduler_manager import scheduler_manager

        chat_id = update.effective_chat.id
        self._setup_request_context(chat_id)
        logger.info("/jobs 명령 수신")

        text = scheduler_manager.get_status_text()

        keyboard = [[
            InlineKeyboardButton("🔄 새로고침", callback_data="jobs:refresh"),
        ]]

        await update.message.reply_text(
            text,
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="HTML"
        )
        logger.trace("/jobs 완료")
        clear_context()

    async def scheduler_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle /scheduler command - manage schedules."""
        from telegram import InlineKeyboardButton, InlineKeyboardMarkup

        chat_id = update.effective_chat.id
        self._setup_request_context(chat_id)
        user_id = str(chat_id)
        logger.info("/scheduler 명령 수신")

        if not self._schedule_manager:
            await update.message.reply_text("❌ 스케줄 기능이 초기화되지 않았습니다.")
            clear_context()
            return

        text = self._schedule_manager.get_status_text(user_id)
        keyboard = self._build_scheduler_keyboard(user_id)

        await update.message.reply_text(
            text,
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="HTML"
        )
        logger.trace("/scheduler 완료")
        clear_context()

    def _build_scheduler_keyboard(self, user_id: str) -> list:
        """스케줄러 UI 키보드 생성."""
        from telegram import InlineKeyboardButton

        buttons = []

        # 등록된 스케줄 목록
        if self._schedule_manager:
            schedules = self._schedule_manager.list_by_user(user_id)
            for s in sorted(schedules, key=lambda x: (x.hour, x.minute)):
                status = "✅" if s.enabled else "⏸"
                type_icon = "📁" if s.type == "project" else "💬"
                buttons.append([
                    InlineKeyboardButton(
                        f"{status} {s.time_str} {type_icon} {s.name[:10]}",
                        callback_data=f"sched:toggle:{s.id}"
                    ),
                    InlineKeyboardButton("🗑", callback_data=f"sched:delete:{s.id}"),
                ])

        # 추가/새로고침 버튼
        buttons.append([
            InlineKeyboardButton("💬 Claude", callback_data="sched:add:claude"),
            InlineKeyboardButton("📁 프로젝트", callback_data="sched:add:project"),
        ])
        buttons.append([
            InlineKeyboardButton("🔄 새로고침", callback_data="sched:refresh"),
        ])

        return buttons

    def _get_allowed_project_paths(self) -> list[str]:
        """허용된 프로젝트 경로 목록 반환."""
        import os
        from pathlib import Path

        # 환경변수에서 허용 경로 가져오기
        allowed = os.getenv("ALLOWED_PROJECT_PATHS", "")
        if not allowed:
            # 기본값: AiSandbox, Projects
            home = Path.home()
            return [
                str(home / "AiSandbox"),
                str(home / "Projects"),
            ]

        paths = []
        for pattern in allowed.split(","):
            pattern = pattern.strip()
            if pattern.endswith("/*"):
                # 와일드카드: 해당 디렉토리의 하위 디렉토리들
                parent = Path(pattern[:-2]).expanduser()
                if parent.exists():
                    paths.extend([str(p) for p in parent.iterdir() if p.is_dir() and not p.name.startswith(".")])
            else:
                paths.append(pattern)

        return sorted(paths)

    async def _handle_scheduler_callback(self, query, chat_id: int, callback_data: str) -> None:
        """스케줄 콜백 처리."""
        from telegram import InlineKeyboardButton, InlineKeyboardMarkup, ForceReply
        from src.schedule import AVAILABLE_HOURS

        user_id = str(chat_id)
        action = callback_data[6:]  # "sched:" 제거

        if not self._schedule_manager:
            await query.answer("❌ 스케줄 기능 비활성화")
            return

        # 새로고침
        if action == "refresh":
            text = self._schedule_manager.get_status_text(user_id)
            keyboard = self._build_scheduler_keyboard(user_id)
            await query.edit_message_text(
                text,
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode="HTML"
            )
            await query.answer("새로고침 완료")
            return

        # 토글 (활성화/비활성화)
        if action.startswith("toggle:"):
            schedule_id = action[7:]
            new_state = self._schedule_manager.toggle(schedule_id)
            if new_state is not None:
                status = "활성화" if new_state else "비활성화"
                await query.answer(f"✅ {status}됨")
                text = self._schedule_manager.get_status_text(user_id)
                keyboard = self._build_scheduler_keyboard(user_id)
                await query.edit_message_text(
                    text,
                    reply_markup=InlineKeyboardMarkup(keyboard),
                    parse_mode="HTML"
                )
            else:
                await query.answer("❌ 스케줄을 찾을 수 없음")
            return

        # 삭제
        if action.startswith("delete:"):
            schedule_id = action[7:]
            if self._schedule_manager.remove(schedule_id):
                await query.answer("✅ 삭제됨")
                text = self._schedule_manager.get_status_text(user_id)
                keyboard = self._build_scheduler_keyboard(user_id)
                await query.edit_message_text(
                    text,
                    reply_markup=InlineKeyboardMarkup(keyboard),
                    parse_mode="HTML"
                )
            else:
                await query.answer("❌ 삭제 실패")
            return

        # 추가 - Claude 타입 (시간 선택)
        if action == "add:claude":
            buttons = []
            row = []
            for hour in AVAILABLE_HOURS:
                row.append(InlineKeyboardButton(
                    f"{hour:02d}:00",
                    callback_data=f"sched:time:claude:_:{hour}"
                ))
                if len(row) == 4:
                    buttons.append(row)
                    row = []
            if row:
                buttons.append(row)
            buttons.append([
                InlineKeyboardButton("↩️ 취소", callback_data="sched:refresh")
            ])

            await query.edit_message_text(
                "📅 <b>Claude 스케줄 추가</b>\n\n"
                "💬 일반 Claude 대화 (새 세션)\n\n"
                "실행 시간을 선택하세요 (매일 반복):",
                reply_markup=InlineKeyboardMarkup(buttons),
                parse_mode="HTML"
            )
            await query.answer()
            return

        # 추가 - Project 타입 (경로 선택)
        if action == "add:project":
            paths = self._get_allowed_project_paths()
            if not paths:
                await query.answer("❌ 허용된 프로젝트 경로가 없습니다.")
                return

            buttons = []
            for i, path in enumerate(paths[:10]):  # 최대 10개
                name = path.split("/")[-1]
                buttons.append([
                    InlineKeyboardButton(
                        f"📁 {name}",
                        callback_data=f"sched:path:{i}"
                    )
                ])

            # 경로 목록 임시 저장
            self._pending_schedule_input[user_id] = {"paths": paths}

            buttons.append([
                InlineKeyboardButton("↩️ 취소", callback_data="sched:refresh")
            ])

            await query.edit_message_text(
                "📅 <b>프로젝트 스케줄 추가</b>\n\n"
                "📁 프로젝트 경로를 선택하세요:",
                reply_markup=InlineKeyboardMarkup(buttons),
                parse_mode="HTML"
            )
            await query.answer()
            return

        # 경로 선택 완료 - 시간 선택
        if action.startswith("path:"):
            path_idx = int(action[5:])
            pending = self._pending_schedule_input.get(user_id, {})
            paths = pending.get("paths", [])

            if path_idx >= len(paths):
                await query.answer("❌ 잘못된 경로")
                return

            project_path = paths[path_idx]
            project_name = project_path.split("/")[-1]

            buttons = []
            row = []
            for hour in AVAILABLE_HOURS:
                row.append(InlineKeyboardButton(
                    f"{hour:02d}:00",
                    callback_data=f"sched:time:project:{path_idx}:{hour}"
                ))
                if len(row) == 4:
                    buttons.append(row)
                    row = []
            if row:
                buttons.append(row)
            buttons.append([
                InlineKeyboardButton("↩️ 취소", callback_data="sched:refresh")
            ])

            await query.edit_message_text(
                f"📅 <b>프로젝트 스케줄 추가</b>\n\n"
                f"📁 프로젝트: <b>{project_name}</b>\n"
                f"<code>{project_path}</code>\n\n"
                f"실행 시간을 선택하세요 (매일 반복):",
                reply_markup=InlineKeyboardMarkup(buttons),
                parse_mode="HTML"
            )
            await query.answer()
            return

        # 시간 선택 완료 - 모델 선택
        if action.startswith("time:"):
            parts = action[5:].split(":")
            if len(parts) != 3:
                await query.answer("❌ 잘못된 요청")
                return

            schedule_type, path_idx, hour = parts[0], parts[1], int(parts[2])

            # 대기 상태 업데이트
            pending = self._pending_schedule_input.get(user_id, {})
            pending["type"] = schedule_type
            pending["hour"] = hour
            pending["minute"] = 0

            if schedule_type == "project" and path_idx != "_":
                paths = pending.get("paths", [])
                idx = int(path_idx)
                if idx < len(paths):
                    pending["project_path"] = paths[idx]
                    pending["name"] = paths[idx].split("/")[-1]

            self._pending_schedule_input[user_id] = pending

            # 모델 선택 버튼
            buttons = [
                [
                    InlineKeyboardButton("🚀 Opus", callback_data="sched:model:opus"),
                    InlineKeyboardButton("⚡ Sonnet", callback_data="sched:model:sonnet"),
                    InlineKeyboardButton("💨 Haiku", callback_data="sched:model:haiku"),
                ],
                [InlineKeyboardButton("↩️ 취소", callback_data="sched:refresh")],
            ]

            type_label = "프로젝트" if schedule_type == "project" else "Claude"
            path_info = f"\n📁 경로: <code>{pending.get('project_path', '')}</code>" if schedule_type == "project" else ""

            await query.edit_message_text(
                f"📅 <b>{type_label} 스케줄 추가</b>\n\n"
                f"⏰ 시간: <b>{hour:02d}:00</b>{path_info}\n\n"
                f"모델을 선택하세요:",
                reply_markup=InlineKeyboardMarkup(buttons),
                parse_mode="HTML"
            )
            await query.answer()
            return

        # 모델 선택 완료 - 메시지 입력 (ForceReply)
        if action.startswith("model:"):
            model = action[6:]
            pending = self._pending_schedule_input.get(user_id, {})
            pending["model"] = model
            self._pending_schedule_input[user_id] = pending

            schedule_type = pending.get("type", "claude")
            hour = pending.get("hour", 9)
            type_label = "프로젝트" if schedule_type == "project" else "Claude"
            path_info = f"\n📁 경로: <code>{pending.get('project_path', '')}</code>" if schedule_type == "project" else ""

            await query.edit_message_text(
                f"📅 <b>{type_label} 스케줄 추가</b>\n\n"
                f"⏰ 시간: <b>{hour:02d}:00</b>\n"
                f"🤖 모델: <b>{model}</b>{path_info}\n\n"
                f"아래에 예약 메시지를 입력하세요:",
                parse_mode="HTML"
            )

            # ForceReply로 메시지 입력 요청
            await query.message.reply_text(
                "💬 예약 메시지를 입력하세요:",
                reply_markup=ForceReply(selective=True, input_field_placeholder="예: 오늘 할 일 정리해줘")
            )
            await query.answer()
            return

        await query.answer("❌ 알 수 없는 동작")

    def _build_lock_status(self, user_id: str) -> tuple[str, list]:
        """Lock 상태 텍스트와 버튼 생성 (대기열 포함)."""
        from telegram import InlineKeyboardButton
        import time

        # 현재 사용자의 활성 태스크 조회
        user_tasks = [
            info for info in self._active_tasks.values()
            if info.user_id == user_id
        ]

        # 세마포어 상태
        if user_id in self._user_semaphores:
            semaphore = self._user_semaphores[user_id]
            available = semaphore._value
        else:
            available = 3
        total = 3

        lines = []

        # 활성 작업 섹션
        if not user_tasks:
            lines.append(f"✅ <b>처리 중인 작업 없음</b>")
        else:
            lines.append(f"🔒 <b>처리 중</b> ({len(user_tasks)}/{total})")

            for i, info in enumerate(user_tasks, 1):
                elapsed = time.time() - info.started_at
                elapsed_str = f"{int(elapsed // 60)}분 {int(elapsed % 60)}초" if elapsed >= 60 else f"{int(elapsed)}초"

                # 세션 이름 가져오기
                session_name = self.sessions.get_session_name(user_id, info.session_id) or info.session_id[:8]

                # 메시지 미리보기
                msg_preview = info.message[:40] + "..." if len(info.message) > 40 else info.message
                msg_preview = msg_preview.replace("<", "&lt;").replace(">", "&gt;")

                lines.append(
                    f"\n<b>{i}.</b> <code>{session_name}</code>\n"
                    f"   ⏱ {elapsed_str} 경과\n"
                    f"   💬 {msg_preview or '(메시지 없음)'}"
                )

        # 대기열 섹션 추가
        all_sessions = self.sessions.list_sessions(user_id)
        total_waiting = 0
        waiting_details = []

        for s in all_sessions:
            sid = s["full_session_id"]
            state = session_queue_manager.get_status(sid)
            if state and state.waiting_queue:
                queue_size = len(state.waiting_queue)
                total_waiting += queue_size
                session_name = s.get("name") or s["session_id"]
                for i, queued in enumerate(state.waiting_queue[:3], 1):  # 세션당 최대 3개
                    msg_preview = queued.message[:30] + "..." if len(queued.message) > 30 else queued.message
                    msg_preview = msg_preview.replace("<", "&lt;").replace(">", "&gt;")
                    waiting_details.append(f"• <code>{session_name}</code>: {msg_preview}")
                if queue_size > 3:
                    waiting_details.append(f"  ... +{queue_size - 3}개 더")

        if total_waiting > 0:
            lines.append(f"\n\n⏳ <b>대기열</b> ({total_waiting}개)")
            lines.extend([f"\n{d}" for d in waiting_details[:8]])  # 최대 8개
            if len(waiting_details) > 8:
                lines.append(f"\n  ... 외 {len(waiting_details) - 8}개")

        lines.append(f"\n\n슬롯: {available}/{total} 사용 가능")
        text = "".join(lines) if lines else "상태 정보 없음"

        keyboard = [[
            InlineKeyboardButton("🔄 새로고침", callback_data="lock:refresh"),
        ]]

        return text, keyboard

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

    # ==================== 인증 명령어 ====================

    @authorized_only
    async def auth_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle /auth command."""
        chat_id = update.effective_chat.id
        self._setup_request_context(chat_id)
        logger.info("/auth 명령 수신")

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

    @authorized_only
    async def status_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle /status command."""
        chat_id = update.effective_chat.id
        self._setup_request_context(chat_id)
        logger.info("/status 명령 수신")

        user_id = str(chat_id)

        if self.auth.is_authenticated(user_id):
            remaining = self.auth.get_remaining_minutes(user_id)
            logger.trace(f"인증됨 - remaining={remaining}분")
            await update.message.reply_text(f"✅ 인증됨 ({remaining}분 남음)")
        else:
            logger.trace("인증 필요")
            await update.message.reply_text("🔒 인증 필요\n/auth <키>로 인증하세요.")

        clear_context()

    # ==================== 세션 명령어 ====================

    @authorized_only
    @authenticated_only
    async def new_session(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle /new command.

        Usage:
            /new              - 모델 선택 버튼 표시
            /new opus         - Opus 모델
            /new haiku 이름   - Haiku 모델 + 세션 이름
        """
        from src.claude.session import SUPPORTED_MODELS, DEFAULT_MODEL
        from telegram import InlineKeyboardButton, InlineKeyboardMarkup

        chat_id = update.effective_chat.id
        user_id = str(chat_id)
        self._setup_request_context(chat_id)

        # 인자가 없으면 모델 선택 버튼 표시
        if not context.args:
            keyboard = [
                [
                    InlineKeyboardButton("🧠 Opus", callback_data="sess:new:opus"),
                    InlineKeyboardButton("⚡ Sonnet", callback_data="sess:new:sonnet"),
                    InlineKeyboardButton("🚀 Haiku", callback_data="sess:new:haiku"),
                ],
                [
                    InlineKeyboardButton("📋 세션 목록", callback_data="sess:list"),
                ]
            ]
            await update.message.reply_text(
                "🆕 <b>새 세션 생성</b>\n\n사용할 모델을 선택하세요:",
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode="HTML"
            )
            clear_context()
            return

        # 모델과 이름 파싱: /new [model] [name...]
        model = DEFAULT_MODEL
        session_name = ""

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

        model_emoji = get_model_emoji(model)
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

    @authorized_only
    @authenticated_only
    async def new_project_session(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle /new_project command - create project-bound session.

        Usage:
            /new_project /path/to/project           - 기본 모델 (sonnet)
            /new_project /path/to/project opus      - Opus 모델
            /new_project /path/to/project haiku 이름 - Haiku 모델 + 세션 이름
        """
        from src.config import get_settings

        chat_id = update.effective_chat.id
        user_id = str(chat_id)
        args = context.args or []

        if not args:
            await update.message.reply_text(
                "📁 <b>프로젝트 세션 사용법</b>\n\n"
                "<code>/new_project 경로 [모델] [이름]</code>\n\n"
                "예시:\n"
                "• <code>/new_project ~/Projects/my-app</code>\n"
                "• <code>/new_project ~/AiSandbox/bot opus</code>\n"
                "• <code>/new_project ~/work/api haiku API봇</code>",
                parse_mode="HTML"
            )
            return

        # 첫 번째 인자: 경로
        project_path = args[0]

        # 경로 검증
        settings = get_settings()
        is_valid, error_msg = settings.validate_project_path(project_path)
        if not is_valid:
            await update.message.reply_text(f"❌ {error_msg}", parse_mode="HTML")
            return

        # 모델과 이름 파싱
        model = None
        session_name = ""
        if len(args) > 1:
            potential_model = args[1].lower()
            from src.claude.session import SUPPORTED_MODELS
            if potential_model in SUPPORTED_MODELS:
                model = potential_model
                if len(args) > 2:
                    session_name = " ".join(args[2:])
            else:
                session_name = " ".join(args[1:])

        # 경로에서 프로젝트 이름 추출
        from pathlib import Path
        expanded_path = str(Path(project_path).expanduser().resolve())
        project_name = Path(expanded_path).name
        display_name = session_name or f"📁{project_name}"

        logger.info(f"/new_project - path={expanded_path}, model={model}, name={display_name}")

        # Claude 세션 생성 (프로젝트 경로에서 생성해야 올바른 위치에 저장됨)
        session_id = await self.claude.create_session(project_path=expanded_path)
        if not session_id:
            await update.message.reply_text("❌ 세션 생성 실패", parse_mode="HTML")
            return

        # 세션 저장 (project_path 포함)
        self.sessions.create_session(
            user_id, session_id, f"(프로젝트: {project_name})",
            model=model, name=display_name, project_path=expanded_path
        )

        model_emoji = {"opus": "🧠", "sonnet": "⚡", "haiku": "🚀"}.get(model or "sonnet", "⚡")

        # CLAUDE.md 존재 여부 확인
        claude_md_exists = (Path(expanded_path) / "CLAUDE.md").exists()
        claude_dir_exists = (Path(expanded_path) / ".claude").exists()
        config_status = "✅ CLAUDE.md" if claude_md_exists else ("✅ .claude/" if claude_dir_exists else "⚠️ 설정 없음")

        await update.message.reply_text(
            f"📁 <b>프로젝트 세션 생성됨</b>\n\n"
            f"• 경로: <code>{expanded_path}</code>\n"
            f"• 모델: {model_emoji} {model or 'sonnet'}\n"
            f"• 이름: {display_name}\n"
            f"• 설정: {config_status}\n\n"
            f"이 세션에서는 프로젝트의 CLAUDE.md 규칙이 적용됩니다.",
            parse_mode="HTML"
        )

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

    @authorized_only
    @authenticated_only
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
            model_emoji = get_model_emoji(current_model)
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
            model_emoji = get_model_emoji(current_model)
            await update.message.reply_text(f"ℹ️ 이미 {model_emoji} {current_model} 모델입니다.")
            clear_context()
            return

        # 세션 데이터에서 모델 변경
        user_data = self.sessions._data.get(user_id)
        if user_data and session_id in user_data.get("sessions", {}):
            user_data["sessions"][session_id]["model"] = new_model
            self.sessions._save()
            logger.info(f"모델 변경: {current_model} -> {new_model}, session={session_id[:8]}")

            model_emoji = get_model_emoji(new_model)
            await update.message.reply_text(
                f"✅ 모델 변경 완료!\n\n"
                f"• 이전: {current_model}\n"
                f"• 현재: {model_emoji} {new_model}",
                parse_mode="HTML"
            )
        else:
            await update.message.reply_text("❌ 세션을 찾을 수 없습니다.")

        clear_context()

    @authorized_only
    @authenticated_only
    async def session_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle /session command - show current session info with buttons."""
        from telegram import InlineKeyboardButton, InlineKeyboardMarkup

        chat_id = update.effective_chat.id
        self._setup_request_context(chat_id)
        logger.info("/session 명령 수신")

        user_id = str(chat_id)

        logger.trace("현재 세션 조회 중")
        session_id = self.sessions.get_current_session_id(user_id)
        if not session_id:
            logger.trace("활성 세션 없음")
            keyboard = [
                [
                    InlineKeyboardButton("🧠 +Opus", callback_data="sess:new:opus"),
                    InlineKeyboardButton("⚡ +Sonnet", callback_data="sess:new:sonnet"),
                    InlineKeyboardButton("🚀 +Haiku", callback_data="sess:new:haiku"),
                ],
                [
                    InlineKeyboardButton("📋 세션 목록", callback_data="sess:list"),
                ]
            ]
            await update.message.reply_text(
                "📭 활성 세션이 없습니다.\n\n새 세션을 생성하세요:",
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode="HTML"
            )
            clear_context()
            return

        logger.trace(f"세션 히스토리 조회 - session={session_id[:8]}")
        history_entries = self.sessions.get_session_history_entries(user_id, session_id)
        count = len(history_entries)
        model = self.sessions.get_session_model(user_id, session_id)
        model_emoji = get_model_emoji(model)
        session_name = self.sessions.get_session_name(user_id, session_id)
        logger.trace(f"히스토리 수: {count}, 모델: {model}, 이름: {session_name or '(없음)'}")

        # Recent 10 messages with processor info
        recent = history_entries[-10:]
        history_lines = []
        start_idx = len(history_entries) - len(recent) + 1

        # processor 이모지 매핑
        processor_emoji = {
            "claude": "🤖",
            "command": "⌨️",
            "rejected": "❌",
        }

        for i, entry in enumerate(recent, start=start_idx):
            msg = entry.get("message", "") if isinstance(entry, dict) else str(entry)
            processor = entry.get("processor", "claude") if isinstance(entry, dict) else "claude"

            # plugin:memo 형태면 🔌 사용
            if processor.startswith("plugin:"):
                emoji = "🔌"
            else:
                emoji = processor_emoji.get(processor, "")

            short_q = truncate_message(msg, 35)
            history_lines.append(f"{i}. {emoji} {short_q}")

        history_text = "\n".join(history_lines) if history_lines else "(없음)"

        name_line = f"• 이름: {session_name}\n" if session_name else ""

        # 버튼 기반 UI
        keyboard = [
            [
                InlineKeyboardButton("🧠 Opus", callback_data=f"sess:model:opus:{session_id}"),
                InlineKeyboardButton("⚡ Sonnet", callback_data=f"sess:model:sonnet:{session_id}"),
                InlineKeyboardButton("🚀 Haiku", callback_data=f"sess:model:haiku:{session_id}"),
            ],
            [
                InlineKeyboardButton("📜 히스토리", callback_data=f"sess:history:{session_id}"),
                InlineKeyboardButton("🗑️ 삭제", callback_data=f"sess:delete:{session_id}"),
            ],
            [
                InlineKeyboardButton("📋 세션 목록", callback_data="sess:list"),
            ]
        ]

        await update.message.reply_text(
            f"📊 <b>현재 세션</b>\n\n"
            f"• ID: <code>{session_id[:8]}</code>\n"
            f"{name_line}"
            f"• 모델: {model_emoji} {model}\n"
            f"• 질문: {count}개\n\n"
            f"<b>대화 내용</b> (최근 10개)\n{history_text}",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="HTML"
        )
        logger.trace("/session 완료")
        clear_context()

    @authorized_only
    @authenticated_only
    async def session_list_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle /session_list command - 버튼 기반 세션 목록."""
        from telegram import InlineKeyboardButton, InlineKeyboardMarkup

        chat_id = update.effective_chat.id
        self._setup_request_context(chat_id)
        logger.info("/session_list 명령 수신")

        user_id = str(chat_id)

        logger.trace("세션 목록 조회 중")
        sessions = self.sessions.list_sessions(user_id)

        # 현재 세션 ID
        current_session_id = self.sessions.get_current_session_id(user_id)

        lines = ["📋 <b>세션 목록</b>\n"]
        buttons = []

        if not sessions:
            lines.append("세션이 없습니다.")
        else:
            for s in sessions[:10]:  # 최대 10개
                sid = s["full_session_id"]
                short_id = s["session_id"]
                name = s.get("name") or f"세션 {short_id}"
                model = s.get("model", "sonnet")
                model_emoji = {"opus": "🧠", "sonnet": "⚡", "haiku": "🚀"}.get(model, "⚡")

                is_current = "👉 " if sid == current_session_id else ""
                # 락 상태 확인 (session_queue_manager가 단일 진실 소스)
                is_locked = session_queue_manager.is_locked(sid)
                lock_indicator = " 🔒" if is_locked else ""
                lines.append(f"{is_current}{model_emoji} <b>{name}</b> (<code>{short_id}</code>){lock_indicator}")

                # 각 세션에 액션 버튼
                buttons.append([
                    InlineKeyboardButton(f"📂 {name[:10]}", callback_data=f"sess:switch:{sid}"),
                    InlineKeyboardButton("📜", callback_data=f"sess:history:{sid}"),
                    InlineKeyboardButton("🗑️", callback_data=f"sess:delete:{sid}"),
                ])

        # 새 세션 생성 버튼
        buttons.append([
            InlineKeyboardButton("🧠 +Opus", callback_data="sess:new:opus"),
            InlineKeyboardButton("⚡ +Sonnet", callback_data="sess:new:sonnet"),
            InlineKeyboardButton("🚀 +Haiku", callback_data="sess:new:haiku"),
        ])
        buttons.append([
            InlineKeyboardButton("🔄 새로고침", callback_data="sess:list"),
            InlineKeyboardButton("📊 작업현황", callback_data="lock:refresh"),
        ])

        await update.message.reply_text(
            "\n".join(lines),
            reply_markup=InlineKeyboardMarkup(buttons),
            parse_mode="HTML"
        )
        logger.trace("/session_list 완료")
        clear_context()

    @authorized_only
    @authenticated_only
    async def switch_session_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle /s_<id> command for session switching."""
        chat_id = update.effective_chat.id
        self._setup_request_context(chat_id)

        user_id = str(chat_id)

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

    @authorized_only
    @authenticated_only
    async def rename_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle /rename command - rename current session or specific session."""
        chat_id = update.effective_chat.id
        user_id = str(chat_id)
        self._setup_request_context(chat_id)
        logger.info("/rename 명령 수신")

        text = update.message.text

        # /r_세션ID_새이름 형태 지원 (특정 세션 이름 변경)
        if text.startswith("/r_") and "_" in text[3:]:
            parts = text[3:].split("_", 1)  # 첫 번째 _ 기준으로만 분리
            if len(parts) == 2:
                target_prefix = parts[0]
                new_name = parts[1]

                # 세션 찾기
                target_info = self.sessions.get_session_by_prefix(user_id, target_prefix)
                if not target_info:
                    logger.debug(f"세션 없음: {target_prefix}")
                    await update.message.reply_text(f"❌ 세션 <code>{target_prefix}</code>을(를) 찾을 수 없습니다.", parse_mode="HTML")
                    clear_context()
                    return

                session_id = target_info["full_session_id"]

                # 이름 길이 체크
                if len(new_name) > 50:
                    await update.message.reply_text("❌ 이름이 너무 깁니다. (최대 50자)")
                    clear_context()
                    return

                # 이름 변경
                if self.sessions.rename_session(user_id, session_id, new_name):
                    logger.info(f"세션 이름 변경: {session_id[:8]} -> {new_name}")
                    await update.message.reply_text(
                        f"✅ 세션 이름 변경 완료!\n\n"
                        f"• 세션: <code>{session_id[:8]}</code>\n"
                        f"• 이름: {new_name}",
                        parse_mode="HTML"
                    )
                else:
                    await update.message.reply_text("❌ 이름 변경 실패")

                clear_context()
                return
            else:
                # 형식 오류
                await update.message.reply_text(
                    "❌ 사용법: <code>/r_세션ID_새이름</code>\n"
                    "예: <code>/r_a1b2c3d4_주식봇</code>",
                    parse_mode="HTML"
                )
                clear_context()
                return

        # 현재 세션 이름 변경 (/rename 또는 /rename_새이름)
        session_id = self.sessions.get_current_session_id(user_id)
        if not session_id:
            logger.trace("활성 세션 없음")
            await update.message.reply_text("📭 활성 세션이 없습니다.")
            clear_context()
            return

        # /rename_새이름 형태 지원
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
                f"사용법: <code>/rename_새이름</code>\n"
                f"또는: <code>/r_세션ID_새이름</code>",
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

    @authorized_only
    @authenticated_only
    async def delete_session_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle /d_<id> command for deleting a session."""
        chat_id = update.effective_chat.id
        user_id = str(chat_id)
        self._setup_request_context(chat_id)

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

        # 현재 활성 세션은 삭제 불가
        current_session_id = self.sessions.get_current_session_id(user_id)
        if current_session_id == full_session_id:
            name_info = f" ({session_name})" if session_name else ""
            await update.message.reply_text(
                f"⚠️ 현재 사용 중인 세션은 삭제할 수 없습니다.\n\n"
                f"• ID: <code>{target_info['session_id']}</code>{name_info}\n\n"
                f"💡 다른 세션으로 전환하거나 /new로 새 세션을 만든 후 삭제하세요.",
                parse_mode="HTML"
            )
            clear_context()
            return

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

    @authorized_only
    @authenticated_only
    async def history_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle /h_<id> command for viewing session history."""
        chat_id = update.effective_chat.id
        self._setup_request_context(chat_id)

        user_id = str(chat_id)

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

    # ==================== 매니저 명령어 ====================

    @authorized_only
    @authorized_only
    async def back_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle /back command - return to previous session."""
        chat_id = update.effective_chat.id
        user_id = str(chat_id)
        self._setup_request_context(chat_id)
        logger.info("/back 명령 수신")

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

    # ==================== /ai 명령어 ====================

    @authorized_only
    @authenticated_only
    async def ai_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle /ai command - force Claude conversation (bypass plugins)."""
        chat_id = update.effective_chat.id
        trace_id = self._setup_request_context(chat_id)
        logger.info("/ai 명령 수신")

        user_id = str(chat_id)

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
        if len(message) > MAX_MESSAGE_LENGTH:
            logger.warning(f"메시지 길이 제한 적용: {len(message)} -> {MAX_MESSAGE_LENGTH}")
            message = message[:MAX_MESSAGE_LENGTH]

        # 세션 생성 중이면 메시지 블로킹
        if user_id in self._creating_sessions:
            logger.info(f"세션 생성 중 - /ai 블로킹: user={user_id}")
            await update.message.reply_text(
                "⏳ <b>세션 준비 중...</b>\n\n"
                "잠시 후 다시 보내주세요!",
                parse_mode="HTML"
            )
            clear_context()
            return

        # 세션 결정
        logger.trace("세션 결정 시작 - Lock 획득 대기")
        async with self._user_locks[user_id]:
            logger.trace("Lock 획득됨")
            session_id = self.sessions.get_current_session_id(user_id)
            logger.trace(f"현재 세션: {session_id[:8] if session_id else 'None'}")

            if not session_id:
                logger.info("새 Claude 세션 생성 중...")
                self._creating_sessions.add(user_id)
                try:
                    session_id = await self.claude.create_session()

                    if not session_id:
                        logger.error("Claude 세션 생성 실패")
                        await update.message.reply_text("❌ Claude 세션 생성 실패. 다시 시도해주세요.")
                        clear_context()
                        return

                    logger.trace(f"세션 저장 중 - session_id={session_id[:8]}")
                    self.sessions.create_session(user_id, session_id, message)
                    is_new_session = True
                finally:
                    self._creating_sessions.discard(user_id)
            else:
                is_new_session = False

        # 세션 모델 및 프로젝트 경로 가져오기
        model = self.sessions.get_session_model(user_id, session_id)
        project_path = self.sessions.get_session_project_path(user_id, session_id)

        # 세션 ID 컨텍스트 설정
        set_session_id(session_id)
        logger.info(f"세션 결정 완료 - model={model}, new={is_new_session}, project={project_path or '(없음)'}")

        self._ensure_watchdog()

        # 세션별 락 체크 (session_queue_manager가 단일 진실 소스)
        if session_queue_manager.is_locked(session_id):
            logger.warning(f"세션 락 충돌 - session={session_id[:8]}, 세션 선택 UI 표시")
            await self._show_session_selection_ui(
                update, user_id, message, session_id, model, is_new_session, project_path
            )
            clear_context()
            return

        # 동시 요청 제한 체크 (Semaphore._value가 0이면 모든 슬롯 사용 중)
        semaphore = self._user_semaphores[user_id]
        logger.trace(f"Semaphore 상태 - available={semaphore._value}")
        if semaphore._value == 0:
            active_count = self.get_active_task_count(user_id)
            logger.warning(f"동시 요청 제한 - 활성 태스크: {active_count}개")
            # 거절된 메시지 미리보기 (최대 50자)
            rejected_preview = message[:50] + "..." if len(message) > 50 else message
            await update.message.reply_text(
                f"⚠️ <b>메시지 처리 불가</b>\n\n"
                f"현재 {active_count}개 요청이 처리 중이에요.\n\n"
                f"❌ <b>거절된 메시지:</b>\n"
                f"<code>{rejected_preview}</code>\n\n"
                f"완료 후 다시 보내주세요!",
                parse_mode="HTML"
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
        self._register_task(task, user_id, session_id, trace_id, message)
        logger.trace("/ai 핸들러 종료 - 백그라운드 처리 중")
        # 컨텍스트는 백그라운드 태스크에서 정리

    # ==================== 메시지 처리 ====================

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
        if len(message) > MAX_MESSAGE_LENGTH:
            original_len = len(message)
            message = message[:MAX_MESSAGE_LENGTH]
            logger.warning(f"메시지 길이 제한 적용: {original_len} -> {MAX_MESSAGE_LENGTH}")

        # ForceReply 응답 처리
        if update.message.reply_to_message:
            reply_text = update.message.reply_to_message.text or ""
            import re

            # "slot:X" 패턴 확인 (Todo ForceReply)
            if "slot:" in reply_text:
                slot_match = re.search(r"slot:([mae])", reply_text)
                if slot_match:
                    slot_code = slot_match.group(1)
                    await self._handle_todo_force_reply(update, chat_id, message, slot_code)
                    clear_context()
                    return

            # "sess_name:model" 패턴 확인 (세션 생성 ForceReply)
            if "sess_name:" in reply_text:
                sess_match = re.search(r"sess_name:(\w+)", reply_text)
                if sess_match:
                    model = sess_match.group(1)
                    await self._handle_new_session_force_reply(update, chat_id, message, model)
                    clear_context()
                    return

            # 메모 추가 ForceReply
            if "메모 내용" in reply_text or "memo_add" in reply_text:
                await self._handle_memo_force_reply(update, chat_id, message)
                clear_context()
                return

            # 스케줄 메시지 입력 ForceReply
            if "예약 메시지" in reply_text and user_id in self._pending_schedule_input:
                await self._handle_schedule_force_reply(update, chat_id, message)
                clear_context()
                return

        # 플러그인 처리 시도 (인증 전에 처리 - 플러그인은 인증 불필요)
        if self.plugins:
            logger.debug(f"[PLUGIN] 처리 시도 - 로드된 플러그인: {len(self.plugins.plugins)}개")
            logger.debug(f"[PLUGIN] 메시지: {message[:100]}")
            try:
                result = await self.plugins.process_message(message, chat_id)
                logger.debug(f"[PLUGIN] result={result}, handled={result.handled if result else 'N/A'}")
                if result and result.handled:
                    plugin_name = result.plugin_name if hasattr(result, 'plugin_name') else "plugin"
                    logger.info(f"[PLUGIN] 처리 완료: {plugin_name}")
                    logger.debug(f"[PLUGIN] response length={len(result.response) if result.response else 0}")
                    # 플러그인 처리도 히스토리에 기록
                    session_id = self.sessions.get_current_session_id(user_id)
                    if session_id:
                        self.sessions.add_message(user_id, session_id, message, processor=f"plugin:{plugin_name}")
                    if result.response:
                        logger.debug(f"[PLUGIN] 응답 전송 시작")
                        try:
                            await update.message.reply_text(
                                result.response,
                                parse_mode="HTML",
                                reply_markup=result.reply_markup if hasattr(result, 'reply_markup') else None
                            )
                        except Exception as e:
                            logger.warning(f"[PLUGIN] HTML 파싱 실패, 재시도: {e}")
                            await update.message.reply_text(result.response)
                        logger.debug(f"[PLUGIN] 응답 전송 완료")
                    else:
                        logger.warning(f"[PLUGIN] handled=True지만 response가 비어있음!")
                    clear_context()
                    return
                logger.debug("[PLUGIN] 매칭 없음 → Claude 처리로 진행")
            except Exception as e:
                logger.error(f"[PLUGIN] 처리 오류: {e}", exc_info=True)
                # 플러그인 오류 시 Claude로 fallback
        else:
            logger.debug("[PLUGIN] 플러그인 로더 없음")

        if not self._is_authenticated(user_id):
            logger.debug("메시지 거부 - 인증 필요")
            await update.message.reply_text(
                "🔒 인증이 필요합니다.\n"
                f"/auth <키>로 인증하세요. ({self.auth.timeout_minutes}분간 유효)\n"
                "/help 도움말"
            )
            clear_context()
            return

        # 세션 생성 중이면 메시지 블로킹
        if user_id in self._creating_sessions:
            logger.info(f"세션 생성 중 - 메시지 블로킹: user={user_id}")
            await update.message.reply_text(
                "⏳ <b>세션 준비 중...</b>\n\n"
                "잠시 후 다시 보내주세요!",
                parse_mode="HTML"
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
                self._creating_sessions.add(user_id)
                try:
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
                finally:
                    self._creating_sessions.discard(user_id)
            else:
                is_new_session = False

        # 세션 모델 및 프로젝트 경로 가져오기
        model = self.sessions.get_session_model(user_id, session_id)
        project_path = self.sessions.get_session_project_path(user_id, session_id)

        # 세션 ID 컨텍스트 설정
        set_session_id(session_id)

        # 로깅 (session_id 확정 후)
        logger.info(f"메시지 접수: model={model}, new={is_new_session}, project={project_path or '(없음)'}")

        # Watchdog 지연 시작
        self._ensure_watchdog()

        # 세션별 락 체크 (session_queue_manager가 단일 진실 소스)
        if session_queue_manager.is_locked(session_id):
            logger.warning(f"세션 락 충돌 - session={session_id[:8]}, 세션 선택 UI 표시")
            await self._show_session_selection_ui(
                update, user_id, message, session_id, model, is_new_session, project_path
            )
            clear_context()
            return

        # 동시 요청 제한 체크 (Semaphore._value가 0이면 모든 슬롯 사용 중)
        semaphore = self._user_semaphores[user_id]
        logger.trace(f"Semaphore 상태 - available={semaphore._value}")
        if semaphore._value == 0:
            active_count = self.get_active_task_count(user_id)
            logger.warning(f"동시 요청 제한 - 활성 태스크: {active_count}개")
            # 거절된 메시지 미리보기 (최대 50자)
            rejected_preview = message[:50] + "..." if len(message) > 50 else message
            await update.message.reply_text(
                f"⚠️ <b>메시지 처리 불가</b>\n\n"
                f"현재 {active_count}개 요청이 처리 중이에요.\n\n"
                f"❌ <b>거절된 메시지:</b>\n"
                f"<code>{rejected_preview}</code>\n\n"
                f"완료 후 다시 보내주세요!",
                parse_mode="HTML"
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
        self._register_task(task, user_id, session_id, trace_id, message)
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
        """Semaphore + 세션 락으로 동시 요청 제한 후 Claude 호출."""
        # 백그라운드 태스크에서 컨텍스트 재설정
        set_trace_id(trace_id)
        set_user_id(user_id)
        set_session_id(session_id)
        logger.trace(f"_process_claude_request_with_semaphore 시작 - model={model}")

        # 세션 락 획득 (session_queue_manager 사용)
        locked = await session_queue_manager.try_lock(session_id, user_id, message)
        if not locked:
            logger.warning(f"세션 락 획득 실패 - session={session_id[:8]}")
            # 세션 선택 UI 표시 (다른 세션으로 처리하거나 대기 선택)
            project_path = self.sessions.get_session_project_path(user_id, session_id) or ""
            await self._show_session_selection_ui(
                update=None,  # 백그라운드이므로 update 없음
                user_id=user_id,
                message=message,
                current_session_id=session_id,
                model=model or "sonnet",
                is_new_session=is_new_session,
                project_path=project_path,
                bot=bot,
                chat_id=chat_id,
            )
            logger.info(f"세션 선택 UI 표시 완료 - session={session_id[:8]}")
            clear_context()
            return

        try:
            logger.trace(f"세션 락 획득됨 - session={session_id[:8]}")
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
        finally:
            # 락 해제 및 대기열 처리
            next_msg = await session_queue_manager.unlock(session_id)
            if next_msg:
                logger.info(f"대기열에서 다음 메시지 처리 - session={session_id[:8]}, user={next_msg.user_id}")
                # 다음 메시지를 새 태스크로 처리
                asyncio.create_task(
                    self._process_queued_message(bot, next_msg)
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

        # 전체 질문 로깅
        logger.info(f"Claude 호출 시작 - session={session_id[:8]}, model={model}")
        logger.info(f"===== 사용자 질문 (START) =====")
        logger.info(message)
        logger.info(f"===== 사용자 질문 (END) =====")

        try:
            # 프로젝트 세션 경로 가져오기
            project_path = self.sessions.get_session_project_path(user_id, session_id)
            if project_path:
                logger.trace(f"프로젝트 세션 - project_path={project_path}")

            # 장시간 작업 알림 태스크
            long_task_notified = False
            short_message = truncate_message(message, 30)  # 메시지 미리보기

            async def notify_long_task():
                nonlocal long_task_notified
                await asyncio.sleep(LONG_TASK_THRESHOLD_SECONDS)
                if not long_task_notified:
                    long_task_notified = True
                    elapsed_min = LONG_TASK_THRESHOLD_SECONDS // 60
                    logger.info(f"장시간 작업 알림 - {elapsed_min}분 경과")
                    await bot.send_message(
                        chat_id=chat_id,
                        text=f"⏳ <code>{short_message}</code>\n작업이 {elapsed_min}분 이상 걸리고 있어요. 완료되면 알려드릴게요!",
                        parse_mode="HTML"
                    )

            # 알림 태스크 시작
            notify_task = asyncio.create_task(notify_long_task())

            # Claude 호출
            logger.trace(f"claude.chat() 호출 - model={model}")
            try:
                response, error, _ = await self.claude.chat(message, session_id, model=model, project_path=project_path or None)
            finally:
                # Claude 완료 시 알림 태스크 취소
                notify_task.cancel()
                try:
                    await notify_task
                except asyncio.CancelledError:
                    pass

            elapsed = time.time() - start_time
            logger.info(f"Claude 응답 완료 - session={session_id[:8]}, elapsed={elapsed:.1f}s, length={len(response) if response else 0}")
            logger.info(f"===== Claude 응답 (START) =====")
            logger.info(response if response else "(None 또는 빈 문자열)")
            logger.info(f"===== Claude 응답 (END) =====")

            # 상세 디버깅 정보
            logger.debug(f"[DEBUG] response type: {type(response)}")
            logger.debug(f"[DEBUG] response repr: {repr(response)[:200] if response else 'None'}")
            logger.debug(f"[DEBUG] error: {error}")

            if error:
                logger.warning(f"Claude 에러: {error}")

            # 기존 세션이면 메시지 추가 (명시적 session_id 사용)
            if not is_new_session:
                logger.trace("세션 히스토리에 메시지 추가")
                self.sessions.add_message(user_id, session_id, message, processor="claude")

            # 에러 처리
            if error == "TIMEOUT":
                logger.warning("Claude 타임아웃")
                response = "⏱️ 응답 시간 초과. 다시 시도해주세요."
            elif error and error != "SESSION_NOT_FOUND":
                logger.error(f"Claude 오류: {error}")
                response = f"❌ 오류 발생: {error}"
            elif not response or not response.strip():
                logger.error(f"[EMPTY RESPONSE] Claude 빈 응답 감지!")
                logger.error(f"  response type: {type(response)}")
                logger.error(f"  response repr: {repr(response)}")
                logger.error(f"  error: {error}")
                logger.error(f"  session_id: {session_id[:8]}")
                logger.error(f"  model: {model}")
                logger.error(f"  is_new_session: {is_new_session}")
                logger.error(f"  message preview: {message[:200]}")
                logger.error(f"  project_path: {project_path}")
                response = f"⚠️ <code>{short_message}</code>\n응답이 비어있습니다. 다시 시도해주세요."

            # 세션 정보 prefix 추가
            session_info = self.sessions.get_session_info(user_id, session_id)
            session_short_id = session_id[:8]  # 명령어용 ID (이름 제외)
            history_count = self.sessions.get_history_count(user_id, session_id)

            # 질문 미리보기 (최대 30자)
            question_preview = truncate_message(message, 30)

            prefix = f"<b>[{session_info}|#{history_count}]</b>\n💬 <code>{question_preview}</code>\n\n"
            suffix = (
                f"\n\n"
                f"/s_{session_short_id} 세션이동\n"
                f"/h_{session_short_id} 히스토리"
            )

            full_response = prefix + response + suffix
            logger.trace(f"최종 응답 길이: {len(full_response)}")

            # 장시간 작업 완료 알림 (5분 넘게 걸렸으면)
            if long_task_notified:
                elapsed_min = int(elapsed // 60)
                elapsed_sec = int(elapsed % 60)
                await bot.send_message(
                    chat_id=chat_id,
                    text=f"✅ <code>{short_message}</code>\n작업 완료! ({elapsed_min}분 {elapsed_sec}초 소요)",
                    parse_mode="HTML"
                )

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

    async def _show_session_selection_ui(
        self,
        update: Update,
        user_id: str,
        message: str,
        current_session_id: str,
        model: str,
        is_new_session: bool,
        project_path: str,
        *,
        bot=None,
        chat_id: int = None,
    ) -> None:
        """세션 락 충돌 시 세션 선택 UI 표시 (개선된 버전).

        옵션:
        1. 이 세션에서 대기 (추천) - 현재 세션 완료 후 자동 처리
        2. 다른 세션 선택 - 바로 사용 가능한 세션 목록
        3. 새 세션 생성
        4. 취소

        Args:
            update: Telegram Update (None이면 bot/chat_id 사용)
            bot: Telegram Bot 객체 (update 없을 때 사용)
            chat_id: 채팅 ID (update 없을 때 사용)
        """
        from telegram import InlineKeyboardButton, InlineKeyboardMarkup

        message_preview = truncate_message(message, 40)
        # update가 있으면 update에서, 없으면 파라미터에서 chat_id 가져오기
        if update:
            chat_id = update.effective_chat.id

        # 현재 세션 상태 정보
        current_state = session_queue_manager.get_status(current_session_id)
        queue_size = current_state.get_queue_size() if current_state else 0

        # 사용 가능한 세션 찾기 (락 안 걸린 세션)
        all_sessions = self.sessions.list_sessions(user_id)
        available_sessions = []

        for s in all_sessions:
            sid = s["full_session_id"]
            if sid == current_session_id:
                continue  # 현재 세션 제외
            # 락 안 걸린 세션만
            if not session_queue_manager.is_locked(sid):
                history = self.sessions.get_session_history(user_id, sid)
                recent = history[-2:] if history else []
                available_sessions.append({
                    **s,
                    "recent": recent,
                })

        # UI 구성
        lines = [
            f"⏳ <b>현재 세션이 처리 중입니다</b>",
            f"",
            f"💬 <code>{message_preview}</code>",
            f"",
        ]

        buttons = []

        # 1. 이 세션에서 대기 (추천)
        wait_label = f"🔄 이 세션에서 대기"
        if queue_size > 0:
            wait_label += f" (대기 {queue_size}개)"
        buttons.append([
            InlineKeyboardButton(
                wait_label + " (추천)",
                callback_data=f"sq:wait:{current_session_id[:16]}"
            )
        ])
        lines.append(f"🔄 <b>이 세션에서 대기</b>: 완료 후 자동 처리")

        # 2. 사용 가능한 다른 세션
        if available_sessions:
            lines.append(f"")
            lines.append(f"📂 <b>바로 사용 가능한 세션:</b>")
            for s in available_sessions[:4]:  # 최대 4개
                sid = s["full_session_id"]
                short_id = s["session_id"]
                name = s.get("name") or f"세션 {short_id}"
                sess_model = s.get("model", "sonnet")
                model_emoji = {"opus": "🧠", "sonnet": "⚡", "haiku": "🚀"}.get(sess_model, "⚡")

                recent_msgs = s.get("recent", [])
                if recent_msgs:
                    recent_preview = " / ".join(truncate_message(m, 12) for m in recent_msgs[-2:])
                    lines.append(f"• {model_emoji} <b>{name[:10]}</b>: {recent_preview}")
                else:
                    lines.append(f"• {model_emoji} <b>{name[:10]}</b>")

                buttons.append([
                    InlineKeyboardButton(
                        f"{model_emoji} {name[:15]}",
                        callback_data=f"sq:switch:{sid[:16]}"
                    )
                ])

        # 3. 새 세션 생성
        lines.append(f"")
        lines.append(f"➕ <b>새 세션 생성:</b>")
        buttons.append([
            InlineKeyboardButton("🧠 Opus", callback_data="sq:new:opus"),
            InlineKeyboardButton("⚡ Sonnet", callback_data="sq:new:sonnet"),
            InlineKeyboardButton("🚀 Haiku", callback_data="sq:new:haiku"),
        ])

        # 4. 취소
        buttons.append([
            InlineKeyboardButton("❌ 취소", callback_data="sq:cancel"),
        ])

        # 메시지 정보를 session_queue에 임시 저장 (콜백에서 사용)
        # 콜백 데이터 크기 제한으로 메시지는 별도 저장
        self._temp_pending = {
            "user_id": user_id,
            "chat_id": chat_id,
            "message": message,
            "model": model,
            "is_new_session": is_new_session,
            "project_path": project_path,
            "current_session_id": current_session_id,
        }

        # update가 있으면 reply_text, 없으면 bot.send_message 사용
        if update:
            await update.message.reply_text(
                "\n".join(lines),
                reply_markup=InlineKeyboardMarkup(buttons),
                parse_mode="HTML"
            )
        else:
            await bot.send_message(
                chat_id=chat_id,
                text="\n".join(lines),
                reply_markup=InlineKeyboardMarkup(buttons),
                parse_mode="HTML"
            )

    async def _process_queued_message(self, bot, queued_msg: QueuedMessage) -> None:
        """대기열에서 꺼낸 메시지 처리."""
        trace_id = self._setup_request_context(queued_msg.chat_id)
        set_user_id(queued_msg.user_id)
        set_session_id(queued_msg.session_id)

        logger.info(f"대기열 메시지 처리 시작 - session={queued_msg.session_id[:8]}, user={queued_msg.user_id}")

        # 대기 완료 알림 - 세션 정보 포함
        session_info = self.sessions.get_session_info(queued_msg.user_id, queued_msg.session_id)
        model_emoji = {"opus": "🧠", "sonnet": "⚡", "haiku": "🚀"}.get(queued_msg.model, "⚡")
        try:
            await bot.send_message(
                chat_id=queued_msg.chat_id,
                text=(
                    f"🔄 <b>대기 완료!</b>\n\n"
                    f"💬 <code>{truncate_message(queued_msg.message, 30)}</code>\n\n"
                    f"📍 세션: {model_emoji} <b>{session_info}</b>\n"
                    f"처리를 시작합니다..."
                ),
                parse_mode="HTML"
            )
        except Exception as e:
            logger.warning(f"대기 완료 알림 실패: {e}")

        # Claude 호출
        await self._process_claude_request_with_semaphore(
            bot=bot,
            chat_id=queued_msg.chat_id,
            user_id=queued_msg.user_id,
            session_id=queued_msg.session_id,
            message=queued_msg.message,
            is_new_session=queued_msg.is_new_session,
            trace_id=trace_id,
            model=queued_msg.model,
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

    # ==================== 오류 처리 ====================

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
        error_msg = str(context.error)

        # 사용자 친화적 에러 메시지 매핑
        friendly_errors = {
            "Query is too old": "⏰ 버튼이 만료되었습니다. 다시 시도해주세요.",
            "Message is not modified": None,  # 무시 (동일 내용 수정 시도)
            "message to edit not found": "🗑️ 메시지가 삭제되어 수정할 수 없습니다.",
        }

        for pattern, friendly_msg in friendly_errors.items():
            if pattern in error_msg:
                logger.debug(f"알려진 에러: {error_type}: {error_msg}")
                if friendly_msg and update and update.effective_chat:
                    await context.bot.send_message(
                        chat_id=update.effective_chat.id,
                        text=friendly_msg
                    )
                clear_context()
                return

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

    async def callback_query_handler(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """인라인 버튼 콜백 처리."""
        query = update.callback_query
        if not query:
            return

        chat_id = query.message.chat_id if query.message else None
        if not chat_id:
            return

        self._setup_request_context(chat_id)
        callback_data = query.data
        logger.info(f"Callback query: {callback_data} (chat_id={chat_id})")

        # 콜백 응답 (로딩 표시 제거)
        await query.answer()

        # Todo 플러그인 콜백 처리
        if callback_data.startswith("td:"):
            await self._handle_todo_callback(query, chat_id, callback_data)
            return

        # Memo 플러그인 콜백 처리
        if callback_data.startswith("memo:"):
            await self._handle_memo_callback(query, chat_id, callback_data)
            return

        # Weather 플러그인 콜백 처리
        if callback_data.startswith("weather:"):
            await self._handle_weather_callback(query, chat_id, callback_data)
            return

        # 세션 관련 콜백 처리
        if callback_data.startswith("sess:"):
            await self._handle_session_callback(query, chat_id, callback_data)
            return

        # Lock 콜백 처리
        if callback_data.startswith("lock:"):
            await self._handle_lock_callback(query, chat_id)
            return

        # Jobs 콜백 처리 (스케줄 작업)
        if callback_data.startswith("jobs:"):
            await self._handle_jobs_callback(query, chat_id)
            return

        # 세션 스케줄 콜백 처리
        if callback_data.startswith("sched:"):
            await self._handle_scheduler_callback(query, chat_id, callback_data)
            return

        # 세션 큐 콜백 처리 (새 방식)
        if callback_data.startswith("sq:"):
            await self._handle_session_queue_callback(query, chat_id, callback_data)
            return

        # 대체 세션 선택 콜백 처리 (레거시 - 호환성 유지)
        if callback_data.startswith("alt:"):
            await self._handle_alternative_session_callback(query, chat_id, callback_data)
            return

        # 다른 플러그인 콜백은 여기에 추가
        logger.warning(f"Unknown callback: {callback_data}")

    async def _handle_todo_force_reply(self, update: Update, chat_id: int, message: str, slot_code: str) -> None:
        """Todo ForceReply 응답 처리."""
        logger.info(f"Todo ForceReply 처리: slot={slot_code}, msg={message[:50]}")

        todo_plugin = None
        if self.plugins:
            todo_plugin = self.plugins.get_plugin_by_name("todo")

        if not todo_plugin or not hasattr(todo_plugin, 'handle_force_reply'):
            await update.message.reply_text("❌ Todo 플러그인을 찾을 수 없습니다.")
            return

        result = todo_plugin.handle_force_reply(message, chat_id, slot_code)

        await update.message.reply_text(
            text=result.get("text", ""),
            reply_markup=result.get("reply_markup"),
            parse_mode="HTML"
        )

    async def _handle_new_session_force_reply(self, update: Update, chat_id: int, name: str, model: str) -> None:
        """세션 생성 ForceReply 응답 처리."""
        from telegram import InlineKeyboardButton, InlineKeyboardMarkup

        logger.info(f"세션 생성 ForceReply 처리: model={model}, name={name}")

        user_id = str(chat_id)
        model_name = model if model in ["opus", "sonnet", "haiku"] else "sonnet"

        # Claude 세션 생성
        session_id = await self.claude.create_session()
        if not session_id:
            await update.message.reply_text("❌ 세션 생성 실패")
            return

        # 이름 정리 (50자 제한)
        session_name = name.strip()[:50] if name.strip() else ""

        # 세션 저장
        self.sessions.create_session(user_id, session_id, "(새 세션)", model=model_name, name=session_name)
        short_id = session_id[:8]

        model_emoji = {"opus": "🧠", "sonnet": "⚡", "haiku": "🚀"}.get(model_name, "⚡")
        name_line = f"\n📝 <b>이름:</b> {session_name}" if session_name else ""

        keyboard = [[
            InlineKeyboardButton("📋 세션 목록", callback_data="sess:list"),
        ]]

        await update.message.reply_text(
            text=f"✅ 새 세션 생성됨!\n\n"
                 f"{model_emoji} <b>모델:</b> {model_name}\n"
                 f"🆔 <b>ID:</b> <code>{short_id}</code>{name_line}",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="HTML"
        )

    async def _handle_memo_force_reply(self, update: Update, chat_id: int, message: str) -> None:
        """메모 추가 ForceReply 응답 처리."""
        logger.info(f"메모 ForceReply 처리: msg={message[:50]}")

        memo_plugin = None
        if self.plugins:
            memo_plugin = self.plugins.get_plugin_by_name("memo")

        if not memo_plugin or not hasattr(memo_plugin, 'handle_force_reply'):
            await update.message.reply_text("❌ 메모 플러그인을 찾을 수 없습니다.")
            return

        result = memo_plugin.handle_force_reply(message, chat_id)

        await update.message.reply_text(
            text=result.get("text", ""),
            reply_markup=result.get("reply_markup"),
            parse_mode="HTML"
        )

    async def _handle_schedule_force_reply(self, update: Update, chat_id: int, message: str) -> None:
        """스케줄 메시지 입력 ForceReply 응답 처리."""
        from telegram import InlineKeyboardButton, InlineKeyboardMarkup

        user_id = str(chat_id)
        pending = self._pending_schedule_input.get(user_id)

        if not pending:
            await update.message.reply_text("❌ 스케줄 입력 상태가 만료되었습니다. 다시 시도하세요.")
            return

        if not self._schedule_manager:
            await update.message.reply_text("❌ 스케줄 기능이 비활성화되어 있습니다.")
            del self._pending_schedule_input[user_id]
            return

        # 스케줄 타입 및 정보
        schedule_type = pending.get("type", "claude")
        project_path = pending.get("project_path") if schedule_type == "project" else None
        name = pending.get("name", "스케줄")
        model = pending.get("model", "sonnet")

        # 이름 자동 생성 (Claude 타입인 경우)
        if schedule_type == "claude" and name == "스케줄":
            # 메시지 앞 15자를 이름으로 사용
            name = message[:15].strip() + ("..." if len(message) > 15 else "")

        # 스케줄 등록
        schedule = self._schedule_manager.add(
            user_id=user_id,
            chat_id=chat_id,
            name=name,
            hour=pending["hour"],
            minute=pending.get("minute", 0),
            message=message,
            schedule_type=schedule_type,
            model=model,
            project_path=project_path,
        )

        # 상태 정리
        del self._pending_schedule_input[user_id]

        # 성공 메시지
        keyboard = [[
            InlineKeyboardButton("📅 스케줄 목록", callback_data="sched:refresh"),
        ]]

        type_label = "프로젝트" if schedule_type == "project" else "Claude"
        path_info = f"\n📁 경로: <code>{project_path}</code>" if project_path else ""

        await update.message.reply_text(
            f"✅ <b>스케줄 등록 완료!</b>\n\n"
            f"{schedule.type_emoji} <b>{schedule.name}</b> ({type_label})\n"
            f"⏰ 시간: <b>{schedule.time_str}</b> (매일)\n"
            f"🤖 모델: <b>{model}</b>{path_info}\n"
            f"💬 메시지: <i>{message[:50]}{'...' if len(message) > 50 else ''}</i>",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="HTML"
        )

        logger.info(f"스케줄 등록: {schedule.name} @ {schedule.time_str} (type={schedule_type})")

    async def _handle_todo_callback(self, query, chat_id: int, callback_data: str) -> None:
        """Todo 플러그인 콜백 처리."""
        try:
            # 플러그인 인스턴스 가져오기
            todo_plugin = None
            if self.plugins:
                todo_plugin = self.plugins.get_plugin_by_name("todo")
                logger.info(f"Todo 플러그인 조회: {todo_plugin}")
            else:
                logger.warning("self.plugins가 None입니다")

            if not todo_plugin or not hasattr(todo_plugin, 'handle_callback'):
                logger.error(f"Todo 플러그인을 찾을 수 없음: {todo_plugin}")
                await query.edit_message_text("❌ Todo 플러그인을 찾을 수 없습니다.")
                return

            # 콜백 처리
            result = todo_plugin.handle_callback(callback_data, chat_id)

            # ForceReply 처리 (새 메시지로)
            if result.get("force_reply"):
                # 기존 메시지 업데이트
                await query.edit_message_text(
                    text=result.get("text", "할일 입력"),
                    parse_mode="HTML"
                )
                # ForceReply 메시지 전송
                slot_code = result.get("slot_code", "m")
                await query.message.reply_text(
                    text=f"⬇️ 아래에 할일을 입력하세요 (slot:{slot_code})",
                    reply_markup=result["force_reply"],
                    parse_mode="HTML"
                )
                return

            # 메시지 수정 또는 전송
            if result.get("edit", True) and query.message:
                await query.edit_message_text(
                    text=result.get("text", ""),
                    reply_markup=result.get("reply_markup"),
                    parse_mode="HTML"
                )
            else:
                await query.message.reply_text(
                    text=result.get("text", ""),
                    reply_markup=result.get("reply_markup"),
                    parse_mode="HTML"
                )
        except Exception as e:
            logger.exception(f"Todo 콜백 처리 중 오류: {e}")
            try:
                await query.edit_message_text(
                    text=f"❌ 오류가 발생했습니다.\n\n<code>{str(e)}</code>",
                    parse_mode="HTML"
                )
            except:
                await query.message.reply_text(
                    text=f"❌ 오류가 발생했습니다.\n\n<code>{str(e)}</code>",
                    parse_mode="HTML"
                )

    async def _handle_memo_callback(self, query, chat_id: int, callback_data: str) -> None:
        """Memo 플러그인 콜백 처리."""
        try:
            memo_plugin = None
            if self.plugins:
                memo_plugin = self.plugins.get_plugin_by_name("memo")

            if not memo_plugin or not hasattr(memo_plugin, 'handle_callback'):
                await query.edit_message_text("❌ Memo 플러그인을 찾을 수 없습니다.")
                return

            result = memo_plugin.handle_callback(callback_data, chat_id)

            # ForceReply 처리 (새 메시지로)
            if result.get("force_reply"):
                # 기존 메시지 업데이트
                await query.edit_message_text(
                    text=result.get("text", "메모 입력"),
                    parse_mode="HTML"
                )
                # ForceReply 메시지 전송
                await query.message.reply_text(
                    text="⬇️ 아래에 메모 내용을 입력하세요",
                    reply_markup=result["force_reply"],
                    parse_mode="HTML"
                )
                return

            if result.get("edit", True):
                await query.edit_message_text(
                    text=result.get("text", ""),
                    reply_markup=result.get("reply_markup"),
                    parse_mode="HTML"
                )
            else:
                await query.message.reply_text(
                    text=result.get("text", ""),
                    reply_markup=result.get("reply_markup"),
                    parse_mode="HTML"
                )
        except Exception as e:
            logger.exception(f"Memo 콜백 처리 중 오류: {e}")
            try:
                await query.edit_message_text(
                    text=f"❌ 오류가 발생했습니다.\n\n<code>{str(e)}</code>",
                    parse_mode="HTML"
                )
            except:
                pass

    async def _handle_weather_callback(self, query, chat_id: int, callback_data: str) -> None:
        """Weather 플러그인 콜백 처리."""
        try:
            weather_plugin = None
            if self.plugins:
                weather_plugin = self.plugins.get_plugin_by_name("weather")

            if not weather_plugin or not hasattr(weather_plugin, 'handle_callback_async'):
                await query.edit_message_text("❌ Weather 플러그인을 찾을 수 없습니다.")
                return

            result = await weather_plugin.handle_callback_async(callback_data, chat_id)

            if result.get("edit", True):
                await query.edit_message_text(
                    text=result.get("text", ""),
                    reply_markup=result.get("reply_markup"),
                    parse_mode="HTML"
                )
            else:
                await query.message.reply_text(
                    text=result.get("text", ""),
                    reply_markup=result.get("reply_markup"),
                    parse_mode="HTML"
                )
        except Exception as e:
            logger.exception(f"Weather 콜백 처리 중 오류: {e}")
            try:
                await query.edit_message_text(
                    text=f"❌ 오류가 발생했습니다.\n\n<code>{str(e)}</code>",
                    parse_mode="HTML"
                )
            except:
                pass

    async def _handle_session_callback(self, query, chat_id: int, callback_data: str) -> None:
        """세션 관련 콜백 처리."""
        try:
            parts = callback_data.split(":")
            if len(parts) < 2:
                await query.edit_message_text("❌ 잘못된 요청")
                return

            action = parts[1]

            if action == "new":
                # sess:new:opus
                model = parts[2] if len(parts) > 2 else "sonnet"
                await self._handle_new_session_name_prompt(query, chat_id, model)

            elif action == "new_confirm":
                # sess:new_confirm:opus (이름 없이 바로 생성)
                model = parts[2] if len(parts) > 2 else "sonnet"
                await self._handle_new_session_callback(query, chat_id, model, "")

            elif action == "switch":
                # sess:switch:12345678
                session_id = parts[2] if len(parts) > 2 else ""
                await self._handle_switch_session_callback(query, chat_id, session_id)

            elif action == "delete":
                # sess:delete:12345678
                session_id = parts[2] if len(parts) > 2 else ""
                await self._handle_delete_session_confirm(query, chat_id, session_id)

            elif action == "confirm_del":
                # sess:confirm_del:12345678
                session_id = parts[2] if len(parts) > 2 else ""
                await self._handle_delete_session_execute(query, chat_id, session_id)

            elif action == "history":
                # sess:history:12345678
                session_id = parts[2] if len(parts) > 2 else ""
                await self._handle_history_callback(query, chat_id, session_id)

            elif action == "list":
                await self._handle_session_list_callback(query, chat_id)

            elif action == "model":
                # sess:model:opus:12345678
                model = parts[2] if len(parts) > 2 else "sonnet"
                session_id = parts[3] if len(parts) > 3 else ""
                await self._handle_model_change_callback(query, chat_id, model, session_id)

            elif action == "cancel":
                await self._handle_session_list_callback(query, chat_id)

            else:
                await query.edit_message_text("❌ 알 수 없는 명령")

        except Exception as e:
            logger.exception(f"세션 콜백 처리 중 오류: {e}")
            try:
                await query.edit_message_text(
                    text=f"❌ 오류가 발생했습니다.\n\n<code>{str(e)}</code>",
                    parse_mode="HTML"
                )
            except:
                pass

    async def _handle_new_session_name_prompt(self, query, chat_id: int, model: str) -> None:
        """새 세션 이름 입력 프롬프트."""
        from telegram import ForceReply, InlineKeyboardButton, InlineKeyboardMarkup

        model_emoji = {"opus": "🧠", "sonnet": "⚡", "haiku": "🚀"}.get(model, "⚡")

        # 기존 메시지 업데이트
        await query.edit_message_text(
            text=f"{model_emoji} <b>{model.upper()}</b> 세션 생성\n\n세션 이름을 입력하세요:",
            parse_mode="HTML"
        )

        # ForceReply로 이름 입력 요청
        await query.message.reply_text(
            text=f"⬇️ 세션 이름 입력 (sess_name:{model})",
            reply_markup=ForceReply(selective=True, input_field_placeholder="세션 이름...")
        )

    async def _handle_new_session_callback(self, query, chat_id: int, model: str, name: str = "") -> None:
        """새 세션 생성 콜백."""
        from telegram import InlineKeyboardButton, InlineKeyboardMarkup

        model_map = {"opus": "opus", "sonnet": "sonnet", "haiku": "haiku"}
        model_name = model_map.get(model, "sonnet")

        user_id = str(chat_id)

        # Claude 세션 생성
        session_id = await self.claude.create_session()
        if not session_id:
            await query.edit_message_text("❌ 세션 생성 실패")
            return

        # 세션 저장
        self.sessions.create_session(user_id, session_id, "(새 세션)", model=model_name, name=name)
        short_id = session_id[:8]

        model_emoji = {"opus": "🧠", "sonnet": "⚡", "haiku": "🚀"}.get(model_name, "⚡")

        keyboard = [
            [
                InlineKeyboardButton("📋 세션 목록", callback_data="sess:list"),
            ]
        ]

        await query.edit_message_text(
            text=f"✅ 새 세션 생성됨!\n\n"
                 f"{model_emoji} <b>모델:</b> {model_name}\n"
                 f"🆔 <b>ID:</b> <code>{short_id}</code>",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="HTML"
        )

    async def _handle_switch_session_callback(self, query, chat_id: int, session_id: str) -> None:
        """세션 전환 콜백."""
        from telegram import InlineKeyboardButton, InlineKeyboardMarkup

        user_id = str(chat_id)
        session = self.sessions.get_session_by_prefix(user_id, session_id[:8])
        if not session:
            await query.edit_message_text("❌ 세션을 찾을 수 없습니다.")
            return

        full_session_id = session.get("full_session_id", session_id)
        self.sessions.set_current(user_id, full_session_id)
        short_id = full_session_id[:8]
        name = session.get("name") or f"세션 {short_id}"
        model = session.get("model", "sonnet")
        model_emoji = {"opus": "🧠", "sonnet": "⚡", "haiku": "🚀"}.get(model, "⚡")

        keyboard = [
            [
                InlineKeyboardButton("🧠 Opus", callback_data=f"sess:model:opus:{full_session_id}"),
                InlineKeyboardButton("⚡ Sonnet", callback_data=f"sess:model:sonnet:{full_session_id}"),
                InlineKeyboardButton("🚀 Haiku", callback_data=f"sess:model:haiku:{full_session_id}"),
            ],
            [
                InlineKeyboardButton("📋 세션 목록", callback_data="sess:list"),
            ]
        ]

        await query.edit_message_text(
            text=f"✅ 세션 전환됨!\n\n"
                 f"📂 <b>{name}</b>\n"
                 f"{model_emoji} 모델: {model}\n"
                 f"🆔 ID: <code>{short_id}</code>",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="HTML"
        )

    async def _handle_delete_session_confirm(self, query, chat_id: int, session_id: str) -> None:
        """세션 삭제 확인."""
        from telegram import InlineKeyboardButton, InlineKeyboardMarkup

        user_id = str(chat_id)
        session = self.sessions.get_session_by_prefix(user_id, session_id[:8])
        if not session:
            await query.edit_message_text("❌ 세션을 찾을 수 없습니다.")
            return

        full_session_id = session.get("full_session_id", session_id)
        short_id = full_session_id[:8]
        name = session.get("name") or f"세션 {short_id}"

        # 현재 활성 세션은 삭제 불가
        current_session_id = self.sessions.get_current_session_id(user_id)
        if current_session_id == full_session_id:
            keyboard = [[InlineKeyboardButton("« 돌아가기", callback_data="sess:list")]]
            await query.edit_message_text(
                text=f"⚠️ <b>삭제 불가</b>\n\n"
                     f"📂 <b>{name}</b>은(는) 현재 사용 중입니다.\n\n"
                     f"💡 다른 세션으로 전환 후 삭제하세요.",
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode="HTML"
            )
            return

        keyboard = [
            [
                InlineKeyboardButton("✅ 삭제", callback_data=f"sess:confirm_del:{full_session_id}"),
                InlineKeyboardButton("❌ 취소", callback_data="sess:cancel"),
            ]
        ]

        await query.edit_message_text(
            text=f"🗑️ <b>세션 삭제 확인</b>\n\n"
                 f"📂 <b>{name}</b>\n"
                 f"🆔 <code>{short_id}</code>\n\n"
                 f"정말 삭제하시겠습니까?",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="HTML"
        )

    async def _handle_delete_session_execute(self, query, chat_id: int, session_id: str) -> None:
        """세션 삭제 실행."""
        from telegram import InlineKeyboardButton, InlineKeyboardMarkup

        user_id = str(chat_id)
        session = self.sessions.get_session_by_prefix(user_id, session_id[:8])
        if not session:
            await query.edit_message_text("❌ 세션을 찾을 수 없습니다.")
            return

        full_session_id = session.get("full_session_id", session_id)
        short_id = full_session_id[:8]
        name = session.get("name") or f"세션 {short_id}"

        # 현재 활성 세션은 삭제 불가 (확인 후 전환된 경우 대비)
        current_session_id = self.sessions.get_current_session_id(user_id)
        if current_session_id == full_session_id:
            keyboard = [[InlineKeyboardButton("« 돌아가기", callback_data="sess:list")]]
            await query.edit_message_text(
                text=f"⚠️ <b>삭제 불가</b>\n\n"
                     f"📂 <b>{name}</b>은(는) 현재 사용 중입니다.\n\n"
                     f"💡 다른 세션으로 전환 후 삭제하세요.",
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode="HTML"
            )
            return

        self.sessions.delete_session(user_id, full_session_id)

        # 삭제 후 세션 목록 표시
        await self._handle_session_list_callback(query, chat_id, f"🗑️ <s>{name}</s> 삭제됨!\n\n")

    async def _handle_history_callback(self, query, chat_id: int, session_id: str) -> None:
        """세션 히스토리 콜백."""
        from telegram import InlineKeyboardButton, InlineKeyboardMarkup

        user_id = str(chat_id)
        session = self.sessions.get_session_by_prefix(user_id, session_id[:8])
        if not session:
            await query.edit_message_text("❌ 세션을 찾을 수 없습니다.")
            return

        full_session_id = session.get("full_session_id", session_id)
        short_id = full_session_id[:8]
        name = session.get("name") or f"세션 {short_id}"
        history = self.sessions.get_session_history_entries(user_id, full_session_id)

        lines = [f"📜 <b>{name}</b> 히스토리\n"]

        if not history:
            lines.append("(대화 기록 없음)")
        else:
            for i, entry in enumerate(history[-10:], 1):  # 최근 10개
                msg = entry.get("message", "")[:50] if isinstance(entry, dict) else str(entry)[:50]
                if len(entry.get("message", "") if isinstance(entry, dict) else str(entry)) > 50:
                    msg += "..."
                lines.append(f"{i}. {msg}")

        keyboard = [
            [
                InlineKeyboardButton("📂 세션으로", callback_data=f"sess:switch:{full_session_id}"),
                InlineKeyboardButton("📋 목록", callback_data="sess:list"),
            ]
        ]

        await query.edit_message_text(
            text="\n".join(lines),
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="HTML"
        )

    async def _handle_session_list_callback(self, query, chat_id: int, prefix: str = "") -> None:
        """세션 목록 콜백."""
        from datetime import datetime
        from telegram import InlineKeyboardButton, InlineKeyboardMarkup

        user_id = str(chat_id)
        sessions = self.sessions.list_sessions(user_id)
        current_session_id = self.sessions.get_current_session_id(user_id)

        timestamp = datetime.now().strftime("%H:%M:%S")
        lines = [f"{prefix}📋 <b>세션 목록</b> <i>({timestamp})</i>\n"]
        buttons = []

        if not sessions:
            lines.append("세션이 없습니다.")
        else:
            for session in sessions[:10]:  # 최대 10개
                sid = session["full_session_id"]
                short_id = session["session_id"]
                name = session.get("name") or f"세션 {short_id}"
                model = session.get("model", "sonnet")
                model_emoji = {"opus": "🧠", "sonnet": "⚡", "haiku": "🚀"}.get(model, "⚡")

                is_current = "👉 " if sid == current_session_id else ""
                # 락 상태 확인 (session_queue_manager가 단일 진실 소스)
                is_locked = session_queue_manager.is_locked(sid)
                lock_indicator = " 🔒" if is_locked else ""
                lines.append(f"{is_current}{model_emoji} <b>{name}</b> (<code>{short_id}</code>){lock_indicator}")

                # 각 세션에 액션 버튼
                buttons.append([
                    InlineKeyboardButton(f"📂 {name[:10]}", callback_data=f"sess:switch:{sid}"),
                    InlineKeyboardButton("📜", callback_data=f"sess:history:{sid}"),
                    InlineKeyboardButton("🗑️", callback_data=f"sess:delete:{sid}"),
                ])

        # 새 세션 생성 버튼
        buttons.append([
            InlineKeyboardButton("🧠 +Opus", callback_data="sess:new:opus"),
            InlineKeyboardButton("⚡ +Sonnet", callback_data="sess:new:sonnet"),
            InlineKeyboardButton("🚀 +Haiku", callback_data="sess:new:haiku"),
        ])
        buttons.append([
            InlineKeyboardButton("🔄 새로고침", callback_data="sess:list"),
            InlineKeyboardButton("📊 작업현황", callback_data="lock:refresh"),
        ])

        await query.edit_message_text(
            text="\n".join(lines),
            reply_markup=InlineKeyboardMarkup(buttons),
            parse_mode="HTML"
        )

    async def _handle_model_change_callback(self, query, chat_id: int, model: str, session_id: str) -> None:
        """모델 변경 콜백."""
        from telegram import InlineKeyboardButton, InlineKeyboardMarkup

        user_id = str(chat_id)
        session = self.sessions.get_session_by_prefix(user_id, session_id[:8])
        if not session:
            await query.edit_message_text("❌ 세션을 찾을 수 없습니다.")
            return

        full_session_id = session.get("full_session_id", session_id)

        # 모델 변경 (직접 데이터 수정)
        user_data = self.sessions._data.get(user_id)
        if user_data and user_data.get("sessions", {}).get(full_session_id):
            user_data["sessions"][full_session_id]["model"] = model
            self.sessions._save()

        short_id = full_session_id[:8]
        name = session.get("name") or f"세션 {short_id}"
        model_emoji = {"opus": "🧠", "sonnet": "⚡", "haiku": "🚀"}.get(model, "⚡")

        keyboard = [
            [
                InlineKeyboardButton("🧠 Opus", callback_data=f"sess:model:opus:{full_session_id}"),
                InlineKeyboardButton("⚡ Sonnet", callback_data=f"sess:model:sonnet:{full_session_id}"),
                InlineKeyboardButton("🚀 Haiku", callback_data=f"sess:model:haiku:{full_session_id}"),
            ],
            [
                InlineKeyboardButton("📋 세션 목록", callback_data="sess:list"),
            ]
        ]

        await query.edit_message_text(
            text=f"✅ 모델 변경됨!\n\n"
                 f"📂 <b>{name}</b>\n"
                 f"{model_emoji} 모델: <b>{model}</b>\n"
                 f"🆔 ID: <code>{short_id}</code>",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="HTML"
        )

    async def _handle_lock_callback(self, query, chat_id: int) -> None:
        """작업 현황 콜백 처리 - /lock과 동일."""
        from telegram import InlineKeyboardMarkup

        user_id = str(chat_id)
        text, keyboard = self._build_lock_status(user_id)

        await query.edit_message_text(
            text=text,
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="HTML"
        )

    async def _handle_jobs_callback(self, query, chat_id: int) -> None:
        """스케줄 작업 현황 콜백 처리 - /jobs와 동일."""
        from telegram import InlineKeyboardButton, InlineKeyboardMarkup
        from src.scheduler_manager import scheduler_manager

        text = scheduler_manager.get_status_text()

        keyboard = [[
            InlineKeyboardButton("🔄 새로고침", callback_data="jobs:refresh"),
        ]]

        await query.edit_message_text(
            text=text,
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="HTML"
        )

    async def _handle_alternative_session_callback(self, query, chat_id: int, callback_data: str) -> None:
        """대체 세션 선택 콜백 처리.

        callback_data 형식:
        - alt:s:{session_id}:{pending_key} - 기존 세션으로 처리
        - alt:n:{model}:{pending_key} - 새 세션 생성 후 처리
        - alt:cancel:{pending_key} - 취소
        """
        from telegram import InlineKeyboardButton, InlineKeyboardMarkup

        user_id = str(chat_id)
        parts = callback_data.split(":")

        if len(parts) < 3:
            await query.edit_message_text("❌ 잘못된 요청입니다.")
            return

        action = parts[1]

        if action == "cancel":
            # 취소 - pending 메시지 삭제
            pending_key = parts[2] if len(parts) > 2 else None
            if pending_key:
                self._pending_messages.pop(pending_key, None)
            await query.edit_message_text("❌ 요청이 취소되었습니다.")
            return

        if len(parts) < 4:
            await query.edit_message_text("❌ 잘못된 요청입니다.")
            return

        target = parts[2]  # session_id 또는 model
        pending_key = parts[3]

        # pending 메시지 조회
        message = self._get_pending_message(pending_key, user_id)
        if not message:
            logger.warning(f"대체 세션 콜백 - pending 메시지 없음: key={pending_key}")
            await query.edit_message_text(
                "⏰ <b>요청이 만료되었습니다</b>\n\n"
                "메시지를 다시 보내주세요.",
                parse_mode="HTML"
            )
            return

        message_preview = truncate_message(message, 30)

        if action == "s":
            # 기존 세션으로 처리
            # session_id는 16자로 잘렸으므로 prefix로 찾기
            session_info = self.sessions.get_session_by_prefix(user_id, target[:8])
            if not session_info:
                await query.edit_message_text("❌ 세션을 찾을 수 없습니다.")
                return

            full_session_id = session_info["full_session_id"]
            session_name = session_info.get("name") or session_info["session_id"]
            model = session_info.get("model", "sonnet")

            # 메시지 처리 시작 알림
            await query.edit_message_text(
                f"✅ <b>{session_name}</b> 세션으로 처리 중...\n\n"
                f"💬 <code>{message_preview}</code>",
                parse_mode="HTML"
            )

            # Claude 호출 (백그라운드)
            await self._process_alternative_session_request(
                bot=query.message.get_bot(),
                chat_id=chat_id,
                user_id=user_id,
                session_id=full_session_id,
                message=message,
                model=model,
            )

        elif action == "n":
            # 새 세션 생성 후 처리
            model = target  # opus, sonnet, haiku

            await query.edit_message_text(
                f"✅ 새 <b>{model.upper()}</b> 세션 생성 중...\n\n"
                f"💬 <code>{message_preview}</code>",
                parse_mode="HTML"
            )

            # 새 세션 생성
            logger.info(f"대체 세션 - 새 {model} 세션 생성")
            new_session_id = await self.claude.create_session()
            if not new_session_id:
                await query.message.reply_text("❌ 세션 생성 실패. 다시 시도해주세요.")
                return

            # 세션 저장
            self.sessions.create_session(user_id, new_session_id, message, model=model)
            logger.info(f"새 세션 생성됨: {new_session_id[:8]}, model={model}")

            # Claude 호출 (백그라운드)
            await self._process_alternative_session_request(
                bot=query.message.get_bot(),
                chat_id=chat_id,
                user_id=user_id,
                session_id=new_session_id,
                message=message,
                model=model,
                is_new_session=True,
            )

        else:
            await query.edit_message_text("❌ 알 수 없는 액션입니다.")

    async def _process_alternative_session_request(
        self,
        bot,
        chat_id: int,
        user_id: str,
        session_id: str,
        message: str,
        model: str,
        is_new_session: bool = False,
    ) -> None:
        """대체 세션으로 메시지 처리 (백그라운드)."""
        trace_id = self._setup_request_context(chat_id)
        set_session_id(session_id)

        logger.info(f"대체 세션 처리 시작 - session={session_id[:8]}, model={model}")

        # 세마포어 + session_queue_manager 사용
        await self._process_claude_request_with_semaphore(
            bot=bot,
            chat_id=chat_id,
            user_id=user_id,
            session_id=session_id,
            message=message,
            is_new_session=is_new_session,
            trace_id=trace_id,
            model=model,
        )

        clear_context()

    async def _handle_session_queue_callback(self, query, chat_id: int, callback_data: str) -> None:
        """세션 큐 콜백 처리 (새 방식).

        callback_data 형식:
        - sq:wait:{session_id} - 이 세션에서 대기
        - sq:switch:{session_id} - 다른 세션으로 전환
        - sq:new:{model} - 새 세션 생성
        - sq:cancel - 취소
        """
        user_id = str(query.from_user.id)
        parts = callback_data.split(":")
        action = parts[1] if len(parts) > 1 else ""

        # 임시 저장된 pending 정보 확인
        pending = getattr(self, '_temp_pending', None)
        if not pending or pending.get("user_id") != user_id:
            await query.edit_message_text(
                "⏰ <b>요청이 만료되었습니다</b>\n\n메시지를 다시 보내주세요.",
                parse_mode="HTML"
            )
            return

        message = pending["message"]
        model = pending["model"]
        is_new_session = pending["is_new_session"]
        project_path = pending["project_path"]
        current_session_id = pending["current_session_id"]
        bot = query.get_bot()

        if action == "cancel":
            self._temp_pending = None
            await query.edit_message_text("❌ 요청이 취소되었습니다.")
            return

        if action == "wait":
            # 현재 세션 대기열에 추가
            target_session_id = current_session_id
            # full session_id 복원
            for s in self.sessions.list_sessions(user_id):
                if s["full_session_id"].startswith(parts[2]):
                    target_session_id = s["full_session_id"]
                    break

            position = await session_queue_manager.add_to_waiting(
                session_id=target_session_id,
                user_id=user_id,
                chat_id=chat_id,
                message=message,
                model=model,
                is_new_session=is_new_session,
                project_path=project_path,
            )

            # 세션 정보 조회
            session_info = self.sessions.get_session_info(user_id, target_session_id)
            model_emoji = {"opus": "🧠", "sonnet": "⚡", "haiku": "🚀"}.get(model, "⚡")

            self._temp_pending = None
            await query.edit_message_text(
                f"🔄 <b>대기열에 추가되었습니다</b>\n\n"
                f"💬 <code>{truncate_message(message, 40)}</code>\n\n"
                f"📍 세션: {model_emoji} <b>{session_info}</b>\n"
                f"📍 대기 순번: {position}번째\n"
                f"현재 작업이 완료되면 자동으로 처리됩니다.",
                parse_mode="HTML"
            )
            return

        if action == "switch":
            # 다른 세션으로 전환 후 즉시 처리
            target_prefix = parts[2]
            target_session = None
            for s in self.sessions.list_sessions(user_id):
                if s["full_session_id"].startswith(target_prefix):
                    target_session = s
                    break

            if not target_session:
                await query.edit_message_text("❌ 세션을 찾을 수 없습니다.")
                return

            target_session_id = target_session["full_session_id"]
            target_model = target_session.get("model", "sonnet")

            # 세션 전환
            self.sessions.set_current(user_id, target_session_id)

            self._temp_pending = None
            await query.edit_message_text(
                f"⚡ <b>세션 전환됨</b>\n\n"
                f"💬 <code>{truncate_message(message, 40)}</code>\n\n"
                f"처리를 시작합니다...",
                parse_mode="HTML"
            )

            # 백그라운드에서 처리
            asyncio.create_task(
                self._process_alternative_session_request(
                    bot=bot,
                    chat_id=chat_id,
                    user_id=user_id,
                    session_id=target_session_id,
                    message=message,
                    model=target_model,
                    is_new_session=False,
                )
            )
            return

        if action == "new":
            # 새 세션 생성
            new_model = parts[2] if len(parts) > 2 else "sonnet"

            self._temp_pending = None
            await query.edit_message_text(
                f"➕ <b>새 {new_model} 세션 생성 중...</b>\n\n"
                f"💬 <code>{truncate_message(message, 40)}</code>",
                parse_mode="HTML"
            )

            # 새 세션 생성
            new_session_id = await self.claude.create_session()
            if not new_session_id:
                await query.message.reply_text("❌ 세션 생성 실패. 다시 시도해주세요.")
                return

            # 세션 저장 및 전환
            self.sessions.create_session(user_id, new_session_id, message, model=new_model)

            # 백그라운드에서 처리
            asyncio.create_task(
                self._process_alternative_session_request(
                    bot=bot,
                    chat_id=chat_id,
                    user_id=user_id,
                    session_id=new_session_id,
                    message=message,
                    model=new_model,
                    is_new_session=True,
                )
            )
            return

        await query.edit_message_text("❌ 알 수 없는 명령입니다.")
