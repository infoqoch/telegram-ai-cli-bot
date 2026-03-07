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
    plugin_name: Optional[str]
    action_name: Optional[str]
    enabled: bool
    created_at: str
    last_run: Optional[str]
    last_error: Optional[str]
    run_count: int

    @property
    def time_str(self) -> str:
        """Return formatted time string HH:MM KST."""
        return f"{self.hour:02d}:{self.minute:02d} KST"

    @property
    def type_emoji(self) -> str:
        """Return emoji based on schedule type."""
        if self.type == "workspace":
            return "📂"
        elif self.type == "plugin":
            return "🔌"
        return "💬"

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
            "plugin_name": self.plugin_name,
            "action_name": self.action_name,
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
            type=s.schedule_type,
            model=s.model,
            workspace_path=s.workspace_path,
            plugin_name=s.plugin_name,
            action_name=s.action_name,
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
        workspace_path: Optional[str] = None,
        plugin_name: Optional[str] = None,
        action_name: Optional[str] = None,
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
            workspace_path=workspace_path,
            plugin_name=plugin_name,
            action_name=action_name,
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

    def update_time(self, schedule_id: str, hour: int, minute: int) -> bool:
        """Update schedule time and re-register with scheduler."""
        result = self._repo.update_schedule_time(schedule_id, hour, minute)

        if result and self._scheduler_manager and self._executor:
            # Re-register with new time
            self._unregister_schedule(schedule_id)
            schedule = self._repo.get_schedule(schedule_id)
            if schedule and schedule.enabled:
                self._register_schedule(ScheduleData.from_repo_schedule(schedule))

        return result

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
        from datetime import time as dt_time
        from zoneinfo import ZoneInfo

        if not self._scheduler_manager or not self._executor:
            return

        job_name = f"schedule_{schedule.id}"
        KST = ZoneInfo("Asia/Seoul")

        # Create callback wrapper for telegram job_queue
        async def job_callback(context) -> None:
            logger.info(f"[ScheduleAdapter] 콜백 실행: {schedule.id} ({schedule.name})")
            repo_schedule = self._repo.get_schedule(schedule.id)
            if repo_schedule and repo_schedule.enabled:
                logger.info(f"[ScheduleAdapter] executor 호출: {schedule.id}")
                await self._executor(repo_schedule)
            else:
                logger.warning(f"[ScheduleAdapter] 스케줄 비활성화 또는 없음: {schedule.id}")

        # Register with SchedulerManager
        self._scheduler_manager.register_daily(
            name=job_name,
            callback=job_callback,
            time_of_day=dt_time(hour=schedule.hour, minute=schedule.minute, tzinfo=KST),
            owner="ScheduleAdapter",
            metadata={"schedule_id": schedule.id},
        )

    def _unregister_schedule(self, schedule_id: str) -> None:
        """Unregister schedule from scheduler."""
        if not self._scheduler_manager:
            return

        job_name = f"schedule_{schedule_id}"
        self._scheduler_manager.unregister(job_name)

    def get_schedule_summary(self, user_id: str) -> str:
        """Get schedule summary for display."""
        schedules = self._repo.list_schedules_by_user(user_id)

        if not schedules:
            return "No scheduled tasks."

        lines = []
        for s in schedules:
            status = "✅" if s.enabled else "⏸"
            type_icon = s.type_emoji
            lines.append(f"{status} {type_icon} <b>{s.name}</b> - {s.time_str}")

        return "\n".join(lines)

    def get_status_text(self, user_id: str) -> str:
        """Get schedule status text (alias for get_schedule_summary)."""
        return self.get_schedule_summary(user_id)
