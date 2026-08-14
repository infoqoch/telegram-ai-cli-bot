#!/bin/bash
# Install the project-local macOS power monitor as a per-user LaunchAgent.

set -u

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
POWER_MANAGER_SCRIPT="$SCRIPT_DIR/power_manager.sh"
RUN_SCRIPT="${BOT_RUN_SCRIPT:-$PROJECT_ROOT/run.sh}"
TEMPLATE_FILE="$PROJECT_ROOT/launchd/com.telegram-ai-cli-bot.power-manager.plist.template"
LABEL="com.telegram-ai-cli-bot.power-manager"
PLATFORM="${BOT_POWER_PLATFORM:-$(/usr/bin/uname -s 2>/dev/null || uname -s)}"
DATA_DIR="${BOT_DATA_DIR:-$PROJECT_ROOT/.data}"
LOG_DIR="${BOT_LOG_DIR:-$DATA_DIR/logs}"
STATE_DIR="${BOT_POWER_STATE_DIR:-$DATA_DIR/power-management}"
ENABLED_FILE="$STATE_DIR/enabled"
DESIRED_STATE_FILE="$STATE_DIR/desired_state"
LAUNCHD_LOG_FILE="${BOT_POWER_LAUNCHD_LOG_FILE:-$LOG_DIR/power-manager-launchd.log}"
LAUNCH_AGENTS_DIR="${BOT_POWER_LAUNCH_AGENTS_DIR:-$HOME/Library/LaunchAgents}"
TARGET_PLIST="$LAUNCH_AGENTS_DIR/$LABEL.plist"
LAUNCHCTL_BIN="${BOT_POWER_LAUNCHCTL_BIN:-/bin/launchctl}"
PLUTIL_BIN="${BOT_POWER_PLUTIL_BIN:-/usr/bin/plutil}"
USER_ID="${BOT_POWER_USER_ID:-$(/usr/bin/id -u)}"
RUNTIME_PATH_INPUT="${BOT_POWER_RUNTIME_PATH:-${PATH:-/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin}}"

if [ "$PLATFORM" != "Darwin" ]; then
    echo "macOS 전원 관리는 Darwin에서만 설치할 수 있습니다." >&2
    exit 1
fi

if [ ! -x "$POWER_MANAGER_SCRIPT" ] || [ ! -x "$RUN_SCRIPT" ]; then
    echo "실행 스크립트를 찾을 수 없습니다." >&2
    exit 1
fi
if [ ! -f "$TEMPLATE_FILE" ]; then
    echo "LaunchAgent 템플릿을 찾을 수 없습니다: $TEMPLATE_FILE" >&2
    exit 1
fi
if [ ! -x "$LAUNCHCTL_BIN" ] || [ ! -x "$PLUTIL_BIN" ]; then
    echo "launchctl 또는 plutil을 실행할 수 없습니다." >&2
    exit 1
fi

_xml_escape() {
    printf '%s' "$1" | /usr/bin/sed -e 's/&/\&amp;/g' -e 's/</\&lt;/g' -e 's/>/\&gt;/g'
}

_sed_replacement() {
    printf '%s' "$1" | /usr/bin/sed -e 's/[&|]/\\&/g'
}

_replacement() {
    _sed_replacement "$(_xml_escape "$1")"
}

_append_runtime_path() {
    local directory="$1"
    case ":$RUNTIME_PATH:" in
      *":$directory:"*) return 0 ;;
    esac
    if [ -n "$RUNTIME_PATH" ]; then
        RUNTIME_PATH="$RUNTIME_PATH:$directory"
    else
        RUNTIME_PATH="$directory"
    fi
}

_build_runtime_path() {
    local original_ifs="$IFS"
    local directory
    RUNTIME_PATH=""
    IFS=:
    for directory in $RUNTIME_PATH_INPUT; do
        case "$directory" in
          /*) ;;
          *) continue ;;
        esac
        case "$directory" in
          /tmp|/tmp/*|/private/tmp|/private/tmp/*|/var/folders/*/T|/var/folders/*/T/*|/private/var/folders/*/T|/private/var/folders/*/T/*|/var/run/*|"$HOME/.codex/tmp"|"$HOME/.codex/tmp/"*)
            continue
            ;;
        esac
        _append_runtime_path "$directory"
    done
    IFS="$original_ifs"

    for directory in "$HOME/.local/bin" /opt/homebrew/bin /usr/local/bin /usr/bin /bin /usr/sbin /sbin; do
        _append_runtime_path "$directory"
    done
}

_build_runtime_path

mkdir -p "$STATE_DIR" "$LOG_DIR" "$LAUNCH_AGENTS_DIR"

desired=""
if [ -f "$DESIRED_STATE_FILE" ]; then
    desired=$(sed -n '1p' "$DESIRED_STATE_FILE" 2>/dev/null || true)
fi
if [ "$desired" != "on" ] && [ "$desired" != "off" ]; then
    desired="off"
    if BOT_POWER_MANAGER_INTERNAL=1 "$RUN_SCRIPT" _power-is-running >/dev/null 2>&1; then
        desired="on"
    fi
fi
BOT_POWER_PLATFORM="$PLATFORM" "$POWER_MANAGER_SCRIPT" set-desired "$desired"

temporary_plist="$TARGET_PLIST.$$"
/usr/bin/sed \
    -e "s|__POWER_MANAGER_SCRIPT__|$(_replacement "$POWER_MANAGER_SCRIPT")|g" \
    -e "s|__PROJECT_ROOT__|$(_replacement "$PROJECT_ROOT")|g" \
    -e "s|__RUNTIME_PATH__|$(_replacement "$RUNTIME_PATH")|g" \
    -e "s|__DATA_DIR__|$(_replacement "$DATA_DIR")|g" \
    -e "s|__LOG_DIR__|$(_replacement "$LOG_DIR")|g" \
    -e "s|__STATE_DIR__|$(_replacement "$STATE_DIR")|g" \
    -e "s|__LAUNCHD_LOG_FILE__|$(_replacement "$LAUNCHD_LOG_FILE")|g" \
    "$TEMPLATE_FILE" > "$temporary_plist"

if ! "$PLUTIL_BIN" -lint "$temporary_plist" >/dev/null; then
    rm -f "$temporary_plist"
    echo "생성된 LaunchAgent plist 검증에 실패했습니다." >&2
    exit 1
fi
mv "$temporary_plist" "$TARGET_PLIST"
touch "$ENABLED_FILE"

if "$LAUNCHCTL_BIN" bootout "gui/$USER_ID/$LABEL" >/dev/null 2>&1; then
    sleep 1
fi

bootstrap_succeeded=0
for bootstrap_attempt in 1 2 3; do
    if "$LAUNCHCTL_BIN" bootstrap "gui/$USER_ID" "$TARGET_PLIST"; then
        bootstrap_succeeded=1
        break
    fi
    [ "$bootstrap_attempt" -lt 3 ] && sleep 1
done
if [ "$bootstrap_succeeded" -ne 1 ]; then
    rm -f "$ENABLED_FILE"
    echo "LaunchAgent 등록에 실패했습니다." >&2
    exit 1
fi
"$LAUNCHCTL_BIN" enable "gui/$USER_ID/$LABEL" >/dev/null 2>&1 || true
"$LAUNCHCTL_BIN" kickstart -k "gui/$USER_ID/$LABEL" >/dev/null 2>&1 || true

echo "✅ macOS 전원 관리 설치 완료"
echo "   desired: $desired"
echo "   plist  : $TARGET_PLIST"
echo "   PATH   : $RUNTIME_PATH"
echo "   log    : $LOG_DIR/power-manager.log"
echo "   status : ./run.sh power-status"
