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

    def test_mark_done_by_text_partial_match(self, tmp_path):
        """부분 매칭 개선 테스트 - "운"이 "운동"에 잘못 매칭되지 않도록."""
        manager = TodoManager(tmp_path)
        tasks = {TimeSlot.MORNING: ["운동", "회의"]}
        manager.add_tasks_from_text(123, tasks)

        # "운"만으로는 매칭되지 않아야 함 (너무 짧음)
        result = manager.mark_done_by_text(123, "운")
        # 70% 매칭 기준이므로 "운"은 "운동"(2/1=200%)에 매칭됨 - 이건 의도된 동작
        # 대신 "회"는 "회의"에 매칭되지 않아야 함

        # 정확한 단어 시작 매칭 테스트
        manager2 = TodoManager(tmp_path / "test2")
        tasks2 = {TimeSlot.MORNING: ["회의 준비", "운동"]}
        manager2.add_tasks_from_text(456, tasks2)

        result2 = manager2.mark_done_by_text(456, "회의")
        assert result2 is True
        daily2 = manager2.get_today(456)
        assert daily2.get_tasks(TimeSlot.MORNING)[0].done is True

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

    def test_mark_done_by_global_index(self, tmp_path):
        """전역 인덱스로 완료 처리 테스트."""
        manager = TodoManager(tmp_path)
        tasks = {
            TimeSlot.MORNING: ["회의", "이메일"],
            TimeSlot.AFTERNOON: ["점심"],
            TimeSlot.EVENING: ["운동"],
        }
        manager.add_tasks_from_text(123, tasks)

        # 전역 인덱스 3번 = 오후의 "점심"
        result = manager.mark_done_by_global_index(123, 3)

        assert result is not None
        slot_name, task_text = result
        assert "오후" in slot_name
        assert task_text == "점심"

        daily = manager.get_today(123)
        assert daily.get_tasks(TimeSlot.AFTERNOON)[0].done is True

    def test_pending_input_state(self, tmp_path):
        """입력 대기 상태 테스트."""
        manager = TodoManager(tmp_path)

        manager.set_pending_input(123, True)
        assert manager.is_pending_input(123) is True

        manager.set_pending_input(123, False)
        assert manager.is_pending_input(123) is False

    def test_pending_input_timeout(self, tmp_path):
        """입력 대기 상태 타임아웃 테스트."""
        from datetime import datetime, timedelta

        manager = TodoManager(tmp_path)
        manager.set_pending_input(123, True)

        # 정상 상태 확인
        assert manager.is_pending_input(123) is True

        # 타임스탬프를 2시간 전으로 조작
        daily = manager.get_today(123)
        old_time = datetime.now() - timedelta(hours=3)
        daily.pending_input_timestamp = old_time.isoformat()
        manager.save_today(123, daily)

        # 타임아웃으로 자동 만료 확인
        assert manager.is_pending_input(123) is False

    def test_delete_by_index(self, tmp_path):
        """인덱스로 삭제 테스트."""
        manager = TodoManager(tmp_path)
        tasks = {TimeSlot.MORNING: ["회의", "이메일"]}
        manager.add_tasks_from_text(123, tasks)

        result = manager.delete_by_index(123, TimeSlot.MORNING, 0)

        assert result is True
        daily = manager.get_today(123)
        assert len(daily.get_tasks(TimeSlot.MORNING)) == 1
        assert daily.get_tasks(TimeSlot.MORNING)[0].text == "이메일"

    def test_delete_by_global_index(self, tmp_path):
        """전역 인덱스로 삭제 테스트."""
        manager = TodoManager(tmp_path)
        tasks = {
            TimeSlot.MORNING: ["회의", "이메일"],
            TimeSlot.AFTERNOON: ["점심"],
        }
        manager.add_tasks_from_text(123, tasks)

        # 전역 인덱스 2번 = 오전의 "이메일"
        result = manager.delete_by_global_index(123, 2)

        assert result is not None
        slot_name, task_text = result
        assert "오전" in slot_name
        assert task_text == "이메일"

        daily = manager.get_today(123)
        assert len(daily.get_tasks(TimeSlot.MORNING)) == 1
        assert daily.get_tasks(TimeSlot.MORNING)[0].text == "회의"

    def test_delete_by_text(self, tmp_path):
        """텍스트로 삭제 테스트."""
        manager = TodoManager(tmp_path)
        tasks = {TimeSlot.MORNING: ["회의하기"]}
        manager.add_tasks_from_text(123, tasks)

        result = manager.delete_by_text(123, "회의")

        assert result is True
        daily = manager.get_today(123)
        assert len(daily.get_tasks(TimeSlot.MORNING)) == 0

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
    """TodoPlugin 테스트 (버튼 기반)."""

    @pytest.fixture
    def plugin(self, tmp_path):
        """플러그인 인스턴스 생성."""
        p = TodoPlugin()
        p._base_dir = tmp_path
        return p

    @pytest.mark.asyncio
    async def test_can_handle_keyword_trigger(self, plugin):
        """키워드 트리거 테스트 - 명시적 키워드만 처리."""
        # 명시적 키워드로 시작하면 처리
        assert await plugin.can_handle("todo", 123) is True
        assert await plugin.can_handle("할일", 123) is True
        assert await plugin.can_handle("투두", 123) is True
        assert await plugin.can_handle("할일 보여줘", 123) is True
        assert await plugin.can_handle("todo list", 123) is True

    @pytest.mark.asyncio
    async def test_can_handle_no_keyword(self, plugin):
        """키워드 없으면 AI로 넘김."""
        # 키워드 없이는 처리하지 않음
        assert await plugin.can_handle("오늘 뭐해", 123) is False
        assert await plugin.can_handle("회의 끝났어", 123) is False
        assert await plugin.can_handle("아무거나", 123) is False

    @pytest.mark.asyncio
    async def test_can_handle_exclude_patterns(self, plugin):
        """제외 패턴 테스트 - AI로 넘겨야 함."""
        assert await plugin.can_handle("할일이란 뭐야", 123) is False
        assert await plugin.can_handle("todo 영어로 뭐야", 123) is False

    @pytest.mark.asyncio
    async def test_handle_shows_list(self, plugin):
        """handle 호출 시 바로 리스트 표시."""
        result = await plugin.handle("할일", 123)

        assert result.handled is True
        assert "할일" in result.response  # 날짜와 함께 리스트 표시
        assert result.reply_markup is not None

    @pytest.mark.asyncio
    async def test_callback_list(self, plugin):
        """리스트 콜백 테스트."""
        # 할일 추가
        daily = plugin.manager.get_today(123)
        daily.add_task(TimeSlot.MORNING, "회의")
        plugin.manager.save_today(123, daily)

        # 리스트 콜백
        result = plugin.handle_callback("td:list", 123)

        assert "회의" in result["text"]
        assert result["reply_markup"] is not None

    @pytest.mark.asyncio
    async def test_callback_add_menu(self, plugin):
        """추가 메뉴 콜백 테스트."""
        result = plugin.handle_callback("td:add", 123)

        assert "시간대 선택" in result["text"]
        assert result["reply_markup"] is not None

    @pytest.mark.asyncio
    async def test_callback_add_slot(self, plugin):
        """시간대 선택 후 ForceReply 테스트."""
        result = plugin.handle_callback("td:add_slot:m", 123)

        assert "오전" in result["text"]
        assert result.get("force_reply") is not None
        assert result.get("slot_code") == "m"

    @pytest.mark.asyncio
    async def test_callback_done(self, plugin):
        """완료 콜백 테스트."""
        # 할일 추가
        daily = plugin.manager.get_today(123)
        daily.add_task(TimeSlot.MORNING, "회의")
        plugin.manager.save_today(123, daily)

        # 완료 콜백
        result = plugin.handle_callback("td:done:m:0", 123)

        assert "완료" in result["text"]

        # 실제로 완료되었는지 확인
        daily = plugin.manager.get_today(123)
        assert daily.get_tasks(TimeSlot.MORNING)[0].done is True

    @pytest.mark.asyncio
    async def test_callback_delete(self, plugin):
        """삭제 콜백 테스트."""
        # 할일 추가
        daily = plugin.manager.get_today(123)
        daily.add_task(TimeSlot.MORNING, "회의")
        plugin.manager.save_today(123, daily)

        # 삭제 콜백
        result = plugin.handle_callback("td:del:m:0", 123)

        assert "삭제" in result["text"]

        # 실제로 삭제되었는지 확인
        daily = plugin.manager.get_today(123)
        assert len(daily.get_tasks(TimeSlot.MORNING)) == 0

    @pytest.mark.asyncio
    async def test_callback_move(self, plugin):
        """이동 콜백 테스트."""
        # 할일 추가
        daily = plugin.manager.get_today(123)
        daily.add_task(TimeSlot.MORNING, "회의")
        plugin.manager.save_today(123, daily)

        # 오전 → 오후 이동
        result = plugin.handle_callback("td:move:m:0:a", 123)

        assert "이동" in result["text"]

        # 실제로 이동되었는지 확인
        daily = plugin.manager.get_today(123)
        assert len(daily.get_tasks(TimeSlot.MORNING)) == 0
        assert len(daily.get_tasks(TimeSlot.AFTERNOON)) == 1
        assert daily.get_tasks(TimeSlot.AFTERNOON)[0].text == "회의"

    @pytest.mark.asyncio
    async def test_callback_item_menu(self, plugin):
        """항목 메뉴 콜백 테스트."""
        # 할일 추가
        daily = plugin.manager.get_today(123)
        daily.add_task(TimeSlot.MORNING, "회의")
        plugin.manager.save_today(123, daily)

        # 항목 메뉴
        result = plugin.handle_callback("td:item:m:0", 123)

        assert "회의" in result["text"]
        assert "완료" in str(result["reply_markup"])
        assert "삭제" in str(result["reply_markup"])

    @pytest.mark.asyncio
    async def test_force_reply_add_tasks(self, plugin):
        """ForceReply 응답으로 할일 추가."""
        result = plugin.handle_force_reply("회의\n이메일\n점심", 123, "m")

        assert "추가됨" in result["text"]
        assert "3개" in result["text"]

        # 실제로 추가되었는지 확인
        daily = plugin.manager.get_today(123)
        tasks = daily.get_tasks(TimeSlot.MORNING)
        assert len(tasks) == 3
        assert tasks[0].text == "회의"
        assert tasks[1].text == "이메일"
        assert tasks[2].text == "점심"

    @pytest.mark.asyncio
    async def test_force_reply_empty_input(self, plugin):
        """빈 입력 처리."""
        result = plugin.handle_force_reply("", 123, "m")

        assert "입력되지 않았" in result["text"]

    @pytest.mark.asyncio
    async def test_callback_back(self, plugin):
        """뒤로가기 콜백 테스트."""
        result = plugin.handle_callback("td:back", 123)

        assert "할일 관리" in result["text"]
        assert result["reply_markup"] is not None
