-- SQLite Schema for Telegram Claude Bot
-- Unified repository replacing JSON-based storage

PRAGMA foreign_keys = ON;
PRAGMA journal_mode = WAL;

-- Users table: tracks current/previous session per user
CREATE TABLE IF NOT EXISTS users (
    id TEXT PRIMARY KEY,
    current_session_id TEXT,
    previous_session_id TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Sessions table: Claude conversation sessions
CREATE TABLE IF NOT EXISTS sessions (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    model TEXT NOT NULL DEFAULT 'sonnet',
    name TEXT,
    workspace_path TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    last_used TEXT NOT NULL DEFAULT (datetime('now')),
    deleted INTEGER NOT NULL DEFAULT 0,
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_sessions_user_id ON sessions(user_id);
CREATE INDEX IF NOT EXISTS idx_sessions_last_used ON sessions(last_used DESC);
CREATE INDEX IF NOT EXISTS idx_sessions_deleted ON sessions(deleted);
CREATE UNIQUE INDEX IF NOT EXISTS idx_sessions_workspace_unique
    ON sessions(user_id, workspace_path) WHERE workspace_path IS NOT NULL AND deleted = 0;

-- Session history: message history per session
CREATE TABLE IF NOT EXISTS session_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    message TEXT NOT NULL,
    timestamp TEXT NOT NULL DEFAULT (datetime('now')),
    processed INTEGER NOT NULL DEFAULT 0,
    processor TEXT,
    FOREIGN KEY (session_id) REFERENCES sessions(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_session_history_session_id ON session_history(session_id);
CREATE INDEX IF NOT EXISTS idx_session_history_timestamp ON session_history(timestamp DESC);

-- Schedules table: scheduled tasks
CREATE TABLE IF NOT EXISTS schedules (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    chat_id INTEGER NOT NULL,
    hour INTEGER NOT NULL CHECK (hour >= 0 AND hour <= 23),
    minute INTEGER NOT NULL CHECK (minute >= 0 AND minute <= 59),
    message TEXT NOT NULL,
    name TEXT NOT NULL,
    type TEXT NOT NULL DEFAULT 'claude',
    model TEXT NOT NULL DEFAULT 'sonnet',
    workspace_path TEXT,
    plugin_name TEXT,
    action_name TEXT,
    enabled INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    last_run TEXT,
    last_error TEXT,
    run_count INTEGER NOT NULL DEFAULT 0,
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_schedules_user_id ON schedules(user_id);
CREATE INDEX IF NOT EXISTS idx_schedules_chat_id ON schedules(chat_id);
CREATE INDEX IF NOT EXISTS idx_schedules_enabled ON schedules(enabled);

-- Workspaces table: registered project directories
CREATE TABLE IF NOT EXISTS workspaces (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    path TEXT NOT NULL,
    name TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    keywords TEXT NOT NULL DEFAULT '[]',  -- JSON array
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    last_used TEXT,
    use_count INTEGER NOT NULL DEFAULT 0,
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
    UNIQUE (user_id, path)
);

CREATE INDEX IF NOT EXISTS idx_workspaces_user_id ON workspaces(user_id);
CREATE INDEX IF NOT EXISTS idx_workspaces_path ON workspaces(path);

-- Memos table: per-chat memo storage
CREATE TABLE IF NOT EXISTS memos (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id INTEGER NOT NULL,
    content TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_memos_chat_id ON memos(chat_id);

-- Todos table: per-chat daily task management
CREATE TABLE IF NOT EXISTS todos (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id INTEGER NOT NULL,
    date TEXT NOT NULL,  -- YYYY-MM-DD format
    slot TEXT NOT NULL DEFAULT 'default',
    text TEXT NOT NULL,
    done INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_todos_chat_id ON todos(chat_id);
CREATE INDEX IF NOT EXISTS idx_todos_date ON todos(date);
CREATE INDEX IF NOT EXISTS idx_todos_chat_date ON todos(chat_id, date);

-- Weather locations table: per-chat weather location preference
CREATE TABLE IF NOT EXISTS weather_locations (
    chat_id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    country TEXT,
    lat REAL NOT NULL,
    lon REAL NOT NULL,
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Message log table: AI 메시지 요청/응답 기록
CREATE TABLE IF NOT EXISTS message_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id INTEGER NOT NULL,
    session_id TEXT NOT NULL,
    model TEXT NOT NULL DEFAULT 'sonnet',
    workspace_path TEXT,

    -- 요청
    request TEXT NOT NULL,
    request_at TEXT NOT NULL DEFAULT (datetime('now')),

    -- 처리 상태
    processed INTEGER NOT NULL DEFAULT 0,  -- 0: 대기, 1: 처리중, 2: 완료
    processed_at TEXT,

    -- 응답
    response TEXT,
    error TEXT,

    FOREIGN KEY (session_id) REFERENCES sessions(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_message_log_chat_id ON message_log(chat_id);
CREATE INDEX IF NOT EXISTS idx_message_log_processed ON message_log(processed);
CREATE INDEX IF NOT EXISTS idx_message_log_request_at ON message_log(request_at);

-- Migration tracking table
CREATE TABLE IF NOT EXISTS migrations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    applied_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Triggers for updated_at
CREATE TRIGGER IF NOT EXISTS update_users_timestamp
AFTER UPDATE ON users
BEGIN
    UPDATE users SET updated_at = datetime('now') WHERE id = NEW.id;
END;

CREATE TRIGGER IF NOT EXISTS update_todos_timestamp
AFTER UPDATE ON todos
BEGIN
    UPDATE todos SET updated_at = datetime('now') WHERE id = NEW.id;
END;
