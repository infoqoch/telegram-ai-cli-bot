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

import importlib.util
from pathlib import Path

def _load_sibling(name: str):
    """Load a sibling module from the same directory (plugin loader workaround)."""
    import sys
    mod_name = f"_calendar_plugin_{name}"
    if mod_name in sys.modules:
        return sys.modules[mod_name]
    sibling = Path(__file__).parent / f"{name}.py"
    spec = importlib.util.spec_from_file_location(mod_name, sibling)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[mod_name] = mod
    spec.loader.exec_module(mod)
    return mod

_gclient = _load_sibling("google_client")
_ui = _load_sibling("ui")

CalendarEvent = _gclient.CalendarEvent
GoogleCalendarClient = _gclient.GoogleCalendarClient

build_calendar_grid = _ui.build_calendar_grid
build_date_quick_select = _ui.build_date_quick_select
build_hour_keyboard = _ui.build_hour_keyboard
build_hub_nav = _ui.build_hub_nav
build_minute_keyboard = _ui.build_minute_keyboard
format_date_display = _ui.format_date_display
format_date_full = _ui.format_date_full

WEEKDAY_NAMES = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


class CalendarPlugin(Plugin):
    """Google Calendar integration plugin."""

    name = "calendar"
    description = "Google Calendar"
    usage = (
        "📅 <b>Calendar Plugin</b>\n\n"
        "<code>/cal</code> - Open calendar\n\n"
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
        # Ephemeral event cache: chat_id -> list[CalendarEvent]
        self._event_cache: dict[int, list[CalendarEvent]] = {}
        # Dedup for reminders: set of "event_id:reminder_type"
        self._sent_reminders: set[str] = set()

    async def get_ai_dynamic_context(self, chat_id: int) -> str:
        from src.time_utils import app_now
        from datetime import timedelta
        if not self._gcal or not self._gcal.available:
            return "(캘린더 연결 안됨)"
        try:
            today = app_now()
            start = today.replace(hour=0, minute=0, second=0, microsecond=0)
            end = start + timedelta(days=7)
            events = self._gcal.list_events(start, end)
            if not events:
                return "향후 7일간 일정이 없습니다."
            lines = ["향후 7일 일정:"]
            for ev in events:
                date_str = ev.start.strftime("%m/%d %H:%M") if not ev.all_day else ev.start.strftime("%m/%d") + " 종일"
                lines.append(f"  - {date_str}: {ev.summary}")
            return "\n".join(lines)
        except Exception as e:
            return f"(캘린더 조회 실패: {e})"

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
                    "⚠️ Google Calendar not configured.\n\n"
                    "Set <code>GOOGLE_SERVICE_ACCOUNT_FILE</code> and "
                    "<code>GOOGLE_CALENDAR_ID</code> in .env"
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

        # Add: minute selected -> ForceReply for title
        if action == "am":
            return self._prompt_title(
                date_str=parts[2], hour=int(parts[3]), minute=int(parts[4]),
            )

        # Add: all-day event -> ForceReply for title
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

        # Edit minute picked -> execute time update
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
                "text": "❌ Title cannot be empty.",
                "reply_markup": InlineKeyboardMarkup(
                    [[InlineKeyboardButton("📅 Calendar", callback_data="cal:hub")]]
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
            header = f"📅 Today — {format_date_display(target_date)}"
        else:
            header = f"📅 {date_label}"

        if not events:
            text = f"{header}\n{'─' * 20}\nNo events ☀️"
        else:
            lines = [f"{header}\n{'─' * 20}"]
            for ev in events:
                if ev.all_day:
                    lines.append(f"🌅 All day  {escape_html(ev.summary)}")
                else:
                    time_str = ev.start.strftime("%H:%M")
                    lines.append(f"⏰ {time_str}  {escape_html(ev.summary)}")
            text = "\n".join(lines)

        # Event buttons (tappable)
        buttons = []
        for i, ev in enumerate(events):
            if ev.all_day:
                label = f"🌅 All day · {ev.summary[:25]}"
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
            return {"text": "❌ Event not found.", "edit": True}

        ev = events[idx]
        lines = [f"📌 <b>{escape_html(ev.summary)}</b>"]

        if ev.all_day:
            lines.append(f"🌅 All day ({format_date_display(ev.start.date())})")
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
                InlineKeyboardButton("✏️ Edit", callback_data=f"cal:edit:{idx}"),
                InlineKeyboardButton("🗑 Delete", callback_data=f"cal:del:{idx}"),
            ],
            [InlineKeyboardButton("◀ Back", callback_data=f"cal:day:{ev.start.date().isoformat()}")],
        ]

        return {
            "text": "\n".join(lines),
            "reply_markup": InlineKeyboardMarkup(buttons),
            "edit": True,
        }

    # ==================== Add Event Flow ====================

    def _show_add_date_select(self) -> dict:
        return {
            "text": "📅 <b>Select date</b>",
            "reply_markup": InlineKeyboardMarkup(build_date_quick_select()),
            "edit": True,
        }

    def _show_calendar_grid(self, year: int, month: int) -> dict:
        return {
            "text": "📅 Select date",
            "reply_markup": InlineKeyboardMarkup(build_calendar_grid(year, month)),
            "edit": True,
        }

    def _show_add_calendar_grid(self, year: int, month: int) -> dict:
        """Calendar grid in add-event context (picks go to hour select)."""
        today = app_today()
        rows: list[list[InlineKeyboardButton]] = []
        import calendar as cal_mod

        rows.append([InlineKeyboardButton(f"📅 {year}/{month:02d}", callback_data="cal:noop")])
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
            InlineKeyboardButton(f"◀ {pm}", callback_data=f"cal:agrid:{py}-{pm:02d}"),
            InlineKeyboardButton(f"{nm} ▶", callback_data=f"cal:agrid:{ny}-{nm:02d}"),
        ])
        rows.append([InlineKeyboardButton("❌ Cancel", callback_data="cal:hub")])

        return {
            "text": "📅 <b>Add Event</b> — Select date",
            "reply_markup": InlineKeyboardMarkup(rows),
            "edit": True,
        }

    def _show_hour_select(self, date_str: str) -> dict:
        d = date.fromisoformat(date_str)
        return {
            "text": f"⏰ <b>Select start hour</b>\n\n📅 {format_date_full(d)}",
            "reply_markup": InlineKeyboardMarkup(build_hour_keyboard(date_str)),
            "edit": True,
        }

    def _show_minute_select(self, date_str: str, hour: int) -> dict:
        d = date.fromisoformat(date_str)
        return {
            "text": f"⏰ <b>{hour:02d}h — select minute</b>\n\n📅 {format_date_full(d)}",
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
            time_line = "🌅 All day"
        else:
            time_line = f"⏰ {hour:02d}:{minute:02d}"

        return {
            "text": (
                f"📝 <b>Add Event</b>\n\n"
                f"📅 {format_date_full(d)}\n"
                f"{time_line}\n\n"
                f"Enter the title."
            ),
            "force_reply_prompt": "📝 Enter event title:",
            "force_reply": ForceReply(
                selective=True,
                input_field_placeholder="e.g., Team meeting, Dentist...",
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
                "text": f"❌ Failed to create event.\n\n<code>{escape_html(self._gcal.last_error)}</code>",
                "reply_markup": InlineKeyboardMarkup(
                    [[InlineKeyboardButton("📅 Calendar", callback_data="cal:hub")]]
                ),
            }

        if all_day:
            time_line = "🌅 All day"
        else:
            time_line = f"⏰ {start.strftime('%H:%M')}"

        return {
            "text": (
                f"✅ Event created!\n\n"
                f"📅 {format_date_full(d)}\n"
                f"{time_line}\n"
                f"📌 {escape_html(title)}"
            ),
            "reply_markup": InlineKeyboardMarkup([
                [InlineKeyboardButton("📅 Calendar", callback_data=f"cal:day:{date_str}")],
            ]),
        }

    # ==================== Edit Event ====================

    def _show_edit_menu(self, chat_id: int, idx: int) -> dict:
        events = self._event_cache.get(chat_id, [])
        if idx < 0 or idx >= len(events):
            return {"text": "❌ Event not found.", "edit": True}

        ev = events[idx]
        buttons = [
            [
                InlineKeyboardButton("📅 Date/Time", callback_data=f"cal:eddate:{ev.id}"),
                InlineKeyboardButton("📌 Title", callback_data=f"cal:edt:{idx}"),
            ],
            [InlineKeyboardButton("◀ Back", callback_data=f"cal:ev:{idx}")],
        ]

        return {
            "text": f"✏️ <b>What to edit?</b>\n\n📌 {escape_html(ev.summary)}",
            "reply_markup": InlineKeyboardMarkup(buttons),
            "edit": True,
        }

    def _show_edit_title_prompt(self, chat_id: int, idx: int) -> dict:
        events = self._event_cache.get(chat_id, [])
        if idx < 0 or idx >= len(events):
            return {"text": "❌ Event not found.", "edit": True}

        ev = events[idx]
        return {
            "text": (
                f"✏️ <b>Edit Title</b>\n\n"
                f"Current: <code>{escape_html(ev.summary)}</code>"
            ),
            "force_reply_prompt": "✏️ Enter new title:",
            "force_reply": ForceReply(
                selective=True,
                input_field_placeholder="Enter new title...",
            ),
            "interaction_action": "edit_title",
            "interaction_state": {"event_id": ev.id},
            "edit": False,
        }

    def _process_edit_title(self, chat_id: int, title: str, event_id: str) -> dict:
        event = self._gcal.update_event(event_id, summary=title)

        if not event:
            return {
                "text": f"❌ Failed to update title.\n\n<code>{escape_html(self._gcal.last_error)}</code>",
                "reply_markup": InlineKeyboardMarkup(
                    [[InlineKeyboardButton("📅 Calendar", callback_data="cal:hub")]]
                ),
            }

        return {
            "text": f"✅ Title updated!\n\n📌 {escape_html(title)}",
            "reply_markup": InlineKeyboardMarkup([
                [InlineKeyboardButton("📅 Calendar", callback_data=f"cal:day:{event.start.date().isoformat()}")],
            ]),
        }

    def _show_edit_date_select(self, event_id: str) -> dict:
        today = app_today()
        buttons = []
        for delta, label in [(0, "Today"), (1, "Tomorrow"), (2, "Day after")]:
            d = today + timedelta(days=delta)
            buttons.append(InlineKeyboardButton(
                f"{label} {d.month}/{d.day}",
                callback_data=f"cal:edd:{event_id}:{d.isoformat()}",
            ))
        return {
            "text": "📅 <b>Select new date</b>",
            "reply_markup": InlineKeyboardMarkup([
                buttons,
                [InlineKeyboardButton("❌ Cancel", callback_data="cal:hub")],
            ]),
            "edit": True,
        }

    def _show_edit_hour_select(self, event_id: str, date_str: str) -> dict:
        d = date.fromisoformat(date_str)
        rows = []
        row = []
        for h in range(24):
            row.append(InlineKeyboardButton(
                f"{h:02d}h", callback_data=f"cal:edh:{event_id}:{date_str}:{h}"
            ))
            if len(row) == 4:
                rows.append(row)
                row = []
        if row:
            rows.append(row)
        rows.append([InlineKeyboardButton("❌ Cancel", callback_data="cal:hub")])

        return {
            "text": f"⏰ <b>Select new hour</b>\n\n📅 {format_date_full(d)}",
            "reply_markup": InlineKeyboardMarkup(rows),
            "edit": True,
        }

    def _show_edit_minute_select(self, event_id: str, date_str: str, hour: int) -> dict:
        d = date.fromisoformat(date_str)
        rows = []
        row = []
        for m in range(0, 60, 5):
            row.append(InlineKeyboardButton(
                f":{m:02d}", callback_data=f"cal:edm:{event_id}:{date_str}:{hour}:{m}"
            ))
            if len(row) == 4:
                rows.append(row)
                row = []
        if row:
            rows.append(row)
        rows.append([InlineKeyboardButton("❌ Cancel", callback_data="cal:hub")])

        return {
            "text": f"⏰ <b>{hour:02d}h — select minute</b>\n\n📅 {format_date_full(d)}",
            "reply_markup": InlineKeyboardMarkup(rows),
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
                "text": f"❌ Failed to update time.\n\n<code>{escape_html(self._gcal.last_error)}</code>",
                "reply_markup": InlineKeyboardMarkup(
                    [[InlineKeyboardButton("📅 Calendar", callback_data="cal:hub")]]
                ),
            }

        return {
            "text": (
                f"✅ Event updated!\n\n"
                f"📅 {format_date_full(d)}\n"
                f"⏰ {hour:02d}:{minute:02d}\n"
                f"📌 {escape_html(event.summary)}"
            ),
            "reply_markup": InlineKeyboardMarkup([
                [InlineKeyboardButton("📅 Calendar", callback_data=f"cal:day:{date_str}")],
            ]),
            "edit": True,
        }

    # ==================== Delete Event ====================

    def _show_delete_confirm(self, chat_id: int, idx: int) -> dict:
        events = self._event_cache.get(chat_id, [])
        if idx < 0 or idx >= len(events):
            return {"text": "❌ Event not found.", "edit": True}

        ev = events[idx]
        if ev.all_day:
            time_line = "🌅 All day"
        else:
            time_line = f"⏰ {ev.start.strftime('%H:%M')} - {ev.end.strftime('%H:%M')}"

        buttons = [
            [
                InlineKeyboardButton("✅ Delete", callback_data=f"cal:delok:{ev.id}"),
                InlineKeyboardButton("❌ Cancel", callback_data=f"cal:ev:{idx}"),
            ]
        ]

        return {
            "text": (
                f"⚠️ <b>Delete this event?</b>\n\n"
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
                "text": f"❌ Failed to delete event.\n\n<code>{escape_html(self._gcal.last_error)}</code>",
                "reply_markup": InlineKeyboardMarkup(
                    [[InlineKeyboardButton("📅 Calendar", callback_data="cal:hub")]]
                ),
                "edit": True,
            }

        return {
            "text": "🗑 Event deleted.",
            "reply_markup": InlineKeyboardMarkup(
                [[InlineKeyboardButton("📅 Calendar", callback_data="cal:hub")]]
            ),
            "edit": True,
        }

    # ==================== Scheduled Actions ====================

    def get_scheduled_actions(self) -> list[ScheduledAction]:
        return [
            ScheduledAction(name="morning_briefing", description="☀️ Morning briefing"),
            ScheduledAction(name="evening_summary", description="🌙 Evening summary (tomorrow)"),
            ScheduledAction(name="reminder_10m", description="🔔 10-min before reminder"),
            ScheduledAction(name="reminder_1h", description="🔔 1-hour before reminder"),
        ]

    async def execute_scheduled_action(self, action_name: str, chat_id: int) -> str | dict:
        from src.time_utils import app_now

        if action_name == "morning_briefing":
            return self._build_day_briefing(
                app_today(), title="☀️ <b>Today</b>"
            )

        if action_name == "evening_summary":
            tomorrow = app_today() + timedelta(days=1)
            return self._build_day_briefing(
                tomorrow, title="🌙 <b>Tomorrow</b>"
            )

        if action_name == "reminder_10m":
            return self._build_reminder(app_now(), minutes=10, label="10 min")

        if action_name == "reminder_1h":
            return self._build_reminder(app_now(), minutes=60, label="1 hour")

        raise NotImplementedError(f"Action '{action_name}' not implemented")

    def _build_day_briefing(self, target: date, title: str) -> dict:
        """Build a day briefing message for the given date."""
        tz = get_app_timezone()
        start = datetime(target.year, target.month, target.day, tzinfo=tz)
        end = start + timedelta(days=1)
        events = self._gcal.list_events(start, end)

        if not events:
            return {
                "text": (
                    f"{title} — {format_date_display(target)}\n"
                    f"{'─' * 20}\n"
                    f"No events ☀️"
                ),
                "reply_markup": InlineKeyboardMarkup(
                    [[InlineKeyboardButton("📅 Open Calendar", callback_data="cal:hub")]]
                ),
            }

        lines = [
            f"{title} — {format_date_display(target)} ({len(events)})",
            "─" * 20,
        ]
        for ev in events:
            if ev.all_day:
                lines.append(f"🌅 All day  {escape_html(ev.summary)}")
            else:
                lines.append(
                    f"⏰ {ev.start.strftime('%H:%M')}  {escape_html(ev.summary)}"
                )

        return {
            "text": "\n".join(lines),
            "reply_markup": InlineKeyboardMarkup(
                [[InlineKeyboardButton("📅 Open Calendar", callback_data="cal:hub")]]
            ),
        }

    def _build_reminder(self, now: datetime, minutes: int, label: str) -> str | dict:
        """Check for events starting within the given window and send reminders."""
        end = now + timedelta(minutes=minutes)
        events = self._gcal.list_events(now, end)

        # Filter: only timed events (not all-day), not already reminded
        to_remind = []
        for ev in events:
            if ev.all_day:
                continue
            key = f"{ev.id}:{label}"
            if key not in self._sent_reminders:
                to_remind.append(ev)
                self._sent_reminders.add(key)

        # Cleanup old entries (keep set from growing indefinitely)
        if len(self._sent_reminders) > 500:
            self._sent_reminders.clear()

        if not to_remind:
            return ""  # Empty = no message sent

        lines = [f"🔔 <b>Upcoming in {label}</b>", "─" * 20]
        for ev in to_remind:
            lines.append(
                f"⏰ {ev.start.strftime('%H:%M')}  {escape_html(ev.summary)}"
            )

        return {
            "text": "\n".join(lines),
            "reply_markup": InlineKeyboardMarkup(
                [[InlineKeyboardButton("📅 Open Calendar", callback_data="cal:hub")]]
            ),
        }
