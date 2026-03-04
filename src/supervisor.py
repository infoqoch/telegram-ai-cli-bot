"""Bot supervisor - 크래시 시 자동 재시작.

부모-자식 프로세스 구조로 봇을 감시하고 비정상 종료 시 재시작.
크로스플랫폼 (macOS, Linux) 지원.
"""

import atexit
import fcntl
import os
import signal
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

import httpx

from src.logging_config import logger, setup_logging

# 상수
LOCK_FILE = Path("/tmp/telegram-bot-supervisor.lock")
MAX_RESTART_DELAY = 300  # 최대 5분
INITIAL_RESTART_DELAY = 5  # 초기 5초
CRASH_RESET_TIME = 60  # 60초 이상 정상 실행 시 딜레이 리셋

# 전역 상태
_lock_fd = None
_child_process = None
_shutdown_requested = False
_telegram_token = None
_maintainer_chat_id = None


def _load_telegram_config():
    """환경변수에서 텔레그램 설정 로드."""
    global _telegram_token, _maintainer_chat_id
    _telegram_token = os.getenv("TELEGRAM_TOKEN")
    _maintainer_chat_id = os.getenv("MAINTAINER_CHAT_ID")
    if _maintainer_chat_id:
        try:
            _maintainer_chat_id = int(_maintainer_chat_id)
        except ValueError:
            _maintainer_chat_id = None


def notify_maintainer(message: str) -> bool:
    """메인테이너에게 텔레그램 메시지 전송.

    Args:
        message: 전송할 메시지 (HTML 형식 지원)

    Returns:
        성공 여부
    """
    if not _telegram_token or not _maintainer_chat_id:
        logger.trace("메인테이너 알림 스킵 - 설정 없음")
        return False

    try:
        url = f"https://api.telegram.org/bot{_telegram_token}/sendMessage"
        data = {
            "chat_id": _maintainer_chat_id,
            "text": message,
            "parse_mode": "HTML",
        }

        with httpx.Client(timeout=10) as client:
            response = client.post(url, json=data)

        if response.status_code == 200:
            logger.debug(f"메인테이너 알림 전송 성공")
            return True
        else:
            logger.warning(f"메인테이너 알림 실패: {response.status_code}")
            return False

    except Exception as e:
        logger.warning(f"메인테이너 알림 오류: {e}")
        return False


def acquire_lock() -> bool:
    """Supervisor 싱글톤 락 획득."""
    global _lock_fd
    logger.trace(f"acquire_lock() - file={LOCK_FILE}")

    try:
        _lock_fd = open(LOCK_FILE, "w")
        fcntl.flock(_lock_fd.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        _lock_fd.write(str(os.getpid()))
        _lock_fd.flush()
        logger.trace("락 획득 성공")
        return True
    except (IOError, OSError) as e:
        logger.trace(f"락 획득 실패: {e}")
        if _lock_fd:
            _lock_fd.close()
        return False


def release_lock():
    """락 해제."""
    global _lock_fd
    logger.trace("release_lock()")

    if _lock_fd:
        try:
            fcntl.flock(_lock_fd.fileno(), fcntl.LOCK_UN)
            _lock_fd.close()
            LOCK_FILE.unlink(missing_ok=True)
            logger.trace("락 해제 완료")
        except Exception as e:
            logger.trace(f"락 해제 오류: {e}")
        _lock_fd = None


def signal_handler(signum, frame):
    """시그널 핸들러 - 자식에게 전달 후 종료."""
    global _shutdown_requested, _child_process

    sig_name = signal.Signals(signum).name
    logger.info(f"시그널 수신: {sig_name}")
    logger.trace(f"signum={signum}, frame={frame}")
    _shutdown_requested = True

    if _child_process and _child_process.poll() is None:
        logger.info("자식 프로세스에 SIGTERM 전달...")
        logger.trace(f"child_pid={_child_process.pid}")
        _child_process.terminate()
        try:
            _child_process.wait(timeout=10)
            logger.trace("자식 프로세스 정상 종료됨")
        except subprocess.TimeoutExpired:
            logger.warning("자식 프로세스 강제 종료 (SIGKILL)")
            _child_process.kill()


def run_bot() -> int:
    """봇 프로세스 실행 및 종료 대기. exit code 반환."""
    global _child_process

    cmd = [sys.executable, "-m", "src.main"]
    cwd = Path(__file__).parent.parent

    logger.info(f"봇 시작: {' '.join(cmd)}")
    logger.trace(f"작업 디렉토리: {cwd}")
    logger.trace(f"Python: {sys.executable}")
    logger.trace(f"환경변수 LOG_LEVEL: {os.getenv('LOG_LEVEL', 'INFO')}")

    _child_process = subprocess.Popen(
        cmd,
        stdout=sys.stdout,
        stderr=sys.stderr,
        cwd=cwd,
    )

    child_pid = _child_process.pid
    logger.trace(f"자식 프로세스 생성됨 - PID: {child_pid}")

    logger.trace("자식 프로세스 종료 대기 중...")
    exit_code = _child_process.wait()
    _child_process = None

    logger.trace(f"자식 프로세스 종료 - PID={child_pid}, exit_code={exit_code}")
    return exit_code


def main():
    """Supervisor 메인 루프."""
    global _shutdown_requested

    # 로깅 초기화
    log_level = os.getenv("LOG_LEVEL", "INFO").upper()
    log_file = os.getenv("LOG_FILE")
    setup_logging(level=log_level, log_file=log_file)

    logger.trace("main() 시작")

    # 텔레그램 설정 로드
    _load_telegram_config()

    # 싱글톤 락
    if not acquire_lock():
        print("❌ Supervisor가 이미 실행 중입니다.", file=sys.stderr)
        sys.exit(1)

    atexit.register(release_lock)
    logger.trace("종료 핸들러 등록됨")

    # 시그널 핸들러 등록
    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)
    logger.trace("시그널 핸들러 등록됨")

    logger.info("=" * 60)
    logger.info("Telegram Bot Supervisor 시작")
    logger.info(f"  PID: {os.getpid()}")
    logger.info(f"  LOG_LEVEL: {log_level}")
    logger.info("=" * 60)

    # 시작 알림
    start_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    notify_maintainer(f"🟢 <b>봇이 시작되었습니다</b>\n\n<code>{start_time}</code>")

    restart_delay = INITIAL_RESTART_DELAY
    restart_count = 0

    while not _shutdown_requested:
        start_time = time.time()
        logger.trace(f"메인 루프 반복 - restart_count={restart_count}, delay={restart_delay}")

        try:
            exit_code = run_bot()
        except Exception as e:
            logger.exception(f"봇 실행 오류: {e}")
            exit_code = 1

        run_duration = time.time() - start_time
        logger.trace(f"봇 종료 - exit_code={exit_code}, duration={run_duration:.1f}s")

        # 종료 요청 확인
        if _shutdown_requested:
            logger.info("정상 종료 요청으로 supervisor 종료")
            break

        # 정상 종료 (exit code 0)
        if exit_code == 0:
            logger.info("봇 정상 종료 (exit_code=0), supervisor 종료")
            break

        # 비정상 종료 - 재시작
        restart_count += 1
        logger.warning(
            f"봇 비정상 종료 (exit_code={exit_code}, "
            f"실행시간={run_duration:.1f}초, 재시작횟수={restart_count})"
        )

        # 충분히 오래 실행됐으면 딜레이 리셋
        if run_duration >= CRASH_RESET_TIME:
            restart_delay = INITIAL_RESTART_DELAY
            logger.info("안정 실행 확인, 재시작 딜레이 리셋")
            logger.trace(f"run_duration({run_duration:.1f}) >= CRASH_RESET_TIME({CRASH_RESET_TIME})")

        logger.info(f"{restart_delay}초 후 재시작...")

        # 대기 (중간에 종료 요청 체크)
        logger.trace("재시작 대기 시작")
        for i in range(restart_delay):
            if _shutdown_requested:
                logger.trace("대기 중 종료 요청 감지")
                break
            time.sleep(1)

        # 지수 백오프 (최대 5분)
        old_delay = restart_delay
        restart_delay = min(restart_delay * 2, MAX_RESTART_DELAY)
        logger.trace(f"지수 백오프: {old_delay} -> {restart_delay}")

    # 종료 알림
    end_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    notify_maintainer(f"🔴 <b>봇이 종료되었습니다</b>\n\n<code>{end_time}</code>")

    logger.info("=" * 60)
    logger.info("Supervisor 종료")
    logger.info(f"  총 재시작 횟수: {restart_count}")
    logger.info("=" * 60)


if __name__ == "__main__":
    main()
