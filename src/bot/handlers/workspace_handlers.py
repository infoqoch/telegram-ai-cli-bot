"""Workspace command handlers."""

import os
from pathlib import Path

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ForceReply
from telegram.ext import ContextTypes

from src.logging_config import logger, clear_context
from src.constants import AVAILABLE_HOURS
from ..constants import MAX_WORKSPACE_PATHS_DISPLAY, get_model_emoji
from .base import BaseHandler


class WorkspaceHandlers(BaseHandler):
    """Workspace command handlers."""

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
                    InlineKeyboardButton("Del", callback_data=f"ws:delete:{ws.id}"),
                ])

        buttons.append([
            InlineKeyboardButton("+ Add New", callback_data="ws:add"),
            InlineKeyboardButton("Refresh", callback_data="ws:refresh"),
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
                    InlineKeyboardButton("Session", callback_data=f"ws:session:{ws_id}"),
                    InlineKeyboardButton("Schedule", callback_data=f"ws:schedule:{ws_id}"),
                ],
                [InlineKeyboardButton("Back", callback_data="ws:refresh")],
            ]

            await query.edit_message_text(
                f"<b>{ws.name}</b>\n\n"
                f"<code>{ws.short_path}</code>\n"
                f"{ws.description}\n\n"
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

            buttons = [
                [
                    InlineKeyboardButton("Opus", callback_data=f"ws:sess_model:{ws_id}:opus"),
                    InlineKeyboardButton("Sonnet", callback_data=f"ws:sess_model:{ws_id}:sonnet"),
                    InlineKeyboardButton("Haiku", callback_data=f"ws:sess_model:{ws_id}:haiku"),
                ],
                [InlineKeyboardButton("Back", callback_data=f"ws:select:{ws_id}")],
            ]

            await query.edit_message_text(
                f"<b>{ws.name}</b> - Start Session\n\n"
                f"<code>{ws.short_path}</code>\n\n"
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

            self._workspace_registry.mark_used(ws_id)

            session_id = await self.claude.create_session()
            if not session_id:
                await query.edit_message_text("Session creation failed")
                return

            session_name = f"{ws.name} ({model})"
            self.sessions.create_session(
                user_id,
                session_id,
                session_name,
                model=model,
                workspace_path=ws.path,
            )

            model_emoji = get_model_emoji(model)
            await query.edit_message_text(
                f"<b>Workspace Session Created!</b>\n\n"
                f"<b>{ws.name}</b>\n"
                f"<code>{ws.short_path}</code>\n"
                f"{model_emoji} Model: <b>{model}</b>\n\n"
                f"Messages will now use this workspace context.",
                parse_mode="HTML"
            )
            await query.answer("Session created")
            return

        # Schedule registration - time selection
        if action.startswith("schedule:"):
            ws_id = action[9:]
            ws = self._workspace_registry.get(ws_id)
            if not ws:
                await query.answer("Workspace not found")
                return

            buttons = []
            row = []
            for hour in AVAILABLE_HOURS:
                row.append(InlineKeyboardButton(
                    f"{hour:02d}:00",
                    callback_data=f"ws:sched_time:{ws_id}:{hour}"
                ))
                if len(row) == 4:
                    buttons.append(row)
                    row = []
            if row:
                buttons.append(row)
            buttons.append([
                InlineKeyboardButton("Back", callback_data=f"ws:select:{ws_id}")
            ])

            await query.edit_message_text(
                f"<b>{ws.name}</b> - Schedule Registration\n\n"
                f"<code>{ws.short_path}</code>\n\n"
                f"Select time (daily repeat):",
                reply_markup=InlineKeyboardMarkup(buttons),
                parse_mode="HTML"
            )
            await query.answer()
            return

        # Schedule time selected - model selection
        if action.startswith("sched_time:"):
            parts = action.split(":")
            ws_id, hour = parts[1], int(parts[2])
            ws = self._workspace_registry.get(ws_id)
            if not ws:
                await query.answer("Workspace not found")
                return

            self._pending_workspace_input[user_id] = {
                "ws_id": ws_id,
                "hour": hour,
                "minute": 0,
            }

            buttons = [
                [
                    InlineKeyboardButton("Opus", callback_data=f"ws:sched_model:{ws_id}:opus"),
                    InlineKeyboardButton("Sonnet", callback_data=f"ws:sched_model:{ws_id}:sonnet"),
                    InlineKeyboardButton("Haiku", callback_data=f"ws:sched_model:{ws_id}:haiku"),
                ],
                [InlineKeyboardButton("Back", callback_data=f"ws:schedule:{ws_id}")],
            ]

            await query.edit_message_text(
                f"<b>{ws.name}</b> - Schedule Registration\n\n"
                f"Time: <b>{hour:02d}:00</b>\n\n"
                f"Select model:",
                reply_markup=InlineKeyboardMarkup(buttons),
                parse_mode="HTML"
            )
            await query.answer()
            return

        # Schedule model selected - message input
        if action.startswith("sched_model:"):
            parts = action.split(":")
            ws_id, model = parts[1], parts[2]
            ws = self._workspace_registry.get(ws_id)
            if not ws:
                await query.answer("Workspace not found")
                return

            pending = self._pending_workspace_input.get(user_id, {})
            pending["model"] = model
            self._pending_workspace_input[user_id] = pending

            hour = pending.get("hour", 9)

            await query.edit_message_text(
                f"<b>{ws.name}</b> - Schedule Registration\n\n"
                f"Time: <b>{hour:02d}:00</b>\n"
                f"Model: <b>{model}</b>\n\n"
                f"Enter scheduled message below:",
                parse_mode="HTML"
            )

            await query.message.reply_text(
                "Enter scheduled message:",
                reply_markup=ForceReply(selective=True, input_field_placeholder="e.g., Summarize today's tasks")
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

            self._pending_workspace_input[user_id] = {"action": "recommend"}

            await query.message.reply_text(
                "Enter purpose:",
                reply_markup=ForceReply(selective=True, input_field_placeholder="e.g., Investment analysis, React project")
            )
            await query.answer()
            return

        # Recommendation selection
        if action.startswith("recommend:"):
            idx = int(action[10:])
            pending = self._pending_workspace_input.get(user_id, {})
            recommendations = pending.get("recommendations", [])

            if idx >= len(recommendations):
                await query.answer("Invalid selection")
                return

            rec = recommendations[idx]
            ws = self._workspace_registry.add(
                user_id=user_id,
                path=rec["path"],
                name=rec["name"],
                description=rec["description"],
            )

            del self._pending_workspace_input[user_id]

            text = self._workspace_registry.get_status_text(user_id)
            keyboard = self._build_workspace_keyboard(user_id)

            await query.edit_message_text(
                f"<b>Workspace Registered!</b>\n\n"
                f"<b>{ws.name}</b>\n"
                f"<code>{ws.short_path}</code>\n"
                f"{ws.description}\n\n"
                f"────────────\n\n{text}",
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode="HTML"
            )
            await query.answer("Registered")
            return

        # Manual input selection
        if action == "manual":
            pending = self._pending_workspace_input.get(user_id, {})
            pending["action"] = "manual_path"
            self._pending_workspace_input[user_id] = pending

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
        pending = self._pending_workspace_input.get(user_id)

        if not pending:
            await update.message.reply_text("Input expired. Please try again.")
            return

        action = pending.get("action")

        # AI recommendation request
        if action == "recommend":
            await update.message.reply_text("AI is finding suitable workspaces...")

            allowed_paths = self._get_allowed_workspace_paths()
            recommendations = await self._workspace_registry.recommend_paths(
                purpose=message,
                user_id=user_id,
                allowed_paths=allowed_paths,
                max_recommendations=3,
            )

            if not recommendations:
                pending["action"] = "manual_path"
                pending["purpose"] = message
                self._pending_workspace_input[user_id] = pending

                await update.message.reply_text(
                    "Could not get AI recommendations.\n\n"
                    "Enter path manually:",
                    reply_markup=ForceReply(selective=True, input_field_placeholder="/path/to/workspace")
                )
                return

            pending["recommendations"] = recommendations
            pending["purpose"] = message
            self._pending_workspace_input[user_id] = pending

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
                InlineKeyboardButton("Manual Input", callback_data="ws:manual"),
                InlineKeyboardButton("Cancel", callback_data="ws:refresh"),
            ])

            rec_text = "\n\n".join([
                f"<b>{i+1}. {r['name']}</b>\n"
                f"<code>{r['path'].replace(str(Path.home()), '~')}</code>\n"
                f"{r['description']}\n"
                f"{r.get('reason', '')}"
                for i, r in enumerate(recommendations)
            ])

            await update.message.reply_text(
                f"<b>AI Recommendations</b>\n\n"
                f"Purpose: <i>{message}</i>\n\n"
                f"────────────\n\n"
                f"{rec_text}",
                reply_markup=InlineKeyboardMarkup(buttons),
                parse_mode="HTML"
            )
            return

        # Manual path input
        if action == "manual_path":
            path = message.strip()
            expanded_path = Path(path).expanduser().resolve()

            if not expanded_path.exists():
                await update.message.reply_text(
                    f"Path does not exist: <code>{path}</code>\n\n"
                    f"Enter again:",
                    reply_markup=ForceReply(selective=True, input_field_placeholder="/path/to/workspace"),
                    parse_mode="HTML"
                )
                return

            pending["path"] = str(expanded_path)
            pending["action"] = "manual_name"
            self._pending_workspace_input[user_id] = pending

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
            self._pending_workspace_input[user_id] = pending

            await update.message.reply_text(
                f"Name: <b>{name}</b>\n\n"
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

            del self._pending_workspace_input[user_id]

            text = self._workspace_registry.get_status_text(user_id)
            keyboard = self._build_workspace_keyboard(user_id)

            await update.message.reply_text(
                f"<b>Workspace Registered!</b>\n\n"
                f"<b>{ws.name}</b>\n"
                f"<code>{ws.short_path}</code>\n"
                f"{ws.description}\n\n"
                f"────────────\n\n{text}",
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode="HTML"
            )
            return

        # Workspace schedule message input
        if "ws_id" in pending and "model" in pending:
            ws_id = pending.get("ws_id")
            ws = self._workspace_registry.get(ws_id)
            if not ws:
                await update.message.reply_text("Workspace not found.")
                del self._pending_workspace_input[user_id]
                return

            if not self._schedule_manager:
                await update.message.reply_text("Schedule feature disabled.")
                del self._pending_workspace_input[user_id]
                return

            schedule = self._schedule_manager.add(
                user_id=user_id,
                chat_id=chat_id,
                name=ws.name,
                hour=pending["hour"],
                minute=pending.get("minute", 0),
                message=message,
                schedule_type="workspace",
                model=pending["model"],
                workspace_path=ws.path,
            )

            self._workspace_registry.mark_used(ws_id)

            del self._pending_workspace_input[user_id]

            keyboard = [[
                InlineKeyboardButton("Schedules", callback_data="sched:refresh"),
                InlineKeyboardButton("Workspaces", callback_data="ws:refresh"),
            ]]

            await update.message.reply_text(
                f"<b>Workspace Schedule Registered!</b>\n\n"
                f"<b>{ws.name}</b>\n"
                f"<code>{ws.short_path}</code>\n"
                f"Time: <b>{schedule.time_str}</b> (daily)\n"
                f"Model: <b>{pending['model']}</b>\n"
                f"Message: <i>{message[:50]}{'...' if len(message) > 50 else ''}</i>",
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode="HTML"
            )

            logger.info(f"Workspace schedule registered: {ws.name} @ {schedule.time_str}")
            return

        await update.message.reply_text("Unknown input state. Please try again.")
        if user_id in self._pending_workspace_input:
            del self._pending_workspace_input[user_id]

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
