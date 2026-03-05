"""Schedule manager adapter for backward compatibility."""

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable, Coroutine, Optional

from ..repository import Repository, Schedule

logger = logging.getLogger(__name__)


@dataclass
class ScheduleData:
    """Schedule data for backward compatibility."""
    id: str
    user_id: str
    chat_id: int
    hour: int
    minute: int
    message: str
    name: str
    type: str
    model: str
    workspace_path: Optional[str]
    enabled: bool
    created_at: str
    last_run: Optional[str]
    last_error: Optional[str]
    run_count: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "user_id": self.user_id,
            "chat_id": self.chat_id,
            "hour": self.hour,
            "minute": self.minute,
            "message": self.message,
            "name": self.name,
            "type": self.type,
            "model": self.model,
            "workspace_path": self.workspace_path,
            "enabled": self.enabled,
            "created_at": self.created_at,
            "last_run": self.last_run,
            "last_error": self.last_error,
            "run_count": self.run_count,
        }

    @classmethod
    def from_repo_schedule(cls, s: Schedule) -> "ScheduleData":
        return cls(
            id=s.id,
            user_id=s.user_id,
            chat_id=s.chat_id,
            hour=s.hour,
            minute=s.minute,
            message=s.message,
            name=s.name,
            type=s.type,
            model=s.model,
            workspace_path=s.workspace_path,
            enabled=s.enabled,
            created_at=s.created_at,
            last_run=s.last_run,
            last_error=s.last_error,
            run_count=s.run_count,
        )


# Type alias for schedule executor callback
ScheduleExecutor = Callable[[Schedule], Coroutine[Any, Any, None]]


class ScheduleManagerAdapter:
    """Adapter that provides ScheduleManager-compatible interface over Repository.

    This adapter maintains the same API as the original ScheduleManager class
    to ensure backward compatibility with existing code.
    """

    def __init__(
        self,
        repo: Repository,
        scheduler_manager: Any = None,
        executor: Optional[ScheduleExecutor] = None
    ):
        self._repo = repo
        self._scheduler_manager = scheduler_manager
        self._executor = executor

    def set_scheduler_manager(self, scheduler_manager: Any) -> None:
        """Set scheduler manager instance."""
        self._scheduler_manager = scheduler_manager

    def set_executor(self, executor: ScheduleExecutor) -> None:
        """Set schedule executor callback."""
        self._executor = executor

    def add(
        self,
        user_id: str,
        chat_id: int,
        hour: int,
        minute: int,
        message: str,
        name: str,
        schedule_type: str = "claude",
        model: str = "sonnet",
        workspace_path: Optional[str] = None
    ) -> ScheduleData:
        """Add a new schedule."""
        schedule = self._repo.add_schedule(
            user_id=user_id,
            chat_id=chat_id,
            hour=hour,
            minute=minute,
            message=message,
            name=name,
            schedule_type=schedule_type,
            model=model,
            workspace_path=workspace_path
        )

        result = ScheduleData.from_repo_schedule(schedule)

        # Register with scheduler if available
        if self._scheduler_manager and self._executor:
            self._register_schedule(result)

        return result

    def remove(self, schedule_id: str) -> bool:
        """Remove schedule."""
        # Unregister from scheduler first
        if self._scheduler_manager:
            self._unregister_schedule(schedule_id)

        return self._repo.remove_schedule(schedule_id)

    def toggle(self, schedule_id: str) -> Optional[bool]:
        """Toggle schedule enabled state."""
        new_state = self._repo.toggle_schedule(schedule_id)

        if new_state is not None and self._scheduler_manager:
            schedule = self._repo.get_schedule(schedule_id)
            if schedule:
                if new_state:
                    self._register_schedule(ScheduleData.from_repo_schedule(schedule))
                else:
                    self._unregister_schedule(schedule_id)

        return new_state

    def get(self, schedule_id: str) -> Optional[ScheduleData]:
        """Get schedule by ID."""
        schedule = self._repo.get_schedule(schedule_id)
        return ScheduleData.from_repo_schedule(schedule) if schedule else None

    def list_by_user(self, user_id: str) -> list[ScheduleData]:
        """List schedules for user."""
        schedules = self._repo.list_schedules_by_user(user_id)
        return [ScheduleData.from_repo_schedule(s) for s in schedules]

    def list_all(self) -> list[ScheduleData]:
        """List all schedules."""
        schedules = self._repo.list_all_schedules()
        return [ScheduleData.from_repo_schedule(s) for s in schedules]

    def update_run(
        self,
        schedule_id: str,
        last_run: Optional[str] = None,
        last_error: Optional[str] = None
    ) -> None:
        """Update schedule after run."""
        if last_run is None:
            last_run = datetime.utcnow().isoformat()
        self._repo.update_schedule_run(schedule_id, last_run, last_error)

    def register_all_to_scheduler(self) -> int:
        """Register all enabled schedules to scheduler."""
        if not self._scheduler_manager or not self._executor:
            logger.warning("Scheduler manager or executor not set")
            return 0

        schedules = self._repo.list_enabled_schedules()
        count = 0

        for schedule in schedules:
            try:
                self._register_schedule(ScheduleData.from_repo_schedule(schedule))
                count += 1
            except Exception as e:
                logger.error(f"Failed to register schedule {schedule.id}: {e}")

        logger.info(f"Registered {count} schedules to scheduler")
        return count

    def _register_schedule(self, schedule: ScheduleData) -> None:
        """Register single schedule with scheduler."""
        if not self._scheduler_manager or not self._executor:
            return

        job_id = f"schedule_{schedule.id}"

        # Remove existing job if any
        try:
            self._scheduler_manager.remove_job(job_id)
        except Exception:
            pass

        # Add new job
        async def job_wrapper():
            repo_schedule = self._repo.get_schedule(schedule.id)
            if repo_schedule and repo_schedule.enabled:
                await self._executor(repo_schedule)

        self._scheduler_manager.add_job(
            job_wrapper,
            "cron",
            hour=schedule.hour,
            minute=schedule.minute,
            id=job_id,
            replace_existing=True,
            timezone="Asia/Seoul"
        )

    def _unregister_schedule(self, schedule_id: str) -> None:
        """Unregister schedule from scheduler."""
        if not self._scheduler_manager:
            return

        job_id = f"schedule_{schedule_id}"
        try:
            self._scheduler_manager.remove_job(job_id)
        except Exception:
            pass

    def get_schedule_summary(self, user_id: str) -> str:
        """Get schedule summary for display."""
        schedules = self._repo.list_schedules_by_user(user_id)

        if not schedules:
            return "예약된 작업이 없습니다."

        lines = []
        for s in schedules:
            status = "✅" if s.enabled else "⏸"
            type_icon = "📂" if s.type == "workspace" else "💬"
            time_str = f"{s.hour:02d}:{s.minute:02d}"
            lines.append(f"{status} {type_icon} <b>{s.name}</b> - {time_str}")

        return "\n".join(lines)
