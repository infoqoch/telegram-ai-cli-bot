#!/bin/bash
# AI Bot 실행 스크립트 - 싱글톤 보장
#
# [운영 정책] 기본 DEBUG 모드로 실행
# - 문제 추적을 위해 항상 DEBUG 레벨로 운영
# - 필요 시 ./run.sh trace로 TRACE 모드 사용

cd "$(dirname "$0")"

PID_FILE="/tmp/telegram-bot.pid"
LOCK_FILE="/tmp/telegram-bot.lock"
LOG_FILE="/tmp/telegram-bot.log"

_get_running_pid() {
    # 락 파일에서 PID 읽기 (supervisor 락 파일 우선)
    for lf in "/tmp/telegram-bot-supervisor.lock" "$LOCK_FILE"; do
        if [ -f "$lf" ]; then
            local pid=$(cat "$lf" 2>/dev/null)
            if [ -n "$pid" ] && ps -p "$pid" > /dev/null 2>&1; then
                echo "$pid"
                return 0
            fi
        fi
    done
    # 락 파일이 없거나 유효하지 않으면 pgrep 사용
    pgrep -f "python.*src\.(supervisor|main)" 2>/dev/null | head -1
}

_kill_all_instances() {
    # 모든 관련 프로세스 강제 종료 (supervisor + main)
    local pids=$(pgrep -f "python.*src\.(supervisor|main)" 2>/dev/null)
    if [ -n "$pids" ]; then
        echo "$pids" | xargs kill -9 2>/dev/null
        sleep 1
    fi
    rm -f "$PID_FILE" "$LOCK_FILE" "/tmp/telegram-bot-supervisor.lock"
}

_is_running() {
    [ -n "$(_get_running_pid)" ]
}

case "$1" in
  start)
    # 기존 프로세스 확인 및 정리 (좀비 방지)
    existing_pids=$(pgrep -f "python.*src\.(supervisor|main)" 2>/dev/null)
    if [ -n "$existing_pids" ]; then
        echo "⚠️  기존 프로세스 발견 - 자동 정리 중..."
        echo "$existing_pids" | xargs kill -9 2>/dev/null
        sleep 1
    fi
    # 락 파일 정리
    rm -f "$LOCK_FILE" "/tmp/telegram-bot-supervisor.lock" "$PID_FILE"
    source venv/bin/activate
    # supervisor로 시작 (크래시 시 자동 재시작)
    # LOG_LEVEL 환경변수로 조정 (기본: DEBUG)
    # - INFO: 일반 운영 (최소 로그)
    # - DEBUG: 상세 로그 (기본값 - 문제 추적용)
    # - TRACE: 최상세 로그 (외부 라이브러리 포함)
    LOG_LEVEL="${LOG_LEVEL:-DEBUG}" PYTHONPYCACHEPREFIX=.build nohup python -m src.supervisor > "$LOG_FILE" 2>&1 &
    new_pid=$!
    echo $new_pid > "$PID_FILE"
    sleep 2
    # 시작 확인
    if ps -p $new_pid > /dev/null 2>&1; then
        echo "✅ 봇 시작됨 (Supervisor PID: $new_pid)"
        echo "   크래시 시 자동 재시작 활성화"
        echo "   LOG_LEVEL: ${LOG_LEVEL:-INFO}"
    else
        echo "❌ 봇 시작 실패. 로그 확인: $LOG_FILE"
        tail -10 "$LOG_FILE"
        exit 1
    fi
    ;;
  stop)
    if _is_running; then
        _kill_all_instances
        echo "✅ 봇 중지됨"
    else
        # 좀비 파일 정리
        rm -f "$PID_FILE" "$LOCK_FILE"
        echo "⚠️  실행 중인 봇 없음"
    fi
    ;;
  restart)
    echo "🔄 봇 재시작 중..."
    $0 stop
    sleep 1
    $0 start
    ;;
  status)
    if _is_running; then
        echo "✅ 봇 실행 중"
        echo ""
        echo "프로세스:"
        ps aux | grep -E "python.*src\.(supervisor|main)" | grep -v grep
        # 중복 프로세스 경고
        proc_count=$(pgrep -f "python.*src\.(supervisor|main)" 2>/dev/null | wc -l | tr -d ' ')
        if [ "$proc_count" -gt 2 ]; then
            echo ""
            echo "⚠️  경고: 중복 프로세스 감지! ($proc_count개)"
            echo "   './run.sh restart'로 정리하세요."
        fi
        echo ""
        echo "락 파일:"
        ls -la /tmp/telegram-bot*.lock 2>/dev/null || echo "  (없음)"
    else
        echo "❌ 봇 중지됨"
    fi
    ;;
  log)
    tail -f "$LOG_FILE"
    ;;
  trace)
    # TRACE 모드로 시작 (최상세 로깅)
    echo "🔍 TRACE 모드로 시작 (최상세 로깅)"
    if _is_running; then
        echo "⚠️  기존 봇 중지 중..."
        _kill_all_instances
        sleep 1
    fi
    rm -f "$LOCK_FILE" "/tmp/telegram-bot-supervisor.lock"
    source venv/bin/activate
    LOG_LEVEL="TRACE" PYTHONPYCACHEPREFIX=.build nohup python -m src.supervisor > "$LOG_FILE" 2>&1 &
    new_pid=$!
    echo $new_pid > "$PID_FILE"
    sleep 2
    if ps -p $new_pid > /dev/null 2>&1; then
        echo "✅ TRACE 모드로 봇 시작됨 (PID: $new_pid)"
        echo "   ./run.sh log 로 로그 확인"
    else
        echo "❌ 봇 시작 실패"
        tail -10 "$LOG_FILE"
        exit 1
    fi
    ;;
  debug)
    # DEBUG 모드로 시작
    echo "🐛 DEBUG 모드로 시작"
    if _is_running; then
        echo "⚠️  기존 봇 중지 중..."
        _kill_all_instances
        sleep 1
    fi
    rm -f "$LOCK_FILE" "/tmp/telegram-bot-supervisor.lock"
    source venv/bin/activate
    LOG_LEVEL="DEBUG" PYTHONPYCACHEPREFIX=.build nohup python -m src.supervisor > "$LOG_FILE" 2>&1 &
    new_pid=$!
    echo $new_pid > "$PID_FILE"
    sleep 2
    if ps -p $new_pid > /dev/null 2>&1; then
        echo "✅ DEBUG 모드로 봇 시작됨 (PID: $new_pid)"
        echo "   ./run.sh log 로 로그 확인"
    else
        echo "❌ 봇 시작 실패"
        tail -10 "$LOG_FILE"
        exit 1
    fi
    ;;
  test)
    source venv/bin/activate
    PYTHONPYCACHEPREFIX=.build pytest
    ;;
  *)
    echo "사용법: $0 {start|stop|restart|status|log|trace|debug|test}"
    echo ""
    echo "  start   - 봇 시작 (LOG_LEVEL 환경변수로 조정 가능)"
    echo "  stop    - 봇 중지"
    echo "  restart - 봇 재시작"
    echo "  status  - 상태 확인"
    echo "  log     - 로그 보기 (tail -f)"
    echo "  trace   - TRACE 모드로 시작 (최상세 로깅, 디버깅용)"
    echo "  debug   - DEBUG 모드로 시작"
    echo "  test    - 테스트 실행"
    echo ""
    echo "환경변수:"
    echo "  LOG_LEVEL - 로그 레벨 (TRACE, DEBUG, INFO, WARNING, ERROR)"
    echo "  LOG_FILE  - 로그 파일 경로 (설정 시 파일에도 저장)"
    exit 1
    ;;
esac
