"""Helpers for schedule trigger parsing, display, and next-run calculation."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Optional

from apscheduler.triggers.cron import CronTrigger
from cron_descriptor import ExpressionDescriptor, Options

from src.time_utils import (
    app_now,
    format_local_datetime,
    get_app_timezone,
    get_app_timezone_label,
    parse_local_datetime,
)

DEFAULT_SCHEDULE_TYPE = "chat"
DEFAULT_TRIGGER_TYPE = "cron"


def normalize_schedule_type(value: Optional[str]) -> str:
    """Normalize legacy schedule type names."""
    if value in (None, "", "claude"):
        return DEFAULT_SCHEDULE_TYPE
    return value


def normalize_trigger_type(value: Optional[str]) -> str:
    """Normalize missing or legacy trigger type values."""
    if value == "once":
        return "once"
    return DEFAULT_TRIGGER_TYPE


def build_daily_cron(hour: int, minute: int) -> str:
    """Return one 5-field cron expression for a daily schedule."""
    return f"{minute} {hour} * * *"


def next_occurrence(hour: int, minute: int, *, now: Optional[datetime] = None) -> datetime:
    """Return the next local occurrence for one simple HH:MM choice."""
    current = now or app_now()
    candidate = current.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if candidate <= current:
        candidate += timedelta(days=1)
    return candidate


def cron_description(expr: Optional[str]) -> str:
    """Return an English human-readable cron description."""
    if not expr:
        return "No cron expression"

    options = Options()
    options.locale_code = "en_US"
    options.use_24hour_time_format = True
    return str(ExpressionDescriptor(expr, options))


def next_run_at(
    trigger_type: Optional[str],
    *,
    cron_expr: Optional[str] = None,
    run_at_local: Optional[str] = None,
    now: Optional[datetime] = None,
) -> Optional[datetime]:
    """Return the next run time in the application timezone."""
    normalized = normalize_trigger_type(trigger_type)
    current = now or app_now()

    if normalized == "once":
        if not run_at_local:
            return None
        scheduled = parse_local_datetime(run_at_local)
        return scheduled if scheduled >= current else None

    if not cron_expr:
        return None

    trigger = CronTrigger.from_crontab(cron_expr, timezone=get_app_timezone())
    return trigger.get_next_fire_time(None, current)


def trigger_summary(
    trigger_type: Optional[str],
    *,
    cron_expr: Optional[str] = None,
    run_at_local: Optional[str] = None,
) -> str:
    """Return a chat-facing trigger summary."""
    normalized = normalize_trigger_type(trigger_type)
    if normalized == "once":
        if not run_at_local:
            return "One-time schedule"
        return f"Once at {format_local_datetime(run_at_local)}"
    return cron_description(cron_expr)


def schedule_time_label(
    *,
    hour: int,
    minute: int,
    trigger_type: Optional[str],
    run_at_local: Optional[str] = None,
) -> str:
    """Return the primary time label used in the current UI."""
    normalized = normalize_trigger_type(trigger_type)
    if normalized == "once" and run_at_local:
        return format_local_datetime(run_at_local)
    return f"{hour:02d}:{minute:02d} {get_app_timezone_label()}"
