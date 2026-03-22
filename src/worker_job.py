"""Detached worker process for Claude jobs."""

import argparse
import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.ai import build_default_registry
from src.config import get_settings
from src.logging_config import logger, setup_logging
from src.repository import init_repository, shutdown_repository
from src.services.job_service import JobService
from src.services.session_service import SessionService


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run a detached Claude worker job.")
    parser.add_argument("--job-id", type=int, required=True, help="message_log job ID")
    return parser.parse_args()


async def _run(job_id: int) -> int:
    settings = get_settings()

    repo = init_repository(settings.db_path)
    session_service = SessionService(
        repo=repo,
        session_timeout_hours=settings.session_timeout_hours,
        session_purge_days=settings.session_purge_days,
    )
    ai_registry = build_default_registry(settings)
    job_service = JobService(
        repo=repo,
        session_service=session_service,
        ai_registry=ai_registry,
        telegram_token=settings.telegram_token,
    )

    ok = await job_service.run_job(job_id)
    shutdown_repository()
    return 0 if ok else 1


def main() -> None:
    log_level = os.getenv("LOG_LEVEL", "INFO").upper()
    setup_logging(level=log_level)

    args = _parse_args()
    logger.info(f"Detached worker start - pid={os.getpid()}, job_id={args.job_id}")

    try:
        raise_code = asyncio.run(_run(args.job_id))
    except Exception:
        logger.exception(f"Detached worker crashed - job_id={args.job_id}")
        raise_code = 1

    logger.info(f"Detached worker exit - pid={os.getpid()}, job_id={args.job_id}, code={raise_code}")
    sys.exit(raise_code)


if __name__ == "__main__":
    main()
