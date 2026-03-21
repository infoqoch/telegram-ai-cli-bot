"""Google Calendar 플러그인 테스트 - UI 동작 및 콜백 플로우."""

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
    summary: str = "테스트 이벤트",
    hour: int = 10,
    minute: int = 0,
    all_day: bool = False,
    d: date | None = None,
) -> CalendarEvent:
    """테스트용 CalendarEvent 생성."""
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
    """CalendarPlugin + mock GoogleCalendarClient 조합 반환."""
    plugin = CalendarPlugin()
    mock_gcal = MagicMock(spec=GoogleCalendarClient)
    mock_gcal.available = True
    plugin._gcal = mock_gcal
    return plugin, mock_gcal


# ---------------------------------------------------------------------------
# UI Helpers
# ---------------------------------------------------------------------------

class TestUIHelpers:
    """UI 유틸리티 함수 테스트."""

    def test_format_date_display(self):
        """날짜 짧은 포맷 (3월 21일 (토))."""
        d = date(2026, 3, 21)
        result = format_date_display(d)
        assert "3월" in result
        assert "21일" in result
        assert "토" in result

    def test_format_date_full(self):
        """날짜 전체 포맷 (2026년 3월 21일 (금))."""
        d = date(2026, 3, 21)
        result = format_date_full(d)
        assert "2026년" in result
        assert "3월" in result
        assert "21일" in result

    def test_build_calendar_grid(self):
        """달력 그리드 키보드 생성."""
        rows = build_calendar_grid(2026, 3)
        assert len(rows) > 0
        # Title row
        assert "2026년 3월" in rows[0][0].text
        # Weekday header
        assert rows[1][0].text == "월"
        assert rows[1][6].text == "일"

    def test_build_date_quick_select(self):
        """빠른 날짜 선택 키보드."""
        rows = build_date_quick_select()
        assert len(rows) >= 2
        # First row has 3 buttons: today, tomorrow, day after
        assert len(rows[0]) == 3
        assert "오늘" in rows[0][0].text

    def test_build_hour_keyboard(self):
        """시간 선택 키보드."""
        rows = build_hour_keyboard("2026-03-21")
        # Should have hour buttons + 종일 + nav
        total_buttons = sum(len(row) for row in rows)
        assert total_buttons > 12

    def test_build_minute_keyboard(self):
        """분 선택 키보드."""
        rows = build_minute_keyboard("2026-03-21", 11)
        assert len(rows[0]) == 4  # 00, 15, 30, 45
        assert "00분" in rows[0][0].text


# ---------------------------------------------------------------------------
# Plugin Basic
# ---------------------------------------------------------------------------

class TestCalendarPlugin:
    """CalendarPlugin 기본 동작 테스트."""

    def test_plugin_metadata(self):
        """플러그인 메타데이터 검증."""
        plugin = CalendarPlugin()
        assert plugin.name == "calendar"
        assert plugin.CALLBACK_PREFIX == "cal:"
        assert plugin.FORCE_REPLY_MARKER == "cal_title"

    @pytest.mark.asyncio
    async def test_can_handle_korean(self):
        """한국어 키워드 매칭."""
        plugin = CalendarPlugin()
        assert await plugin.can_handle("캘린더", 1)
        assert await plugin.can_handle("일정", 1)
        assert await plugin.can_handle("달력", 1)
        assert await plugin.can_handle("일정 추가", 1)

    @pytest.mark.asyncio
    async def test_can_handle_exclude(self):
        """제외 패턴 - AI에게 넘겨야 할 메시지."""
        plugin = CalendarPlugin()
        assert not await plugin.can_handle("캘린더란 뭐야", 1)
        assert not await plugin.can_handle("일정이란 뭐", 1)

    @pytest.mark.asyncio
    async def test_can_handle_no_match(self):
        """매칭되지 않는 메시지."""
        plugin = CalendarPlugin()
        assert not await plugin.can_handle("안녕하세요", 1)
        assert not await plugin.can_handle("오늘 뭐 먹지", 1)

    @pytest.mark.asyncio
    async def test_handle_unavailable(self):
        """Google API 미설정 시 안내 메시지."""
        plugin = CalendarPlugin()
        plugin._gcal = MagicMock(available=False)
        result = await plugin.handle("캘린더", 1)
        assert result.handled
        assert "설정되지 않았습니다" in result.response

    @pytest.mark.asyncio
    async def test_handle_shows_hub(self):
        """기본 handle → 오늘 일정 허브."""
        plugin, mock_gcal = _make_plugin()
        mock_gcal.list_events.return_value = [
            _make_event("ev1", "스탠드업", 9),
            _make_event("ev2", "점심 미팅", 12),
        ]
        result = await plugin.handle("캘린더", 1)
        assert result.handled
        assert "스탠드업" in result.response
        assert "점심 미팅" in result.response

    @pytest.mark.asyncio
    async def test_handle_empty_day(self):
        """일정 없는 날."""
        plugin, mock_gcal = _make_plugin()
        mock_gcal.list_events.return_value = []
        result = await plugin.handle("캘린더", 1)
        assert result.handled
        assert "일정이 없습니다" in result.response

    def test_scheduled_actions(self):
        """스케줄 액션 목록."""
        plugin = CalendarPlugin()
        actions = plugin.get_scheduled_actions()
        assert len(actions) == 1
        assert actions[0].name == "morning_briefing"


# ---------------------------------------------------------------------------
# Callback Flows
# ---------------------------------------------------------------------------

class TestCalendarCallbacks:
    """콜백 라우팅 및 플로우 테스트."""

    @pytest.mark.asyncio
    async def test_hub_callback(self):
        """cal:hub → 오늘 일정 표시."""
        plugin, mock_gcal = _make_plugin()
        mock_gcal.list_events.return_value = []
        result = await plugin.handle_callback_async("cal:hub", 1)
        assert "일정이 없습니다" in result["text"]

    @pytest.mark.asyncio
    async def test_day_navigation(self):
        """cal:day:날짜 → 해당 날 일정."""
        plugin, mock_gcal = _make_plugin()
        mock_gcal.list_events.return_value = [_make_event(d=date(2026, 3, 22))]
        result = await plugin.handle_callback_async("cal:day:2026-03-22", 1)
        assert "3월 22일" in result["text"] or "테스트 이벤트" in result["text"]

    @pytest.mark.asyncio
    async def test_add_flow_date_select(self):
        """cal:add → 날짜 선택 UI."""
        plugin, _ = _make_plugin()
        result = await plugin.handle_callback_async("cal:add", 1)
        assert "날짜" in result["text"]

    @pytest.mark.asyncio
    async def test_add_flow_hour_select(self):
        """cal:ad:날짜 → 시간 선택 UI."""
        plugin, _ = _make_plugin()
        result = await plugin.handle_callback_async("cal:ad:2026-03-22", 1)
        assert "시간" in result["text"] or "시작" in result["text"]

    @pytest.mark.asyncio
    async def test_add_flow_minute_select(self):
        """cal:ah:날짜:시간 → 분 선택 UI."""
        plugin, _ = _make_plugin()
        result = await plugin.handle_callback_async("cal:ah:2026-03-22:11", 1)
        assert "11시" in result["text"]

    @pytest.mark.asyncio
    async def test_add_flow_title_prompt(self):
        """cal:am:날짜:시간:분 → ForceReply."""
        plugin, _ = _make_plugin()
        result = await plugin.handle_callback_async("cal:am:2026-03-22:11:30", 1)
        assert result.get("force_reply") is not None
        assert result["interaction_action"] == "create"
        assert result["interaction_state"]["date"] == "2026-03-22"
        assert result["interaction_state"]["hour"] == 11
        assert result["interaction_state"]["minute"] == 30

    @pytest.mark.asyncio
    async def test_add_flow_allday(self):
        """cal:allday:날짜 → 종일 ForceReply."""
        plugin, _ = _make_plugin()
        result = await plugin.handle_callback_async("cal:allday:2026-03-22", 1)
        assert result.get("force_reply") is not None
        assert result["interaction_state"]["all_day"] is True

    @pytest.mark.asyncio
    async def test_calendar_grid(self):
        """cal:grid:년-월 → 달력 그리드."""
        plugin, _ = _make_plugin()
        result = await plugin.handle_callback_async("cal:grid:2026-03", 1)
        assert result.get("reply_markup") is not None

    @pytest.mark.asyncio
    async def test_event_detail(self):
        """cal:ev:인덱스 → 이벤트 상세."""
        plugin, mock_gcal = _make_plugin()
        mock_gcal.list_events.return_value = [_make_event("ev1", "디자인 리뷰", 11)]
        # Prime cache
        await plugin.handle_callback_async("cal:hub", 1)
        result = await plugin.handle_callback_async("cal:ev:0", 1)
        assert "디자인 리뷰" in result["text"]

    @pytest.mark.asyncio
    async def test_delete_confirm(self):
        """cal:del:인덱스 → 삭제 확인."""
        plugin, mock_gcal = _make_plugin()
        mock_gcal.list_events.return_value = [_make_event("ev1", "삭제 대상")]
        await plugin.handle_callback_async("cal:hub", 1)
        result = await plugin.handle_callback_async("cal:del:0", 1)
        assert "삭제할까요" in result["text"]

    @pytest.mark.asyncio
    async def test_delete_execute(self):
        """cal:delok:이벤트ID → 삭제 실행."""
        plugin, mock_gcal = _make_plugin()
        mock_gcal.delete_event.return_value = True
        result = await plugin.handle_callback_async("cal:delok:ev1", 1)
        assert "삭제되었습니다" in result["text"]
        mock_gcal.delete_event.assert_called_once_with("ev1")

    @pytest.mark.asyncio
    async def test_noop_callback(self):
        """cal:noop → 아무 동작 안함."""
        plugin, _ = _make_plugin()
        result = await plugin.handle_callback_async("cal:noop", 1)
        assert result.get("noop") is True


# ---------------------------------------------------------------------------
# Interaction (ForceReply)
# ---------------------------------------------------------------------------

class TestCalendarInteraction:
    """ForceReply 응답 처리 테스트."""

    def test_create_event(self):
        """제목 입력 → 이벤트 생성."""
        plugin, mock_gcal = _make_plugin()
        created = _make_event("new1", "새 이벤트", 11, 30)
        mock_gcal.create_event.return_value = created

        from src.plugins.loader import PluginInteraction
        interaction = PluginInteraction(
            plugin_name="calendar",
            chat_id=1,
            action="create",
            state={"date": "2026-03-22", "hour": 11, "minute": 30, "all_day": False},
        )

        result = plugin.handle_interaction("새 이벤트", 1, interaction)
        assert "등록되었습니다" in result["text"]
        mock_gcal.create_event.assert_called_once()

    def test_create_allday_event(self):
        """종일 이벤트 생성."""
        plugin, mock_gcal = _make_plugin()
        created = _make_event("new2", "종일 이벤트", all_day=True)
        mock_gcal.create_event.return_value = created

        from src.plugins.loader import PluginInteraction
        interaction = PluginInteraction(
            plugin_name="calendar",
            chat_id=1,
            action="create",
            state={"date": "2026-03-22", "hour": 0, "minute": 0, "all_day": True},
        )

        result = plugin.handle_interaction("종일 이벤트", 1, interaction)
        assert "등록되었습니다" in result["text"]

    def test_create_failure(self):
        """이벤트 생성 실패."""
        plugin, mock_gcal = _make_plugin()
        mock_gcal.create_event.return_value = None

        from src.plugins.loader import PluginInteraction
        interaction = PluginInteraction(
            plugin_name="calendar",
            chat_id=1,
            action="create",
            state={"date": "2026-03-22", "hour": 11, "minute": 0, "all_day": False},
        )

        result = plugin.handle_interaction("실패 테스트", 1, interaction)
        assert "실패" in result["text"]

    def test_empty_title(self):
        """빈 제목 거부."""
        plugin, _ = _make_plugin()

        from src.plugins.loader import PluginInteraction
        interaction = PluginInteraction(
            plugin_name="calendar",
            chat_id=1,
            action="create",
            state={"date": "2026-03-22", "hour": 11, "minute": 0, "all_day": False},
        )

        result = plugin.handle_interaction("", 1, interaction)
        assert "비어 있습니다" in result["text"]

    def test_edit_title(self):
        """제목 수정."""
        plugin, mock_gcal = _make_plugin()
        updated = _make_event("ev1", "수정된 제목", 11)
        mock_gcal.update_event.return_value = updated

        from src.plugins.loader import PluginInteraction
        interaction = PluginInteraction(
            plugin_name="calendar",
            chat_id=1,
            action="edit_title",
            state={"event_id": "ev1"},
        )

        result = plugin.handle_interaction("수정된 제목", 1, interaction)
        assert "수정되었습니다" in result["text"]


# ---------------------------------------------------------------------------
# Scheduled Actions
# ---------------------------------------------------------------------------

class TestCalendarScheduledActions:
    """스케줄 액션 테스트."""

    @pytest.mark.asyncio
    async def test_morning_briefing_with_events(self):
        """아침 브리핑 - 일정 있는 경우."""
        plugin, mock_gcal = _make_plugin()
        mock_gcal.list_events.return_value = [
            _make_event("ev1", "스탠드업", 9),
            _make_event("ev2", "점심", 12),
        ]

        result = await plugin.execute_scheduled_action("morning_briefing", 1)
        assert isinstance(result, dict)
        assert "스탠드업" in result["text"]
        assert "점심" in result["text"]
        assert "2건" in result["text"]

    @pytest.mark.asyncio
    async def test_morning_briefing_empty(self):
        """아침 브리핑 - 일정 없는 경우."""
        plugin, mock_gcal = _make_plugin()
        mock_gcal.list_events.return_value = []

        result = await plugin.execute_scheduled_action("morning_briefing", 1)
        assert isinstance(result, dict)
        assert "일정이 없습니다" in result["text"]


# ---------------------------------------------------------------------------
# Multi-step Happy Case: Add Event (E2E flow)
# ---------------------------------------------------------------------------

class TestCalendarAddEventFlow:
    """일정 추가 멀티스텝 해피케이스 (날짜→시간→분→제목→완료)."""

    @pytest.mark.asyncio
    async def test_full_add_flow(self):
        """완전한 일정 추가 플로우."""
        plugin, mock_gcal = _make_plugin()
        created = _make_event("new1", "팀 미팅", 14, 30)
        mock_gcal.create_event.return_value = created

        # Step 1: 추가 시작
        r1 = await plugin.handle_callback_async("cal:add", 1)
        assert "날짜" in r1["text"]

        # Step 2: 날짜 선택
        r2 = await plugin.handle_callback_async("cal:ad:2026-03-22", 1)
        assert "시간" in r2["text"] or "시작" in r2["text"]

        # Step 3: 시간 선택
        r3 = await plugin.handle_callback_async("cal:ah:2026-03-22:14", 1)
        assert "14시" in r3["text"]

        # Step 4: 분 선택 → ForceReply
        r4 = await plugin.handle_callback_async("cal:am:2026-03-22:14:30", 1)
        assert r4.get("force_reply") is not None
        assert r4["interaction_state"]["date"] == "2026-03-22"
        assert r4["interaction_state"]["hour"] == 14
        assert r4["interaction_state"]["minute"] == 30

        # Step 5: 제목 입력 → 이벤트 생성
        from src.plugins.loader import PluginInteraction
        interaction = PluginInteraction(
            plugin_name="calendar",
            chat_id=1,
            action="create",
            state=r4["interaction_state"],
        )
        r5 = plugin.handle_interaction("팀 미팅", 1, interaction)
        assert "등록되었습니다" in r5["text"]
        assert "팀 미팅" in r5["text"]
        mock_gcal.create_event.assert_called_once()


# ---------------------------------------------------------------------------
# GoogleCalendarClient
# ---------------------------------------------------------------------------

class TestGoogleCalendarClient:
    """GoogleCalendarClient 기본 검증."""

    def test_available_no_file(self):
        """크레덴셜 파일 없으면 unavailable."""
        client = GoogleCalendarClient(
            credentials_file="/nonexistent/path.json",
            calendar_id="test",
        )
        assert client.available is False

    def test_parse_event_timed(self):
        """시간 이벤트 파싱."""
        item = {
            "id": "abc123",
            "summary": "미팅",
            "start": {"dateTime": "2026-03-21T10:00:00+09:00"},
            "end": {"dateTime": "2026-03-21T11:00:00+09:00"},
            "location": "회의실",
        }
        ev = GoogleCalendarClient._parse_event(item)
        assert ev.id == "abc123"
        assert ev.summary == "미팅"
        assert ev.all_day is False
        assert ev.location == "회의실"
        assert ev.start.hour == 10

    def test_parse_event_allday(self):
        """종일 이벤트 파싱."""
        item = {
            "id": "allday1",
            "summary": "휴일",
            "start": {"date": "2026-03-21"},
            "end": {"date": "2026-03-22"},
        }
        ev = GoogleCalendarClient._parse_event(item)
        assert ev.all_day is True
        assert ev.summary == "휴일"

    def test_parse_event_no_summary(self):
        """제목 없는 이벤트."""
        item = {
            "id": "nosummary",
            "start": {"dateTime": "2026-03-21T10:00:00+09:00"},
            "end": {"dateTime": "2026-03-21T11:00:00+09:00"},
        }
        ev = GoogleCalendarClient._parse_event(item)
        assert ev.summary == "(제목 없음)"
