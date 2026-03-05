"""스케줄러 - 경로 기반 예약 작업 관리.

세션 독립적인 스케줄 시스템:
- claude: 일반 스케줄 (새 세션)
- workspace: 워크스페이스 스케줄 (CLAUDE.md 적용)
"""

import json
import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime, time
from pathlib import Path
from typing import TYPE_CHECKING, Optional, Callable

from src.logging_config import logger
from src.scheduler_manager import scheduler_manager

if TYPE_CHECKING:
    from telegram import Bot
    from src.claude.client import ClaudeClient


# 한국 시간대
from zoneinfo import ZoneInfo
KST = ZoneInfo("Asia/Seoul")

# 선택 가능한 시간대 (06:00 ~ 22:00)
AVAILABLE_HOURS = list(range(6, 23))


@dataclass
class Schedule:
    """예약 작업."""
    id: str
    user_id: str
    chat_id: int
    hour: int  # 0-23 (KST)
    minute: int  # 0-59
    message: str
    name: str  # 스케줄 이름 (표시용)

    # 타입: "claude" (일반) 또는 "workspace" (워크스페이스 경로)
    type: str = "claude"

    # 모델 선택 (기본: sonnet)
    model: str = "sonnet"

    # workspace 타입 전용
    workspace_path: Optional[str] = None

    enabled: bool = True
    created_at: str = field(default_factory=lambda: datetime.now(KST).isoformat())
    last_run: Optional[str] = None
    last_error: Optional[str] = None
    run_count: int = 0

    @property
    def time_str(self) -> str:
        """시간 문자열 (HH:MM)."""
        return f"{self.hour:02d}:{self.minute:02d}"

    @property
    def schedule_time(self) -> time:
        """datetime.time 객체."""
        return time(self.hour, self.minute, tzinfo=KST)

    @property
    def type_emoji(self) -> str:
        """타입 이모지."""
        return "📁" if self.type == "workspace" else "💬"

    def to_dict(self) -> dict:
        """딕셔너리로 변환."""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "Schedule":
        """딕셔너리에서 생성 (하위 호환성 포함)."""
        # 기존 session_schedule 데이터 마이그레이션
        if "session_id" in data:
            # 기존 세션 기반 → claude 타입으로 변환
            return cls(
                id=data.get("id", str(uuid.uuid4())[:8]),
                user_id=data.get("user_id", ""),
                chat_id=data.get("chat_id", 0),
                hour=data.get("hour", 9),
                minute=data.get("minute", 0),
                message=data.get("message", ""),
                name=data.get("session_name", data.get("name", "스케줄")),
                type="claude",
                model=data.get("model", "sonnet"),
                workspace_path=None,
                enabled=data.get("enabled", True),
                created_at=data.get("created_at", datetime.now(KST).isoformat()),
                last_run=data.get("last_run"),
                last_error=data.get("last_error"),
                run_count=data.get("run_count", 0),
            )
        # 하위 호환성: project_path → workspace_path, type: project → workspace
        if "project_path" in data and "workspace_path" not in data:
            data["workspace_path"] = data.pop("project_path")
        if data.get("type") == "project":
            data["type"] = "workspace"
        return cls(**data)


class ScheduleManager:
    """스케줄 매니저."""

    OWNER = "Schedule"

    def __init__(self, data_file: Path, claude_client: "ClaudeClient" = None):
        """
        Args:
            data_file: 스케줄 데이터 저장 파일 경로
            claude_client: Claude CLI 클라이언트
        """
        self.data_file = data_file
        self.claude = claude_client
        self._bot: Optional["Bot"] = None
        self._schedules: dict[str, Schedule] = {}
        self._load()

    def set_bot(self, bot: "Bot") -> None:
        """Telegram Bot 설정."""
        self._bot = bot

    def set_claude_client(self, client: "ClaudeClient") -> None:
        """Claude 클라이언트 설정."""
        self.claude = client

    def _load(self) -> None:
        """파일에서 스케줄 로드."""
        if not self.data_file.exists():
            self._schedules = {}
            return

        try:
            with open(self.data_file, "r", encoding="utf-8") as f:
                data = json.load(f)

            self._schedules = {}
            for item in data.get("schedules", []):
                schedule = Schedule.from_dict(item)
                self._schedules[schedule.id] = schedule

            logger.info(f"[Schedule] {len(self._schedules)}개 스케줄 로드됨")
        except Exception as e:
            logger.error(f"[Schedule] 로드 실패: {e}")
            self._schedules = {}

    def _save(self) -> None:
        """파일에 스케줄 저장."""
        try:
            self.data_file.parent.mkdir(parents=True, exist_ok=True)
            data = {
                "schedules": [s.to_dict() for s in self._schedules.values()]
            }
            with open(self.data_file, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            logger.debug(f"[Schedule] 저장됨: {len(self._schedules)}개")
        except Exception as e:
            logger.error(f"[Schedule] 저장 실패: {e}")

    def register_all_to_scheduler(self) -> int:
        """모든 스케줄을 SchedulerManager에 등록."""
        scheduler_manager.unregister_by_owner(self.OWNER)

        count = 0
        for schedule in self._schedules.values():
            if schedule.enabled:
                if self._register_to_scheduler(schedule):
                    count += 1

        logger.info(f"[Schedule] {count}개 스케줄 SchedulerManager에 등록됨")
        return count

    def _register_to_scheduler(self, schedule: Schedule) -> bool:
        """단일 스케줄을 SchedulerManager에 등록."""
        job_name = f"schedule_{schedule.id}"

        return scheduler_manager.register_daily(
            name=job_name,
            callback=self._create_callback(schedule),
            time_of_day=schedule.schedule_time,
            owner=self.OWNER,
            metadata={
                "schedule_id": schedule.id,
                "name": schedule.name,
                "type": schedule.type,
                "deletable": True,
            },
        )

    def _create_callback(self, schedule: Schedule) -> Callable:
        """스케줄 실행 콜백 생성."""
        async def callback(context) -> None:
            # 최신 스케줄 정보 사용 (런타임 변경 반영)
            current_schedule = self._schedules.get(schedule.id)
            if current_schedule and current_schedule.enabled:
                await self._execute_schedule(current_schedule)
        return callback

    async def _execute_schedule(self, schedule: Schedule) -> None:
        """스케줄 실행."""
        logger.info(f"[Schedule] 실행: {schedule.name} @ {schedule.time_str} (type={schedule.type})")

        if not self._bot or not self.claude:
            logger.error("[Schedule] Bot 또는 Claude 클라이언트 없음")
            return

        try:
            # 실행 알림
            type_label = "워크스페이스" if schedule.type == "workspace" else "스케줄"
            path_info = f"\n📁 경로: <code>{schedule.workspace_path}</code>" if schedule.workspace_path else ""

            await self._bot.send_message(
                chat_id=schedule.chat_id,
                text=(
                    f"⏰ <b>예약 작업 실행</b>\n\n"
                    f"{schedule.type_emoji} <b>{schedule.name}</b> ({type_label})"
                    f"{path_info}\n"
                    f"💬 <code>{schedule.message[:50]}{'...' if len(schedule.message) > 50 else ''}</code>\n\n"
                    f"처리 중..."
                ),
                parse_mode="HTML",
            )

            # Claude 호출 (새 세션, 세션 ID 없음)
            response = await self.claude.chat(
                message=schedule.message,
                session_id=None,  # 항상 새 세션
                model=schedule.model,
                workspace_path=schedule.workspace_path,  # workspace 타입이면 경로 전달
            )

            # 응답 전송
            if response.text:
                text = response.text
                max_len = 4000
                chunks = [text[i:i+max_len] for i in range(0, len(text), max_len)]

                for chunk in chunks:
                    try:
                        await self._bot.send_message(
                            chat_id=schedule.chat_id,
                            text=chunk,
                            parse_mode="HTML",
                        )
                    except Exception:
                        await self._bot.send_message(
                            chat_id=schedule.chat_id,
                            text=chunk,
                        )

                # 성공 기록
                schedule.last_error = None

            elif response.error:
                error_msg = str(response.error.value if hasattr(response.error, 'value') else response.error)
                await self._bot.send_message(
                    chat_id=schedule.chat_id,
                    text=f"❌ 오류: {error_msg}",
                )
                schedule.last_error = error_msg

            # 실행 기록 업데이트
            schedule.last_run = datetime.now(KST).isoformat()
            schedule.run_count += 1
            self._save()

            logger.info(f"[Schedule] 완료: {schedule.name}")

        except Exception as e:
            logger.error(f"[Schedule] 실행 오류: {e}")
            schedule.last_error = str(e)
            self._save()

            try:
                await self._bot.send_message(
                    chat_id=schedule.chat_id,
                    text=f"❌ 예약 작업 실행 오류: {e}",
                )
            except Exception:
                pass

    def add(
        self,
        user_id: str,
        chat_id: int,
        name: str,
        hour: int,
        minute: int,
        message: str,
        schedule_type: str = "claude",
        model: str = "sonnet",
        workspace_path: Optional[str] = None,
    ) -> Schedule:
        """새 스케줄 추가."""
        schedule = Schedule(
            id=str(uuid.uuid4())[:8],
            user_id=user_id,
            chat_id=chat_id,
            hour=hour,
            minute=minute,
            message=message,
            name=name,
            type=schedule_type,
            model=model,
            workspace_path=workspace_path,
        )

        self._schedules[schedule.id] = schedule
        self._save()
        self._register_to_scheduler(schedule)

        logger.info(f"[Schedule] 추가: {name} @ {schedule.time_str} (type={schedule_type})")
        return schedule

    def remove(self, schedule_id: str) -> bool:
        """스케줄 삭제."""
        if schedule_id not in self._schedules:
            return False

        schedule = self._schedules[schedule_id]
        job_name = f"schedule_{schedule_id}"
        scheduler_manager.unregister(job_name)

        del self._schedules[schedule_id]
        self._save()

        logger.info(f"[Schedule] 삭제: {schedule.name} @ {schedule.time_str}")
        return True

    def toggle(self, schedule_id: str) -> Optional[bool]:
        """스케줄 활성화/비활성화 토글."""
        if schedule_id not in self._schedules:
            return None

        schedule = self._schedules[schedule_id]
        schedule.enabled = not schedule.enabled
        self._save()

        job_name = f"schedule_{schedule_id}"
        if schedule.enabled:
            self._register_to_scheduler(schedule)
        else:
            scheduler_manager.unregister(job_name)

        logger.info(f"[Schedule] {'활성화' if schedule.enabled else '비활성화'}: {schedule.name}")
        return schedule.enabled

    def get(self, schedule_id: str) -> Optional[Schedule]:
        """스케줄 조회."""
        return self._schedules.get(schedule_id)

    def list_by_user(self, user_id: str) -> list[Schedule]:
        """사용자별 스케줄 목록."""
        return [s for s in self._schedules.values() if s.user_id == user_id]

    def list_all(self) -> list[Schedule]:
        """모든 스케줄 목록."""
        return list(self._schedules.values())

    def get_status_text(self, user_id: str = None) -> str:
        """스케줄 현황 텍스트."""
        if user_id:
            schedules = self.list_by_user(user_id)
        else:
            schedules = self.list_all()

        if not schedules:
            return "📅 등록된 스케줄이 없습니다.\n\n➕ 추가하려면 아래 버튼을 누르세요."

        lines = [f"📅 <b>스케줄</b> ({len(schedules)}개)\n"]

        sorted_schedules = sorted(schedules, key=lambda s: (s.hour, s.minute))

        for s in sorted_schedules:
            status = "✅" if s.enabled else "⏸"
            type_info = f" 📁" if s.type == "workspace" else ""
            error_indicator = " ⚠️" if s.last_error else ""
            lines.append(
                f"{status} <b>{s.time_str}</b> → {s.name}{type_info}{error_indicator}\n"
                f"   💬 <i>{s.message[:30]}{'...' if len(s.message) > 30 else ''}</i>"
            )

        return "\n".join(lines)


# 전역 인스턴스
schedule_manager: Optional[ScheduleManager] = None


def init_schedule_manager(
    data_dir: Path,
    claude_client: "ClaudeClient" = None,
) -> ScheduleManager:
    """ScheduleManager 초기화."""
    global schedule_manager

    data_file = data_dir / "schedules.json"
    schedule_manager = ScheduleManager(
        data_file=data_file,
        claude_client=claude_client,
    )
    return schedule_manager


def get_schedule_manager() -> Optional[ScheduleManager]:
    """ScheduleManager 인스턴스 반환."""
    return schedule_manager
