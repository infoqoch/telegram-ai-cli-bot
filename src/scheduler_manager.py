"""Scheduler Manager - 중앙화된 스케줄러 관리.

단일 job_queue를 통해 모든 스케줄 작업을 관리.
플러그인과 기타 기능이 시작 시 또는 런타임에 작업 등록 가능.
"""

from dataclasses import dataclass, field
from datetime import time
from typing import TYPE_CHECKING, Callable, Optional, Any
from zoneinfo import ZoneInfo

from src.logging_config import logger

if TYPE_CHECKING:
    from telegram.ext import Application, Job


# 한국 시간대
KST = ZoneInfo("Asia/Seoul")


@dataclass
class ScheduledJob:
    """등록된 스케줄 작업 정보."""
    name: str
    callback: Callable
    schedule_type: str  # "daily", "repeating", "once"
    schedule_info: str  # 사람이 읽을 수 있는 스케줄 설명
    owner: str  # 등록한 모듈/플러그인 이름
    job: Optional["Job"] = None  # telegram.ext.Job 객체
    enabled: bool = True
    metadata: dict = field(default_factory=dict)


class SchedulerManager:
    """중앙화된 스케줄러 매니저 (싱글톤)."""

    _instance: Optional["SchedulerManager"] = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return
        self._initialized = True
        self._app: Optional["Application"] = None
        self._jobs: dict[str, ScheduledJob] = {}  # name -> ScheduledJob
        logger.debug("[SchedulerManager] 초기화됨")

    def set_app(self, app: "Application") -> None:
        """Application 설정 (job_queue 접근용)."""
        self._app = app
        logger.info("[SchedulerManager] Application 연결됨")

    @property
    def job_queue(self):
        """telegram job_queue 접근."""
        if not self._app:
            raise RuntimeError("Application이 설정되지 않음. set_app()을 먼저 호출하세요.")
        return self._app.job_queue

    def register_daily(
        self,
        name: str,
        callback: Callable,
        time_of_day: time,
        owner: str,
        *,
        days: tuple = (0, 1, 2, 3, 4, 5, 6),  # 매일
        data: Any = None,
        metadata: dict = None,
    ) -> bool:
        """매일 특정 시각에 실행되는 작업 등록.

        Args:
            name: 작업 고유 이름
            callback: 실행할 콜백 함수
            time_of_day: 실행 시각 (KST)
            owner: 등록한 모듈/플러그인 이름
            days: 실행할 요일 (0=월요일, 6=일요일)
            data: 콜백에 전달할 데이터
            metadata: 추가 메타데이터

        Returns:
            등록 성공 여부
        """
        if name in self._jobs:
            logger.warning(f"[SchedulerManager] 작업 '{name}' 이미 존재 - 덮어쓰기")
            self.unregister(name)

        try:
            job = self.job_queue.run_daily(
                callback,
                time=time_of_day,
                days=days,
                name=name,
                data=data,
            )

            schedule_info = f"매일 {time_of_day.strftime('%H:%M')} KST"
            if days != (0, 1, 2, 3, 4, 5, 6):
                day_names = ["월", "화", "수", "목", "금", "토", "일"]
                schedule_info = f"{','.join(day_names[d] for d in days)} {time_of_day.strftime('%H:%M')} KST"

            self._jobs[name] = ScheduledJob(
                name=name,
                callback=callback,
                schedule_type="daily",
                schedule_info=schedule_info,
                owner=owner,
                job=job,
                metadata=metadata or {},
            )

            logger.info(f"[SchedulerManager] 작업 등록: {name} ({schedule_info}, owner={owner})")
            return True

        except Exception as e:
            logger.error(f"[SchedulerManager] 작업 등록 실패: {name} - {e}")
            return False

    def register_repeating(
        self,
        name: str,
        callback: Callable,
        interval: float,
        owner: str,
        *,
        first: float = None,
        data: Any = None,
        metadata: dict = None,
    ) -> bool:
        """일정 간격으로 반복 실행되는 작업 등록.

        Args:
            name: 작업 고유 이름
            callback: 실행할 콜백 함수
            interval: 실행 간격 (초)
            owner: 등록한 모듈/플러그인 이름
            first: 첫 실행까지 대기 시간 (초)
            data: 콜백에 전달할 데이터
            metadata: 추가 메타데이터
        """
        if name in self._jobs:
            logger.warning(f"[SchedulerManager] 작업 '{name}' 이미 존재 - 덮어쓰기")
            self.unregister(name)

        try:
            job = self.job_queue.run_repeating(
                callback,
                interval=interval,
                first=first,
                name=name,
                data=data,
            )

            if interval >= 3600:
                schedule_info = f"매 {int(interval // 3600)}시간마다"
            elif interval >= 60:
                schedule_info = f"매 {int(interval // 60)}분마다"
            else:
                schedule_info = f"매 {int(interval)}초마다"

            self._jobs[name] = ScheduledJob(
                name=name,
                callback=callback,
                schedule_type="repeating",
                schedule_info=schedule_info,
                owner=owner,
                job=job,
                metadata=metadata or {},
            )

            logger.info(f"[SchedulerManager] 작업 등록: {name} ({schedule_info}, owner={owner})")
            return True

        except Exception as e:
            logger.error(f"[SchedulerManager] 작업 등록 실패: {name} - {e}")
            return False

    def register_once(
        self,
        name: str,
        callback: Callable,
        when: float,
        owner: str,
        *,
        data: Any = None,
        metadata: dict = None,
    ) -> bool:
        """일회성 작업 등록.

        Args:
            name: 작업 고유 이름
            callback: 실행할 콜백 함수
            when: 실행까지 대기 시간 (초)
            owner: 등록한 모듈/플러그인 이름
        """
        if name in self._jobs:
            logger.warning(f"[SchedulerManager] 작업 '{name}' 이미 존재 - 덮어쓰기")
            self.unregister(name)

        try:
            job = self.job_queue.run_once(
                callback,
                when=when,
                name=name,
                data=data,
            )

            schedule_info = f"{int(when)}초 후 1회"

            self._jobs[name] = ScheduledJob(
                name=name,
                callback=callback,
                schedule_type="once",
                schedule_info=schedule_info,
                owner=owner,
                job=job,
                metadata=metadata or {},
            )

            logger.info(f"[SchedulerManager] 작업 등록: {name} ({schedule_info}, owner={owner})")
            return True

        except Exception as e:
            logger.error(f"[SchedulerManager] 작업 등록 실패: {name} - {e}")
            return False

    def unregister(self, name: str) -> bool:
        """작업 등록 해제.

        Args:
            name: 제거할 작업 이름

        Returns:
            성공 여부
        """
        if name not in self._jobs:
            logger.warning(f"[SchedulerManager] 작업 '{name}' 없음")
            return False

        job_info = self._jobs[name]
        if job_info.job:
            job_info.job.schedule_removal()

        del self._jobs[name]
        logger.info(f"[SchedulerManager] 작업 해제: {name}")
        return True

    def unregister_by_owner(self, owner: str) -> int:
        """특정 owner의 모든 작업 해제.

        Args:
            owner: 모듈/플러그인 이름

        Returns:
            해제된 작업 수
        """
        to_remove = [name for name, job in self._jobs.items() if job.owner == owner]
        for name in to_remove:
            self.unregister(name)
        return len(to_remove)

    def get_job(self, name: str) -> Optional[ScheduledJob]:
        """작업 정보 조회."""
        return self._jobs.get(name)

    def list_jobs(self) -> list[ScheduledJob]:
        """모든 등록된 작업 목록."""
        return list(self._jobs.values())

    def list_jobs_by_owner(self, owner: str) -> list[ScheduledJob]:
        """특정 owner의 작업 목록."""
        return [job for job in self._jobs.values() if job.owner == owner]

    def get_status_text(self) -> str:
        """등록된 작업 현황 텍스트 생성."""
        if not self._jobs:
            return "등록된 스케줄 작업 없음"

        lines = [f"📅 <b>스케줄 작업</b> ({len(self._jobs)}개)\n"]

        # owner별로 그룹화
        by_owner: dict[str, list[ScheduledJob]] = {}
        for job in self._jobs.values():
            if job.owner not in by_owner:
                by_owner[job.owner] = []
            by_owner[job.owner].append(job)

        for owner, jobs in sorted(by_owner.items()):
            lines.append(f"\n<b>{owner}</b>:")
            for job in jobs:
                status = "✅" if job.enabled else "⏸"
                lines.append(f"  {status} {job.name}: {job.schedule_info}")

        return "\n".join(lines)

    def get_system_jobs_text(self) -> str:
        """ScheduleAdapter 외 시스템 잡 텍스트 생성."""
        system_jobs = [
            job for job in self._jobs.values()
            if job.owner != "ScheduleAdapter"
        ]
        if not system_jobs:
            return ""

        lines = ["\n\n⚙️ <b>시스템 작업</b>"]
        for job in system_jobs:
            lines.append(f"  {job.schedule_info} - {job.name}")

        return "\n".join(lines)


# 전역 싱글톤 인스턴스
scheduler_manager = SchedulerManager()
