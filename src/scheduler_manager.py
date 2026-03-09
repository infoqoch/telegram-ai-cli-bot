"""Shared runtime scheduler manager."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, time
from typing import TYPE_CHECKING, Any, Callable, Optional

from apscheduler.jobstores.base import JobLookupError
from apscheduler.triggers.cron import CronTrigger

from src.logging_config import logger
from src.schedule_utils import cron_description
from src.time_utils import format_local_datetime, get_app_timezone, get_app_timezone_label, parse_local_datetime

if TYPE_CHECKING:
    from telegram.ext import Application


@dataclass
class ScheduledJob:
    """Runtime metadata for one scheduled job."""

    name: str
    callback: Callable[..., Any]
    schedule_type: str
    schedule_info: str
    owner: str
    job: Optional[Any] = None
    enabled: bool = True
    metadata: dict = field(default_factory=dict)
    next_run_time: Optional[datetime] = None


class SchedulerManager:
    """Centralized wrapper around the Telegram JobQueue."""

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
        self._jobs: dict[str, ScheduledJob] = {}
        logger.debug("[SchedulerManager] 초기화됨")

    def set_app(self, app: "Application") -> None:
        """Connect the application so the shared JobQueue can be used."""
        self._app = app
        logger.info("[SchedulerManager] Application 연결됨")

    @property
    def job_queue(self):
        """Return the Telegram JobQueue or fail loudly if not wired yet."""
        if not self._app:
            raise RuntimeError("Application이 설정되지 않음. set_app()을 먼저 호출하세요.")
        return self._app.job_queue

    def register_daily(
        self,
        name: str,
        callback: Callable[..., Any],
        time_of_day: time,
        owner: str,
        *,
        days: tuple = (0, 1, 2, 3, 4, 5, 6),
        data: Any = None,
        metadata: Optional[dict] = None,
    ) -> bool:
        """Register one daily job."""
        if name in self._jobs:
            logger.warning(f"[SchedulerManager] 작업 '{name}' 이미 존재 - 덮어쓰기")
            self.unregister(name)

        try:
            tz = get_app_timezone()
            effective_time = time_of_day if time_of_day.tzinfo else time(
                time_of_day.hour,
                time_of_day.minute,
                time_of_day.second,
                time_of_day.microsecond,
                tzinfo=tz,
            )
            job = self.job_queue.run_daily(
                callback,
                time=effective_time,
                days=days,
                name=name,
                data=data,
            )

            timezone_label = get_app_timezone_label()
            schedule_info = f"Daily {effective_time.strftime('%H:%M')} {timezone_label}"
            if days != (0, 1, 2, 3, 4, 5, 6):
                # PTB documents run_daily days as Sunday=0 ... Saturday=6.
                day_names = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"]
                schedule_info = f"{','.join(day_names[d] for d in days)} {effective_time.strftime('%H:%M')} {timezone_label}"

            self._jobs[name] = ScheduledJob(
                name=name,
                callback=callback,
                schedule_type="daily",
                schedule_info=schedule_info,
                owner=owner,
                job=job,
                metadata=metadata or {},
                next_run_time=getattr(job, "next_t", None),
            )
            logger.info(f"[SchedulerManager] 작업 등록: {name} ({schedule_info}, owner={owner})")
            return True
        except Exception as exc:
            logger.error(f"[SchedulerManager] 작업 등록 실패: {name} - {exc}")
            return False

    def register_repeating(
        self,
        name: str,
        callback: Callable[..., Any],
        interval: float,
        owner: str,
        *,
        first: float | None = None,
        data: Any = None,
        metadata: Optional[dict] = None,
    ) -> bool:
        """Register one repeating job."""
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
                schedule_info = f"Every {int(interval // 3600)}h"
            elif interval >= 60:
                schedule_info = f"Every {int(interval // 60)}m"
            else:
                schedule_info = f"Every {int(interval)}s"

            self._jobs[name] = ScheduledJob(
                name=name,
                callback=callback,
                schedule_type="repeating",
                schedule_info=schedule_info,
                owner=owner,
                job=job,
                metadata=metadata or {},
                next_run_time=getattr(job, "next_t", None),
            )
            logger.info(f"[SchedulerManager] 작업 등록: {name} ({schedule_info}, owner={owner})")
            return True
        except Exception as exc:
            logger.error(f"[SchedulerManager] 작업 등록 실패: {name} - {exc}")
            return False

    def register_once(
        self,
        name: str,
        callback: Callable[..., Any],
        when: float,
        owner: str,
        *,
        data: Any = None,
        metadata: Optional[dict] = None,
    ) -> bool:
        """Register one one-shot job using a relative delay."""
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

            schedule_info = f"Once in {int(when)}s"
            self._jobs[name] = ScheduledJob(
                name=name,
                callback=callback,
                schedule_type="once",
                schedule_info=schedule_info,
                owner=owner,
                job=job,
                metadata=metadata or {},
                next_run_time=getattr(job, "next_t", None),
            )
            logger.info(f"[SchedulerManager] 작업 등록: {name} ({schedule_info}, owner={owner})")
            return True
        except Exception as exc:
            logger.error(f"[SchedulerManager] 작업 등록 실패: {name} - {exc}")
            return False

    def register_once_at(
        self,
        name: str,
        callback: Callable[..., Any],
        when,
        owner: str,
        *,
        data: Any = None,
        metadata: Optional[dict] = None,
    ) -> bool:
        """Register one one-shot job using an absolute local datetime."""
        if name in self._jobs:
            logger.warning(f"[SchedulerManager] 작업 '{name}' 이미 존재 - 덮어쓰기")
            self.unregister(name)

        try:
            target = parse_local_datetime(when)
            job = self.job_queue.run_once(
                callback,
                when=target,
                name=name,
                data=data,
            )

            schedule_info = f"Once at {format_local_datetime(target)}"
            self._jobs[name] = ScheduledJob(
                name=name,
                callback=callback,
                schedule_type="once",
                schedule_info=schedule_info,
                owner=owner,
                job=job,
                metadata=metadata or {},
                next_run_time=target,
            )
            logger.info(f"[SchedulerManager] 작업 등록: {name} ({schedule_info}, owner={owner})")
            return True
        except Exception as exc:
            logger.error(f"[SchedulerManager] 작업 등록 실패: {name} - {exc}")
            return False

    def register_cron(
        self,
        name: str,
        callback: Callable[..., Any],
        cron_expr: str,
        owner: str,
        *,
        data: Any = None,
        metadata: Optional[dict] = None,
    ) -> bool:
        """Register one cron-style job."""
        if name in self._jobs:
            logger.warning(f"[SchedulerManager] 작업 '{name}' 이미 존재 - 덮어쓰기")
            self.unregister(name)

        try:
            trigger = CronTrigger.from_crontab(cron_expr, timezone=get_app_timezone())
            job = self.job_queue.run_custom(
                callback,
                job_kwargs={"trigger": trigger},
                name=name,
                data=data,
            )

            now = datetime.now(get_app_timezone())
            self._jobs[name] = ScheduledJob(
                name=name,
                callback=callback,
                schedule_type="cron",
                schedule_info=cron_description(cron_expr),
                owner=owner,
                job=job,
                metadata=metadata or {},
                next_run_time=trigger.get_next_fire_time(None, now),
            )
            logger.info(f"[SchedulerManager] 작업 등록: {name} ({self._jobs[name].schedule_info}, owner={owner})")
            return True
        except Exception as exc:
            logger.error(f"[SchedulerManager] 작업 등록 실패: {name} - {exc}")
            return False

    def unregister(self, name: str) -> bool:
        """Remove one job by name."""
        if name not in self._jobs:
            logger.warning(f"[SchedulerManager] 작업 '{name}' 없음")
            return False

        job_info = self._jobs[name]
        if job_info.job:
            try:
                job_info.job.schedule_removal()
            except JobLookupError:
                logger.warning(f"[SchedulerManager] 작업 '{name}'는 이미 scheduler에서 제거됨")

        del self._jobs[name]
        logger.info(f"[SchedulerManager] 작업 해제: {name}")
        return True

    def unregister_by_owner(self, owner: str) -> int:
        """Remove all jobs registered by one owner."""
        names = [name for name, job in self._jobs.items() if job.owner == owner]
        for name in names:
            self.unregister(name)
        return len(names)

    def list_jobs(self) -> list[ScheduledJob]:
        """Return all registered jobs."""
        return list(self._jobs.values())

    def list_jobs_by_owner(self, owner: str) -> list[ScheduledJob]:
        """Return all jobs for one owner."""
        return [job for job in self._jobs.values() if job.owner == owner]

    def get_status_text(self) -> str:
        """Return a chat-facing summary for all registered jobs."""
        if not self._jobs:
            return "No scheduled jobs"

        lines = ["<b>Scheduled Jobs</b>"]
        for job in sorted(self._jobs.values(), key=lambda item: (item.owner, item.name)):
            status = "🟢" if job.enabled else "🔴"
            lines.append(f"  {status} <b>{job.owner}</b>: {job.name} ({job.schedule_info})")
        return "\n".join(lines)

    def get_system_jobs_text(self) -> str:
        """Return non-user scheduler jobs for the `/scheduler` screen."""
        system_jobs = [
            job for job in self._jobs.values()
            if job.owner != "ScheduleAdapter"
        ]
        if not system_jobs:
            return ""

        lines = ["\n\n⚙️ <b>System Jobs</b>"]
        for job in sorted(system_jobs, key=lambda item: (item.owner, item.name)):
            lines.append(f"  {job.schedule_info} - {job.name}")
        return "\n".join(lines)


scheduler_manager = SchedulerManager()
