"""Application-wide local time helpers."""

from __future__ import annotations

import os
from datetime import date, datetime
from zoneinfo import ZoneInfo

DEFAULT_APP_TIMEZONE = "Asia/Seoul"

_app_timezone_name = os.getenv("APP_TIMEZONE", DEFAULT_APP_TIMEZONE)
_app_timezone = ZoneInfo(_app_timezone_name)


def configure_app_timezone(name: str) -> None:
    """Set the single application timezone used for local scheduling."""
    global _app_timezone_name, _app_timezone
    _app_timezone_name = name
    _app_timezone = ZoneInfo(name)


def get_app_timezone() -> ZoneInfo:
    """Return the configured application timezone."""
    return _app_timezone


def get_app_timezone_name() -> str:
    """Return the configured application timezone name."""
    return _app_timezone_name


def get_app_timezone_label() -> str:
    """Return a short timezone label for UI copy."""
    return datetime.now(_app_timezone).tzname() or _app_timezone_name


def app_now() -> datetime:
    """Return the current time in the application timezone."""
    return datetime.now(_app_timezone)


def app_today() -> date:
    """Return today's date in the application timezone."""
    return app_now().date()


def parse_local_datetime(value: str | datetime) -> datetime:
    """Parse one local datetime value and normalize to the application timezone."""
    if isinstance(value, datetime):
        dt = value
    else:
        dt = datetime.fromisoformat(value)

    if dt.tzinfo is None:
        return dt.replace(tzinfo=_app_timezone)
    return dt.astimezone(_app_timezone)


def format_local_datetime(value: str | datetime, *, include_seconds: bool = False) -> str:
    """Format one local datetime for chat-facing UI."""
    dt = parse_local_datetime(value)
    pattern = "%Y-%m-%d %H:%M:%S %Z" if include_seconds else "%Y-%m-%d %H:%M %Z"
    return dt.strftime(pattern)

