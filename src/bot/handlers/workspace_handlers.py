"""Workspace command handlers."""

import os
from pathlib import Path

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ForceReply
from telegram.ext import ContextTypes

from src.ai import get_profile_label
from src.logging_config import logger, clear_context
from src.constants import AVAILABLE_HOURS
from src.schedule_utils import next_occurrence, normalize_trigger_type
from src.ui_emoji import (
    BUTTON_ADD_NEW,
    BUTTON_BACK,
    BUTTON_CANCEL,
    BUTTON_DELETE,
    BUTTON_MANUAL_INPUT,
    BUTTON_REFRESH,
    BUTTON_SCHEDULES,
    BUTTON_WORKSPACES,
    BUTTON_WORKSPACE_SESSION,
    BUTTON_WORKSPACE_SCHEDULE,
)
from ..constants import get_model_emoji
from ..formatters import escape_html
from ..middleware import authorized_only, authenticated_only
from .base import BaseHandler


class WorkspaceHandlers(BaseHandler):
    """Workspace command handlers."""

    @authorized_only
    @authenticated_only
    async def workspace_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle /workspace command - manage workspaces."""
        chat_id = update.effective_chat.id
        self._setup_request_context(chat_id)
        user_id = str(chat_id)
        logger.info("/workspace command received")

        if not self._workspace_registry:
            await update.message.reply_text("Workspace feature not initialized.")
            clear_context()
            return

        text = self._workspace_registry.get_status_text(user_id)
        keyboard = self._build_workspace_keyboard(user_id)

        await update.message.reply_text(
            text,
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="HTML"
        )
        logger.trace("/workspace complete")
        clear_context()

    def _build_workspace_keyboard(self, user_id: str) -> list:
        """Build workspace UI keyboard."""
        buttons = []

        if self._workspace_registry:
            workspaces = self._workspace_registry.list_by_user(user_id)
            for ws in workspaces[:10]:
                buttons.append([
                    InlineKeyboardButton(
                        f"{ws.name[:15]}",
                        callback_data=f"ws:select:{ws.id}"
                    ),
                    InlineKeyboardButton(BUTTON_DELETE, callback_data=f"ws:delete:{ws.id}"),
                ])

        buttons.append([
            InlineKeyboardButton(BUTTON_ADD_NEW, callback_data="ws:add"),
            InlineKeyboardButton(BUTTON_REFRESH, callback_data="ws:refresh"),
        ])

        return buttons

    async def _handle_workspace_callback(self, query, chat_id: int, callback_data: str) -> None:
        """Handle workspace callbacks."""
        user_id = str(chat_id)
        action = callback_data[3:]  # Remove "ws:"

        if not self._workspace_registry:
            await query.answer("Workspace feature disabled")
            return

        # Refresh
        if action == "refresh":
            text = self._workspace_registry.get_status_text(user_id)
            keyboard = self._build_workspace_keyboard(user_id)
            await query.edit_message_text(
                text,
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode="HTML"
            )
            await query.answer("Refreshed")
            return

        # Workspace select - action selection
        if action.startswith("select:"):
            ws_id = action[7:]
            ws = self._workspace_registry.get(ws_id)
            if not ws:
                await query.answer("Workspace not found")
                return

            buttons = [
                [
                    InlineKeyboardButton(BUTTON_WORKSPACE_SESSION, callback_data=f"ws:session:{ws_id}"),
                    InlineKeyboardButton(BUTTON_WORKSPACE_SCHEDULE, callback_data=f"ws:schedule:{ws_id}"),
                ],
                [InlineKeyboardButton(BUTTON_BACK, callback_data="ws:refresh")],
            ]

            await query.edit_message_text(
                f"<b>{escape_html(ws.name)}</b>\n\n"
                f"<code>{escape_html(ws.short_path)}</code>\n"
                f"{escape_html(ws.description)}\n\n"
                f"What would you like to do?",
                reply_markup=InlineKeyboardMarkup(buttons),
                parse_mode="HTML"
            )
            await query.answer()
            return

        # Start session - model selection
        if action.startswith("session:"):
            ws_id = action[8:]
            ws = self._workspace_registry.get(ws_id)
            if not ws:
                await query.answer("Workspace not found")
                return

            provider = self._get_selected_ai_provider(user_id)

            buttons = [
                self._build_model_buttons(provider, f"ws:sess_model:{ws_id}:"),
                [InlineKeyboardButton(BUTTON_BACK, callback_data=f"ws:select:{ws_id}")],
            ]

            await query.edit_message_text(
                f"<b>{escape_html(ws.name)}</b> - Start Session\n\n"
                f"<code>{escape_html(ws.short_path)}</code>\n\n"
                f"Current AI: <b>{self._format_provider_display(provider)}</b>\n"
                f"Select model:",
                reply_markup=InlineKeyboardMarkup(buttons),
                parse_mode="HTML"
            )
            await query.answer()
            return

        # Create session
        if action.startswith("sess_model:"):
            parts = action.split(":")
            ws_id, model = parts[1], parts[2]
            ws = self._workspace_registry.get(ws_id)
            if not ws:
                await query.answer("Workspace not found")
                return

            # Prevent duplicate workspace sessions
            existing = self.sessions.list_sessions(user_id)
            for s in existing:
                if s.get("workspace_path") == ws.path:
                    self.sessions.switch_session(user_id, s["full_session_id"])
                    await query.edit_message_text(
                        f"A workspace session already exists.\n"
                        f"Switched to existing session: <b>{escape_html(s.get('name', ws.name))}</b>",
                        parse_mode="HTML"
                    )
                    await query.answer("Switched to existing session")
                    return

            self._workspace_registry.mark_used(ws_id)
            provider = self._get_selected_ai_provider(user_id)

            session_name = f"{ws.name} ({model})"
            session_id = self.sessions.create_session(
                user_id=user_id,
                ai_provider=provider,
                model=model,
                name=session_name,
                workspace_path=ws.path,
            )

            model_emoji = get_model_emoji(model)
            await query.edit_message_text(
                f"<b>Workspace Session Created!</b>\n\n"
                f"<b>{escape_html(ws.name)}</b>\n"
                f"<code>{escape_html(ws.short_path)}</code>\n"
                f"AI: <b>{self._format_provider_display(provider)}</b>\n"
                f"{model_emoji} Model: <b>{get_profile_label(provider, model)}</b> (<code>{model}</code>)\n"
                f"Session: <code>{session_id[:8]}</code>\n\n"
                f"Messages will now use this workspace context.",
                parse_mode="HTML"
            )
            await query.answer("Session created")
            return

        if action.startswith("schedule:"):
            ws_id = action[9:]
            ws = self._workspace_registry.get(ws_id)
            if not ws:
                await query.answer("Workspace not found")
                return

            buttons = []
            row = []
            for hour in AVAILABLE_HOURS:
                row.append(InlineKeyboardButton(f"{hour:02d}:00", callback_data=f"ws:sched_time:{ws_id}:{hour}"))
                if len(row) == 4:
                    buttons.append(row)
                    row = []
            if row:
                buttons.append(row)
            buttons.append([InlineKeyboardButton(BUTTON_BACK, callback_data=f"ws:select:{ws_id}")])

            await query.edit_message_text(
                f"<b>{escape_html(ws.name)}</b> - Schedule Registration\n\n"
                f"<code>{escape_html(ws.short_path)}</code>\n\n"
                f"Select hour:",
                reply_markup=InlineKeyboardMarkup(buttons),
                parse_mode="HTML",
            )
            await query.answer()
            return

        if action.startswith("sched_time:"):
            _, ws_id, hour_text = action.split(":")
            ws = self._workspace_registry.get(ws_id)
            if not ws:
                await query.answer("Workspace not found")
                return

            self._ws_pending[user_id] = {
                "ws_id": ws_id,
                "hour": int(hour_text),
                "ai_provider": self._get_selected_ai_provider(user_id),
            }

            buttons = []
            row = []
            for minute in range(0, 60, 5):
                row.append(InlineKeyboardButton(f":{minute:02d}", callback_data=f"ws:sched_minute:{ws_id}:{minute}"))
                if len(row) == 4:
                    buttons.append(row)
                    row = []
            if row:
                buttons.append(row)
            buttons.append([InlineKeyboardButton(BUTTON_BACK, callback_data=f"ws:schedule:{ws_id}")])

            await query.edit_message_text(
                f"<b>{escape_html(ws.name)}</b> - Schedule Registration\n\n"
                f"Hour: <b>{int(hour_text):02d}:00</b>\n\n"
                f"Select minute:",
                reply_markup=InlineKeyboardMarkup(buttons),
                parse_mode="HTML",
            )
            await query.answer()
            return

        if action.startswith("sched_minute:"):
            _, ws_id, minute_text = action.split(":")
            ws = self._workspace_registry.get(ws_id)
            if not ws:
                await query.answer("Workspace not found")
                return

            pending = self._ws_pending.get(user_id, {})
            pending["minute"] = int(minute_text)
            pending["ai_provider"] = self._get_selected_ai_provider(user_id)
            self._ws_pending[user_id] = pending

            await query.edit_message_text(
                f"<b>{escape_html(ws.name)}</b> - Schedule Registration\n\n"
                f"Time: <b>{pending.get('hour', 0):02d}:{int(minute_text):02d}</b>\n\n"
                f"Choose schedule mode:",
                reply_markup=InlineKeyboardMarkup([
                    [
                        InlineKeyboardButton("Daily", callback_data=f"ws:sched_trigger:{ws_id}:cron"),
                        InlineKeyboardButton("One-time", callback_data=f"ws:sched_trigger:{ws_id}:once"),
                    ],
                    [InlineKeyboardButton(BUTTON_BACK, callback_data=f"ws:schedule:{ws_id}")],
                ]),
                parse_mode="HTML",
            )
            await query.answer()
            return

        if action.startswith("sched_trigger:"):
            _, ws_id, trigger_type = action.split(":")
            ws = self._workspace_registry.get(ws_id)
            if not ws:
                await query.answer("Workspace not found")
                return

            pending = self._ws_pending.get(user_id, {})
            pending["trigger_type"] = normalize_trigger_type(trigger_type)
            if pending["trigger_type"] == "once":
                pending["run_at_local"] = next_occurrence(
                    pending.get("hour", 0),
                    pending.get("minute", 0),
                ).isoformat()
            else:
                pending["run_at_local"] = None
            self._ws_pending[user_id] = pending

            provider = pending.get("ai_provider", self._get_selected_ai_provider(user_id))
            buttons = [
                self._build_model_buttons(provider, f"ws:sched_model:{ws_id}:"),
                [InlineKeyboardButton(BUTTON_BACK, callback_data=f"ws:schedule:{ws_id}")],
            ]

            await query.edit_message_text(
                f"<b>{escape_html(ws.name)}</b> - Schedule Registration\n\n"
                f"Time: <b>{pending.get('hour', 0):02d}:{pending.get('minute', 0):02d}</b>\n"
                f"Schedule: <b>{'One-time' if pending['trigger_type'] == 'once' else 'Daily'}</b>\n"
                f"Current AI: <b>{self._format_provider_display(provider)}</b>\n\n"
                f"Select model:",
                reply_markup=InlineKeyboardMarkup(buttons),
                parse_mode="HTML",
            )
            await query.answer()
            return

        if action.startswith("sched_model:"):
            _, ws_id, model = action.split(":")
            ws = self._workspace_registry.get(ws_id)
            if not ws:
                await query.answer("Workspace not found")
                return

            pending = self._ws_pending.get(user_id, {})
            pending["model"] = model
            self._ws_pending[user_id] = pending

            provider = pending.get("ai_provider", self._get_selected_ai_provider(user_id))
            await query.edit_message_text(
                f"<b>{escape_html(ws.name)}</b> - Schedule Registration\n\n"
                f"Time: <b>{pending.get('hour', 0):02d}:{pending.get('minute', 0):02d}</b>\n"
                f"Schedule: <b>{'One-time' if pending.get('trigger_type') == 'once' else 'Daily'}</b>\n"
                f"AI: <b>{self._format_provider_display(provider)}</b>\n"
                f"Model: <b>{get_profile_label(provider, model)}</b> (<code>{model}</code>)\n\n"
                f"Enter scheduled message below:",
                parse_mode="HTML",
            )
            await query.message.reply_text(
                "Enter scheduled message:",
                reply_markup=ForceReply(selective=True, input_field_placeholder="e.g., Summarize today's tasks"),
            )
            await query.answer()
            return

        # Add new - AI recommendation start
        if action == "add":
            await query.edit_message_text(
                "<b>Register Workspace</b>\n\n"
                "What is this workspace for?\n"
                "AI will recommend suitable paths.\n\n"
                "Enter the purpose below:",
                parse_mode="HTML"
            )

            self._ws_pending[user_id] = {"action": "recommend"}

            await query.message.reply_text(
                "Enter purpose:",
                reply_markup=ForceReply(selective=True, input_field_placeholder="e.g., Investment analysis, React project")
            )
            await query.answer()
            return

        # Recommendation selection → ask for name
        if action.startswith("recommend:"):
            idx = int(action[10:])
            pending = self._ws_pending.get(user_id, {})
            recommendations = pending.get("recommendations", [])

            if idx >= len(recommendations):
                await query.answer("Invalid selection")
                return

            rec = recommendations[idx]
            pending["action"] = "recommend_name"
            pending["path"] = rec["path"]
            pending["suggested_name"] = rec["name"]
            pending["description"] = rec.get("description", rec.get("reason", ""))
            self._ws_pending[user_id] = pending

            await query.edit_message_text(
                f"<b>Selected:</b> <code>{rec['path'].replace(str(Path.home()), '~')}</code>\n\n"
                f"Enter workspace name:",
                parse_mode="HTML"
            )
            await query.message.reply_text(
                f"{rec['name']}",
                reply_markup=ForceReply(selective=True, input_field_placeholder=rec["name"])
            )
            await query.answer()
            return

        # Manual input selection
        if action == "manual":
            pending = self._ws_pending.get(user_id, {})
            pending["action"] = "manual_path"
            self._ws_pending[user_id] = pending

            await query.edit_message_text(
                "<b>Manual Workspace Registration</b>\n\n"
                "Enter path directly:",
                parse_mode="HTML"
            )

            await query.message.reply_text(
                "Enter path:",
                reply_markup=ForceReply(selective=True, input_field_placeholder="/path/to/workspace")
            )
            await query.answer()
            return

        # Delete
        if action.startswith("delete:"):
            ws_id = action[7:]
            if self._workspace_registry.remove(ws_id):
                await query.answer("Deleted")
                text = self._workspace_registry.get_status_text(user_id)
                keyboard = self._build_workspace_keyboard(user_id)
                await query.edit_message_text(
                    text,
                    reply_markup=InlineKeyboardMarkup(keyboard),
                    parse_mode="HTML"
                )
            else:
                await query.answer("Delete failed")
            return

        await query.answer("Unknown action")

    async def _handle_workspace_force_reply(self, update: Update, chat_id: int, message: str) -> None:
        """Handle workspace ForceReply responses."""
        user_id = str(chat_id)
        pending = self._ws_pending.get(user_id)

        if not pending:
            await update.message.reply_text("Input expired. Please try again.")
            return

        action = pending.get("action")

        # AI recommendation request
        if action == "recommend":
            await update.message.reply_text("AI is finding suitable workspaces...")

            allowed_paths = self._get_allowed_workspace_paths()
            recommendations = await self._workspace_registry.recommend_paths(
                user_id=user_id,
                purpose=message,
                allowed_patterns=allowed_paths,
            )

            if not recommendations:
                pending["action"] = "manual_path"
                pending["purpose"] = message
                self._ws_pending[user_id] = pending

                await update.message.reply_text(
                    "Could not get AI recommendations.\n\n"
                    "Enter path manually:",
                    reply_markup=ForceReply(selective=True, input_field_placeholder="/path/to/workspace")
                )
                return

            pending["recommendations"] = recommendations
            pending["purpose"] = message
            self._ws_pending[user_id] = pending

            buttons = []
            for i, rec in enumerate(recommendations):
                path_short = rec["path"].replace(str(Path.home()), "~")
                buttons.append([
                    InlineKeyboardButton(
                        f"{rec['name']}",
                        callback_data=f"ws:recommend:{i}"
                    )
                ])

            buttons.append([
                InlineKeyboardButton(BUTTON_MANUAL_INPUT, callback_data="ws:manual"),
                InlineKeyboardButton(BUTTON_CANCEL, callback_data="ws:refresh"),
            ])

            rec_text = "\n\n".join([
                f"<b>{i+1}. {escape_html(r['name'])}</b>\n"
                f"<code>{escape_html(r['path'].replace(str(Path.home()), '~'))}</code>\n"
                f"{escape_html(r['description'])}\n"
                f"{escape_html(r.get('reason', ''))}"
                for i, r in enumerate(recommendations)
            ])

            await update.message.reply_text(
                f"<b>AI Recommendations</b>\n\n"
                f"Purpose: <i>{escape_html(message)}</i>\n\n"
                f"────────────\n\n"
                f"{rec_text}",
                reply_markup=InlineKeyboardMarkup(buttons),
                parse_mode="HTML"
            )
            return

        # Recommend name input - registration complete
        if action == "recommend_name":
            name = message.strip()[:30]
            path = pending.get("path", "")
            description = pending.get("description", "")

            ws = self._workspace_registry.add(
                user_id=user_id,
                path=path,
                name=name,
                description=description,
            )

            del self._ws_pending[user_id]

            text = self._workspace_registry.get_status_text(user_id)
            keyboard = self._build_workspace_keyboard(user_id)

            await update.message.reply_text(
                f"<b>Workspace Registered!</b>\n\n"
                f"<b>{escape_html(ws.name)}</b>\n"
                f"<code>{escape_html(ws.short_path)}</code>\n"
                f"{escape_html(ws.description)}\n\n"
                f"────────────\n\n{text}",
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode="HTML"
            )
            return

        # Manual path input
        if action == "manual_path":
            path = message.strip()
            expanded_path = Path(path).expanduser().resolve()

            if not expanded_path.exists():
                await update.message.reply_text(
                    f"Path does not exist: <code>{escape_html(path)}</code>\n\n"
                    f"Enter again:",
                    reply_markup=ForceReply(selective=True, input_field_placeholder="/path/to/workspace"),
                    parse_mode="HTML"
                )
                return

            pending["path"] = str(expanded_path)
            pending["action"] = "manual_name"
            self._ws_pending[user_id] = pending

            await update.message.reply_text(
                f"Path confirmed: <code>{expanded_path}</code>\n\n"
                f"Enter workspace name:",
                reply_markup=ForceReply(selective=True, input_field_placeholder="e.g., Investment"),
                parse_mode="HTML"
            )
            return

        # Name input
        if action == "manual_name":
            name = message.strip()[:30]
            pending["name"] = name
            pending["action"] = "manual_desc"
            self._ws_pending[user_id] = pending

            await update.message.reply_text(
                f"Name: <b>{escape_html(name)}</b>\n\n"
                f"Enter workspace description:",
                reply_markup=ForceReply(selective=True, input_field_placeholder="e.g., Stock investment analysis project"),
                parse_mode="HTML"
            )
            return

        # Description input - registration complete
        if action == "manual_desc":
            description = message.strip()[:100]
            path = pending.get("path", "")
            name = pending.get("name", "Workspace")

            ws = self._workspace_registry.add(
                user_id=user_id,
                path=path,
                name=name,
                description=description,
            )

            del self._ws_pending[user_id]

            text = self._workspace_registry.get_status_text(user_id)
            keyboard = self._build_workspace_keyboard(user_id)

            await update.message.reply_text(
                f"<b>Workspace Registered!</b>\n\n"
                f"<b>{escape_html(ws.name)}</b>\n"
                f"<code>{escape_html(ws.short_path)}</code>\n"
                f"{escape_html(ws.description)}\n\n"
                f"────────────\n\n{text}",
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode="HTML"
            )
            return

        if "ws_id" in pending and "model" in pending:
            ws_id = pending.get("ws_id")
            ws = self._workspace_registry.get(ws_id)
            if not ws:
                await update.message.reply_text("Workspace not found.")
                del self._ws_pending[user_id]
                return

            if not self._schedule_manager:
                await update.message.reply_text("Schedule feature not initialized.")
                del self._ws_pending[user_id]
                return

            schedule = self._schedule_manager.add(
                user_id=user_id,
                chat_id=chat_id,
                name=ws.name,
                hour=pending["hour"],
                minute=pending.get("minute", 0),
                message=message,
                schedule_type="workspace",
                trigger_type=pending.get("trigger_type", "cron"),
                ai_provider=pending.get("ai_provider", self._get_selected_ai_provider(user_id)),
                model=pending["model"],
                workspace_path=ws.path,
                run_at_local=pending.get("run_at_local"),
            )

            self._workspace_registry.mark_used(ws_id)
            del self._ws_pending[user_id]

            await update.message.reply_text(
                f"<b>Workspace Schedule Registered!</b>\n\n"
                f"<b>{escape_html(ws.name)}</b>\n"
                f"<code>{escape_html(ws.short_path)}</code>\n"
                f"AI: <b>{self._format_provider_display(schedule.ai_provider)}</b>\n"
                f"Time: <b>{escape_html(schedule.time_str)}</b>\n"
                f"Schedule: <b>{escape_html('Once at ' + schedule.time_str if schedule.trigger_type == 'once' else f'Daily at {pending['hour']:02d}:{pending.get('minute', 0):02d}')}</b>\n"
                f"Next run: <b>{escape_html(schedule.next_run_text)}</b>\n"
                f"Message: <i>{escape_html(message[:50])}{'...' if len(message) > 50 else ''}</i>",
                reply_markup=InlineKeyboardMarkup([
                    [
                        InlineKeyboardButton(BUTTON_SCHEDULES, callback_data="sched:refresh"),
                        InlineKeyboardButton(BUTTON_WORKSPACES, callback_data="ws:refresh"),
                    ]
                ]),
                parse_mode="HTML",
            )
            logger.info(f"Workspace schedule registered: {ws.name} ({schedule.trigger_type})")
            return

        await update.message.reply_text("Unknown input state. Please try again.")
        if user_id in self._ws_pending:
            del self._ws_pending[user_id]

    def _get_allowed_workspace_paths(self) -> list[str]:
        """Get allowed workspace path list."""
        allowed = os.getenv("ALLOWED_WORKSPACE_PATHS", "") or os.getenv("ALLOWED_PROJECT_PATHS", "")
        if not allowed:
            home = Path.home()
            return [
                str(home / "AiSandbox"),
                str(home / "Projects"),
            ]

        paths = []
        for pattern in allowed.split(","):
            pattern = pattern.strip()
            if pattern.endswith("/*"):
                parent = Path(pattern[:-2]).expanduser()
                if parent.exists():
                    paths.extend([str(p) for p in parent.iterdir() if p.is_dir() and not p.name.startswith(".")])
            else:
                paths.append(pattern)

        return sorted(paths)
