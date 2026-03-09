"""Schedule manager adapter over the repository-backed schedule store."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Callable, Coroutine, Optional

from src.bot.formatters import escape_html
from src.logging_config import logger
from src.schedule_utils import (
    build_daily_cron,
    next_occurrence,
    normalize_schedule_type,
    normalize_trigger_type,
)

if TYPE_CHECKING:
    from src.repository.repository import Repository, Schedule


@dataclass
class ScheduleData:
    """Compatibility wrapper used by legacy callers and tests."""

    schedule: "Schedule"

    def __getattr__(self, item: str):
        return getattr(self.schedule, item)

    def to_dict(self) -> dict[str, Any]:
        return self.schedule.to_dict()


ScheduleExecutor = Callable[["Schedule"], Coroutine[Any, Any, None]]


class ScheduleManagerAdapter:
    """Repository-backed schedule manager with runtime registration hooks."""

    def __init__(
        self,
        repo: "Repository",
        scheduler_manager: Any = None,
        executor: Optional[ScheduleExecutor] = None,
    ):
        self._repo = repo
        self._scheduler_manager = scheduler_manager
        self._executor = executor

    def set_scheduler_manager(self, scheduler_manager: Any) -> None:
        """Attach the runtime scheduler manager."""
        self._scheduler_manager = scheduler_manager

    def set_executor(self, executor: ScheduleExecutor) -> None:
        """Attach the async schedule executor."""
        self._executor = executor

    def add(
        self,
        user_id: str,
        chat_id: int,
        hour: int,
        minute: int,
        message: str,
        name: str,
        schedule_type: str = "chat",
        trigger_type: str = "cron",
        cron_expr: Optional[str] = None,
        run_at_local: Optional[str] = None,
        ai_provider: str = "claude",
        model: str = "sonnet",
        workspace_path: Optional[str] = None,
        plugin_name: Optional[str] = None,
        action_name: Optional[str] = None,
    ) -> ScheduleData:
        """Persist and optionally register one schedule."""
        normalized_type = normalize_schedule_type(schedule_type)
        normalized_trigger = normalize_trigger_type(trigger_type)

        resolved_run_at = run_at_local
        resolved_cron = cron_expr
        if normalized_trigger == "once":
            if not resolved_run_at:
                resolved_run_at = next_occurrence(hour, minute).isoformat()
            resolved_cron = None
        elif not resolved_cron:
            resolved_cron = build_daily_cron(hour, minute)

        schedule = self._repo.add_schedule(
            user_id=user_id,
            chat_id=chat_id,
            hour=hour,
            minute=minute,
            message=message,
            name=name,
            schedule_type=normalized_type,
            trigger_type=normalized_trigger,
            cron_expr=resolved_cron,
            run_at_local=resolved_run_at,
            ai_provider=ai_provider,
            model=model,
            workspace_path=workspace_path,
            plugin_name=plugin_name,
            action_name=action_name,
        )

        wrapped = ScheduleData(schedule)
        if self._scheduler_manager and self._executor and schedule.enabled:
            self._register_schedule(schedule)
        return wrapped

    def remove(self, schedule_id: str) -> bool:
        """Remove one schedule from runtime and storage."""
        if self._scheduler_manager:
            self._unregister_schedule(schedule_id)
        return self._repo.remove_schedule(schedule_id)

    def toggle(self, schedule_id: str) -> Optional[bool]:
        """Toggle one schedule and sync runtime registration."""
        new_state = self._repo.toggle_schedule(schedule_id)
        if new_state is None:
            return None

        if not self._scheduler_manager or not self._executor:
            return new_state

        if new_state:
            schedule = self._repo.get_schedule(schedule_id)
            if schedule:
                if schedule.trigger_type == "once":
                    self._repo.update_schedule_trigger(
                        schedule_id,
                        trigger_type="once",
                        cron_expr=None,
                        run_at_local=next_occurrence(schedule.hour, schedule.minute).isoformat(),
                        hour=schedule.hour,
                        minute=schedule.minute,
                    )
                    schedule = self._repo.get_schedule(schedule_id)
                self._register_schedule(schedule)
        else:
            self._unregister_schedule(schedule_id)
        return new_state

    def update_time(self, schedule_id: str, hour: int, minute: int) -> bool:
        """Update the visible time fields while preserving the trigger type."""
        current = self._repo.get_schedule(schedule_id)
        if not current:
            return False

        trigger_type = normalize_trigger_type(current.trigger_type)
        cron_expr = build_daily_cron(hour, minute) if trigger_type == "cron" else None
        run_at_local = next_occurrence(hour, minute).isoformat() if trigger_type == "once" else None

        result = self._repo.update_schedule_trigger(
            schedule_id,
            trigger_type=trigger_type,
            cron_expr=cron_expr,
            run_at_local=run_at_local,
            hour=hour,
            minute=minute,
        )
        if not result:
            return False

        if not self._scheduler_manager or not self._executor:
            return True

        self._unregister_schedule(schedule_id)
        updated = self._repo.get_schedule(schedule_id)
        if updated and updated.enabled:
            self._register_schedule(updated)
        return True

    def get(self, schedule_id: str) -> Optional[ScheduleData]:
        """Return one schedule."""
        schedule = self._repo.get_schedule(schedule_id)
        return ScheduleData(schedule) if schedule else None

    def list_by_user(self, user_id: str) -> list[ScheduleData]:
        """List one user's schedules ordered by next run."""
        schedules = self._sort_schedules(self._repo.list_schedules_by_user(user_id))
        return [ScheduleData(schedule) for schedule in schedules]

    def list_all(self) -> list[ScheduleData]:
        """List every schedule ordered by next run."""
        schedules = self._sort_schedules(self._repo.list_all_schedules())
        return [ScheduleData(schedule) for schedule in schedules]

    def update_run(
        self,
        schedule_id: str,
        last_run: Optional[str] = None,
        last_error: Optional[str] = None,
    ) -> None:
        """Persist one execution result and auto-disable successful one-time jobs."""
        schedule = self._repo.get_schedule(schedule_id)
        if not schedule:
            return

        timestamp = last_run or datetime.now(timezone.utc).isoformat()
        self._repo.update_schedule_run(schedule_id, timestamp, last_error)

        if schedule.trigger_type == "once" and last_error is None and schedule.enabled:
            self._repo.toggle_schedule(schedule_id)
            if self._scheduler_manager:
                self._unregister_schedule(schedule_id)

    def register_all_to_scheduler(self) -> int:
        """Register all enabled schedules into the live scheduler runtime."""
        if not self._scheduler_manager or not self._executor:
            logger.warning("Scheduler manager or executor not set")
            return 0

        count = 0
        for schedule in self._repo.list_enabled_schedules():
            if self._register_schedule(schedule):
                count += 1
        logger.info(f"Registered {count} schedules to scheduler")
        return count

    def get_schedule_summary(self, user_id: str) -> str:
        """Build the `/scheduler` list body for one user."""
        schedules = self.list_by_user(user_id)
        if not schedules:
            return "No scheduled tasks."

        lines = ["<b>Your Schedules</b>"]
        for schedule in schedules:
            status = "🟢" if schedule.enabled else "⏸"
            lines.append(f"{status} {schedule.type_emoji} <b>{escape_html(schedule.name)}</b>")
            lines.append(f"  Next: {escape_html(schedule.next_run_text)}")
        return "\n".join(lines)

    def get_status_text(self, user_id: str) -> str:
        """Compatibility alias for summary output."""
        return self.get_schedule_summary(user_id)

    def _register_schedule(self, schedule: "Schedule") -> bool:
        """Register one schedule with the runtime scheduler."""
        if not self._scheduler_manager or not self._executor:
            return False

        job_name = f"schedule_{schedule.id}"

        async def job_callback(context) -> None:
            repo_schedule = self._repo.get_schedule(schedule.id)
            if not repo_schedule or not repo_schedule.enabled:
                logger.warning(f"[ScheduleAdapter] skipped disabled schedule: {schedule.id}")
                return
            await self._executor(repo_schedule)

        metadata = {"schedule_id": schedule.id}
        if schedule.trigger_type == "once" and schedule.run_at_local:
            if schedule.next_run_at is None:
                logger.warning(f"[ScheduleAdapter] stale one-time schedule disabled: {schedule.id}")
                if schedule.enabled:
                    self._repo.toggle_schedule(schedule.id)
                return False
            return self._scheduler_manager.register_once_at(
                name=job_name,
                callback=job_callback,
                when=schedule.run_at_local,
                owner="ScheduleAdapter",
                metadata=metadata,
            )

        cron_expr = schedule.cron_expr or build_daily_cron(schedule.hour, schedule.minute)
        return self._scheduler_manager.register_cron(
            name=job_name,
            callback=job_callback,
            cron_expr=cron_expr,
            owner="ScheduleAdapter",
            metadata=metadata,
        )

    def _unregister_schedule(self, schedule_id: str) -> None:
        """Remove one runtime job if present."""
        if not self._scheduler_manager:
            return
        self._scheduler_manager.unregister(f"schedule_{schedule_id}")

    @staticmethod
    def _sort_schedules(schedules: list["Schedule"]) -> list["Schedule"]:
        """Sort schedules by next run, then by name for stable UI output."""
        return sorted(
            schedules,
            key=lambda item: (
                item.next_run_at is None,
                item.next_run_at or "",
                item.name.lower(),
            ),
        )
