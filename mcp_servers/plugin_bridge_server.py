"""MCP bridge server - exposes plugin ToolSpecs and DB query as MCP tools for Claude CLI."""

import asyncio
import inspect
import sqlite3
import sys
import os
from pathlib import Path

# Add project root to path
_PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_PROJECT_ROOT))

# Load .env for environment variables (GOOGLE_SERVICE_ACCOUNT_FILE, etc.)
from dotenv import load_dotenv
load_dotenv(_PROJECT_ROOT / ".env")

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("bot-plugins")

_TYPE_MAP = {
    "string": str,
    "boolean": bool,
    "integer": int,
    "number": float,
}

# DB connection for query_db tool
_db_conn: sqlite3.Connection | None = None


def _get_db() -> sqlite3.Connection:
    global _db_conn
    if _db_conn is None:
        db_path = _PROJECT_ROOT / os.getenv("BOT_DATA_DIR", ".data") / "bot.db"
        _db_conn = sqlite3.connect(str(db_path))
        _db_conn.row_factory = sqlite3.Row
    return _db_conn


def _get_chat_id() -> str:
    """Get the default chat_id for single-user bot."""
    return os.getenv("ADMIN_CHAT_ID", "0")


# ==================== DB Query Tool ====================

@mcp.tool(
    name="query_db",
    description=(
        "봇 SQLite DB에 읽기 전용 SQL을 실행한다. "
        "SELECT만 허용. chat_id가 필요한 테이블에서는 {chat_id}를 사용하면 자동 치환된다. "
        "예: SELECT * FROM todos WHERE chat_id = {chat_id} AND date = '2026-03-22'"
    ),
)
def query_db(sql: str) -> str:
    """Execute read-only SQL against the bot database."""
    normalized = sql.strip().upper()
    if not normalized.startswith("SELECT"):
        return "ERROR: 읽기 전용입니다. SELECT 쿼리만 허용됩니다."

    # Replace {chat_id} placeholder
    chat_id = _get_chat_id()
    resolved_sql = sql.replace("{chat_id}", chat_id)

    try:
        conn = _get_db()
        cursor = conn.execute(resolved_sql)
        rows = cursor.fetchall()

        if not rows:
            return "결과 없음."

        # Format as readable text
        columns = [desc[0] for desc in cursor.description]
        lines = [" | ".join(columns)]
        lines.append("-" * len(lines[0]))
        for row in rows[:100]:  # limit 100 rows
            lines.append(" | ".join(str(v) for v in row))

        if len(rows) > 100:
            lines.append(f"... ({len(rows)}건 중 100건만 표시)")

        return "\n".join(lines)
    except Exception as e:
        return f"SQL 실행 오류: {e}"


@mcp.tool(
    name="db_schema",
    description="봇 DB의 테이블 목록과 스키마를 조회한다. 테이블명을 지정하면 해당 테이블의 컬럼 정보를 반환한다.",
)
def db_schema(table_name: str = "") -> str:
    """Show database schema information."""
    conn = _get_db()

    if table_name:
        cursor = conn.execute(f"PRAGMA table_info({table_name})")
        columns = cursor.fetchall()
        if not columns:
            return f"테이블 '{table_name}'을 찾을 수 없습니다."
        lines = [f"Table: {table_name}", ""]
        for col in columns:
            nullable = "" if col[3] else " (nullable)"
            pk = " [PK]" if col[5] else ""
            default = f" DEFAULT {col[4]}" if col[4] else ""
            lines.append(f"  {col[1]} {col[2]}{nullable}{default}{pk}")
        return "\n".join(lines)

    # List all tables
    cursor = conn.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
    tables = [row[0] for row in cursor.fetchall()]
    return "Tables:\n" + "\n".join(f"  - {t}" for t in tables)


# ==================== Plugin Tools ====================

def _make_tool_function(name: str, handler, parameters: dict):
    """Build a function with a proper typed signature so FastMCP generates the correct schema."""
    props = parameters.get("properties", {})
    required = set(parameters.get("required", []))

    params = []
    for param_name, schema in props.items():
        annotation = _TYPE_MAP.get(schema.get("type", "string"), str)
        if param_name in required:
            p = inspect.Parameter(param_name, inspect.Parameter.POSITIONAL_OR_KEYWORD, annotation=annotation)
        else:
            default = schema.get("default", None)
            p = inspect.Parameter(param_name, inspect.Parameter.POSITIONAL_OR_KEYWORD, default=default, annotation=annotation)
        params.append(p)

    sig = inspect.Signature(params)

    def wrapper(*args, **kwargs):
        bound = sig.bind(*args, **kwargs)
        bound.apply_defaults()
        if asyncio.iscoroutinefunction(handler):
            return asyncio.run(handler(**bound.arguments))
        return handler(**bound.arguments)

    wrapper.__signature__ = sig
    wrapper.__name__ = name
    return wrapper


def _register_plugins():
    """Load plugins and register their ToolSpecs as MCP tools."""
    from src.repository.database import get_connection
    from src.repository.repository import Repository
    from src.plugins.loader import PluginLoader

    db_path = _PROJECT_ROOT / os.getenv("BOT_DATA_DIR", ".data") / "bot.db"
    conn = get_connection(db_path)
    repo = Repository(conn)
    loader = PluginLoader(_PROJECT_ROOT, repository=repo)
    loader.load_all()

    for plugin in loader.plugins:
        for tool in plugin.get_tool_specs():
            fn = _make_tool_function(tool.name, tool.handler, tool.parameters)
            mcp.tool(name=tool.name, description=tool.description)(fn)


_register_plugins()

if __name__ == "__main__":
    mcp.run()
