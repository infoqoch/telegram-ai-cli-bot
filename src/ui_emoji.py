"""Canonical emoji and button labels for the Telegram UI.

Keep chat-facing emoji choices here so new features reuse the same semantics.
"""

# Rule: emojis do not overlap. One emoji maps to one UI meaning only.

PROVIDER_ICON_CLAUDE = "📚"
PROVIDER_ICON_CODEX = "🤖"

PROVIDER_BUTTON_CLAUDE = f"{PROVIDER_ICON_CLAUDE} Claude"
PROVIDER_BUTTON_CODEX = f"{PROVIDER_ICON_CODEX} Codex"

MODEL_BADGE_TOP = "🧠"
MODEL_BADGE_MID = "🚀"
MODEL_BADGE_LIGHT = "⚡"

STATUS_SUCCESS = "✅"
STATUS_ERROR = "❌"
STATUS_WARNING = "⚠️"
STATUS_LOCKED = "🔒"
STATUS_DENIED = "⛔"
STATUS_OPEN = "🔓"
STATUS_ON = "🟢"
STATUS_OFF = "🔴"
STATUS_PAUSED = "⏸"

ENTITY_AI = "💬"
ENTITY_WORKSPACE = "📂"
ENTITY_WORKSPACE_INACTIVE = "🗂"
ENTITY_PLUGIN = "🔌"
ENTITY_SESSION_CURRENT = "📍"
ENTITY_BOT = "🖥️"
ENTITY_TASKS = "📌"

BUTTON_SESSION_LIST = "📋 Session List"
BUTTON_SESSION = "💬 Session"
BUTTON_NEW_SESSION = "🆕 New Session"
BUTTON_HISTORY = "📜 History"
BUTTON_RENAME = "✏️ Rename"
BUTTON_DELETE = "🗑️ Delete"
BUTTON_REFRESH = "🔄 Refresh"
BUTTON_TASKS = "📌 Tasks"
BUTTON_SWITCH_AI = "🔀 Switch AI"
BUTTON_BACK = "← Back"
BUTTON_CANCEL = "↩️ Cancel"
BUTTON_SWITCH = "🔁 Switch"
BUTTON_LIST = "📋 List"
BUTTON_SCHEDULE_LIST = "📅 Schedule List"
BUTTON_ADD_CHAT = "💬 Chat"
BUTTON_ADD_WORKSPACE = "📂 Workspace"
BUTTON_ADD_PLUGIN = "🔌 Plugin"
BUTTON_ADD_NEW = "➕ Add New"
BUTTON_WORKSPACE_SESSION = "💬 Session"
BUTTON_WORKSPACE_SCHEDULE = "📅 Schedule"
BUTTON_MANUAL_INPUT = "⌨️ Manual Input"
BUTTON_SCHEDULES = "📅 Schedules"
BUTTON_WORKSPACES = "📂 Workspaces"
