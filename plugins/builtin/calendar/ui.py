"""Calendar UI helpers - keyboard builders and date formatters."""

from __future__ import annotations

import calendar as cal_mod
from datetime import date, timedelta

from telegram import InlineKeyboardButton

from src.time_utils import app_today

WEEKDAY_NAMES = ["월", "화", "수", "목", "금", "토", "일"]


def format_date_display(d: date) -> str:
    """Short: 3월 21일 (금)."""
    return f"{d.month}월 {d.day}일 ({WEEKDAY_NAMES[d.weekday()]})"


def format_date_full(d: date) -> str:
    """Full: 2026년 3월 21일 (금)."""
    return f"{d.year}년 {d.month}월 {d.day}일 ({WEEKDAY_NAMES[d.weekday()]})"


def build_calendar_grid(year: int, month: int) -> list[list[InlineKeyboardButton]]:
    """Build a month calendar grid as inline keyboard rows."""
    today = app_today()
    rows: list[list[InlineKeyboardButton]] = []

    # Title
    rows.append([
        InlineKeyboardButton(f"📅 {year}년 {month}월", callback_data="cal:noop"),
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
    nav.append(InlineKeyboardButton(f"◀ {pm}월", callback_data=f"cal:grid:{py}-{pm:02d}"))
    nav.append(InlineKeyboardButton(f"{nm}월 ▶", callback_data=f"cal:grid:{ny}-{nm:02d}"))
    rows.append(nav)

    rows.append([InlineKeyboardButton("❌ 취소", callback_data="cal:hub")])
    return rows


def build_date_quick_select() -> list[list[InlineKeyboardButton]]:
    """Quick date selection: today, tomorrow, day after."""
    today = app_today()
    buttons = []
    for delta, label in [(0, "오늘"), (1, "내일"), (2, "모레")]:
        d = today + timedelta(days=delta)
        buttons.append(InlineKeyboardButton(
            f"{label} {d.month}/{d.day}",
            callback_data=f"cal:ad:{d.isoformat()}",
        ))
    return [
        buttons,
        [InlineKeyboardButton("📅 달력에서 선택", callback_data=f"cal:agrid:{today.year}-{today.month:02d}")],
        [InlineKeyboardButton("❌ 취소", callback_data="cal:hub")],
    ]


def build_hour_keyboard(date_str: str) -> list[list[InlineKeyboardButton]]:
    """Hour selection grid (09-20, 6 columns)."""
    rows = []
    row = []
    for h in range(9, 21):
        row.append(InlineKeyboardButton(
            f"{h:02d}", callback_data=f"cal:ah:{date_str}:{h}"
        ))
        if len(row) == 6:
            rows.append(row)
            row = []
    if row:
        rows.append(row)

    rows.append([InlineKeyboardButton("🌅 종일", callback_data=f"cal:allday:{date_str}")])
    rows.append([
        InlineKeyboardButton("◀ 날짜 다시", callback_data="cal:add"),
        InlineKeyboardButton("❌ 취소", callback_data="cal:hub"),
    ])
    return rows


def build_minute_keyboard(date_str: str, hour: int) -> list[list[InlineKeyboardButton]]:
    """Minute selection (00, 15, 30, 45)."""
    return [
        [
            InlineKeyboardButton(
                f"{m:02d}분", callback_data=f"cal:am:{date_str}:{hour}:{m}"
            )
            for m in (0, 15, 30, 45)
        ],
        [
            InlineKeyboardButton("◀ 시간 다시", callback_data=f"cal:ad:{date_str}"),
            InlineKeyboardButton("❌ 취소", callback_data="cal:hub"),
        ],
    ]


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
        nav.append(InlineKeyboardButton("오늘", callback_data="cal:hub"))
    nav.append(
        InlineKeyboardButton(f"{next_d.month}/{next_d.day} ▶", callback_data=f"cal:day:{next_d.isoformat()}")
    )

    return [
        nav,
        [
            InlineKeyboardButton("+ 일정 추가", callback_data="cal:add"),
            InlineKeyboardButton("📅 달력", callback_data=f"cal:grid:{d.year}-{d.month:02d}"),
        ],
    ]
