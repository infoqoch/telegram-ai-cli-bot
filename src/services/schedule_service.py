"""Schedule service - scheduled task execution logic."""

from datetime import datetime
from typing import TYPE_CHECKING, Optional, Any

from src.logging_config import logger
from src.repository import Repository
from src.repository.repository import Schedule

if TYPE_CHECKING:
    from telegram import Bot
    from src.claude.client import ClaudeClient
    from src.scheduler_manager import SchedulerManager


class ScheduleService:
    """Schedule execution service.

    Handles scheduled task execution:
    - Execute Claude tasks on schedule
    - Send results to Telegram
    - Track execution history
    """

    def __init__(
        self,
        repo: Repository,
        claude_client: "ClaudeClient",
        scheduler_manager: "SchedulerManager",
    ):
        self._repo = repo
        self._claude = claude_client
        self._scheduler_manager = scheduler_manager
        self._bot: Optional["Bot"] = None

    def set_bot(self, bot: "Bot") -> None:
        """Set Telegram bot instance."""
        self._bot = bot

    async def execute_schedule(self, schedule: Schedule) -> None:
        """Execute a scheduled task."""
        import uuid

        try:
            session_id = f"schedule_{schedule.id}_{uuid.uuid4().hex[:8]}"
            workspace_path = None

            if schedule.type == "workspace" and schedule.workspace_path:
                workspace_path = schedule.workspace_path

            response = await self._claude.chat(
                message=schedule.message,
                session_id=session_id,
                model=schedule.model,
                cwd=workspace_path,
            )

            if self._bot and schedule.chat_id:
                max_len = 4000
                for i in range(0, len(response), max_len):
                    chunk = response[i:i + max_len]
                    await self._bot.send_message(
                        chat_id=schedule.chat_id,
                        text=f"📅 <b>{schedule.name}</b>\n\n{chunk}",
                        parse_mode="HTML",
                    )

            self._update_run(schedule.id)
            logger.info(f"Schedule {schedule.id} executed successfully")

        except Exception as e:
            self._update_run(schedule.id, last_error=str(e))
            logger.error(f"Schedule {schedule.id} failed: {e}")

    def _update_run(
        self,
        schedule_id: str,
        last_error: Optional[str] = None
    ) -> None:
        """Update schedule after run."""
        last_run = datetime.utcnow().isoformat()
        self._repo.update_schedule_run(schedule_id, last_run, last_error)

    def add_schedule(
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
    ) -> Schedule:
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
        )
        self._register_schedule(schedule)
        return schedule

    def remove_schedule(self, schedule_id: str) -> bool:
        """Remove schedule."""
        self._unregister_schedule(schedule_id)
        return self._repo.remove_schedule(schedule_id)

    def toggle_schedule(self, schedule_id: str) -> Optional[bool]:
        """Toggle schedule enabled state."""
        new_state = self._repo.toggle_schedule(schedule_id)

        if new_state is not None:
            schedule = self._repo.get_schedule(schedule_id)
            if schedule:
                if new_state:
                    self._register_schedule(schedule)
                else:
                    self._unregister_schedule(schedule_id)

        return new_state

    def get_schedule(self, schedule_id: str) -> Optional[Schedule]:
        """Get schedule by ID."""
        return self._repo.get_schedule(schedule_id)

    def list_by_user(self, user_id: str) -> list[Schedule]:
        """List schedules for user."""
        return self._repo.list_schedules_by_user(user_id)

    def list_all(self) -> list[Schedule]:
        """List all schedules."""
        return self._repo.list_all_schedules()

    def register_all(self) -> int:
        """Register all enabled schedules to scheduler."""
        schedules = self._repo.list_enabled_schedules()
        count = 0

        for schedule in schedules:
            try:
                self._register_schedule(schedule)
                count += 1
            except Exception as e:
                logger.error(f"Failed to register schedule {schedule.id}: {e}")

        logger.info(f"Registered {count} schedules to scheduler")
        return count

    def _register_schedule(self, schedule: Schedule) -> None:
        """Register single schedule with scheduler."""
        from datetime import time as dt_time

        job_name = f"schedule_{schedule.id}"

        async def job_callback(context) -> None:
            repo_schedule = self._repo.get_schedule(schedule.id)
            if repo_schedule and repo_schedule.enabled:
                await self.execute_schedule(repo_schedule)

        self._scheduler_manager.register_daily(
            name=job_name,
            callback=job_callback,
            time_of_day=dt_time(hour=schedule.hour, minute=schedule.minute),
            owner="ScheduleService",
            metadata={"schedule_id": schedule.id},
        )

    def _unregister_schedule(self, schedule_id: str) -> None:
        """Unregister schedule from scheduler."""
        job_name = f"schedule_{schedule_id}"
        self._scheduler_manager.unregister(job_name)

    def get_status_text(self, user_id: str) -> str:
        """Get schedule status text for display."""
        schedules = self._repo.list_schedules_by_user(user_id)

        if not schedules:
            return "No scheduled tasks."

        lines = []
        for s in schedules:
            status = "✅" if s.enabled else "⏸"
            type_icon = "🔌" if s.type == "plugin" else ("📂" if s.type == "workspace" else "💬")
            lines.append(f"{status} {type_icon} <b>{s.name}</b> - {s.time_str}")

        return "\n".join(lines)
