"""Hidden command schedule draft workflow."""

from __future__ import annotations

import json
import os
import re
import shlex
import signal
from pathlib import Path
from typing import Any, Optional

from apscheduler.triggers.cron import CronTrigger
from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from src.bot.formatters import escape_html
from src.logging_config import logger
from src.plugins.loader import Plugin, PluginMenuEntry, PluginResult, ToolSpec
from src.repository.adapters import RepositoryCommandScheduleDraftStore
from src.runtime_paths import get_main_lock_path
from src.schedule_utils import cron_description
from src.services.command_execution_service import CommandExecutionService


_SEQ_RE = re.compile(r"send_message:seq:(\d+)")
_DRAFT_RE = re.compile(r"send_message:command_schedule_draft\s*(.+)\s*\Z", re.DOTALL)
_JSON_FENCE_RE = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.DOTALL)


class CommandSchedulePlugin(Plugin):
    """Internal plugin for command schedule drafts."""

    name = "command_schedule"
    description = "Internal command schedule draft workflow"
    display_name = "Command Schedule"
    CALLBACK_PREFIX = "cmdsched:"
    MENU_ENTRY = PluginMenuEntry(label="Command Schedule", surfaces=())

    def __init__(self):
        super().__init__()
        self._runner = CommandExecutionService(default_cwd=self._project_root())

    def get_schema(self) -> str:
        return """
CREATE TABLE IF NOT EXISTS command_schedule_drafts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id INTEGER NOT NULL,
    title TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    script_path TEXT NOT NULL,
    script_content TEXT NOT NULL,
    command TEXT NOT NULL,
    cron_expr TEXT NOT NULL,
    cron_description TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'draft',
    schedule_id TEXT,
    last_test_output TEXT,
    last_test_error TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_command_schedule_drafts_chat_id ON command_schedule_drafts(chat_id);
CREATE TRIGGER IF NOT EXISTS update_command_schedule_drafts_timestamp
AFTER UPDATE ON command_schedule_drafts
BEGIN
    UPDATE command_schedule_drafts SET updated_at = datetime('now') WHERE id = NEW.id;
END;
"""

    async def handle(self, message: str, chat_id: int) -> PluginResult:
        del message, chat_id
        return PluginResult(handled=False)

    def get_menu_entry(self) -> PluginMenuEntry:
        return PluginMenuEntry(label="Command Schedule", surfaces=())

    @property
    def store(self) -> RepositoryCommandScheduleDraftStore:
        return self.storage

    def build_storage(self, repository):
        return RepositoryCommandScheduleDraftStore(repository)

    def get_tool_specs(self) -> list[ToolSpec]:
        return [
            ToolSpec(
                name="command_schedule_create_draft",
                description=(
                    "Create a temporary command schedule draft. Use this instead of inserting "
                    "directly into schedules. After creating the draft, respond exactly with "
                    "send_message:seq:<id>."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "title": {"type": "string", "description": "Short user-facing title"},
                        "description": {"type": "string", "description": "What this command does"},
                        "script_path": {"type": "string", "description": "Relative .py path inside the project"},
                        "script_content": {"type": "string", "description": "Full Python script content"},
                        "command": {"type": "string", "description": "Command to execute the script"},
                        "cron_expr": {"type": "string", "description": "5-field cron expression"},
                    },
                    "required": ["title", "description", "script_path", "script_content", "command", "cron_expr"],
                },
                handler=self._tool_create_draft,
            )
        ]

    def _tool_create_draft(
        self,
        title: str,
        description: str,
        script_path: str,
        script_content: str,
        command: str,
        cron_expr: str,
    ) -> str:
        """MCP tool: create one command schedule draft."""
        chat_id = int(os.getenv("ADMIN_CHAT_ID", "0") or 0)
        try:
            draft_id = self._create_draft(
                chat_id=chat_id,
                title=title,
                description=description,
                script_path=script_path,
                script_content=script_content,
                command=command,
                cron_expr=cron_expr,
            )
        except ValueError as exc:
            return f"ERROR: {exc}"
        return f"OK: draft created. Respond exactly with send_message:seq:{draft_id}"

    def _create_draft(
        self,
        *,
        chat_id: int,
        title: str,
        description: str,
        script_path: str,
        script_content: str,
        command: str,
        cron_expr: str,
    ) -> int:
        if not self.store:
            raise ValueError("repository unavailable")

        safe_script_path = self._safe_script_path(script_path)
        self._validate_cron(cron_expr)
        self._validate_command(command, safe_script_path)

        description_text = (description or "").strip()
        cron_text = cron_description(cron_expr)
        return self.store.create_draft(
            chat_id=chat_id,
            title=title.strip()[:120] or "Command schedule",
            description=description_text,
            script_path=str(safe_script_path.relative_to(self._project_root())),
            script_content=script_content,
            command=command.strip(),
            cron_expr=cron_expr.strip(),
            cron_description=cron_text,
        )

    async def handle_ai_completion(
        self,
        action_name: str,
        chat_id: int,
        payload: dict[str, Any],
        *,
        ai_response: str,
        ai_error: Optional[str],
        session_id: Optional[str] = None,
    ) -> str | dict | None:
        del payload, session_id
        if action_name != "render_draft":
            return None
        if ai_error:
            return {
                "text": (
                    "<b>Command schedule draft failed</b>\n\n"
                    f"<code>{escape_html(ai_error)}</code>"
                )
            }

        match = _SEQ_RE.search(ai_response or "")
        if not match:
            draft = self._create_draft_from_structured_response(ai_response or "", chat_id)
            if draft:
                return {
                    "text": self._render_draft(draft),
                    "delivery_buttons": self._draft_buttons(draft["id"], include_register=True),
                }
            return {
                "text": (
                    "<b>Command schedule draft was not created</b>\n\n"
                    "AI did not return a valid <code>send_message:seq:&lt;id&gt;</code> token, "
                    "so no registration action is available."
                )
            }

        draft = self._get_draft(int(match.group(1)), chat_id)
        if not draft:
            return {
                "text": "<b>Command schedule draft not found</b>\n\nThe referenced draft is missing or expired."
            }

        return {
            "text": self._render_draft(draft),
            "delivery_buttons": self._draft_buttons(draft["id"], include_register=True),
        }

    def _create_draft_from_structured_response(self, ai_response: str, chat_id: int) -> Optional[dict[str, Any]]:
        payload = self._parse_structured_draft(ai_response)
        if not payload:
            return None

        try:
            draft_id = self._create_draft(
                chat_id=chat_id,
                title=str(payload.get("title", "")),
                description=str(payload.get("description", "")),
                script_path=str(payload.get("script_path", "")),
                script_content=str(payload.get("script_content", "")),
                command=str(payload.get("command", "")),
                cron_expr=str(payload.get("cron_expr", "")),
            )
        except Exception as exc:
            logger.warning(f"Command schedule structured draft rejected: {exc}")
            return None
        return self._get_draft(draft_id, chat_id)

    @staticmethod
    def _parse_structured_draft(ai_response: str) -> Optional[dict[str, Any]]:
        text = (ai_response or "").strip()
        match = _DRAFT_RE.search(text)
        if match:
            text = match.group(1).strip()

        fence = _JSON_FENCE_RE.search(text)
        if fence:
            text = fence.group(1).strip()

        if not text.startswith("{"):
            return None

        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            return None
        if not isinstance(payload, dict):
            return None

        required = {"title", "description", "script_path", "script_content", "command", "cron_expr"}
        if not required.issubset(payload):
            return None
        return payload

    async def handle_callback_async(self, callback_data: str, chat_id: int) -> dict:
        parts = callback_data.split(":")
        action = parts[1] if len(parts) > 1 else ""
        draft_id = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else 0
        draft = self._get_draft(draft_id, chat_id)
        if not draft:
            return {"text": "Draft not found.", "edit": False}

        if action == "test":
            return await self._test_draft(draft)
        if action == "register":
            return self._register_draft(draft, chat_id)
        if action == "cancel":
            self._update_draft(draft["id"], status="cancelled")
            return {"text": "Command schedule draft cancelled.", "edit": True}
        return {"text": "Unknown command schedule action.", "edit": False}

    async def _test_draft(self, draft: dict[str, Any]) -> dict:
        try:
            self._write_script(draft)
            result = await self._runner.run(draft["command"], cwd=self._project_root(), timeout_seconds=60)
            body = self._runner.build_telegram_body(result)
            self._update_draft(
                draft["id"],
                status="tested" if result.ok else "failed",
                last_test_output=result.stdout,
                last_test_error=result.stderr if not result.ok else None,
            )
            if body is None:
                body = "(No output)"
            return {
                "text": f"<b>Command test: {escape_html(draft['title'])}</b>\n\n{body}",
                "reply_markup": self._draft_markup(draft["id"], include_register=True),
                "edit": False,
            }
        except Exception as exc:
            logger.exception(f"Command schedule draft test failed: {exc}")
            self._update_draft(draft["id"], status="failed", last_test_error=str(exc))
            return {
                "text": f"<b>Command test failed</b>\n\n<code>{escape_html(str(exc))}</code>",
                "reply_markup": self._draft_markup(draft["id"], include_register=False),
                "edit": False,
            }

    def _register_draft(self, draft: dict[str, Any], chat_id: int) -> dict:
        if not self.store:
            return {"text": "Repository unavailable.", "edit": False}

        try:
            self._write_script(draft)
            schedule = self.store.add_command_schedule(
                user_id=str(chat_id),
                chat_id=chat_id,
                name=draft["title"],
                command=draft["command"],
                cron_expr=draft["cron_expr"],
                workspace_path=str(self._project_root()),
            )
            self._update_draft(draft["id"], status="registered", schedule_id=schedule.id)
            reload_result = self._reload_schedules()
            return {
                "text": (
                    "<b>Command schedule registered</b>\n\n"
                    f"Title: <b>{escape_html(draft['title'])}</b>\n"
                    f"Schedule ID: <code>{escape_html(schedule.id)}</code>\n"
                    f"Cron: <code>{escape_html(draft['cron_expr'])}</code>\n"
                    f"{escape_html(draft['cron_description'])}\n\n"
                    f"<code>{escape_html(reload_result)}</code>"
                ),
                "edit": True,
            }
        except Exception as exc:
            logger.exception(f"Command schedule draft registration failed: {exc}")
            self._update_draft(draft["id"], status="failed", last_test_error=str(exc))
            return {
                "text": f"<b>Command schedule registration failed</b>\n\n<code>{escape_html(str(exc))}</code>",
                "edit": False,
            }

    def _get_draft(self, draft_id: int, chat_id: int) -> Optional[dict[str, Any]]:
        if not self.store:
            return None
        return self.store.get_draft(draft_id, chat_id)

    def _update_draft(self, draft_id: int, **updates: Any) -> None:
        if not self.store or not updates:
            return
        self.store.update_draft(draft_id, **updates)

    def _render_draft(self, draft: dict[str, Any]) -> str:
        return (
            f"<b>{escape_html(draft['title'])}</b>\n\n"
            f"<b>Temporary command</b>\n<code>{escape_html(draft['command'])}</code>\n\n"
            f"<b>Message</b>\n{escape_html(draft['description'])}\n\n"
            f"<b>Cron</b>\n"
            f"<code>{escape_html(draft['cron_expr'])}</code>\n"
            f"{escape_html(draft['cron_description'])}\n\n"
            f"<b>Script</b>\n<code>{escape_html(draft['script_path'])}</code>"
        )

    def _draft_buttons(self, draft_id: int, *, include_register: bool) -> list[list[dict[str, str]]]:
        rows = [[{"text": "Run command", "callback_data": f"cmdsched:test:{draft_id}"}]]
        if include_register:
            rows.append([{"text": "Register schedule", "callback_data": f"cmdsched:register:{draft_id}"}])
        rows.append([{"text": "Cancel", "callback_data": f"cmdsched:cancel:{draft_id}"}])
        return rows

    def _draft_markup(self, draft_id: int, *, include_register: bool) -> InlineKeyboardMarkup:
        rows = []
        for row in self._draft_buttons(draft_id, include_register=include_register):
            rows.append([
                InlineKeyboardButton(item["text"], callback_data=item["callback_data"])
                for item in row
            ])
        return InlineKeyboardMarkup(rows)

    def _write_script(self, draft: dict[str, Any]) -> Path:
        path = self._safe_script_path(draft["script_path"])
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(draft["script_content"], encoding="utf-8")
        return path

    def _safe_script_path(self, script_path: str) -> Path:
        if not script_path or Path(script_path).is_absolute():
            raise ValueError("script_path must be a relative path")
        path = (self._project_root() / script_path).resolve()
        if self._project_root() not in path.parents:
            raise ValueError("script_path must stay inside the project")
        if path.suffix != ".py":
            raise ValueError("script_path must point to a .py file")
        return path

    def _validate_command(self, command: str, script_path: Path) -> None:
        parts = shlex.split(command)
        if len(parts) < 2:
            raise ValueError("command must execute a Python script")
        executable = parts[0]
        allowed = {"python", "python3", "venv/bin/python", "./venv/bin/python"}
        if executable not in allowed:
            raise ValueError("command must start with python, python3, or venv/bin/python")
        command_script = (self._project_root() / parts[1]).resolve()
        if command_script != script_path:
            raise ValueError("command script path must match script_path")

    @staticmethod
    def _validate_cron(cron_expr: str) -> None:
        CronTrigger.from_crontab(cron_expr)

    def _reload_schedules(self) -> str:
        pid_file = get_main_lock_path()
        if not pid_file.exists():
            return "reload skipped: main PID file not found"
        pid = int(pid_file.read_text().strip())
        os.kill(pid, signal.SIGUSR1)
        return f"reload signal sent (PID={pid})"

    @staticmethod
    def _project_root() -> Path:
        return Path(__file__).resolve().parents[3]
