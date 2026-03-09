"""Schedule integration tests.

스케줄 통합 테스트.
"""

import pytest
from datetime import datetime

from src.repository.adapters.schedule_adapter import ScheduleManagerAdapter


class TestScheduleCreation:
    """스케줄 생성 테스트."""

    def test_add_schedule(self, repository):
        """스케줄 추가."""
        adapter = ScheduleManagerAdapter(repository)

        schedule = adapter.add(
            user_id="12345",
            chat_id=12345,
            hour=9,
            minute=0,
            message="아침 인사",
            name="모닝콜",
            schedule_type="chat",
            model="sonnet"
        )

        assert schedule.id is not None
        assert schedule.name == "모닝콜"
        assert schedule.hour == 9
        assert schedule.minute == 0
        assert schedule.enabled is True

    def test_add_workspace_schedule(self, repository):
        """워크스페이스 스케줄 추가."""
        adapter = ScheduleManagerAdapter(repository)

        schedule = adapter.add(
            user_id="12345",
            chat_id=12345,
            hour=10,
            minute=30,
            message="빌드 체크",
            name="빌드",
            schedule_type="workspace",
            model="sonnet",
            workspace_path="/Users/test/project"
        )

        assert schedule.type == "workspace"
        assert schedule.workspace_path == "/Users/test/project"

    def test_add_one_time_schedule(self, repository):
        """1회성 스케줄 추가."""
        adapter = ScheduleManagerAdapter(repository)

        schedule = adapter.add(
            user_id="12345",
            chat_id=12345,
            hour=22,
            minute=20,
            message="한 번만 실행",
            name="원타임",
            trigger_type="once",
        )

        assert schedule.trigger_type == "once"
        assert schedule.run_at_local is not None
        assert schedule.cron_expr is None
        assert "Once at" in schedule.trigger_summary


class TestScheduleManagement:
    """스케줄 관리 테스트."""

    def test_list_schedules_by_user(self, repository):
        """사용자별 스케줄 조회."""
        adapter = ScheduleManagerAdapter(repository)

        # 여러 스케줄 추가
        adapter.add("user1", 111, 8, 0, "메시지1", "스케줄1")
        adapter.add("user1", 111, 12, 0, "메시지2", "스케줄2")
        adapter.add("user2", 222, 9, 0, "메시지3", "스케줄3")

        # user1 스케줄만 조회
        user1_schedules = adapter.list_by_user("user1")
        assert len(user1_schedules) == 2

        # user2 스케줄
        user2_schedules = adapter.list_by_user("user2")
        assert len(user2_schedules) == 1

    def test_get_schedule_by_id(self, repository):
        """ID로 스케줄 조회."""
        adapter = ScheduleManagerAdapter(repository)

        created = adapter.add("12345", 12345, 7, 0, "메시지", "테스트")

        fetched = adapter.get(created.id)
        assert fetched is not None
        assert fetched.name == "테스트"

    def test_remove_schedule(self, repository):
        """스케줄 삭제."""
        adapter = ScheduleManagerAdapter(repository)

        schedule = adapter.add("12345", 12345, 8, 0, "메시지", "삭제할것")

        result = adapter.remove(schedule.id)
        assert result is True

        # 삭제 확인
        assert adapter.get(schedule.id) is None


class TestScheduleToggle:
    """스케줄 토글 테스트."""

    def test_toggle_schedule(self, repository):
        """스케줄 활성화/비활성화 토글."""
        adapter = ScheduleManagerAdapter(repository)

        schedule = adapter.add("12345", 12345, 8, 0, "메시지", "토글테스트")
        assert schedule.enabled is True

        # 비활성화
        new_state = adapter.toggle(schedule.id)
        assert new_state is False

        # 다시 활성화
        new_state = adapter.toggle(schedule.id)
        assert new_state is True

    def test_toggle_nonexistent_schedule(self, repository):
        """존재하지 않는 스케줄 토글."""
        adapter = ScheduleManagerAdapter(repository)

        result = adapter.toggle("nonexistent-id")
        assert result is None

    def test_toggle_once_schedule_rearms_next_occurrence(self, repository):
        """과거 1회성 스케줄을 다시 켜면 다음 로컬 시각으로 재무장."""
        from unittest.mock import AsyncMock, MagicMock

        schedule = repository.add_schedule(
            user_id="12345",
            chat_id=12345,
            hour=7,
            minute=5,
            message="한 번만",
            name="원타임",
            trigger_type="once",
            run_at_local="2020-01-01T07:05:00+09:00",
        )
        repository.toggle_schedule(schedule.id)  # disable first

        mock_scheduler = MagicMock()
        mock_executor = AsyncMock()
        adapter = ScheduleManagerAdapter(repository, mock_scheduler, mock_executor)

        new_state = adapter.toggle(schedule.id)

        assert new_state is True
        updated = adapter.get(schedule.id)
        assert updated is not None
        assert updated.run_at_local != "2020-01-01T07:05:00+09:00"
        mock_scheduler.register_once_at.assert_called_once()


class TestScheduleExecution:
    """스케줄 실행 테스트."""

    def test_update_run_info(self, repository):
        """실행 정보 업데이트."""
        adapter = ScheduleManagerAdapter(repository)

        schedule = adapter.add("12345", 12345, 8, 0, "메시지", "실행테스트")

        # 실행 정보 업데이트
        run_time = datetime.utcnow().isoformat()
        adapter.update_run(schedule.id, last_run=run_time)

        # 확인
        updated = adapter.get(schedule.id)
        assert updated.last_run == run_time
        assert updated.run_count == 1

    def test_update_run_with_error(self, repository):
        """에러 발생 시 실행 정보."""
        adapter = ScheduleManagerAdapter(repository)

        schedule = adapter.add("12345", 12345, 8, 0, "메시지", "에러테스트")

        # 에러와 함께 업데이트
        adapter.update_run(
            schedule.id,
            last_run=datetime.utcnow().isoformat(),
            last_error="TIMEOUT"
        )

        updated = adapter.get(schedule.id)
        assert updated.last_error == "TIMEOUT"

    def test_one_time_schedule_disables_after_successful_run(self, repository):
        """1회성 스케줄은 성공 실행 후 자동 비활성화."""
        adapter = ScheduleManagerAdapter(repository)

        schedule = adapter.add(
            "12345",
            12345,
            8,
            0,
            "메시지",
            "원타임",
            trigger_type="once",
        )

        adapter.update_run(schedule.id, last_run=datetime.utcnow().isoformat())

        updated = adapter.get(schedule.id)
        assert updated is not None
        assert updated.enabled is False
        assert updated.run_count == 1

    def test_stale_one_time_schedule_is_disabled_on_registration(self, repository):
        """지난 1회성 스케줄은 재시작 시 다시 등록하지 않고 비활성화."""
        from unittest.mock import AsyncMock, MagicMock

        schedule = repository.add_schedule(
            user_id="12345",
            chat_id=12345,
            hour=7,
            minute=5,
            message="한 번만",
            name="지난 스케줄",
            trigger_type="once",
            run_at_local="2020-01-01T07:05:00+09:00",
        )

        adapter = ScheduleManagerAdapter(repository, MagicMock(), AsyncMock())
        count = adapter.register_all_to_scheduler()

        assert count == 0
        updated = adapter.get(schedule.id)
        assert updated is not None
        assert updated.enabled is False


class TestScheduleList:
    """스케줄 목록 테스트."""

    def test_list_all_schedules(self, repository):
        """전체 스케줄 조회."""
        adapter = ScheduleManagerAdapter(repository)

        adapter.add("user1", 111, 8, 0, "메시지1", "스케줄1")
        adapter.add("user2", 222, 9, 0, "메시지2", "스케줄2")

        all_schedules = adapter.list_all()
        assert len(all_schedules) >= 2

    def test_get_schedule_summary(self, repository):
        """스케줄 요약 조회."""
        adapter = ScheduleManagerAdapter(repository)

        adapter.add("12345", 12345, 8, 0, "아침", "모닝")
        adapter.add("12345", 12345, 18, 0, "저녁", "이브닝")

        summary = adapter.get_schedule_summary("12345")
        assert "모닝" in summary
        assert "이브닝" in summary

    def test_empty_schedule_summary(self, repository):
        """빈 스케줄 요약."""
        adapter = ScheduleManagerAdapter(repository)

        summary = adapter.get_schedule_summary("no_schedules_user")
        assert "No scheduled tasks" in summary


class TestScheduleData:
    """스케줄 데이터 테스트."""

    def test_schedule_to_dict(self, repository):
        """스케줄 딕셔너리 변환."""
        adapter = ScheduleManagerAdapter(repository)

        schedule = adapter.add(
            user_id="12345",
            chat_id=12345,
            hour=14,
            minute=30,
            message="오후 알림",
            name="오후",
            model="opus",
            workspace_path="/test/path"
        )

        data = schedule.to_dict()

        assert data["id"] == schedule.id
        assert data["user_id"] == "12345"
        assert data["chat_id"] == 12345
        assert data["hour"] == 14
        assert data["minute"] == 30
        assert data["message"] == "오후 알림"
        assert data["name"] == "오후"
        assert data["model"] == "opus"
        assert data["workspace_path"] == "/test/path"


class TestSchedulerManagerIntegration:
    """SchedulerManager 통합 테스트."""

    def test_set_scheduler_manager(self, repository):
        """스케줄러 매니저 설정."""
        adapter = ScheduleManagerAdapter(repository)

        mock_scheduler = object()  # 간단한 mock
        adapter.set_scheduler_manager(mock_scheduler)

        assert adapter._scheduler_manager == mock_scheduler

    def test_register_all_without_scheduler(self, repository):
        """스케줄러 없이 등록 시도."""
        adapter = ScheduleManagerAdapter(repository)

        # 스케줄 추가
        adapter.add("12345", 12345, 8, 0, "메시지", "테스트")

        # 스케줄러 없이 등록 - 경고만 발생
        count = adapter.register_all_to_scheduler()
        assert count == 0


class TestScheduleEdgeCases:
    """스케줄 엣지 케이스 테스트."""

    def test_schedule_at_midnight(self, repository):
        """자정 스케줄."""
        adapter = ScheduleManagerAdapter(repository)

        schedule = adapter.add("12345", 12345, 0, 0, "자정", "미드나잇")
        assert schedule.hour == 0
        assert schedule.minute == 0

    def test_schedule_at_2359(self, repository):
        """23:59 스케줄."""
        adapter = ScheduleManagerAdapter(repository)

        schedule = adapter.add("12345", 12345, 23, 59, "막차", "라스트")
        assert schedule.hour == 23
        assert schedule.minute == 59

    def test_duplicate_name_allowed(self, repository):
        """중복 이름 허용."""
        adapter = ScheduleManagerAdapter(repository)

        s1 = adapter.add("12345", 12345, 8, 0, "메시지1", "같은이름")
        s2 = adapter.add("12345", 12345, 9, 0, "메시지2", "같은이름")

        assert s1.id != s2.id
        assert s1.name == s2.name
