-- SQLite Schema for Telegram Claude Bot
-- 이 파일이 DB 스키마의 단일 소스 (Single Source of Truth)
-- 테이블 추가/변경 시 이 파일만 수정

PRAGMA foreign_keys = ON;
PRAGMA journal_mode = WAL;

-- Users table: tracks current/previous session per user
CREATE TABLE IF NOT EXISTS users (
    id TEXT PRIMARY KEY,
    current_session_id TEXT,
    previous_session_id TEXT,
    selected_ai_provider TEXT NOT NULL DEFAULT 'claude',
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Sessions table: Claude conversation sessions
CREATE TABLE IF NOT EXISTS sessions (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    ai_provider TEXT NOT NULL DEFAULT 'claude',
    provider_session_id TEXT,
    model TEXT NOT NULL DEFAULT 'sonnet',
    name TEXT,
    workspace_path TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    last_used TEXT NOT NULL DEFAULT (datetime('now')),
    deleted INTEGER NOT NULL DEFAULT 0,
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_sessions_user_id ON sessions(user_id);
CREATE INDEX IF NOT EXISTS idx_sessions_ai_provider ON sessions(ai_provider);
CREATE INDEX IF NOT EXISTS idx_sessions_last_used ON sessions(last_used DESC);
CREATE INDEX IF NOT EXISTS idx_sessions_deleted ON sessions(deleted);
CREATE UNIQUE INDEX IF NOT EXISTS idx_sessions_workspace_unique
    ON sessions(user_id, ai_provider, workspace_path) WHERE workspace_path IS NOT NULL AND deleted = 0;

-- user_provider_state: provider별 current/previous session 분리 관리
CREATE TABLE IF NOT EXISTS user_provider_state (
    user_id TEXT NOT NULL,
    ai_provider TEXT NOT NULL,
    current_session_id TEXT,
    previous_session_id TEXT,
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (user_id, ai_provider),
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_user_provider_state_provider ON user_provider_state(ai_provider);

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
    schedule_type TEXT NOT NULL DEFAULT 'chat',
    trigger_type TEXT NOT NULL DEFAULT 'cron',
    cron_expr TEXT,
    run_at_local TEXT,
    ai_provider TEXT NOT NULL DEFAULT 'claude',
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
CREATE INDEX IF NOT EXISTS idx_schedules_ai_provider ON schedules(ai_provider);
CREATE INDEX IF NOT EXISTS idx_schedules_trigger_type ON schedules(trigger_type);

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

-- Plugin tables (memos, todos, weather_locations) are managed by each plugin's get_schema()

-- Message log table: AI message request/response tracking
CREATE TABLE IF NOT EXISTS message_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id INTEGER NOT NULL,
    session_id TEXT NOT NULL,
    model TEXT NOT NULL DEFAULT 'sonnet',
    workspace_path TEXT,

    -- request
    request TEXT NOT NULL,
    request_at TEXT NOT NULL DEFAULT (datetime('now')),

    -- processing state
    processed INTEGER NOT NULL DEFAULT 0,  -- 0: pending, 1: processing, 2: completed
    processed_at TEXT,
    retry_count INTEGER NOT NULL DEFAULT 0,

    -- response
    response TEXT,
    error TEXT,

    FOREIGN KEY (session_id) REFERENCES sessions(id) ON DELETE CASCADE
);

-- auth_sessions: 인증 세션 영속화
CREATE TABLE IF NOT EXISTS auth_sessions (
    user_id TEXT PRIMARY KEY,
    authenticated_at TEXT NOT NULL
);

-- pending_messages: 세션 충돌 시 임시 메시지 영속화
CREATE TABLE IF NOT EXISTS pending_messages (
    pending_key TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    chat_id INTEGER NOT NULL,
    message TEXT NOT NULL,
    model TEXT,
    is_new_session INTEGER NOT NULL DEFAULT 0,
    workspace_path TEXT,
    current_session_id TEXT,
    created_at REAL NOT NULL
);

-- queued_messages: persistent queue for concurrent requests
CREATE TABLE IF NOT EXISTS queued_messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    chat_id INTEGER NOT NULL,
    message TEXT NOT NULL,
    model TEXT NOT NULL,
    is_new_session INTEGER NOT NULL,
    workspace_path TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    expires_at TEXT NOT NULL DEFAULT '9999-12-31T23:59:59+00:00'
);

CREATE INDEX IF NOT EXISTS idx_message_log_chat_id ON message_log(chat_id);
CREATE INDEX IF NOT EXISTS idx_message_log_processed ON message_log(processed);
CREATE INDEX IF NOT EXISTS idx_message_log_request_at ON message_log(request_at);
CREATE INDEX IF NOT EXISTS idx_queued_messages_session_id ON queued_messages(session_id);

-- session_locks: detached worker ownership for Claude processing
CREATE TABLE IF NOT EXISTS session_locks (
    session_id TEXT PRIMARY KEY,
    job_id INTEGER NOT NULL,
    worker_pid INTEGER,
    acquired_at TEXT NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (session_id) REFERENCES sessions(id) ON DELETE CASCADE,
    FOREIGN KEY (job_id) REFERENCES message_log(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_session_locks_job_id ON session_locks(job_id);
CREATE INDEX IF NOT EXISTS idx_session_locks_worker_pid ON session_locks(worker_pid);

-- Triggers for updated_at
CREATE TRIGGER IF NOT EXISTS update_users_timestamp
AFTER UPDATE ON users
BEGIN
    UPDATE users SET updated_at = datetime('now') WHERE id = NEW.id;
END;
