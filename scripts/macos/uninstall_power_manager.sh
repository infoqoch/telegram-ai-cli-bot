#!/bin/bash
# Remove the per-user LaunchAgent without changing the bot's current process state.

set -u

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
LABEL="com.telegram-ai-cli-bot.power-manager"
PLATFORM="${BOT_POWER_PLATFORM:-$(/usr/bin/uname -s 2>/dev/null || uname -s)}"
DATA_DIR="${BOT_DATA_DIR:-$PROJECT_ROOT/.data}"
STATE_DIR="${BOT_POWER_STATE_DIR:-$DATA_DIR/power-management}"
LAUNCH_AGENTS_DIR="${BOT_POWER_LAUNCH_AGENTS_DIR:-$HOME/Library/LaunchAgents}"
TARGET_PLIST="$LAUNCH_AGENTS_DIR/$LABEL.plist"
LAUNCHCTL_BIN="${BOT_POWER_LAUNCHCTL_BIN:-/bin/launchctl}"
USER_ID="${BOT_POWER_USER_ID:-$(/usr/bin/id -u)}"

if [ "$PLATFORM" != "Darwin" ]; then
    echo "macOS 전원 관리는 Darwin에서만 제거할 수 있습니다." >&2
    exit 1
fi

if [ -x "$LAUNCHCTL_BIN" ]; then
    "$LAUNCHCTL_BIN" bootout "gui/$USER_ID/$LABEL" >/dev/null 2>&1 || true
fi

rm -f "$STATE_DIR/enabled" "$STATE_DIR/desired_state" "$STATE_DIR/reconcile.lock/pid"
rmdir "$STATE_DIR/reconcile.lock" 2>/dev/null || true
rm -f "$TARGET_PLIST"

echo "✅ macOS 전원 관리 제거 완료"
echo "   현재 봇 프로세스 상태는 변경하지 않았습니다."
