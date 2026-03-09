"""SchedulerManager 테스트."""

import pytest
from datetime import time
from unittest.mock import MagicMock, AsyncMock

from src.scheduler_manager import SchedulerManager, ScheduledJob


class TestSchedulerManager:
    """SchedulerManager 테스트."""

    def setup_method(self):
        """각 테스트 전 새 인스턴스 생성."""
        # 싱글톤 리셋
        SchedulerManager._instance = None
        self.manager = SchedulerManager()

    def test_singleton(self):
        """싱글톤 패턴 테스트."""
        manager1 = SchedulerManager()
        manager2 = SchedulerManager()
        assert manager1 is manager2

    def test_set_app(self):
        """Application 설정 테스트."""
        mock_app = MagicMock()
        mock_app.job_queue = MagicMock()

        self.manager.set_app(mock_app)
        assert self.manager._app is mock_app
        assert self.manager.job_queue is mock_app.job_queue

    def test_job_queue_without_app_raises(self):
        """Application 없이 job_queue 접근 시 에러."""
        with pytest.raises(RuntimeError, match="Application이 설정되지 않음"):
            _ = self.manager.job_queue

    def test_register_daily(self):
        """매일 실행 작업 등록 테스트."""
        mock_app = MagicMock()
        mock_job = MagicMock()
        mock_app.job_queue.run_daily.return_value = mock_job
        self.manager.set_app(mock_app)

        callback = AsyncMock()
        result = self.manager.register_daily(
            name="test_job",
            callback=callback,
            time_of_day=time(10, 0),
            owner="TestOwner",
        )

        assert result is True
        assert "test_job" in self.manager._jobs
        job_info = self.manager._jobs["test_job"]
        assert job_info.name == "test_job"
        assert job_info.owner == "TestOwner"
        assert job_info.schedule_type == "daily"
        assert "10:00" in job_info.schedule_info

    def test_register_repeating(self):
        """반복 실행 작업 등록 테스트."""
        mock_app = MagicMock()
        mock_job = MagicMock()
        mock_app.job_queue.run_repeating.return_value = mock_job
        self.manager.set_app(mock_app)

        callback = AsyncMock()
        result = self.manager.register_repeating(
            name="repeating_job",
            callback=callback,
            interval=3600,  # 1시간
            owner="TestOwner",
        )

        assert result is True
        assert "repeating_job" in self.manager._jobs
        job_info = self.manager._jobs["repeating_job"]
        assert job_info.schedule_type == "repeating"
        assert "1h" in job_info.schedule_info

    def test_register_once(self):
        """일회성 작업 등록 테스트."""
        mock_app = MagicMock()
        mock_job = MagicMock()
        mock_app.job_queue.run_once.return_value = mock_job
        self.manager.set_app(mock_app)

        callback = AsyncMock()
        result = self.manager.register_once(
            name="once_job",
            callback=callback,
            when=60,
            owner="TestOwner",
        )

        assert result is True
        assert "once_job" in self.manager._jobs
        job_info = self.manager._jobs["once_job"]
        assert job_info.schedule_type == "once"
        assert "60s" in job_info.schedule_info

    def test_unregister(self):
        """작업 등록 해제 테스트."""
        mock_app = MagicMock()
        mock_job = MagicMock()
        mock_app.job_queue.run_daily.return_value = mock_job
        self.manager.set_app(mock_app)

        # 등록
        callback = AsyncMock()
        self.manager.register_daily(
            name="to_remove",
            callback=callback,
            time_of_day=time(10, 0),
            owner="TestOwner",
        )
        assert "to_remove" in self.manager._jobs

        # 해제
        result = self.manager.unregister("to_remove")
        assert result is True
        assert "to_remove" not in self.manager._jobs
        mock_job.schedule_removal.assert_called_once()

    def test_unregister_nonexistent(self):
        """존재하지 않는 작업 해제 시도."""
        result = self.manager.unregister("nonexistent")
        assert result is False

    def test_unregister_handles_missing_underlying_job(self):
        """APScheduler에서 이미 사라진 job도 안전하게 해제."""
        from apscheduler.jobstores.base import JobLookupError

        mock_app = MagicMock()
        mock_job = MagicMock()
        mock_job.schedule_removal.side_effect = JobLookupError("gone")
        mock_app.job_queue.run_daily.return_value = mock_job
        self.manager.set_app(mock_app)

        callback = AsyncMock()
        self.manager.register_daily("gone_job", callback, time(10, 0), "OwnerA")

        assert self.manager.unregister("gone_job") is True
        assert "gone_job" not in self.manager._jobs

    def test_unregister_by_owner(self):
        """특정 owner의 모든 작업 해제."""
        mock_app = MagicMock()
        mock_app.job_queue.run_daily.return_value = MagicMock()
        self.manager.set_app(mock_app)

        callback = AsyncMock()
        self.manager.register_daily("job1", callback, time(10, 0), "OwnerA")
        self.manager.register_daily("job2", callback, time(11, 0), "OwnerA")
        self.manager.register_daily("job3", callback, time(12, 0), "OwnerB")

        assert len(self.manager._jobs) == 3

        # OwnerA 작업만 해제
        removed = self.manager.unregister_by_owner("OwnerA")
        assert removed == 2
        assert len(self.manager._jobs) == 1
        assert "job3" in self.manager._jobs

    def test_list_jobs(self):
        """모든 작업 목록 조회."""
        mock_app = MagicMock()
        mock_app.job_queue.run_daily.return_value = MagicMock()
        self.manager.set_app(mock_app)

        callback = AsyncMock()
        self.manager.register_daily("job1", callback, time(10, 0), "Owner1")
        self.manager.register_daily("job2", callback, time(11, 0), "Owner2")

        jobs = self.manager.list_jobs()
        assert len(jobs) == 2
        assert all(isinstance(j, ScheduledJob) for j in jobs)

    def test_list_jobs_by_owner(self):
        """특정 owner의 작업 목록 조회."""
        mock_app = MagicMock()
        mock_app.job_queue.run_daily.return_value = MagicMock()
        self.manager.set_app(mock_app)

        callback = AsyncMock()
        self.manager.register_daily("job1", callback, time(10, 0), "OwnerA")
        self.manager.register_daily("job2", callback, time(11, 0), "OwnerA")
        self.manager.register_daily("job3", callback, time(12, 0), "OwnerB")

        jobs = self.manager.list_jobs_by_owner("OwnerA")
        assert len(jobs) == 2
        assert all(j.owner == "OwnerA" for j in jobs)

    def test_get_status_text_empty(self):
        """작업 없을 때 상태 텍스트."""
        text = self.manager.get_status_text()
        assert "No scheduled jobs" in text

    def test_get_status_text_with_jobs(self):
        """작업 있을 때 상태 텍스트."""
        mock_app = MagicMock()
        mock_app.job_queue.run_daily.return_value = MagicMock()
        self.manager.set_app(mock_app)

        callback = AsyncMock()
        self.manager.register_daily("test_job", callback, time(10, 0), "TestOwner")

        text = self.manager.get_status_text()
        assert "Scheduled Jobs" in text
        assert "TestOwner" in text
        assert "test_job" in text

    def test_overwrite_existing_job(self):
        """동일 이름 작업 덮어쓰기."""
        mock_app = MagicMock()
        mock_job1 = MagicMock()
        mock_job2 = MagicMock()
        mock_app.job_queue.run_daily.side_effect = [mock_job1, mock_job2]
        self.manager.set_app(mock_app)

        callback = AsyncMock()
        self.manager.register_daily("same_name", callback, time(10, 0), "Owner1")
        self.manager.register_daily("same_name", callback, time(11, 0), "Owner2")

        # 기존 작업 제거됨
        mock_job1.schedule_removal.assert_called_once()
        # 새 작업으로 대체
        assert self.manager._jobs["same_name"].owner == "Owner2"
