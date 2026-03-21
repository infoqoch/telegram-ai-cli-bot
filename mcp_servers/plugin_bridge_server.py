"""MCP bridge server - exposes plugin ToolSpecs as MCP tools for Claude CLI."""

import asyncio
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
            handler = tool.handler

            @mcp.tool(name=tool.name, description=tool.description)
            def _make_handler(handler=handler, **kwargs):
                if asyncio.iscoroutinefunction(handler):
                    return asyncio.run(handler(**kwargs))
                return handler(**kwargs)


_register_plugins()

if __name__ == "__main__":
    mcp.run()
