"""Google Calendar plugin - view, add, edit, delete events via Telegram UI."""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta
from typing import Optional

from telegram import ForceReply, InlineKeyboardButton, InlineKeyboardMarkup

from src.bot.formatters import escape_html
from src.logging_config import logger
from src.plugins.loader import Plugin, PluginInteraction, PluginResult, ScheduledAction
from src.time_utils import app_today, get_app_timezone

from .google_client import CalendarEvent, GoogleCalendarClient
from .ui import (
    build_calendar_grid,
    build_date_quick_select,
    build_hour_keyboard,
    build_hub_nav,
    build_minute_keyboard,
    format_date_display,
    format_date_full,
)

WEEKDAY_NAMES = ["월", "화", "수", "목", "금", "토", "일"]


class CalendarPlugin(Plugin):
    """Google Calendar integration plugin."""

    name = "calendar"
    description = "Google Calendar 연동"
    usage = (
        "📅 <b>Calendar Plugin</b>\n\n"
        "<code>캘린더</code> or <code>/cal</code> - Open calendar\n\n"
        "<b>Features</b>\n"
        "• 📅 View today's events\n"
        "• ➕ Add new events\n"
        "• ✏️ Edit / 🗑 Delete events\n"
        "• ☀️ Morning briefing (schedule)"
    )

    CALLBACK_PREFIX = "cal:"
    FORCE_REPLY_MARKER = "cal_title"

    PATTERNS = [r"^캘린더$", r"^일정$", r"^달력$", r"^일정\s+(추가|보기|목록)"]
    EXCLUDE_PATTERNS = [r"(란|이란)\s*뭐", r"(가|이)\s*뭐"]

    def __init__(self):
        super().__init__()
        self._gcal = GoogleCalendarClient()
        # Ephemeral event cache: chat_id → list[CalendarEvent]
        self._event_cache: dict[int, list[CalendarEvent]] = {}

    async def can_handle(self, message: str, chat_id: int) -> bool:
        msg = message.strip()
        for pattern in self.EXCLUDE_PATTERNS:
            if re.search(pattern, msg, re.IGNORECASE):
                return False
        for pattern in self.PATTERNS:
            if re.search(pattern, msg):
                return True
        return False

    async def handle(self, message: str, chat_id: int) -> PluginResult:
        if not self._gcal.available:
            return PluginResult(
                handled=True,
                response=(
                    "⚠️ Google Calendar이 설정되지 않았습니다.\n\n"
                    "<code>GOOGLE_SERVICE_ACCOUNT_FILE</code>과 "
                    "<code>GOOGLE_CALENDAR_ID</code> 환경변수를 설정하세요."
                ),
            )

        msg = message.strip()
        if re.search(r"추가", msg):
            result = self._show_add_date_select()
        else:
            result = self._show_hub(chat_id, app_today())

        return PluginResult(
            handled=True,
            response=result["text"],
            reply_markup=result.get("reply_markup"),
        )

    # ==================== Callback Router ====================

    async def handle_callback_async(self, callback_data: str, chat_id: int) -> dict:
        parts = callback_data.split(":")
        if len(parts) < 2:
            return {"text": "❌ Invalid request.", "edit": True}

        action = parts[1]

        if action == "noop":
            return {"text": "", "edit": False, "noop": True}

        if action == "hub":
            return self._show_hub(chat_id, app_today())

        if action == "day":
            d = date.fromisoformat(parts[2])
            return self._show_hub(chat_id, d)

        if action == "pick":
            d = date.fromisoformat(parts[2])
            return self._show_hub(chat_id, d)

        # Calendar grid
        if action == "grid":
            y, m = parts[2].split("-")
            return self._show_calendar_grid(int(y), int(m))

        # Add flow: grid from add context
        if action == "agrid":
            y, m = parts[2].split("-")
            return self._show_add_calendar_grid(int(y), int(m))

        # Add flow: date picked from add-calendar-grid
        if action == "apick":
            d = date.fromisoformat(parts[2])
            return self._show_hour_select(d.isoformat())

        if action == "add":
            return self._show_add_date_select()

        # Add: date selected
        if action == "ad":
            return self._show_hour_select(parts[2])

        # Add: hour selected
        if action == "ah":
            return self._show_minute_select(parts[2], int(parts[3]))

        # Add: minute selected → ForceReply for title
        if action == "am":
            return self._prompt_title(
                date_str=parts[2], hour=int(parts[3]), minute=int(parts[4]),
            )

        # Add: all-day event → ForceReply for title
        if action == "allday":
            return self._prompt_title(date_str=parts[2], all_day=True)

        # Event detail
        if action == "ev":
            return self._show_event_detail(chat_id, int(parts[2]))

        # Delete confirm
        if action == "del":
            return self._show_delete_confirm(chat_id, int(parts[2]))

        # Delete execute
        if action == "delok":
            return self._execute_delete(chat_id, parts[2])

        # Edit title prompt
        if action == "edt":
            return self._show_edit_title_prompt(chat_id, int(parts[2]))

        # Edit submenu
        if action == "edit":
            return self._show_edit_menu(chat_id, int(parts[2]))

        # Edit date
        if action == "eddate":
            event_id = parts[2]
            return self._show_edit_date_select(event_id)

        # Edit date picked
        if action == "edd":
            event_id = parts[2]
            d = date.fromisoformat(parts[3])
            return self._show_edit_hour_select(event_id, d.isoformat())

        # Edit hour picked
        if action == "edh":
            event_id = parts[2]
            return self._show_edit_minute_select(event_id, parts[3], int(parts[4]))

        # Edit minute picked → execute time update
        if action == "edm":
            event_id = parts[2]
            return self._execute_edit_time(
                chat_id, event_id, parts[3], int(parts[4]), int(parts[5])
            )

        return {"text": "❌ Unknown command.", "edit": True}

    # ==================== Interaction (ForceReply) ====================

    def handle_interaction(
        self,
        message: str,
        chat_id: int,
        interaction: Optional[PluginInteraction] = None,
    ) -> dict:
        title = message.strip()
        if not title:
            return {
                "text": "❌ 제목이 비어 있습니다.",
                "reply_markup": InlineKeyboardMarkup(
                    [[InlineKeyboardButton("📅 캘린더", callback_data="cal:hub")]]
                ),
            }

        action = interaction.action if interaction else "create"
        state = interaction.state if interaction else {}

        if action == "edit_title":
            return self._process_edit_title(chat_id, title, state.get("event_id", ""))

        # Default: create event
        return self._process_create(chat_id, title, state)

    # ==================== Hub (Day View) ====================

    def _show_hub(self, chat_id: int, target_date: date) -> dict:
        tz = get_app_timezone()
        start = datetime(target_date.year, target_date.month, target_date.day, tzinfo=tz)
        end = start + timedelta(days=1)

        events = self._gcal.list_events(start, end)
        self._event_cache[chat_id] = events

        date_label = format_date_full(target_date)
        today = app_today()
        if target_date == today:
            header = f"📅 오늘 일정 — {format_date_display(target_date)}"
        else:
            header = f"📅 {date_label}"

        if not events:
            text = f"{header}\n{'─' * 20}\n일정이 없습니다 ☀️"
        else:
            lines = [f"{header}\n{'─' * 20}"]
            for ev in events:
                if ev.all_day:
                    lines.append(f"🌅 종일  {escape_html(ev.summary)}")
                else:
                    time_str = ev.start.strftime("%H:%M")
                    lines.append(f"⏰ {time_str}  {escape_html(ev.summary)}")
            text = "\n".join(lines)

        # Event buttons (tappable)
        buttons = []
        for i, ev in enumerate(events):
            if ev.all_day:
                label = f"🌅 종일 · {ev.summary[:25]}"
            else:
                label = f"{ev.start.strftime('%H:%M')} · {ev.summary[:25]}"
            buttons.append([InlineKeyboardButton(label, callback_data=f"cal:ev:{i}")])

        buttons.extend(build_hub_nav(target_date.isoformat()))

        return {
            "text": text,
            "reply_markup": InlineKeyboardMarkup(buttons),
            "edit": True,
        }

    # ==================== Event Detail ====================

    def _show_event_detail(self, chat_id: int, idx: int) -> dict:
        events = self._event_cache.get(chat_id, [])
        if idx < 0 or idx >= len(events):
            return {"text": "❌ 이벤트를 찾을 수 없습니다.", "edit": True}

        ev = events[idx]
        lines = [f"📌 <b>{escape_html(ev.summary)}</b>"]

        if ev.all_day:
            lines.append(f"🌅 종일 ({format_date_display(ev.start.date())})")
        else:
            lines.append(
                f"⏰ {ev.start.strftime('%H:%M')} - {ev.end.strftime('%H:%M')}"
            )
            lines.append(f"📅 {format_date_full(ev.start.date())}")

        if ev.location:
            lines.append(f"📍 {escape_html(ev.location)}")
        if ev.description:
            desc = ev.description[:200]
            lines.append(f"\n📝 {escape_html(desc)}")

        buttons = [
            [
                InlineKeyboardButton("✏️ 수정", callback_data=f"cal:edit:{idx}"),
                InlineKeyboardButton("🗑 삭제", callback_data=f"cal:del:{idx}"),
            ],
            [InlineKeyboardButton("◀ 목록", callback_data=f"cal:day:{ev.start.date().isoformat()}")],
        ]

        return {
            "text": "\n".join(lines),
            "reply_markup": InlineKeyboardMarkup(buttons),
            "edit": True,
        }

    # ==================== Add Event Flow ====================

    def _show_add_date_select(self) -> dict:
        return {
            "text": "📅 <b>날짜를 선택하세요</b>",
            "reply_markup": InlineKeyboardMarkup(build_date_quick_select()),
            "edit": True,
        }

    def _show_calendar_grid(self, year: int, month: int) -> dict:
        return {
            "text": "📅 날짜를 선택하세요",
            "reply_markup": InlineKeyboardMarkup(build_calendar_grid(year, month)),
            "edit": True,
        }

    def _show_add_calendar_grid(self, year: int, month: int) -> dict:
        """Calendar grid in add-event context (picks go to hour select)."""
        today = app_today()
        rows: list[list[InlineKeyboardButton]] = []
        import calendar as cal_mod

        rows.append([InlineKeyboardButton(f"📅 {year}년 {month}월", callback_data="cal:noop")])
        rows.append([
            InlineKeyboardButton(d, callback_data="cal:noop")
            for d in WEEKDAY_NAMES
        ])

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
                        label, callback_data=f"cal:apick:{d.isoformat()}"
                    ))
            rows.append(row)

        py, pm = (year - 1, 12) if month == 1 else (year, month - 1)
        ny, nm = (year + 1, 1) if month == 12 else (year, month + 1)
        rows.append([
            InlineKeyboardButton(f"◀ {pm}월", callback_data=f"cal:agrid:{py}-{pm:02d}"),
            InlineKeyboardButton(f"{nm}월 ▶", callback_data=f"cal:agrid:{ny}-{nm:02d}"),
        ])
        rows.append([InlineKeyboardButton("❌ 취소", callback_data="cal:hub")])

        return {
            "text": "📅 <b>일정 추가</b> — 날짜를 선택하세요",
            "reply_markup": InlineKeyboardMarkup(rows),
            "edit": True,
        }

    def _show_hour_select(self, date_str: str) -> dict:
        d = date.fromisoformat(date_str)
        return {
            "text": f"⏰ <b>시작 시간을 선택하세요</b>\n\n📅 {format_date_full(d)}",
            "reply_markup": InlineKeyboardMarkup(build_hour_keyboard(date_str)),
            "edit": True,
        }

    def _show_minute_select(self, date_str: str, hour: int) -> dict:
        d = date.fromisoformat(date_str)
        return {
            "text": f"⏰ <b>{hour}시 몇 분?</b>\n\n📅 {format_date_full(d)}",
            "reply_markup": InlineKeyboardMarkup(build_minute_keyboard(date_str, hour)),
            "edit": True,
        }

    def _prompt_title(
        self,
        date_str: str,
        hour: int = 0,
        minute: int = 0,
        all_day: bool = False,
    ) -> dict:
        d = date.fromisoformat(date_str)
        if all_day:
            time_line = "🌅 종일"
        else:
            time_line = f"⏰ {hour:02d}:{minute:02d}"

        return {
            "text": (
                f"📝 <b>일정 추가</b>\n\n"
                f"📅 {format_date_full(d)}\n"
                f"{time_line}\n\n"
                f"제목을 입력하세요."
            ),
            "force_reply_prompt": "📝 일정 제목을 입력하세요:",
            "force_reply": ForceReply(
                selective=True,
                input_field_placeholder="예: 팀 미팅, 치과 예약...",
            ),
            "interaction_action": "create",
            "interaction_state": {
                "date": date_str,
                "hour": hour,
                "minute": minute,
                "all_day": all_day,
            },
            "edit": False,
        }

    def _process_create(self, chat_id: int, title: str, state: dict) -> dict:
        date_str = state.get("date", app_today().isoformat())
        hour = state.get("hour", 0)
        minute = state.get("minute", 0)
        all_day = state.get("all_day", False)

        tz = get_app_timezone()
        d = date.fromisoformat(date_str)
        start = datetime(d.year, d.month, d.day, hour, minute, tzinfo=tz)

        event = self._gcal.create_event(summary=title, start=start, all_day=all_day)

        if not event:
            return {
                "text": "❌ 일정 등록에 실패했습니다.",
                "reply_markup": InlineKeyboardMarkup(
                    [[InlineKeyboardButton("📅 캘린더", callback_data="cal:hub")]]
                ),
            }

        if all_day:
            time_line = "🌅 종일"
        else:
            time_line = f"⏰ {start.strftime('%H:%M')}"

        return {
            "text": (
                f"✅ 일정이 등록되었습니다!\n\n"
                f"📅 {format_date_full(d)}\n"
                f"{time_line}\n"
                f"📌 {escape_html(title)}"
            ),
            "reply_markup": InlineKeyboardMarkup([
                [InlineKeyboardButton("📅 캘린더로", callback_data=f"cal:day:{date_str}")],
            ]),
        }

    # ==================== Edit Event ====================

    def _show_edit_menu(self, chat_id: int, idx: int) -> dict:
        events = self._event_cache.get(chat_id, [])
        if idx < 0 or idx >= len(events):
            return {"text": "❌ 이벤트를 찾을 수 없습니다.", "edit": True}

        ev = events[idx]
        buttons = [
            [
                InlineKeyboardButton("📅 날짜/시간", callback_data=f"cal:eddate:{ev.id}"),
                InlineKeyboardButton("📌 제목", callback_data=f"cal:edt:{idx}"),
            ],
            [InlineKeyboardButton("◀ 돌아가기", callback_data=f"cal:ev:{idx}")],
        ]

        return {
            "text": f"✏️ <b>무엇을 수정할까요?</b>\n\n📌 {escape_html(ev.summary)}",
            "reply_markup": InlineKeyboardMarkup(buttons),
            "edit": True,
        }

    def _show_edit_title_prompt(self, chat_id: int, idx: int) -> dict:
        events = self._event_cache.get(chat_id, [])
        if idx < 0 or idx >= len(events):
            return {"text": "❌ 이벤트를 찾을 수 없습니다.", "edit": True}

        ev = events[idx]
        return {
            "text": (
                f"✏️ <b>제목 수정</b>\n\n"
                f"현재: <code>{escape_html(ev.summary)}</code>"
            ),
            "force_reply_prompt": "✏️ 새 제목을 입력하세요:",
            "force_reply": ForceReply(
                selective=True,
                input_field_placeholder="새 제목을 입력하세요...",
            ),
            "interaction_action": "edit_title",
            "interaction_state": {"event_id": ev.id},
            "edit": False,
        }

    def _process_edit_title(self, chat_id: int, title: str, event_id: str) -> dict:
        event = self._gcal.update_event(event_id, summary=title)

        if not event:
            return {
                "text": "❌ 제목 수정에 실패했습니다.",
                "reply_markup": InlineKeyboardMarkup(
                    [[InlineKeyboardButton("📅 캘린더", callback_data="cal:hub")]]
                ),
            }

        return {
            "text": f"✅ 제목이 수정되었습니다!\n\n📌 {escape_html(title)}",
            "reply_markup": InlineKeyboardMarkup([
                [InlineKeyboardButton("📅 캘린더로", callback_data=f"cal:day:{event.start.date().isoformat()}")],
            ]),
        }

    def _show_edit_date_select(self, event_id: str) -> dict:
        today = app_today()
        buttons = []
        for delta, label in [(0, "오늘"), (1, "내일"), (2, "모레")]:
            d = today + timedelta(days=delta)
            buttons.append(InlineKeyboardButton(
                f"{label} {d.month}/{d.day}",
                callback_data=f"cal:edd:{event_id}:{d.isoformat()}",
            ))
        return {
            "text": "📅 <b>새 날짜를 선택하세요</b>",
            "reply_markup": InlineKeyboardMarkup([
                buttons,
                [InlineKeyboardButton("❌ 취소", callback_data="cal:hub")],
            ]),
            "edit": True,
        }

    def _show_edit_hour_select(self, event_id: str, date_str: str) -> dict:
        d = date.fromisoformat(date_str)
        rows = []
        row = []
        for h in range(9, 21):
            row.append(InlineKeyboardButton(
                f"{h:02d}", callback_data=f"cal:edh:{event_id}:{date_str}:{h}"
            ))
            if len(row) == 6:
                rows.append(row)
                row = []
        if row:
            rows.append(row)
        rows.append([InlineKeyboardButton("❌ 취소", callback_data="cal:hub")])

        return {
            "text": f"⏰ <b>새 시간을 선택하세요</b>\n\n📅 {format_date_full(d)}",
            "reply_markup": InlineKeyboardMarkup(rows),
            "edit": True,
        }

    def _show_edit_minute_select(self, event_id: str, date_str: str, hour: int) -> dict:
        d = date.fromisoformat(date_str)
        return {
            "text": f"⏰ <b>{hour}시 몇 분?</b>\n\n📅 {format_date_full(d)}",
            "reply_markup": InlineKeyboardMarkup([
                [
                    InlineKeyboardButton(
                        f"{m:02d}분",
                        callback_data=f"cal:edm:{event_id}:{date_str}:{hour}:{m}",
                    )
                    for m in (0, 15, 30, 45)
                ],
                [InlineKeyboardButton("❌ 취소", callback_data="cal:hub")],
            ]),
            "edit": True,
        }

    def _execute_edit_time(
        self, chat_id: int, event_id: str, date_str: str, hour: int, minute: int
    ) -> dict:
        tz = get_app_timezone()
        d = date.fromisoformat(date_str)
        new_start = datetime(d.year, d.month, d.day, hour, minute, tzinfo=tz)
        new_end = new_start + timedelta(hours=1)

        event = self._gcal.update_event(event_id, start=new_start, end=new_end)
        if not event:
            return {
                "text": "❌ 시간 수정에 실패했습니다.",
                "reply_markup": InlineKeyboardMarkup(
                    [[InlineKeyboardButton("📅 캘린더", callback_data="cal:hub")]]
                ),
            }

        return {
            "text": (
                f"✅ 일정이 수정되었습니다!\n\n"
                f"📅 {format_date_full(d)}\n"
                f"⏰ {hour:02d}:{minute:02d}\n"
                f"📌 {escape_html(event.summary)}"
            ),
            "reply_markup": InlineKeyboardMarkup([
                [InlineKeyboardButton("📅 캘린더로", callback_data=f"cal:day:{date_str}")],
            ]),
            "edit": True,
        }

    # ==================== Delete Event ====================

    def _show_delete_confirm(self, chat_id: int, idx: int) -> dict:
        events = self._event_cache.get(chat_id, [])
        if idx < 0 or idx >= len(events):
            return {"text": "❌ 이벤트를 찾을 수 없습니다.", "edit": True}

        ev = events[idx]
        if ev.all_day:
            time_line = "🌅 종일"
        else:
            time_line = f"⏰ {ev.start.strftime('%H:%M')} - {ev.end.strftime('%H:%M')}"

        buttons = [
            [
                InlineKeyboardButton("✅ 삭제", callback_data=f"cal:delok:{ev.id}"),
                InlineKeyboardButton("❌ 취소", callback_data=f"cal:ev:{idx}"),
            ]
        ]

        return {
            "text": (
                f"⚠️ <b>이 일정을 삭제할까요?</b>\n\n"
                f"📌 {escape_html(ev.summary)}\n"
                f"📅 {format_date_display(ev.start.date())}\n"
                f"{time_line}"
            ),
            "reply_markup": InlineKeyboardMarkup(buttons),
            "edit": True,
        }

    def _execute_delete(self, chat_id: int, event_id: str) -> dict:
        success = self._gcal.delete_event(event_id)

        if not success:
            return {
                "text": "❌ 삭제에 실패했습니다.",
                "reply_markup": InlineKeyboardMarkup(
                    [[InlineKeyboardButton("📅 캘린더", callback_data="cal:hub")]]
                ),
                "edit": True,
            }

        return {
            "text": "🗑 일정이 삭제되었습니다.",
            "reply_markup": InlineKeyboardMarkup(
                [[InlineKeyboardButton("📅 캘린더로", callback_data="cal:hub")]]
            ),
            "edit": True,
        }

    # ==================== Scheduled Actions ====================

    def get_scheduled_actions(self) -> list[ScheduledAction]:
        return [
            ScheduledAction(name="morning_briefing", description="☀️ 아침 일정 브리핑"),
        ]

    async def execute_scheduled_action(self, action_name: str, chat_id: int) -> str | dict:
        if action_name == "morning_briefing":
            today = app_today()
            tz = get_app_timezone()
            start = datetime(today.year, today.month, today.day, tzinfo=tz)
            end = start + timedelta(days=1)
            events = self._gcal.list_events(start, end)

            if not events:
                return {
                    "text": (
                        f"☀️ <b>오늘 일정</b> — {format_date_display(today)}\n"
                        f"{'─' * 20}\n"
                        f"오늘 일정이 없습니다 ☀️"
                    ),
                    "reply_markup": InlineKeyboardMarkup(
                        [[InlineKeyboardButton("📅 캘린더 열기", callback_data="cal:hub")]]
                    ),
                }

            lines = [
                f"☀️ <b>오늘 일정</b> — {format_date_display(today)} ({len(events)}건)",
                "─" * 20,
            ]
            for ev in events:
                if ev.all_day:
                    lines.append(f"🌅 종일  {escape_html(ev.summary)}")
                else:
                    lines.append(
                        f"⏰ {ev.start.strftime('%H:%M')}  {escape_html(ev.summary)}"
                    )

            return {
                "text": "\n".join(lines),
                "reply_markup": InlineKeyboardMarkup(
                    [[InlineKeyboardButton("📅 캘린더 열기", callback_data="cal:hub")]]
                ),
            }

        raise NotImplementedError(f"Action '{action_name}' not implemented")
