"""Logging configuration with loguru and contextvars for request tracing.

Java의 MDC(Mapped Diagnostic Context)와 유사하게 동작:
- contextvars: 비동기 코루틴별 컨텍스트 유지 (스레드 로컬 대신)
- trace_id: 요청 추적용 UUID (같은 요청의 모든 로그에 동일 ID)

Usage:
    from src.logging_config import setup_logging, logger, set_trace_id, clear_trace_id

    # 앱 시작 시
    setup_logging(level="TRACE")

    # 요청 처리 시작
    set_trace_id()  # 새 UUID 생성
    logger.trace("상세 로그")
    logger.debug("디버그 로그")

    # 요청 처리 끝
    clear_trace_id()
"""

import contextvars
import logging
import sys
import uuid
from typing import Optional

from loguru import logger

# Context variable for trace ID (like Java MDC)
# 각 asyncio 코루틴은 자신만의 trace_id를 가짐
_trace_id: contextvars.ContextVar[str] = contextvars.ContextVar("trace_id", default="-")
_user_id: contextvars.ContextVar[str] = contextvars.ContextVar("user_id", default="-")
_session_id: contextvars.ContextVar[str] = contextvars.ContextVar("session_id", default="-")


def get_trace_id() -> str:
    """현재 컨텍스트의 trace_id 반환."""
    return _trace_id.get()


def get_user_id() -> str:
    """현재 컨텍스트의 user_id 반환."""
    return _user_id.get()


def get_session_id() -> str:
    """현재 컨텍스트의 session_id 반환."""
    return _session_id.get()


def set_trace_id(trace_id: Optional[str] = None) -> str:
    """새 trace_id 설정 (없으면 UUID 생성). 설정된 ID 반환."""
    new_id = trace_id or uuid.uuid4().hex[:8]
    _trace_id.set(new_id)
    return new_id


def set_user_id(user_id: str) -> None:
    """user_id 설정."""
    _user_id.set(user_id)


def set_session_id(session_id: Optional[str]) -> None:
    """session_id 설정."""
    _session_id.set(session_id[:8] if session_id else "-")


def clear_context() -> None:
    """trace_id, user_id, session_id 초기화."""
    _trace_id.set("-")
    _user_id.set("-")
    _session_id.set("-")


def _log_format(record: dict) -> str:
    """로그 포맷 생성 (trace_id, user_id, session_id 포함)."""
    trace_id = get_trace_id()
    user_id = get_user_id()
    session_id = get_session_id()

    # 컬러 포맷 (| 대신 - 사용)
    return (
        "<green>{time:HH:mm:ss.SSS}</green> - "
        "<level>{level: <8}</level> - "
        f"<cyan>{trace_id}</cyan> - "
        f"<yellow>{user_id: <12}</yellow> - "
        f"<magenta>{session_id: <8}</magenta> - "
        "<blue>{name}</blue>:<blue>{function}</blue>:<blue>{line}</blue> - "
        "<level>{message}</level>\n"
        "{exception}"
    )


def _log_format_file(record: dict) -> str:
    """파일용 로그 포맷 (컬러 없음)."""
    trace_id = get_trace_id()
    user_id = get_user_id()
    session_id = get_session_id()

    return (
        "{time:YYYY-MM-DD HH:mm:ss.SSS} - "
        "{level: <8} - "
        f"{trace_id} - "
        f"{user_id: <12} - "
        f"{session_id: <8} - "
        "{name}:{function}:{line} - "
        "{message}\n"
        "{exception}"
    )


class InterceptHandler(logging.Handler):
    """표준 logging → loguru 리다이렉트 핸들러.

    Python의 표준 logging을 사용하는 라이브러리(telegram, httpx 등)의
    로그도 loguru로 통합하여 같은 포맷으로 출력.
    """

    def emit(self, record: logging.LogRecord) -> None:
        # loguru 레벨로 변환
        try:
            level = logger.level(record.levelname).name
        except ValueError:
            level = record.levelno

        # 실제 호출 위치 찾기 (logging 모듈 내부 스킵)
        frame, depth = logging.currentframe(), 2
        while frame and frame.f_code.co_filename == logging.__file__:
            frame = frame.f_back
            depth += 1

        logger.opt(depth=depth, exception=record.exc_info).log(level, record.getMessage())


def setup_logging(
    level: str = "INFO",
    log_file: Optional[str] = None,
    intercept_stdlib: bool = True,
) -> None:
    """로깅 시스템 초기화.

    Args:
        level: 로그 레벨 (TRACE, DEBUG, INFO, WARNING, ERROR, CRITICAL)
        log_file: 로그 파일 경로 (None이면 콘솔만)
        intercept_stdlib: 표준 logging 라이브러리 통합 여부
    """
    # 기존 핸들러 제거
    logger.remove()

    # 콘솔 출력 (컬러)
    logger.add(
        sys.stderr,
        format=_log_format,
        level=level,
        colorize=True,
        backtrace=True,
        diagnose=True,
    )

    # 파일 출력 (옵션)
    if log_file:
        logger.add(
            log_file,
            format=_log_format_file,
            level=level,
            rotation="50 MB",
            retention="7 days",
            compression="gz",
            backtrace=True,
            diagnose=True,
        )

    # 표준 logging 통합
    if intercept_stdlib:
        logging.basicConfig(handlers=[InterceptHandler()], level=0, force=True)

        # 외부 라이브러리 로그 레벨 조정
        # TRACE일 때만 외부 라이브러리 DEBUG 출력, 그 외에는 WARNING으로 억제
        for lib_name in ["httpx", "httpcore", "telegram", "asyncio"]:
            lib_logger = logging.getLogger(lib_name)
            if level == "TRACE":
                lib_logger.setLevel(logging.DEBUG)
            else:
                lib_logger.setLevel(logging.WARNING)

    logger.info(f"로깅 초기화 완료 - level={level}, file={log_file or 'None'}")


# Re-export logger for convenience
__all__ = [
    "logger",
    "setup_logging",
    "set_trace_id",
    "set_user_id",
    "set_session_id",
    "get_trace_id",
    "get_user_id",
    "get_session_id",
    "clear_context",
]
