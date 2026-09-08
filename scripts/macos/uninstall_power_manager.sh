#!/bin/bash
# Remove the per-user LaunchAgent without changing the bot's current process state.

set -u

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
MONITOR_LABEL="com.telegram-ai-cli-bot.power-manager"
DAILY_LABEL="com.telegram-ai-cli-bot.daily-start"
PLATFORM="${BOT_POWER_PLATFORM:-$(/usr/bin/uname -s 2>/dev/null || uname -s)}"
DATA_DIR="${BOT_DATA_DIR:-$PROJECT_ROOT/.data}"
STATE_DIR="${BOT_POWER_STATE_DIR:-$DATA_DIR/power-management}"
RUNTIME_SCRIPT_DIR="${BOT_POWER_RUNTIME_SCRIPT_DIR:-$STATE_DIR/runtime}"
RUNTIME_POWER_MANAGER="$RUNTIME_SCRIPT_DIR/power_manager.sh"
LAUNCH_AGENTS_DIR="${BOT_POWER_LAUNCH_AGENTS_DIR:-$HOME/Library/LaunchAgents}"
MONITOR_TARGET_PLIST="$LAUNCH_AGENTS_DIR/$MONITOR_LABEL.plist"
DAILY_TARGET_PLIST="$LAUNCH_AGENTS_DIR/$DAILY_LABEL.plist"
LAUNCHCTL_BIN="${BOT_POWER_LAUNCHCTL_BIN:-/bin/launchctl}"
USER_ID="${BOT_POWER_USER_ID:-$(/usr/bin/id -u)}"

if [ "$PLATFORM" != "Darwin" ]; then
    echo "macOS 전원 관리는 Darwin에서만 제거할 수 있습니다." >&2
    exit 1
fi

if [ -x "$LAUNCHCTL_BIN" ]; then
    "$LAUNCHCTL_BIN" bootout "gui/$USER_ID/$DAILY_LABEL" >/dev/null 2>&1 || true
    "$LAUNCHCTL_BIN" bootout "gui/$USER_ID/$MONITOR_LABEL" >/dev/null 2>&1 || true
fi

rm -f "$STATE_DIR/enabled" "$STATE_DIR/desired_state" "$STATE_DIR/reconcile.lock/pid"
rmdir "$STATE_DIR/reconcile.lock" 2>/dev/null || true
rm -f "$MONITOR_TARGET_PLIST" "$DAILY_TARGET_PLIST"
rm -f "$RUNTIME_POWER_MANAGER"
rmdir "$RUNTIME_SCRIPT_DIR" 2>/dev/null || true

echo "✅ macOS 전원 관리 제거 완료"
echo "   현재 봇 프로세스 상태는 변경하지 않았습니다."
