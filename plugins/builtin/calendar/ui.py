"""Calendar UI helpers - keyboard builders and date formatters."""

from __future__ import annotations

import calendar as cal_mod
from datetime import date, timedelta

from telegram import InlineKeyboardButton

from src.time_utils import app_today

WEEKDAY_NAMES = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


def format_date_display(d: date) -> str:
    """Short: 3/21 (Sat)."""
    return f"{d.month}/{d.day} ({WEEKDAY_NAMES[d.weekday()]})"


def format_date_full(d: date) -> str:
    """Full: 2026/03/21 (Sat)."""
    return f"{d.year}/{d.month:02d}/{d.day:02d} ({WEEKDAY_NAMES[d.weekday()]})"


def build_calendar_grid(year: int, month: int) -> list[list[InlineKeyboardButton]]:
    """Build a month calendar grid as inline keyboard rows."""
    today = app_today()
    rows: list[list[InlineKeyboardButton]] = []

    # Title
    rows.append([
        InlineKeyboardButton(f"📅 {year}/{month:02d}", callback_data="cal:noop"),
    ])

    # Weekday header
    rows.append([
        InlineKeyboardButton(d, callback_data="cal:noop") for d in WEEKDAY_NAMES
    ])

    # Day grid
    weeks = cal_mod.Calendar(firstweekday=0).monthdayscalendar(year, month)
    for week in weeks:
        row = []
        for day in week:
            if day == 0:
                row.append(InlineKeyboardButton(" ", callback_data="cal:noop"))
            else:
                d = date(year, month, day)
                label = f"•{day}•" if d == today else str(day)
                row.append(InlineKeyboardButton(
                    label, callback_data=f"cal:pick:{d.isoformat()}"
                ))
        rows.append(row)

    # Navigation
    nav = []
    py, pm = (year - 1, 12) if month == 1 else (year, month - 1)
    ny, nm = (year + 1, 1) if month == 12 else (year, month + 1)
    nav.append(InlineKeyboardButton(f"◀ {pm}", callback_data=f"cal:grid:{py}-{pm:02d}"))
    nav.append(InlineKeyboardButton(f"{nm} ▶", callback_data=f"cal:grid:{ny}-{nm:02d}"))
    rows.append(nav)

    rows.append([InlineKeyboardButton("❌ Cancel", callback_data="cal:hub")])
    return rows


def build_date_quick_select() -> list[list[InlineKeyboardButton]]:
    """Quick date selection: today, tomorrow, day after."""
    today = app_today()
    buttons = []
    for delta, label in [(0, "Today"), (1, "Tomorrow"), (2, "Day after")]:
        d = today + timedelta(days=delta)
        buttons.append(InlineKeyboardButton(
            f"{label} {d.month}/{d.day}",
            callback_data=f"cal:ad:{d.isoformat()}",
        ))
    return [
        buttons,
        [InlineKeyboardButton("📅 Pick from calendar", callback_data=f"cal:agrid:{today.year}-{today.month:02d}")],
        [InlineKeyboardButton("❌ Cancel", callback_data="cal:hub")],
    ]


def build_hour_keyboard(date_str: str) -> list[list[InlineKeyboardButton]]:
    """Hour selection grid (00-23, 4 columns) - same layout as scheduler."""
    rows = []
    row = []
    for h in range(24):
        row.append(InlineKeyboardButton(
            f"{h:02d}h", callback_data=f"cal:ah:{date_str}:{h}"
        ))
        if len(row) == 4:
            rows.append(row)
            row = []
    if row:
        rows.append(row)

    rows.append([InlineKeyboardButton("🌅 All day", callback_data=f"cal:allday:{date_str}")])
    rows.append([
        InlineKeyboardButton("◀ Back to date", callback_data="cal:add"),
        InlineKeyboardButton("❌ Cancel", callback_data="cal:hub"),
    ])
    return rows


def build_minute_keyboard(date_str: str, hour: int) -> list[list[InlineKeyboardButton]]:
    """Minute selection (0-55, 5-min intervals, 4 columns) - same layout as scheduler."""
    rows = []
    row = []
    for m in range(0, 60, 5):
        row.append(InlineKeyboardButton(
            f":{m:02d}", callback_data=f"cal:am:{date_str}:{hour}:{m}"
        ))
        if len(row) == 4:
            rows.append(row)
            row = []
    if row:
        rows.append(row)

    rows.append([
        InlineKeyboardButton("◀ Back to hour", callback_data=f"cal:ad:{date_str}"),
        InlineKeyboardButton("❌ Cancel", callback_data="cal:hub"),
    ])
    return rows


def build_hub_nav(date_str: str) -> list[list[InlineKeyboardButton]]:
    """Hub navigation: prev/today/next day + add + calendar."""
    d = date.fromisoformat(date_str)
    prev_d = d - timedelta(days=1)
    next_d = d + timedelta(days=1)
    today = app_today()

    nav = [
        InlineKeyboardButton(f"◀ {prev_d.month}/{prev_d.day}", callback_data=f"cal:day:{prev_d.isoformat()}"),
    ]
    if d != today:
        nav.append(InlineKeyboardButton("Today", callback_data="cal:hub"))
    nav.append(
        InlineKeyboardButton(f"{next_d.month}/{next_d.day} ▶", callback_data=f"cal:day:{next_d.isoformat()}")
    )

    return [
        nav,
        [
            InlineKeyboardButton("+ Add Event", callback_data="cal:add"),
            InlineKeyboardButton("📅 Calendar", callback_data=f"cal:grid:{d.year}-{d.month:02d}"),
        ],
    ]
