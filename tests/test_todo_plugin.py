"""Todo 플러그인 테스트."""

import pytest
import tempfile
from pathlib import Path

from plugins.builtin.todo.plugin import TodoPlugin
from plugins.builtin.todo.manager import TodoManager, TimeSlot, DailyTodo


class TestTodoManager:
    """TodoManager 테스트."""

    def test_create_daily_todo(self, tmp_path):
        """일일 할일 생성 테스트."""
        manager = TodoManager(tmp_path)
        daily = manager.get_today(123)

        assert daily.date is not None
        assert daily.pending_input is False

    def test_add_tasks(self, tmp_path):
        """할일 추가 테스트."""
        manager = TodoManager(tmp_path)
        tasks = {
            TimeSlot.MORNING: ["회의", "이메일"],
            TimeSlot.AFTERNOON: ["점심 약속"],
            TimeSlot.EVENING: ["운동"],
        }

        daily = manager.add_tasks_from_text(123, tasks)

        assert len(daily.get_tasks(TimeSlot.MORNING)) == 2
        assert len(daily.get_tasks(TimeSlot.AFTERNOON)) == 1
        assert len(daily.get_tasks(TimeSlot.EVENING)) == 1

    def test_mark_done_by_text(self, tmp_path):
        """텍스트로 완료 처리 테스트."""
        manager = TodoManager(tmp_path)
        tasks = {TimeSlot.MORNING: ["회의하기"]}
        manager.add_tasks_from_text(123, tasks)

        result = manager.mark_done_by_text(123, "회의")

        assert result is True
        daily = manager.get_today(123)
        assert daily.get_tasks(TimeSlot.MORNING)[0].done is True

    def test_mark_done_by_index(self, tmp_path):
        """인덱스로 완료 처리 테스트."""
        manager = TodoManager(tmp_path)
        tasks = {TimeSlot.MORNING: ["회의", "이메일"]}
        manager.add_tasks_from_text(123, tasks)

        result = manager.mark_done_by_index(123, TimeSlot.MORNING, 1)

        assert result is True
        daily = manager.get_today(123)
        assert daily.get_tasks(TimeSlot.MORNING)[1].done is True
        assert daily.get_tasks(TimeSlot.MORNING)[0].done is False

    def test_pending_input_state(self, tmp_path):
        """입력 대기 상태 테스트."""
        manager = TodoManager(tmp_path)

        manager.set_pending_input(123, True)
        assert manager.is_pending_input(123) is True

        manager.set_pending_input(123, False)
        assert manager.is_pending_input(123) is False

    def test_get_daily_summary(self, tmp_path):
        """일일 요약 테스트."""
        manager = TodoManager(tmp_path)
        tasks = {
            TimeSlot.MORNING: ["회의"],
            TimeSlot.AFTERNOON: ["점심"],
        }
        manager.add_tasks_from_text(123, tasks)

        summary = manager.get_daily_summary(123)

        assert "오전" in summary
        assert "오후" in summary
        assert "회의" in summary
        assert "점심" in summary


class TestTodoPlugin:
    """TodoPlugin 테스트."""

    @pytest.fixture
    def plugin(self, tmp_path):
        """플러그인 인스턴스 생성."""
        p = TodoPlugin()
        p._base_dir = tmp_path
        return p

    @pytest.mark.asyncio
    async def test_can_handle_todo_query(self, plugin):
        """할일 조회 패턴 감지."""
        assert await plugin.can_handle("오늘 할일 보여줘", 123)
        assert await plugin.can_handle("할일 목록", 123)
        assert await plugin.can_handle("오전 할일", 123)

    @pytest.mark.asyncio
    async def test_can_handle_exclude_patterns(self, plugin):
        """제외 패턴 테스트 - AI로 넘겨야 함."""
        assert await plugin.can_handle("할일이란 뭐야", 123) is False
        assert await plugin.can_handle("todo 영어로 뭐야", 123) is False

    @pytest.mark.asyncio
    async def test_can_handle_pending_input(self, plugin):
        """입력 대기 상태에서 모든 메시지 처리."""
        plugin.manager.set_pending_input(123, True)

        # 아무 메시지나 처리 가능
        assert await plugin.can_handle("아무거나", 123) is True
        assert await plugin.can_handle("오전에 회의하고 점심에 밥먹기", 123) is True

    @pytest.mark.asyncio
    async def test_handle_pending_input(self, plugin):
        """자유 형식 입력 처리 테스트."""
        plugin.manager.set_pending_input(123, True)

        result = await plugin.handle(
            "오전에 회의하고, 점심에 친구 만나고, 저녁에 운동",
            123
        )

        assert result.handled is True
        assert "할일 등록 완료" in result.response
        assert "회의" in result.response

    @pytest.mark.asyncio
    async def test_handle_done_by_text(self, plugin):
        """완료 처리 테스트 (텍스트)."""
        # 먼저 할일 추가
        plugin.manager.set_pending_input(123, True)
        await plugin.handle("오전에 회의", 123)

        # 완료 처리
        result = await plugin.handle("회의 끝났어", 123)

        assert result.handled is True
        assert "완료" in result.response

    @pytest.mark.asyncio
    async def test_handle_done_by_index(self, plugin):
        """완료 처리 테스트 (인덱스)."""
        # 먼저 할일 추가
        plugin.manager.set_pending_input(123, True)
        await plugin.handle("오전에 회의", 123)

        # 완료 처리
        result = await plugin.handle("1번 완료", 123)

        assert result.handled is True
        assert "완료" in result.response

    @pytest.mark.asyncio
    async def test_handle_query(self, plugin):
        """조회 테스트."""
        # 먼저 할일 추가
        plugin.manager.set_pending_input(123, True)
        await plugin.handle("오전에 회의", 123)

        # 조회
        result = await plugin.handle("오늘 할일", 123)

        assert result.handled is True
        assert "회의" in result.response

    @pytest.mark.asyncio
    async def test_handle_add_task(self, plugin):
        """할일 추가 테스트."""
        result = await plugin.handle("할일 추가: 보고서 작성", 123)

        assert result.handled is True
        assert "추가됨" in result.response
        assert "보고서 작성" in result.response


class TestTaskParsing:
    """할일 파싱 테스트."""

    @pytest.fixture
    def plugin(self, tmp_path):
        p = TodoPlugin()
        p._base_dir = tmp_path
        return p

    def test_parse_with_time_slots(self, plugin):
        """시간대별 파싱 테스트."""
        text = "오전에 회의, 오후에 점심, 저녁에 운동"
        tasks = plugin._parse_tasks_simple(text)

        assert "회의" in tasks[TimeSlot.MORNING]
        assert "점심" in tasks[TimeSlot.AFTERNOON]
        assert "운동" in tasks[TimeSlot.EVENING]

    def test_parse_with_informal_style(self, plugin):
        """구어체 파싱 테스트."""
        text = "저녁엔 운동해야해"
        tasks = plugin._parse_tasks_simple(text)

        assert "운동" in tasks[TimeSlot.EVENING]

    def test_parse_multiple_items(self, plugin):
        """여러 항목 파싱."""
        text = "회의하고, 점심, 운동"  # 쉼표로 명확히 분리
        tasks = plugin._parse_tasks_simple(text)

        total = sum(len(t) for t in tasks.values())
        assert total >= 3
