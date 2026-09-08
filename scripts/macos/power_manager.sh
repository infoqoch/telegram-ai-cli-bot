#!/bin/bash
# macOS-only, opt-in power reconciliation for the Telegram bot.

set -u

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
DATA_DIR="${BOT_DATA_DIR:-$PROJECT_ROOT/.data}"
LOG_DIR="${BOT_LOG_DIR:-$DATA_DIR/logs}"
STATE_DIR="${BOT_POWER_STATE_DIR:-$DATA_DIR/power-management}"
DESIRED_STATE_FILE="$STATE_DIR/desired_state"
ENABLED_FILE="$STATE_DIR/enabled"
LOCK_DIR="$STATE_DIR/reconcile.lock"
LOG_FILE="${BOT_POWER_LOG_FILE:-$LOG_DIR/power-manager.log}"
RUN_SCRIPT="${BOT_RUN_SCRIPT:-$PROJECT_ROOT/run.sh}"
PLATFORM="${BOT_POWER_PLATFORM:-$(/usr/bin/uname -s 2>/dev/null || uname -s)}"
PMSET_BIN="${BOT_POWER_PMSET_BIN:-/usr/bin/pmset}"
OSASCRIPT_BIN="${BOT_POWER_OSASCRIPT_BIN:-/usr/bin/osascript}"
DAILY_RETRY_ATTEMPTS="${BOT_POWER_DAILY_RETRY_ATTEMPTS:-61}"
DAILY_RETRY_DELAY_SECONDS="${BOT_POWER_DAILY_RETRY_DELAY_SECONDS:-5}"
RECONCILE_BUSY_EXIT_CODE=75

_require_macos() {
    if [ "$PLATFORM" != "Darwin" ]; then
        echo "macOS 전원 관리는 Darwin에서만 사용할 수 있습니다." >&2
        return 1
    fi
}

_ensure_dirs() {
    mkdir -p "$STATE_DIR" "$LOG_DIR"
}

_log() {
    _ensure_dirs
    printf '%s %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*" >> "$LOG_FILE"
}

_is_enabled() {
    [ "$PLATFORM" = "Darwin" ] && [ -f "$ENABLED_FILE" ]
}

_get_desired_state() {
    local desired="off"
    if [ -f "$DESIRED_STATE_FILE" ]; then
        desired=$(sed -n '1p' "$DESIRED_STATE_FILE" 2>/dev/null || true)
    fi
    case "$desired" in
      on|off) printf '%s\n' "$desired" ;;
      *) printf '%s\n' "off" ;;
    esac
}

_set_desired_state() {
    local desired="$1"
    case "$desired" in
      on|off) ;;
      *)
        echo "희망 상태는 on 또는 off여야 합니다." >&2
        return 1
        ;;
    esac

    _ensure_dirs
    local temporary_file="$DESIRED_STATE_FILE.$$"
    if ! printf '%s\n' "$desired" > "$temporary_file"; then
        return 1
    fi
    if ! mv "$temporary_file" "$DESIRED_STATE_FILE"; then
        rm -f "$temporary_file"
        return 1
    fi
}

_current_power() {
    if [ ! -x "$PMSET_BIN" ]; then
        echo "unknown"
        return 1
    fi

    local output
    if ! output=$("$PMSET_BIN" -g batt 2>/dev/null); then
        echo "unknown"
        return 1
    fi

    case "$output" in
      *"'AC Power'"*) echo "ac" ;;
      *"'Battery Power'"*) echo "battery" ;;
      *) echo "unknown"; return 1 ;;
    esac
}

_bot_is_running() {
    BOT_POWER_MANAGER_INTERNAL=1 "$RUN_SCRIPT" _power-is-running >/dev/null 2>&1
}

_bot_runtime_state() {
    local state
    state=$(BOT_POWER_MANAGER_INTERNAL=1 "$RUN_SCRIPT" _power-runtime-state 2>/dev/null || true)
    case "$state" in
      running|degraded|stopped) echo "$state" ;;
      *)
        if _bot_is_running; then
            echo "running"
        else
            echo "stopped"
        fi
        ;;
    esac
}

_bot_has_processes() {
    BOT_POWER_MANAGER_INTERNAL=1 "$RUN_SCRIPT" _power-has-processes >/dev/null 2>&1
}

_start_bot() {
    BOT_POWER_MANAGER_INTERNAL=1 "$RUN_SCRIPT" start >> "$LOG_FILE" 2>&1
}

_stop_bot() {
    local mode="$1"
    BOT_POWER_MANAGER_INTERNAL=1 "$RUN_SCRIPT" "stop-$mode" >> "$LOG_FILE" 2>&1
}

_notify() {
    local event="$1"
    if [ ! -x "$OSASCRIPT_BIN" ]; then
        _log "notification skipped: osascript unavailable"
        return 0
    fi

    case "$event" in
      started)
        "$OSASCRIPT_BIN" -e 'display notification "전원이 연결되어 봇을 시작했습니다." with title "Telegram AI CLI Bot"' >/dev/null 2>&1 || true
        ;;
      stopped)
        "$OSASCRIPT_BIN" -e 'display notification "배터리 사용으로 봇을 종료했습니다." with title "Telegram AI CLI Bot"' >/dev/null 2>&1 || true
        ;;
      start_failed)
        "$OSASCRIPT_BIN" -e 'display notification "전원 연결 후 봇 시작에 실패했습니다. 로그를 확인하세요." with title "Telegram AI CLI Bot"' >/dev/null 2>&1 || true
        ;;
      stop_failed)
        "$OSASCRIPT_BIN" -e 'display notification "배터리 전환 후 봇 종료에 실패했습니다. 로그를 확인하세요." with title "Telegram AI CLI Bot"' >/dev/null 2>&1 || true
        ;;
    esac
}

_acquire_lock() {
    _ensure_dirs
    if mkdir "$LOCK_DIR" 2>/dev/null; then
        printf '%s\n' "$$" > "$LOCK_DIR/pid"
        return 0
    fi

    local owner=""
    if [ -f "$LOCK_DIR/pid" ]; then
        owner=$(sed -n '1p' "$LOCK_DIR/pid" 2>/dev/null || true)
    fi
    if [ -n "$owner" ] && kill -0 "$owner" 2>/dev/null; then
        return 1
    fi

    rm -f "$LOCK_DIR/pid"
    rmdir "$LOCK_DIR" 2>/dev/null || return 1
    mkdir "$LOCK_DIR" 2>/dev/null || return 1
    printf '%s\n' "$$" > "$LOCK_DIR/pid"
}

_release_lock() {
    rm -f "$LOCK_DIR/pid"
    rmdir "$LOCK_DIR" 2>/dev/null || true
}

_reconcile() (
    local log_busy="${1:-yes}"
    _require_macos || return 1
    if ! _is_enabled; then
        return 0
    fi
    if ! _acquire_lock; then
        if [ "$log_busy" = "yes" ]; then
            _log "reconcile skipped: another reconciliation is active"
        fi
        return "$RECONCILE_BUSY_EXIT_CODE"
    fi
    trap _release_lock EXIT INT TERM

    local desired power
    desired=$(_get_desired_state)
    power=$(_current_power || true)

    if [ "$power" = "unknown" ]; then
        _log "reconcile skipped: power state is unknown (desired=$desired)"
        return 0
    fi

    if [ "$desired" = "on" ] && [ "$power" = "ac" ]; then
        if ! _bot_is_running; then
            _log "AC power and desired=on: starting bot"
            if _start_bot; then
                _log "bot started by power manager"
                _notify started
            else
                _log "bot start failed"
                _notify start_failed
                return 1
            fi
        fi
        return 0
    fi

    if [ "$power" = "battery" ]; then
        if _bot_has_processes; then
            _log "battery power: hard-stopping bot and detached workers (desired=$desired)"
            if _stop_bot hard; then
                _log "bot stopped by power manager"
                _notify stopped
            else
                _log "bot hard stop failed"
                _notify stop_failed
                return 1
            fi
        fi
        return 0
    fi

    if [ "$desired" = "off" ] && _bot_is_running; then
        _log "desired=off: soft-stopping supervisor/main"
        if ! _stop_bot soft; then
            _log "bot soft stop failed"
            return 1
        fi
    fi
)

_daily_start() {
    _require_macos || return 1
    if ! _is_enabled; then
        echo "전원 관리가 설치되어 있지 않습니다." >&2
        return 1
    fi

    if ! _set_desired_state on; then
        _log "daily start failed: could not persist desired=on"
        return 1
    fi

    local power
    power=$(_current_power || true)
    _log "daily start policy applied: desired=on (power=$power)"

    local attempt=1
    local result
    while [ "$attempt" -le "$DAILY_RETRY_ATTEMPTS" ]; do
        _reconcile no
        result=$?
        if [ "$result" -eq 0 ]; then
            return 0
        fi
        if [ "$result" -ne "$RECONCILE_BUSY_EXIT_CODE" ]; then
            return "$result"
        fi
        if [ "$attempt" -eq 1 ]; then
            _log "daily start waiting for active reconciliation"
        fi
        if [ "$attempt" -lt "$DAILY_RETRY_ATTEMPTS" ]; then
            sleep "$DAILY_RETRY_DELAY_SECONDS"
        fi
        attempt=$((attempt + 1))
    done

    _log "daily start failed: reconciliation remained busy"
    return 1
}

_monitor() {
    _require_macos || return 1
    if ! _is_enabled; then
        echo "전원 관리가 설치되어 있지 않습니다." >&2
        return 1
    fi
    if [ ! -x "$PMSET_BIN" ]; then
        echo "pmset을 실행할 수 없습니다: $PMSET_BIN" >&2
        return 1
    fi

    _log "power monitor started"
    _reconcile || true

    _cleanup_monitor_children() {
        local child_pids
        child_pids=$(ps ax -o pid= -o ppid= | awk -v parent="$$" '$2 == parent {print $1}')
        if [ -n "$child_pids" ]; then
            kill -TERM $child_pids 2>/dev/null || true
        fi
    }
    trap _cleanup_monitor_children EXIT INT TERM

    "$PMSET_BIN" -g pslog 2>> "$LOG_FILE" | while IFS= read -r line; do
        if ! _is_enabled; then
            break
        fi
        case "$line" in
          *"Now drawing from"*|*"wake"*|*"Wake"*) "$SCRIPT_DIR/power_manager.sh" reconcile || true ;;
        esac
    done &
    local stream_pid=$!
    wait "$stream_pid" 2>/dev/null || true

    _log "power monitor stopped"
}

_show_status() {
    _require_macos || return 1
    local enabled="off"
    local desired
    local power
    local actual
    _is_enabled && enabled="on"
    desired=$(_get_desired_state)
    power=$(_current_power || true)
    actual=$(_bot_runtime_state)

    echo "macOS 전원 관리:"
    echo "  installed: $enabled"
    echo "  desired  : $desired"
    echo "  power    : $power"
    echo "  bot      : $actual"
    echo "  log      : $LOG_FILE"
}

case "${1:-}" in
  is-enabled)
    _is_enabled
    ;;
  current-power)
    _require_macos || exit 1
    _current_power
    ;;
  get-desired)
    _require_macos || exit 1
    _get_desired_state
    ;;
  set-desired)
    _require_macos || exit 1
    _set_desired_state "${2:-}" || exit 1
    ;;
  reconcile)
    _reconcile
    ;;
  daily-start)
    _daily_start
    ;;
  monitor)
    _monitor
    ;;
  status)
    _show_status
    ;;
  *)
    echo "사용법: $0 {is-enabled|current-power|get-desired|set-desired on|off|reconcile|daily-start|monitor|status}" >&2
    exit 1
    ;;
esac
