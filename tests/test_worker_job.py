"""worker_job 모듈 테스트.

detached worker 프로세스의 진입점인 worker_job의 핵심 경로를 검증한다:
- CLI 인자 파싱 (_parse_args)
- 정상 실행 및 실패 시 종료코드 (_run)
- 예외 발생 시 크래시 핸들링 (main)
"""

import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


class TestParseArgs:
    """_parse_args CLI 인자 파싱 테스트."""

    def test_valid_job_id(self):
        """--job-id가 정상적으로 파싱된다."""
        with patch("sys.argv", ["worker_job", "--job-id", "42"]):
            from src.worker_job import _parse_args
            args = _parse_args()
            assert args.job_id == 42

    def test_missing_job_id_exits(self):
        """--job-id 누락 시 SystemExit이 발생한다."""
        with patch("sys.argv", ["worker_job"]):
            from src.worker_job import _parse_args
            with pytest.raises(SystemExit):
                _parse_args()


class TestRun:
    """_run 비동기 실행 테스트."""

    @pytest.mark.asyncio
    async def test_successful_job_returns_zero(self):
        """run_job이 True를 반환하면 종료코드 0."""
        with patch("src.worker_job.get_settings") as mock_settings, \
             patch("src.worker_job.init_repository") as mock_init_repo, \
             patch("src.worker_job.build_default_registry") as mock_registry, \
             patch("src.worker_job.JobService") as MockJobService, \
             patch("src.worker_job.SessionService"), \
             patch("src.worker_job.shutdown_repository"):

            mock_settings.return_value = MagicMock(
                db_path=":memory:",
                session_timeout_hours=24,
                telegram_token="test-token",
            )
            mock_init_repo.return_value = MagicMock()
            mock_registry.return_value = MagicMock()

            mock_job_service = MagicMock()
            mock_job_service.run_job = AsyncMock(return_value=True)
            MockJobService.return_value = mock_job_service

            from src.worker_job import _run
            result = await _run(42)

            assert result == 0
            mock_job_service.run_job.assert_called_once_with(42)

    @pytest.mark.asyncio
    async def test_failed_job_returns_one(self):
        """run_job이 False를 반환하면 종료코드 1."""
        with patch("src.worker_job.get_settings") as mock_settings, \
             patch("src.worker_job.init_repository") as mock_init_repo, \
             patch("src.worker_job.build_default_registry") as mock_registry, \
             patch("src.worker_job.JobService") as MockJobService, \
             patch("src.worker_job.SessionService"), \
             patch("src.worker_job.shutdown_repository"):

            mock_settings.return_value = MagicMock(
                db_path=":memory:",
                session_timeout_hours=24,
                telegram_token="test-token",
            )
            mock_init_repo.return_value = MagicMock()
            mock_registry.return_value = MagicMock()

            mock_job_service = MagicMock()
            mock_job_service.run_job = AsyncMock(return_value=False)
            MockJobService.return_value = mock_job_service

            from src.worker_job import _run
            result = await _run(99)

            assert result == 1

    @pytest.mark.asyncio
    async def test_shutdown_called_on_success(self):
        """run_job 성공 후 shutdown_repository가 호출된다."""
        with patch("src.worker_job.get_settings") as mock_settings, \
             patch("src.worker_job.init_repository") as mock_init_repo, \
             patch("src.worker_job.build_default_registry") as mock_registry, \
             patch("src.worker_job.JobService") as MockJobService, \
             patch("src.worker_job.SessionService"), \
             patch("src.worker_job.shutdown_repository") as mock_shutdown:

            mock_settings.return_value = MagicMock(
                db_path=":memory:",
                session_timeout_hours=24,
                telegram_token="test-token",
            )
            mock_init_repo.return_value = MagicMock()
            mock_registry.return_value = MagicMock()

            mock_job_service = MagicMock()
            mock_job_service.run_job = AsyncMock(return_value=True)
            MockJobService.return_value = mock_job_service

            from src.worker_job import _run
            await _run(5)

            mock_shutdown.assert_called_once()


class TestMain:
    """main 함수 테스트."""

    def test_main_calls_run_and_exits_with_zero(self):
        """main이 _run을 호출하고 성공 시 sys.exit(0)으로 종료한다."""
        with patch("sys.argv", ["worker_job", "--job-id", "7"]), \
             patch("src.worker_job.setup_logging"), \
             patch("src.worker_job.asyncio") as mock_asyncio, \
             patch("src.worker_job.sys") as mock_sys:

            mock_asyncio.run.return_value = 0

            from src.worker_job import main
            main()

            mock_sys.exit.assert_called_once_with(0)

    def test_main_calls_run_and_exits_with_one_on_failure(self):
        """main이 _run을 호출하고 실패 시 sys.exit(1)으로 종료한다."""
        with patch("sys.argv", ["worker_job", "--job-id", "7"]), \
             patch("src.worker_job.setup_logging"), \
             patch("src.worker_job.asyncio") as mock_asyncio, \
             patch("src.worker_job.sys") as mock_sys:

            mock_asyncio.run.return_value = 1

            from src.worker_job import main
            main()

            mock_sys.exit.assert_called_once_with(1)

    def test_main_handles_exception_exits_with_one(self):
        """_run에서 예외 발생 시 exit code 1로 종료한다."""
        with patch("sys.argv", ["worker_job", "--job-id", "7"]), \
             patch("src.worker_job.setup_logging"), \
             patch("src.worker_job.asyncio") as mock_asyncio, \
             patch("src.worker_job.sys") as mock_sys:

            mock_asyncio.run.side_effect = RuntimeError("DB connection failed")

            from src.worker_job import main
            main()

            mock_sys.exit.assert_called_once_with(1)
