"""Google Calendar plugin tests - UI, callbacks, and interaction flows."""

from datetime import date, datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch
from zoneinfo import ZoneInfo

import pytest

from plugins.builtin.calendar.google_client import CalendarEvent, GoogleCalendarClient
from plugins.builtin.calendar.plugin import CalendarPlugin
from plugins.builtin.calendar.ui import (
    build_calendar_grid,
    build_date_quick_select,
    build_hour_keyboard,
    build_minute_keyboard,
    format_date_display,
    format_date_full,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

KST = ZoneInfo("Asia/Seoul")

def _make_event(
    event_id: str = "ev1",
    summary: str = "Test Event",
    hour: int = 10,
    minute: int = 0,
    all_day: bool = False,
    d: date | None = None,
) -> CalendarEvent:
    target = d or date.today()
    if all_day:
        return CalendarEvent(
            id=event_id,
            summary=summary,
            start=datetime(target.year, target.month, target.day),
            end=datetime(target.year, target.month, target.day) + timedelta(days=1),
            all_day=True,
        )
    start = datetime(target.year, target.month, target.day, hour, minute, tzinfo=KST)
    return CalendarEvent(
        id=event_id,
        summary=summary,
        start=start,
        end=start + timedelta(hours=1),
    )


def _make_plugin() -> tuple[CalendarPlugin, MagicMock]:
    plugin = CalendarPlugin()
    mock_gcal = MagicMock(spec=GoogleCalendarClient)
    mock_gcal.available = True
    mock_gcal.last_error = ""
    plugin._gcal = mock_gcal
    return plugin, mock_gcal


# ---------------------------------------------------------------------------
# UI Helpers
# ---------------------------------------------------------------------------

class TestUIHelpers:

    def test_format_date_display(self):
        """Short date format: 3/21 (Sat)."""
        d = date(2026, 3, 21)
        result = format_date_display(d)
        assert "3/21" in result
        assert "Sat" in result

    def test_format_date_full(self):
        """Full date format: 2026/03/21 (Sat)."""
        d = date(2026, 3, 21)
        result = format_date_full(d)
        assert "2026" in result
        assert "03/21" in result

    def test_build_calendar_grid(self):
        rows = build_calendar_grid(2026, 3)
        assert len(rows) > 0
        assert "2026/03" in rows[0][0].text
        assert rows[1][0].text == "Mon"
        assert rows[1][6].text == "Sun"

    def test_build_date_quick_select(self):
        rows = build_date_quick_select()
        assert len(rows) >= 2
        assert len(rows[0]) == 3
        assert "Today" in rows[0][0].text

    def test_build_hour_keyboard(self):
        """Hour keyboard: 00-23, 4 columns."""
        rows = build_hour_keyboard("2026-03-21")
        # 24 hours / 4 cols = 6 rows + all day + nav = 8 rows
        assert len(rows) == 8
        assert rows[0][0].text == "00h"
        assert len(rows[0]) == 4

    def test_build_minute_keyboard(self):
        """Minute keyboard: 5-min intervals, 4 columns."""
        rows = build_minute_keyboard("2026-03-21", 11)
        # 12 minutes / 4 cols = 3 rows + nav = 4 rows
        assert len(rows) == 4
        assert rows[0][0].text == ":00"
        assert len(rows[0]) == 4


# ---------------------------------------------------------------------------
# Plugin Basic
# ---------------------------------------------------------------------------

class TestCalendarPlugin:

    def test_plugin_metadata(self):
        plugin = CalendarPlugin()
        assert plugin.name == "calendar"
        assert plugin.CALLBACK_PREFIX == "cal:"
        assert plugin.FORCE_REPLY_MARKER == "cal_title"

    @pytest.mark.asyncio
    async def test_can_handle_korean(self):
        """Korean keyword matching."""
        plugin = CalendarPlugin()
        assert await plugin.can_handle("캘린더", 1)
        assert await plugin.can_handle("일정", 1)
        assert await plugin.can_handle("달력", 1)
        assert await plugin.can_handle("일정 추가", 1)

    @pytest.mark.asyncio
    async def test_can_handle_exclude(self):
        """Exclude patterns - should go to AI."""
        plugin = CalendarPlugin()
        assert not await plugin.can_handle("캘린더란 뭐야", 1)
        assert not await plugin.can_handle("일정이란 뭐", 1)

    @pytest.mark.asyncio
    async def test_can_handle_no_match(self):
        plugin = CalendarPlugin()
        assert not await plugin.can_handle("hello", 1)
        assert not await plugin.can_handle("what to eat", 1)

    @pytest.mark.asyncio
    async def test_handle_unavailable(self):
        """Shows config message when Google API not set up."""
        plugin = CalendarPlugin()
        plugin._gcal = MagicMock(available=False)
        result = await plugin.handle("캘린더", 1)
        assert result.handled
        assert "not configured" in result.response

    @pytest.mark.asyncio
    async def test_handle_shows_hub(self):
        plugin, mock_gcal = _make_plugin()
        mock_gcal.list_events.return_value = [
            _make_event("ev1", "Standup", 9),
            _make_event("ev2", "Lunch", 12),
        ]
        result = await plugin.handle("캘린더", 1)
        assert result.handled
        assert "Standup" in result.response
        assert "Lunch" in result.response

    @pytest.mark.asyncio
    async def test_handle_empty_day(self):
        plugin, mock_gcal = _make_plugin()
        mock_gcal.list_events.return_value = []
        result = await plugin.handle("캘린더", 1)
        assert result.handled
        assert "No events" in result.response

    def test_scheduled_actions(self):
        plugin = CalendarPlugin()
        actions = plugin.get_scheduled_actions()
        assert len(actions) == 4
        names = [a.name for a in actions]
        assert "morning_briefing" in names
        assert "evening_summary" in names
        assert "reminder_10m" in names
        assert "reminder_1h" in names


# ---------------------------------------------------------------------------
# Callback Flows
# ---------------------------------------------------------------------------

class TestCalendarCallbacks:

    @pytest.mark.asyncio
    async def test_hub_callback(self):
        plugin, mock_gcal = _make_plugin()
        mock_gcal.list_events.return_value = []
        result = await plugin.handle_callback_async("cal:hub", 1)
        assert "No events" in result["text"]

    @pytest.mark.asyncio
    async def test_day_navigation(self):
        plugin, mock_gcal = _make_plugin()
        mock_gcal.list_events.return_value = [_make_event(d=date(2026, 3, 22))]
        result = await plugin.handle_callback_async("cal:day:2026-03-22", 1)
        assert "3/22" in result["text"] or "Test Event" in result["text"]

    @pytest.mark.asyncio
    async def test_add_flow_date_select(self):
        plugin, _ = _make_plugin()
        result = await plugin.handle_callback_async("cal:add", 1)
        assert "date" in result["text"].lower()

    @pytest.mark.asyncio
    async def test_add_flow_hour_select(self):
        plugin, _ = _make_plugin()
        result = await plugin.handle_callback_async("cal:ad:2026-03-22", 1)
        assert "hour" in result["text"].lower()

    @pytest.mark.asyncio
    async def test_add_flow_minute_select(self):
        plugin, _ = _make_plugin()
        result = await plugin.handle_callback_async("cal:ah:2026-03-22:11", 1)
        assert "11" in result["text"]

    @pytest.mark.asyncio
    async def test_add_flow_title_prompt(self):
        plugin, _ = _make_plugin()
        result = await plugin.handle_callback_async("cal:am:2026-03-22:11:30", 1)
        assert result.get("force_reply") is not None
        assert result["interaction_action"] == "create"
        assert result["interaction_state"]["date"] == "2026-03-22"
        assert result["interaction_state"]["hour"] == 11
        assert result["interaction_state"]["minute"] == 30

    @pytest.mark.asyncio
    async def test_add_flow_allday(self):
        plugin, _ = _make_plugin()
        result = await plugin.handle_callback_async("cal:allday:2026-03-22", 1)
        assert result.get("force_reply") is not None
        assert result["interaction_state"]["all_day"] is True

    @pytest.mark.asyncio
    async def test_calendar_grid(self):
        plugin, _ = _make_plugin()
        result = await plugin.handle_callback_async("cal:grid:2026-03", 1)
        assert result.get("reply_markup") is not None

    @pytest.mark.asyncio
    async def test_event_detail(self):
        plugin, mock_gcal = _make_plugin()
        mock_gcal.list_events.return_value = [_make_event("ev1", "Design Review", 11)]
        await plugin.handle_callback_async("cal:hub", 1)
        result = await plugin.handle_callback_async("cal:ev:0", 1)
        assert "Design Review" in result["text"]

    @pytest.mark.asyncio
    async def test_delete_confirm(self):
        plugin, mock_gcal = _make_plugin()
        mock_gcal.list_events.return_value = [_make_event("ev1", "To Delete")]
        await plugin.handle_callback_async("cal:hub", 1)
        result = await plugin.handle_callback_async("cal:del:0", 1)
        assert "Delete this event" in result["text"]

    @pytest.mark.asyncio
    async def test_delete_execute(self):
        plugin, mock_gcal = _make_plugin()
        mock_gcal.delete_event.return_value = True
        result = await plugin.handle_callback_async("cal:delok:ev1", 1)
        assert "deleted" in result["text"].lower()
        mock_gcal.delete_event.assert_called_once_with("ev1")

    @pytest.mark.asyncio
    async def test_noop_callback(self):
        plugin, _ = _make_plugin()
        result = await plugin.handle_callback_async("cal:noop", 1)
        assert result.get("noop") is True


# ---------------------------------------------------------------------------
# Interaction (ForceReply)
# ---------------------------------------------------------------------------

class TestCalendarInteraction:

    def test_create_event(self):
        plugin, mock_gcal = _make_plugin()
        created = _make_event("new1", "New Event", 11, 30)
        mock_gcal.create_event.return_value = created

        from src.plugins.loader import PluginInteraction
        interaction = PluginInteraction(
            plugin_name="calendar",
            chat_id=1,
            action="create",
            state={"date": "2026-03-22", "hour": 11, "minute": 30, "all_day": False},
        )

        result = plugin.handle_interaction("New Event", 1, interaction)
        assert "created" in result["text"].lower()
        mock_gcal.create_event.assert_called_once()

    def test_create_allday_event(self):
        plugin, mock_gcal = _make_plugin()
        created = _make_event("new2", "All Day Event", all_day=True)
        mock_gcal.create_event.return_value = created

        from src.plugins.loader import PluginInteraction
        interaction = PluginInteraction(
            plugin_name="calendar",
            chat_id=1,
            action="create",
            state={"date": "2026-03-22", "hour": 0, "minute": 0, "all_day": True},
        )

        result = plugin.handle_interaction("All Day Event", 1, interaction)
        assert "created" in result["text"].lower()

    def test_create_failure(self):
        plugin, mock_gcal = _make_plugin()
        mock_gcal.create_event.return_value = None

        from src.plugins.loader import PluginInteraction
        interaction = PluginInteraction(
            plugin_name="calendar",
            chat_id=1,
            action="create",
            state={"date": "2026-03-22", "hour": 11, "minute": 0, "all_day": False},
        )

        result = plugin.handle_interaction("Fail Test", 1, interaction)
        assert "Failed" in result["text"]

    def test_empty_title(self):
        plugin, _ = _make_plugin()

        from src.plugins.loader import PluginInteraction
        interaction = PluginInteraction(
            plugin_name="calendar",
            chat_id=1,
            action="create",
            state={"date": "2026-03-22", "hour": 11, "minute": 0, "all_day": False},
        )

        result = plugin.handle_interaction("", 1, interaction)
        assert "empty" in result["text"].lower()

    def test_edit_title(self):
        plugin, mock_gcal = _make_plugin()
        updated = _make_event("ev1", "Updated Title", 11)
        mock_gcal.update_event.return_value = updated

        from src.plugins.loader import PluginInteraction
        interaction = PluginInteraction(
            plugin_name="calendar",
            chat_id=1,
            action="edit_title",
            state={"event_id": "ev1"},
        )

        result = plugin.handle_interaction("Updated Title", 1, interaction)
        assert "updated" in result["text"].lower()


# ---------------------------------------------------------------------------
# Scheduled Actions
# ---------------------------------------------------------------------------

class TestCalendarScheduledActions:

    @pytest.mark.asyncio
    async def test_morning_briefing_with_events(self):
        plugin, mock_gcal = _make_plugin()
        mock_gcal.list_events.return_value = [
            _make_event("ev1", "Standup", 9),
            _make_event("ev2", "Lunch", 12),
        ]

        result = await plugin.execute_scheduled_action("morning_briefing", 1)
        assert isinstance(result, dict)
        assert "Standup" in result["text"]
        assert "Lunch" in result["text"]
        assert "(2)" in result["text"]

    @pytest.mark.asyncio
    async def test_morning_briefing_empty(self):
        plugin, mock_gcal = _make_plugin()
        mock_gcal.list_events.return_value = []

        result = await plugin.execute_scheduled_action("morning_briefing", 1)
        assert isinstance(result, dict)
        assert "No events" in result["text"]

    @pytest.mark.asyncio
    async def test_evening_summary(self):
        plugin, mock_gcal = _make_plugin()
        mock_gcal.list_events.return_value = [
            _make_event("ev1", "Morning Meeting", 9),
        ]
        result = await plugin.execute_scheduled_action("evening_summary", 1)
        assert isinstance(result, dict)
        assert "Tomorrow" in result["text"]
        assert "Morning Meeting" in result["text"]

    @pytest.mark.asyncio
    async def test_reminder_10m_with_event(self):
        plugin, mock_gcal = _make_plugin()
        mock_gcal.list_events.return_value = [
            _make_event("ev1", "Soon Meeting", 10),
        ]
        result = await plugin.execute_scheduled_action("reminder_10m", 1)
        assert isinstance(result, dict)
        assert "Soon Meeting" in result["text"]
        assert "10 min" in result["text"]

    @pytest.mark.asyncio
    async def test_reminder_dedup(self):
        """Same event should not be reminded twice."""
        plugin, mock_gcal = _make_plugin()
        mock_gcal.list_events.return_value = [
            _make_event("ev1", "Dedup Test", 10),
        ]
        r1 = await plugin.execute_scheduled_action("reminder_10m", 1)
        assert isinstance(r1, dict)
        assert "Dedup Test" in r1["text"]

        # Second call - same event should be skipped
        r2 = await plugin.execute_scheduled_action("reminder_10m", 1)
        assert r2 == ""  # Empty = no message

    @pytest.mark.asyncio
    async def test_reminder_no_events(self):
        plugin, mock_gcal = _make_plugin()
        mock_gcal.list_events.return_value = []
        result = await plugin.execute_scheduled_action("reminder_10m", 1)
        assert result == ""

    @pytest.mark.asyncio
    async def test_reminder_1h(self):
        plugin, mock_gcal = _make_plugin()
        mock_gcal.list_events.return_value = [
            _make_event("ev1", "Later Meeting", 11),
        ]
        result = await plugin.execute_scheduled_action("reminder_1h", 1)
        assert isinstance(result, dict)
        assert "1 hour" in result["text"]


# ---------------------------------------------------------------------------
# Multi-step Happy Case: Add Event (E2E flow)
# ---------------------------------------------------------------------------

class TestCalendarAddEventFlow:

    @pytest.mark.asyncio
    async def test_full_add_flow(self):
        """Full add event flow: date -> hour -> minute -> title -> created."""
        plugin, mock_gcal = _make_plugin()
        created = _make_event("new1", "Team Meeting", 14, 30)
        mock_gcal.create_event.return_value = created

        # Step 1: Start add
        r1 = await plugin.handle_callback_async("cal:add", 1)
        assert "date" in r1["text"].lower()

        # Step 2: Select date
        r2 = await plugin.handle_callback_async("cal:ad:2026-03-22", 1)
        assert "hour" in r2["text"].lower()

        # Step 3: Select hour
        r3 = await plugin.handle_callback_async("cal:ah:2026-03-22:14", 1)
        assert "14" in r3["text"]

        # Step 4: Select minute -> ForceReply
        r4 = await plugin.handle_callback_async("cal:am:2026-03-22:14:30", 1)
        assert r4.get("force_reply") is not None
        assert r4["interaction_state"]["date"] == "2026-03-22"
        assert r4["interaction_state"]["hour"] == 14
        assert r4["interaction_state"]["minute"] == 30

        # Step 5: Enter title -> event created
        from src.plugins.loader import PluginInteraction
        interaction = PluginInteraction(
            plugin_name="calendar",
            chat_id=1,
            action="create",
            state=r4["interaction_state"],
        )
        r5 = plugin.handle_interaction("Team Meeting", 1, interaction)
        assert "created" in r5["text"].lower()
        assert "Team Meeting" in r5["text"]
        mock_gcal.create_event.assert_called_once()


# ---------------------------------------------------------------------------
# GoogleCalendarClient
# ---------------------------------------------------------------------------

class TestGoogleCalendarClient:

    def test_available_no_file(self):
        client = GoogleCalendarClient(
            credentials_file="/nonexistent/path.json",
            calendar_id="test",
        )
        assert client.available is False

    def test_parse_event_timed(self):
        item = {
            "id": "abc123",
            "summary": "Meeting",
            "start": {"dateTime": "2026-03-21T10:00:00+09:00"},
            "end": {"dateTime": "2026-03-21T11:00:00+09:00"},
            "location": "Room A",
        }
        ev = GoogleCalendarClient._parse_event(item)
        assert ev.id == "abc123"
        assert ev.summary == "Meeting"
        assert ev.all_day is False
        assert ev.location == "Room A"
        assert ev.start.hour == 10

    def test_parse_event_allday(self):
        item = {
            "id": "allday1",
            "summary": "Holiday",
            "start": {"date": "2026-03-21"},
            "end": {"date": "2026-03-22"},
        }
        ev = GoogleCalendarClient._parse_event(item)
        assert ev.all_day is True
        assert ev.summary == "Holiday"

    def test_parse_event_no_summary(self):
        item = {
            "id": "nosummary",
            "start": {"dateTime": "2026-03-21T10:00:00+09:00"},
            "end": {"dateTime": "2026-03-21T11:00:00+09:00"},
        }
        ev = GoogleCalendarClient._parse_event(item)
        assert ev.summary == "(제목 없음)"
