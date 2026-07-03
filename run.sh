#!/bin/bash
# AI Bot 실행 스크립트 - 라이프사이클 명령 통합
#
# 기본 원칙
# - `restart-soft`: supervisor/main만 교체, in-flight detached worker 유지 시도
# - `stop-hard`: supervisor/main/worker 전체 종료
# - 부팅 로그는 날짜별 파일에 append

cd "$(dirname "$0")"

BASE_DIR="$(pwd)"
DATA_DIR="${BOT_DATA_DIR:-$BASE_DIR/.data}"
PID_FILE="${BOT_PID_FILE:-$DATA_DIR/telegram-bot.pid}"
LOCK_FILE="${BOT_LOCK_FILE:-$DATA_DIR/telegram-bot.lock}"
SUPERVISOR_LOCK_FILE="${BOT_SUPERVISOR_LOCK_FILE:-$DATA_DIR/telegram-bot-supervisor.lock}"
APP_LOG_LINK="${BOT_APP_LOG_LINK:-$DATA_DIR/telegram-bot.log}"
BOOT_LOG_LINK="${BOT_BOOT_LOG_LINK:-$DATA_DIR/telegram-bot-boot.log}"
LOG_DIR="${BOT_LOG_DIR:-$DATA_DIR/logs}"
APP_LOG_FILE="$LOG_DIR/bot.log"
DEFAULT_LOG_LEVEL="${LOG_LEVEL:-DEBUG}"
BOOT_LOG_RETENTION_DAYS="${BOOT_LOG_RETENTION_DAYS:-14}"
STARTUP_WAIT_SECONDS="${STARTUP_WAIT_SECONDS:-30}"
LEGACY_PID_FILE="/tmp/telegram-bot.pid"
LEGACY_LOCK_FILE="/tmp/telegram-bot.lock"
LEGACY_SUPERVISOR_LOCK_FILE="/tmp/telegram-bot-supervisor.lock"
LEGACY_APP_LOG_LINK="/tmp/telegram-bot.log"
LEGACY_BOOT_LOG_LINK="/tmp/telegram-bot-boot.log"
LEGACY_LOG_DIR="/tmp/telegram-bot-logs"

_get_running_pid() {
    # 락 파일에서 PID 읽기 (supervisor 락 파일 우선)
    for lf in "$SUPERVISOR_LOCK_FILE" "$LOCK_FILE"; do
        if [ -f "$lf" ]; then
            local pid
            pid=$(cat "$lf" 2>/dev/null)
            if [ -n "$pid" ] && ps -p "$pid" > /dev/null 2>&1; then
                echo "$pid"
                return 0
            fi
        fi
    done

    local pid
    pid=$(_get_matching_pids 'python.*src\.supervisor')
    if [ -n "$pid" ]; then
        echo "$pid" | awk '{print $1}'
        return 0
    fi

    pid=$(_get_matching_pids 'python.*src\.main')
    if [ -n "$pid" ]; then
        echo "$pid" | awk '{print $1}'
    fi
}

_get_matching_pids() {
    local pattern="$1"
    ps ax -o pid= -o command= | grep -E "$pattern" | grep -v grep | awk '{print $1}' | tr '\n' ' ' | sed 's/[[:space:]]*$//'
}

_get_supervisor_pids() {
    _get_matching_pids 'python.*src\.supervisor'
}

_get_main_pids() {
    _get_matching_pids 'python.*src\.main'
}

_get_worker_pids() {
    _get_matching_pids 'python.*src\.worker_job'
}

_alive_pids() {
    local alive=""
    for pid in "$@"; do
        if [ -n "$pid" ] && ps -p "$pid" > /dev/null 2>&1; then
            alive="$alive $pid"
        fi
    done
    echo "${alive# }"
}

_send_signal() {
    local signal="$1"
    shift
    for pid in "$@"; do
        if [ -n "$pid" ]; then
            kill "-$signal" "$pid" 2>/dev/null || true
        fi
    done
}

_wait_for_exit() {
    local timeout="$1"
    shift
    local tracked="$*"
    local remaining="$tracked"

    while [ "$timeout" -gt 0 ]; do
        remaining=$(_alive_pids "$@")
        if [ -z "$remaining" ]; then
            return 0
        fi
        sleep 1
        timeout=$((timeout - 1))
    done

    remaining=$(_alive_pids "$@")
    echo "$remaining"
    return 1
}

_terminate_pids() {
    local label="$1"
    shift
    local pids="$*"
    if [ -z "$pids" ]; then
        return 0
    fi

    _send_signal TERM $pids
    local remaining
    remaining=$(_wait_for_exit 10 $pids)
    if [ -n "$remaining" ]; then
        echo "⚠️  $label 종료 지연, SIGKILL 수행: $remaining"
        _send_signal KILL $remaining
        _wait_for_exit 3 $remaining > /dev/null 2>&1 || true
    fi
}

_cleanup_pid_files() {
    mkdir -p "$DATA_DIR"
    rm -f "$PID_FILE" "$LOCK_FILE" "$SUPERVISOR_LOCK_FILE"
}

_cleanup_legacy_tmp_artifacts() {
    if [ -n "$(_get_supervisor_pids)$(_get_main_pids)$(_get_worker_pids)" ]; then
        return 0
    fi

    rm -f "$LEGACY_PID_FILE" "$LEGACY_LOCK_FILE" "$LEGACY_SUPERVISOR_LOCK_FILE"
    rm -f "$LEGACY_APP_LOG_LINK" "$LEGACY_BOOT_LOG_LINK"

    if [ "$LOG_DIR" != "$LEGACY_LOG_DIR" ]; then
        rm -rf "$LEGACY_LOG_DIR"
    fi
}

_stop_bot_processes() {
    local mode="${1:-soft}"
    local supervisors mains workers

    supervisors=$(_get_supervisor_pids)
    mains=$(_get_main_pids)
    _terminate_pids "supervisor/main" $supervisors $mains

    if [ "$mode" = "hard" ]; then
        workers=$(_get_worker_pids)
        _terminate_pids "detached worker" $workers
    fi

    _cleanup_pid_files
}

_is_running() {
    [ -n "$(_get_running_pid)" ]
}

_ensure_log_dir() {
    mkdir -p "$DATA_DIR" "$LOG_DIR"
}

_cleanup_old_boot_logs() {
    _ensure_log_dir
    find "$LOG_DIR" -maxdepth 1 -type f -name 'supervisor-*.log' -mtime "+$BOOT_LOG_RETENTION_DAYS" -delete 2>/dev/null || true
}

_get_boot_log_path() {
    echo "$LOG_DIR/supervisor-$(date '+%Y-%m-%d').log"
}

_prepare_boot_log() {
    _ensure_log_dir
    _cleanup_old_boot_logs

    local boot_log
    boot_log=$(_get_boot_log_path)
    touch "$boot_log"
    ln -sf "$boot_log" "$BOOT_LOG_LINK"
    echo "$boot_log"
}

_prepare_app_log_link() {
    _ensure_log_dir
    touch "$APP_LOG_FILE"
    ln -sf "$APP_LOG_FILE" "$APP_LOG_LINK"
}

_preflight_startup() {
    if [ ! -x "./venv/bin/python" ]; then
        echo "❌ 시작 전 점검 실패: ./venv/bin/python 없음"
        echo "   먼저 가상환경을 준비하세요."
        return 1
    fi

    local check_output
    if ! check_output=$(PYTHONPYCACHEPREFIX=.build ./venv/bin/python - <<'PY' 2>&1
import src.main  # noqa: F401 - startup import validation only
PY
    ); then
        echo "❌ 시작 전 점검 실패: 앱 import 단계에서 오류 발생"
        echo "$check_output" | tail -20
        echo "   의존성/환경을 먼저 확인하세요."
        echo "   예: ./venv/bin/pip install -e ."
        return 1
    fi

    return 0
}

_wait_for_main_start() {
    local supervisor_pid="$1"
    local timeout="${2:-5}"

    while [ "$timeout" -gt 0 ]; do
        local main_pids
        main_pids=$(_get_main_pids)
        if [ -n "$main_pids" ]; then
            echo "$main_pids"
            return 0
        fi

        if ! ps -p "$supervisor_pid" > /dev/null 2>&1; then
            return 1
        fi

        sleep 1
        timeout=$((timeout - 1))
    done

    return 1
}

_start_supervisor() {
    local level="$1"
    local boot_log
    boot_log=$(_prepare_boot_log)
    _prepare_app_log_link

    source venv/bin/activate
    unset CLAUDECODE

    LOG_LEVEL="$level" BOT_DATA_DIR="$DATA_DIR" BOT_LOG_DIR="$LOG_DIR" BOT_LOCK_FILE="$LOCK_FILE" \
        BOT_SUPERVISOR_LOCK_FILE="$SUPERVISOR_LOCK_FILE" PYTHONUNBUFFERED=1 PYTHONPYCACHEPREFIX=.build \
        nohup python -m src.supervisor >> "$boot_log" 2>&1 < /dev/null &
    local new_pid=$!
    echo "$new_pid" > "$PID_FILE"

    sleep 3
    local main_pids
    if ps -p "$new_pid" > /dev/null 2>&1 && main_pids=$(_wait_for_main_start "$new_pid" "$STARTUP_WAIT_SECONDS"); then
        echo "✅ 봇 시작됨 (Supervisor PID: $new_pid)"
        echo "   Main PID   : $main_pids"
        echo "   크래시 시 자동 재시작 활성화"
        echo "   LOG_LEVEL: $level"
        echo "   log      : $APP_LOG_LINK"
        return 0
    fi

    echo "❌ 봇 시작 실패. 로그 확인:"
    echo "   main log: $APP_LOG_LINK"
    echo "   boot log: $boot_log"
    _stop_bot_processes soft > /dev/null 2>&1 || true
    tail -20 "$APP_LOG_LINK" 2>/dev/null || true
    tail -10 "$boot_log" 2>/dev/null || true
    return 1
}

_comma_join_pids() {
    echo "$*" | tr ' ' ',' | sed 's/,,*/,/g' | sed 's/^,//; s/,$//'
}

_print_process_table() {
    local pids="$*"
    if [ -z "$pids" ]; then
        echo "  (없음)"
        return 0
    fi

    local pid_list
    pid_list=$(_comma_join_pids $pids)
    ps -o pid=,ppid=,command= -p "$pid_list"
}

_show_status() {
    local supervisors mains workers
    supervisors=$(_get_supervisor_pids)
    mains=$(_get_main_pids)
    workers=$(_get_worker_pids)

    if [ -n "$supervisors$mains" ]; then
        echo "✅ 봇 실행 중"
    else
        echo "❌ 봇 중지됨"
    fi

    echo ""
    echo "Supervisor/Main:"
    _print_process_table $supervisors $mains

    if [ -n "$workers" ]; then
        echo ""
        echo "Detached workers:"
        _print_process_table $workers
    fi

    echo ""
    echo "로그:"
    echo "  main: $APP_LOG_LINK (daily rotate at midnight)"
}

_tail_logs() {
    local target="${1:-app}"
    _prepare_app_log_link

    case "$target" in
      app)
        tail -f "$APP_LOG_LINK"
        ;;
      boot|supervisor|nohup)
        local boot_log
        boot_log=$(_prepare_boot_log)
        tail -f "$boot_log"
        ;;
      *)
        echo "사용법: ./run.sh log [app|boot]"
        return 1
        ;;
    esac
}

case "$1" in
  start)
    if _is_running; then
        echo "⚠️  봇이 이미 실행 중입니다."
        echo "   soft 재시작: ./run.sh restart-soft"
        echo "   hard 재시작: ./run.sh restart-hard"
        echo "   상태 확인  : ./run.sh status"
        exit 1
    fi
    _cleanup_legacy_tmp_artifacts
    _preflight_startup || exit 1
    _start_supervisor "$DEFAULT_LOG_LEVEL" || exit 1
    ;;
  stop-soft)
    if _is_running; then
        echo "🛑 봇 soft stop 중 (detached worker 유지 시도)..."
        _stop_bot_processes soft
        echo "✅ supervisor/main 중지됨 (detached worker는 유지 가능)"
    else
        _cleanup_pid_files
        echo "⚠️  실행 중인 supervisor/main 없음"
    fi
    ;;
  stop-hard)
    if _is_running || [ -n "$(_get_worker_pids)" ]; then
        echo "🛑 봇 hard stop 중 (detached worker 포함)..."
        _stop_bot_processes hard
        _cleanup_legacy_tmp_artifacts
        echo "✅ 봇 전체 중지됨"
    else
        _cleanup_pid_files
        _cleanup_legacy_tmp_artifacts
        echo "⚠️  실행 중인 봇/worker 없음"
    fi
    ;;
  restart-soft)
    echo "🔄 봇 soft 재시작 중 (in-flight worker 유지 시도)..."
    _preflight_startup || exit 1
    _stop_bot_processes soft
    sleep 1
    _start_supervisor "$DEFAULT_LOG_LEVEL" || exit 1
    ;;
  restart-hard)
    echo "🔄 봇 hard 재시작 중 (detached worker 포함 종료)..."
    _preflight_startup || exit 1
    _stop_bot_processes hard
    _cleanup_legacy_tmp_artifacts
    sleep 1
    _start_supervisor "$DEFAULT_LOG_LEVEL" || exit 1
    ;;
  stop)
    echo "❌ 명시적으로 선택하세요: ./run.sh stop-soft 또는 ./run.sh stop-hard"
    exit 1
    ;;
  restart)
    echo "❌ 명시적으로 선택하세요: ./run.sh restart-soft 또는 ./run.sh restart-hard"
    exit 1
    ;;
  status)
    _show_status
    ;;
  log)
    _tail_logs "$2"
    ;;
  trace)
    echo "🔍 TRACE 모드로 시작"
    _preflight_startup || exit 1
    if _is_running; then
        echo "⚠️  기존 supervisor/main soft stop 중..."
        _stop_bot_processes soft
        sleep 1
    fi
    _start_supervisor "TRACE" || exit 1
    ;;
  debug)
    echo "🐛 DEBUG 모드로 시작"
    _preflight_startup || exit 1
    if _is_running; then
        echo "⚠️  기존 supervisor/main soft stop 중..."
        _stop_bot_processes soft
        sleep 1
    fi
    _start_supervisor "DEBUG" || exit 1
    ;;
  test)
    source venv/bin/activate
    PYTHONPYCACHEPREFIX=.build pytest tests/ --ignore=tests/integration -v
    ;;
  test-integration)
    echo "🧪 통합 테스트 실행 (텔레그램 목킹, 실제 Repository)"
    source venv/bin/activate
    PYTHONPYCACHEPREFIX=.build pytest tests/integration -v --tb=short
    ;;
  test-all)
    echo "🧪 전체 테스트 실행"
    source venv/bin/activate
    PYTHONPYCACHEPREFIX=.build pytest tests/ -v --tb=short
    ;;
  *)
    echo "사용법: $0 {start|stop-soft|stop-hard|restart-soft|restart-hard|status|log|trace|debug|test|test-integration|test-all}"
    echo ""
    echo "  start         - 봇 시작 (기본 LOG_LEVEL=${DEFAULT_LOG_LEVEL})"
    echo "  stop-soft     - supervisor/main만 중지, detached worker 유지 시도"
    echo "  stop-hard     - 봇 전체 중지 (detached worker 포함)"
    echo "  restart-soft  - soft 재시작 (in-flight worker 유지 시도)"
    echo "  restart-hard  - hard 재시작 (detached worker 포함 종료)"
    echo "  status        - 상태 확인"
    echo "  log [target]  - 로그 보기 (기본: app, 선택: boot)"
    echo "  trace         - TRACE 모드 soft 재시작"
    echo "  debug         - DEBUG 모드 soft 재시작"
    echo "  test          - 단위 테스트 실행"
    echo "  test-integration - 통합 테스트 실행"
    echo "  test-all      - 전체 테스트 실행"
    echo ""
    echo "환경변수:"
    echo "  LOG_LEVEL               - start/restart 기본 로그 레벨"
    echo "  STARTUP_WAIT_SECONDS    - supervisor 시작 후 main 대기 시간 (기본: 30)"
    echo "  BOT_DATA_DIR            - 런타임 데이터 루트 (기본: $BASE_DIR/.data)"
    echo "  BOT_LOG_DIR             - 로그 디렉토리 (기본: \$BOT_DATA_DIR/logs)"
    echo "  BOOT_LOG_RETENTION_DAYS - boot 로그 보관 일수 (기본: 14)"
    exit 1
    ;;
esac
