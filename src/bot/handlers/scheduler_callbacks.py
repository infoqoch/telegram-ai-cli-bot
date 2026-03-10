"""Scheduler-related callback handlers."""

from __future__ import annotations

from telegram import ForceReply, InlineKeyboardButton, InlineKeyboardMarkup

from src.ai import get_profile_label, is_supported_model, is_supported_provider
from src.constants import AVAILABLE_HOURS
from src.logging_config import logger
from src.schedule_utils import build_daily_cron, next_occurrence, normalize_schedule_type, normalize_trigger_type
from src.time_utils import format_local_datetime
from src.ui_emoji import (
    BUTTON_ADD_CHAT,
    BUTTON_ADD_PLUGIN,
    BUTTON_ADD_WORKSPACE,
    BUTTON_BACK,
    BUTTON_CANCEL,
    BUTTON_DELETE,
    BUTTON_REFRESH,
    BUTTON_SCHEDULE_LIST,
)
from ..formatters import escape_html
from .base import BaseHandler


class SchedulerCallbackHandlers(BaseHandler):
    """Scheduler callback handlers (sched: prefix)."""

    def _build_scheduler_screen_text(self, user_id: str) -> str:
        """Build the main scheduler screen body."""
        from src.scheduler_manager import scheduler_manager

        provider = self._get_selected_ai_provider(user_id)
        text = (
            f"<b>Scheduler</b>\n"
            f"Current AI: <b>{self._format_provider_display(provider)}</b>\n\n"
            f"{self._schedule_manager.get_status_text(user_id)}"
        )
        return text + scheduler_manager.get_system_jobs_text()

    def _build_scheduler_keyboard(self, user_id: str) -> list[list[InlineKeyboardButton]]:
        """Build the main scheduler list keyboard."""
        buttons: list[list[InlineKeyboardButton]] = []
        schedules = self._schedule_manager.list_by_user(user_id) if self._schedule_manager else []

        for schedule in schedules:
            status = "✅" if schedule.enabled else "⏸"
            buttons.append([
                InlineKeyboardButton(
                    f"{status} {schedule.type_emoji} {schedule.name[:18]}",
                    callback_data=f"sched:detail:{schedule.id}",
                )
            ])

        buttons.append([
            InlineKeyboardButton(BUTTON_ADD_CHAT, callback_data="sched:add:chat"),
            InlineKeyboardButton(BUTTON_ADD_WORKSPACE, callback_data="sched:add:workspace"),
            InlineKeyboardButton(BUTTON_ADD_PLUGIN, callback_data="sched:add:plugin"),
        ])
        buttons.append([
            InlineKeyboardButton(BUTTON_REFRESH, callback_data="sched:refresh"),
        ])
        return buttons

    async def _handle_schedule_force_reply(self, update, chat_id: int, message: str) -> None:
        """Handle scheduled message input ForceReply."""
        user_id = str(chat_id)
        pending = self._sched_pending.get(user_id)

        if not pending:
            await update.message.reply_text("Schedule input expired. Please try again.")
            return

        if not self._schedule_manager:
            self._sched_pending.pop(user_id, None)
            await update.message.reply_text("Schedule feature not initialized.")
            return

        schedule_type = normalize_schedule_type(pending.get("type"))
        trigger_type = normalize_trigger_type(pending.get("trigger_type"))
        hour = pending["hour"]
        minute = pending.get("minute", 0)
        workspace_path = pending.get("workspace_path") if schedule_type == "workspace" else None
        ai_provider = pending.get("ai_provider", self._get_selected_ai_provider(user_id))
        model = pending.get("model", "sonnet")
        name = pending.get("name") or self._default_schedule_name(schedule_type, message, workspace_path)
        run_at_local = pending.get("run_at_local")
        if trigger_type == "once" and not run_at_local:
            run_at_local = self._build_once_run_at(hour, minute)

        schedule = self._schedule_manager.add(
            user_id=user_id,
            chat_id=chat_id,
            name=name,
            hour=hour,
            minute=minute,
            message=message,
            schedule_type=schedule_type,
            trigger_type=trigger_type,
            ai_provider=ai_provider,
            model=model,
            workspace_path=workspace_path,
            run_at_local=run_at_local,
        )

        self._sched_pending.pop(user_id, None)

        await update.message.reply_text(
            self._build_schedule_registered_text(
                schedule,
                message=message,
                fallback_type=schedule_type,
                fallback_trigger=trigger_type,
                fallback_provider=ai_provider,
                fallback_workspace_path=workspace_path,
            ),
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton(BUTTON_SCHEDULE_LIST, callback_data="sched:refresh")]
            ]),
            parse_mode="HTML",
        )
        logger.info(f"Schedule registered: {name} ({schedule_type}, {trigger_type})")

    async def _handle_scheduler_callback(self, query, chat_id: int, callback_data: str) -> None:
        """Handle scheduler callbacks."""
        user_id = str(chat_id)
        action = callback_data[6:]

        if not self._schedule_manager:
            await query.answer("Schedule feature disabled")
            return

        if action == "refresh":
            await query.edit_message_text(
                self._build_scheduler_screen_text(user_id),
                reply_markup=InlineKeyboardMarkup(self._build_scheduler_keyboard(user_id)),
                parse_mode="HTML",
            )
            await query.answer("Refreshed")
            return

        if action.startswith("detail:"):
            schedule_id = action[7:]
            schedule = self._schedule_manager.get(schedule_id)
            if not schedule:
                await query.answer("Schedule not found")
                return

            toggle_label = "⏸ OFF" if schedule.enabled else "✅ ON"
            buttons = [
                [InlineKeyboardButton(toggle_label, callback_data=f"sched:toggle:{schedule_id}")],
                [InlineKeyboardButton(f"⏰ Change Time ({schedule.time_str})", callback_data=f"sched:chtime:{schedule_id}")],
                [InlineKeyboardButton(BUTTON_DELETE, callback_data=f"sched:delete:{schedule_id}")],
                [InlineKeyboardButton(BUTTON_BACK, callback_data="sched:refresh")],
            ]

            await query.edit_message_text(
                self._build_schedule_detail_text(schedule),
                reply_markup=InlineKeyboardMarkup(buttons),
                parse_mode="HTML",
            )
            await query.answer()
            return

        if action.startswith("toggle:"):
            schedule_id = action[7:]
            new_state = self._schedule_manager.toggle(schedule_id)
            if new_state is None:
                await query.answer("Schedule not found")
                return
            await query.answer("ON" if new_state else "OFF")
            await self._handle_scheduler_callback(query, chat_id, f"sched:detail:{schedule_id}")
            return

        if action.startswith("delete:"):
            schedule_id = action[7:]
            if self._schedule_manager.remove(schedule_id):
                await query.edit_message_text(
                    self._build_scheduler_screen_text(user_id),
                    reply_markup=InlineKeyboardMarkup(self._build_scheduler_keyboard(user_id)),
                    parse_mode="HTML",
                )
                await query.answer("Deleted")
            else:
                await query.answer("Delete failed")
            return

        if action.startswith("chtime:"):
            schedule_id = action[7:]
            schedule = self._schedule_manager.get(schedule_id)
            if not schedule:
                await query.answer("Schedule not found")
                return
            await query.edit_message_text(
                f"<b>Change Time</b>\n\n"
                f"{schedule.type_emoji} <b>{escape_html(schedule.name)}</b>\n"
                f"Current: <b>{escape_html(schedule.time_str)}</b>\n\n"
                f"Select new hour:",
                reply_markup=InlineKeyboardMarkup(
                    self._build_hour_keyboard(f"sched:chtime_hour:{schedule_id}:") + [[
                        InlineKeyboardButton(BUTTON_CANCEL, callback_data=f"sched:detail:{schedule_id}")
                    ]]
                ),
                parse_mode="HTML",
            )
            await query.answer()
            return

        if action.startswith("chtime_hour:"):
            schedule_id, hour = action[12:].split(":")
            await query.edit_message_text(
                f"<b>Change Time</b>\n\n"
                f"New hour: <b>{int(hour):02d}:00</b>\n\n"
                f"Select minute:",
                reply_markup=InlineKeyboardMarkup(
                    self._build_minute_keyboard("sched:chtime_min", schedule_id, int(hour)) + [[
                        InlineKeyboardButton(BUTTON_CANCEL, callback_data=f"sched:detail:{schedule_id}")
                    ]]
                ),
                parse_mode="HTML",
            )
            await query.answer()
            return

        if action.startswith("chtime_min:"):
            schedule_id, hour, minute = action[11:].split(":")
            result = self._schedule_manager.update_time(schedule_id, int(hour), int(minute))
            if result:
                await query.answer(f"Changed to {int(hour):02d}:{int(minute):02d}")
            else:
                await query.answer("Update failed")
            await query.edit_message_text(
                self._build_scheduler_screen_text(user_id),
                reply_markup=InlineKeyboardMarkup(self._build_scheduler_keyboard(user_id)),
                parse_mode="HTML",
            )
            return

        if action in ("add:ai", "add:claude", "add:chat"):
            provider = self._get_selected_ai_provider(user_id)
            self._sched_pending[user_id] = {
                "type": "chat",
                "ai_provider": provider,
            }
            await query.edit_message_text(
                f"<b>Add Chat Schedule</b>\n\n"
                f"Current AI: <b>{self._format_provider_display(provider)}</b>\n\n"
                f"Select hour:",
                reply_markup=InlineKeyboardMarkup(
                    self._build_hour_keyboard("sched:time:chat:_:") + [[
                        InlineKeyboardButton(BUTTON_CANCEL, callback_data="sched:refresh")
                    ]]
                ),
                parse_mode="HTML",
            )
            await query.answer()
            return

        if action == "add:workspace":
            if not self._workspace_registry:
                await query.answer("Workspace feature not initialized.")
                return

            workspaces = self._workspace_registry.list_by_user(user_id)
            if not workspaces:
                await query.edit_message_text(
                    "<b>No workspaces registered.</b>\n\nRegister one first at /workspace.",
                    parse_mode="HTML",
                )
                await query.answer()
                return

            workspace_map = {}
            buttons: list[list[InlineKeyboardButton]] = []
            for idx, workspace in enumerate(workspaces):
                workspace_map[idx] = {"path": workspace.path, "name": workspace.name}
                buttons.append([
                    InlineKeyboardButton(workspace.name, callback_data=f"sched:wspath:{idx}")
                ])

            self._sched_pending[user_id] = {
                "workspaces": workspace_map,
                "ai_provider": self._get_selected_ai_provider(user_id),
            }
            buttons.append([InlineKeyboardButton(BUTTON_CANCEL, callback_data="sched:refresh")])

            await query.edit_message_text(
                "<b>Add Workspace Schedule</b>\n\nSelect workspace:",
                reply_markup=InlineKeyboardMarkup(buttons),
                parse_mode="HTML",
            )
            await query.answer()
            return

        if action == "add:plugin":
            if not self.plugins or not self.plugins.plugins:
                await query.edit_message_text("<b>No plugins loaded.</b>", parse_mode="HTML")
                await query.answer()
                return

            plugin_map: dict[int, dict] = {}
            buttons: list[list[InlineKeyboardButton]] = []
            idx = 0
            for plugin in self.plugins.plugins:
                actions = plugin.get_scheduled_actions()
                if not actions:
                    continue
                plugin_map[idx] = {"name": plugin.name, "actions": actions}
                buttons.append([
                    InlineKeyboardButton(
                        f"🔌 {plugin.name} ({len(actions)} actions)",
                        callback_data=f"sched:plugin:{idx}",
                    )
                ])
                idx += 1

            if not buttons:
                await query.edit_message_text(
                    "<b>No schedulable plugins.</b>\n\nImplement <code>get_scheduled_actions()</code> in your plugin.",
                    parse_mode="HTML",
                )
                await query.answer()
                return

            self._sched_pending[user_id] = {"plugin_map": plugin_map}
            buttons.append([InlineKeyboardButton(BUTTON_CANCEL, callback_data="sched:refresh")])
            await query.edit_message_text(
                "<b>Add Plugin Schedule</b>\n\nSelect plugin:",
                reply_markup=InlineKeyboardMarkup(buttons),
                parse_mode="HTML",
            )
            await query.answer()
            return

        if action.startswith("plugin:") and not action.startswith("pluginaction:"):
            plugin_idx = int(action[7:])
            pending = self._sched_pending.get(user_id, {})
            plugin_info = pending.get("plugin_map", {}).get(plugin_idx)
            if not plugin_info:
                await query.answer("Invalid plugin")
                return

            pending["selected_plugin"] = plugin_info["name"]
            self._sched_pending[user_id] = pending
            buttons = [
                [InlineKeyboardButton(item.description, callback_data=f"sched:pluginaction:{idx}")]
                for idx, item in enumerate(plugin_info["actions"])
            ]
            buttons.append([InlineKeyboardButton(BUTTON_CANCEL, callback_data="sched:refresh")])
            await query.edit_message_text(
                f"<b>🔌 {plugin_info['name']}</b>\n\nSelect action:",
                reply_markup=InlineKeyboardMarkup(buttons),
                parse_mode="HTML",
            )
            await query.answer()
            return

        if action.startswith("pluginaction:"):
            action_idx = int(action[13:])
            pending = self._sched_pending.get(user_id, {})
            plugin_name = pending.get("selected_plugin")
            actions = []
            for info in pending.get("plugin_map", {}).values():
                if info["name"] == plugin_name:
                    actions = info["actions"]
                    break
            if action_idx >= len(actions):
                await query.answer("Invalid action")
                return

            selected_action = actions[action_idx]
            pending.update({
                "type": "plugin",
                "plugin_name": plugin_name,
                "action_name": selected_action.name,
                "name": f"{plugin_name}:{selected_action.description}",
            })
            self._sched_pending[user_id] = pending
            await query.edit_message_text(
                f"<b>Plugin Schedule</b>\n\n"
                f"🔌 <b>{escape_html(plugin_name)}</b> - {escape_html(selected_action.description)}\n\n"
                f"Select hour:",
                reply_markup=InlineKeyboardMarkup(
                    self._build_hour_keyboard("sched:time:plugin:_:") + [[
                        InlineKeyboardButton(BUTTON_CANCEL, callback_data="sched:refresh")
                    ]]
                ),
                parse_mode="HTML",
            )
            await query.answer()
            return

        if action.startswith("wspath:"):
            ws_idx = int(action[7:])
            pending = self._sched_pending.get(user_id, {})
            ws_info = pending.get("workspaces", {}).get(ws_idx)
            if not ws_info:
                await query.answer("Invalid workspace")
                return

            pending.update({
                "type": "workspace",
                "workspace_path": ws_info["path"],
                "name": ws_info["name"],
            })
            self._sched_pending[user_id] = pending
            await query.edit_message_text(
                f"<b>Add Workspace Schedule</b>\n\n"
                f"Workspace: <b>{escape_html(ws_info['name'])}</b>\n"
                f"<code>{escape_html(ws_info['path'])}</code>\n\n"
                f"Select hour:",
                reply_markup=InlineKeyboardMarkup(
                    self._build_hour_keyboard(f"sched:time:workspace:{ws_idx}:") + [[
                        InlineKeyboardButton(BUTTON_CANCEL, callback_data="sched:refresh")
                    ]]
                ),
                parse_mode="HTML",
            )
            await query.answer()
            return

        if action.startswith("time:"):
            parts = action[5:].split(":")
            if len(parts) != 3:
                await query.answer("Invalid request")
                return

            schedule_type, path_idx, hour = parts[0], parts[1], int(parts[2])
            normalized_type = normalize_schedule_type(schedule_type)
            pending = self._sched_pending.get(user_id, {})
            pending["type"] = normalized_type
            pending["hour"] = hour
            pending.setdefault("ai_provider", self._get_selected_ai_provider(user_id))

            if normalized_type == "workspace" and path_idx != "_":
                ws_info = pending.get("workspaces", {}).get(int(path_idx))
                if ws_info:
                    pending["workspace_path"] = ws_info["path"]
                    pending["name"] = ws_info["name"]

            self._sched_pending[user_id] = pending
            await query.edit_message_text(
                f"<b>Add {self._schedule_type_title(normalized_type)} Schedule</b>\n\n"
                f"Hour: <b>{hour:02d}:00</b>{self._format_workspace_path_line(pending)}\n\n"
                f"Select minute:",
                reply_markup=InlineKeyboardMarkup(
                    self._build_minute_keyboard("sched:minute") + [[
                        InlineKeyboardButton(BUTTON_CANCEL, callback_data="sched:refresh")
                    ]]
                ),
                parse_mode="HTML",
            )
            await query.answer()
            return

        if action.startswith("minute:"):
            minute = int(action[7:])
            pending = self._sched_pending.get(user_id, {})
            pending["minute"] = minute
            self._sched_pending[user_id] = pending

            hour = pending.get("hour", 9)
            await query.edit_message_text(
                f"<b>{self._schedule_type_title(normalize_schedule_type(pending.get('type')))} Schedule</b>\n\n"
                f"Time: <b>{hour:02d}:{minute:02d}</b>{self._format_workspace_path_line(pending)}\n\n"
                f"Choose schedule mode:",
                reply_markup=InlineKeyboardMarkup([
                    [
                        InlineKeyboardButton("Daily", callback_data="sched:trigger:cron"),
                        InlineKeyboardButton("One-time", callback_data="sched:trigger:once"),
                    ],
                    [InlineKeyboardButton(BUTTON_CANCEL, callback_data="sched:refresh")],
                ]),
                parse_mode="HTML",
            )
            await query.answer()
            return

        if action.startswith("trigger:"):
            trigger_type = normalize_trigger_type(action[8:])
            pending = self._sched_pending.get(user_id, {})
            if not pending:
                await query.answer("Schedule flow expired")
                return
            pending["trigger_type"] = trigger_type
            if trigger_type == "once":
                pending["run_at_local"] = self._build_once_run_at(pending.get("hour", 0), pending.get("minute", 0))
            else:
                pending["run_at_local"] = None
            self._sched_pending[user_id] = pending

            schedule_type = normalize_schedule_type(pending.get("type"))
            if schedule_type == "plugin":
                await query.edit_message_text(
                    self._register_plugin_schedule(user_id, chat_id, pending),
                    reply_markup=InlineKeyboardMarkup([
                        [InlineKeyboardButton(BUTTON_SCHEDULE_LIST, callback_data="sched:refresh")]
                    ]),
                    parse_mode="HTML",
                )
                self._sched_pending.pop(user_id, None)
                await query.answer("Registered")
                return

            provider = pending.get("ai_provider", self._get_selected_ai_provider(user_id))
            await query.edit_message_text(
                f"<b>Add {self._schedule_type_title(schedule_type)} Schedule</b>\n\n"
                f"Time: <b>{pending.get('hour', 0):02d}:{pending.get('minute', 0):02d}</b>{self._format_workspace_path_line(pending)}\n"
                f"Schedule: <b>{'One-time' if trigger_type == 'once' else 'Daily'}</b>\n"
                f"Current AI: <b>{self._format_provider_display(provider)}</b>\n\n"
                f"Select AI:",
                reply_markup=InlineKeyboardMarkup(
                    self._build_provider_choice_keyboard(
                        provider,
                        "sched:provider:",
                        back_callback=f"sched:minute:{pending.get('minute', 0)}",
                    )
                ),
                parse_mode="HTML",
            )
            await query.answer()
            return

        if action.startswith("provider:"):
            provider = action[9:]
            pending = self._sched_pending.get(user_id, {})
            if not pending:
                await query.answer("Schedule flow expired")
                return
            if not is_supported_provider(provider):
                await query.answer("Unsupported AI")
                return

            pending["ai_provider"] = provider
            self._sched_pending[user_id] = pending
            schedule_type = normalize_schedule_type(pending.get("type"))
            trigger_type = normalize_trigger_type(pending.get("trigger_type"))

            await query.edit_message_text(
                f"<b>Add {self._schedule_type_title(schedule_type)} Schedule</b>\n\n"
                f"Time: <b>{pending.get('hour', 0):02d}:{pending.get('minute', 0):02d}</b>{self._format_workspace_path_line(pending)}\n"
                f"Schedule: <b>{'One-time' if trigger_type == 'once' else 'Daily'}</b>\n"
                f"AI: <b>{self._format_provider_display(provider)}</b>\n\n"
                f"Select model:",
                reply_markup=InlineKeyboardMarkup([
                    self._build_model_buttons(provider, "sched:model:"),
                    [InlineKeyboardButton(BUTTON_BACK, callback_data=f"sched:trigger:{trigger_type}")],
                ]),
                parse_mode="HTML",
            )
            await query.answer()
            return

        if action.startswith("model:"):
            model = action[6:]
            pending = self._sched_pending.get(user_id, {})
            provider = pending.get("ai_provider", self._get_selected_ai_provider(user_id))
            if not is_supported_model(provider, model):
                await query.edit_message_text("❌ Unsupported model for the selected AI.")
                return

            pending["model"] = model
            self._sched_pending[user_id] = pending
            schedule_type = normalize_schedule_type(pending.get("type"))
            trigger_type = normalize_trigger_type(pending.get("trigger_type"))
            hour = pending.get("hour", 0)
            minute = pending.get("minute", 0)
            await query.edit_message_text(
                f"<b>Add {self._schedule_type_title(schedule_type)} Schedule</b>\n\n"
                f"Time: <b>{hour:02d}:{minute:02d}</b>\n"
                f"Schedule: <b>{'One-time' if trigger_type == 'once' else 'Daily'}</b>\n"
                f"AI: <b>{self._format_provider_display(provider)}</b>\n"
                f"Model: <b>{get_profile_label(provider, model)}</b> (<code>{model}</code>){self._format_workspace_path_line(pending)}\n\n"
                f"Enter scheduled message below:",
                parse_mode="HTML",
            )
            await query.message.reply_text(
                "Enter scheduled message (schedule_input):",
                reply_markup=ForceReply(
                    selective=True,
                    input_field_placeholder="e.g., Summarize today's tasks",
                ),
            )
            await query.answer()
            return

        await query.answer("Unknown action")

    @staticmethod
    def _build_hour_keyboard(prefix: str) -> list[list[InlineKeyboardButton]]:
        """Build a 24-hour picker."""
        rows: list[list[InlineKeyboardButton]] = []
        row: list[InlineKeyboardButton] = []
        for hour in AVAILABLE_HOURS:
            row.append(InlineKeyboardButton(f"{hour:02d}h", callback_data=f"{prefix}{hour}"))
            if len(row) == 4:
                rows.append(row)
                row = []
        if row:
            rows.append(row)
        return rows

    @staticmethod
    def _build_minute_keyboard(prefix: str, *parts: int | str) -> list[list[InlineKeyboardButton]]:
        """Build a 5-minute picker."""
        rows: list[list[InlineKeyboardButton]] = []
        row: list[InlineKeyboardButton] = []
        path = ":".join(str(part) for part in parts if part != "")
        base = f"{prefix}:{path}" if path else prefix
        for minute in range(0, 60, 5):
            row.append(InlineKeyboardButton(f":{minute:02d}", callback_data=f"{base}:{minute}"))
            if len(row) == 4:
                rows.append(row)
                row = []
        if row:
            rows.append(row)
        return rows

    def _register_plugin_schedule(self, user_id: str, chat_id: int, pending: dict) -> str:
        """Persist a plugin schedule and return the confirmation card."""
        ai_provider = pending.get("ai_provider", self._get_selected_ai_provider(user_id))
        trigger_type = normalize_trigger_type(pending.get("trigger_type"))
        run_at_local = pending.get("run_at_local")
        schedule = self._schedule_manager.add(
            user_id=user_id,
            chat_id=chat_id,
            name=pending.get("name", "Plugin Schedule"),
            hour=pending.get("hour", 0),
            minute=pending.get("minute", 0),
            message="",
            schedule_type="plugin",
            trigger_type=trigger_type,
            ai_provider=ai_provider,
            model="sonnet",
            plugin_name=pending.get("plugin_name"),
            action_name=pending.get("action_name"),
            run_at_local=run_at_local,
        )
        return self._build_plugin_registered_text(schedule)

    @staticmethod
    def _default_schedule_name(schedule_type: str, message: str, workspace_path: str | None) -> str:
        """Choose a default name when the UI did not supply one."""
        if schedule_type == "workspace" and workspace_path:
            return workspace_path.rstrip("/").split("/")[-1] or "Workspace Schedule"
        trimmed = message.strip()
        return trimmed[:20] + ("..." if len(trimmed) > 20 else "") if trimmed else "Schedule"

    @staticmethod
    def _build_once_run_at(hour: int, minute: int) -> str:
        """Return the next local occurrence for one HH:MM selection."""
        return next_occurrence(hour, minute).isoformat()

    @staticmethod
    def _schedule_type_title(schedule_type: str) -> str:
        """Return a display title for the schedule type."""
        if schedule_type == "workspace":
            return "Workspace"
        if schedule_type == "plugin":
            return "Plugin"
        return "Chat"

    @staticmethod
    def _format_workspace_path_line(data) -> str:
        """Return an optional workspace path line suffix."""
        workspace_path = getattr(data, "workspace_path", None) if not isinstance(data, dict) else data.get("workspace_path")
        if not workspace_path:
            return ""
        return f"\nPath: <code>{escape_html(workspace_path)}</code>"

    def _build_schedule_detail_text(self, schedule) -> str:
        """Build the detail card for one schedule."""
        schedule_type = self._resolve_schedule_type(schedule)
        provider = self._resolve_provider(schedule)
        time_str = self._string_attr(schedule, "time_str")
        next_run_text = self._string_attr(schedule, "next_run_text", fallback=time_str or "No upcoming run")
        lines = [
            f"{self._string_attr(schedule, 'type_emoji', fallback='💬')} <b>{escape_html(schedule.name)}</b> ({schedule_type})",
            "",
            f"Status: <b>{'ON' if schedule.enabled else 'OFF'}</b>",
            f"Time: <b>{escape_html(time_str)}</b>",
            f"Schedule: <b>{escape_html(self._resolve_schedule_summary(schedule))}</b>",
            f"Next run: <b>{escape_html(next_run_text)}</b>",
        ]

        if schedule_type == "plugin":
            lines.append(f"Plugin: <b>{escape_html(getattr(schedule, 'plugin_name', '') or '-')}</b>")
            lines.append(f"Action: <b>{escape_html(getattr(schedule, 'action_name', '') or '-')}</b>")
        else:
            model = self._string_attr(schedule, "model", fallback="sonnet")
            lines.append(f"AI: <b>{self._format_provider_display(provider)}</b>")
            lines.append(f"Model: <b>{get_profile_label(provider, model)}</b> (<code>{model}</code>)")
            if schedule_type == "workspace" and getattr(schedule, "workspace_path", None):
                lines.append(f"Path: <code>{escape_html(schedule.workspace_path)}</code>")
            lines.append(
                f"Message: <i>{escape_html((getattr(schedule, 'message', '') or '')[:80])}"
                f"{'...' if len(getattr(schedule, 'message', '') or '') > 80 else ''}</i>"
            )

        lines.append(f"Runs: {getattr(schedule, 'run_count', 0)}")
        return "\n".join(lines)

    def _build_schedule_registered_text(
        self,
        schedule,
        *,
        message: str,
        fallback_type: str,
        fallback_trigger: str,
        fallback_provider: str,
        fallback_workspace_path: str | None = None,
    ) -> str:
        """Build the success card for chat/workspace schedules."""
        schedule_type = self._resolve_schedule_type(schedule, fallback=fallback_type)
        provider = self._resolve_provider(schedule, fallback=fallback_provider)
        workspace_path = getattr(schedule, "workspace_path", None) or fallback_workspace_path
        time_str = self._string_attr(schedule, "time_str")
        next_run_text = self._string_attr(schedule, "next_run_text", fallback=time_str)
        lines = [
            "<b>Schedule Registered!</b>",
            "",
            f"{self._string_attr(schedule, 'type_emoji', fallback='💬')} <b>{escape_html(schedule.name)}</b> ({schedule_type})",
            f"Time: <b>{escape_html(time_str)}</b>",
            f"Schedule: <b>{escape_html(self._resolve_schedule_summary(schedule, fallback_trigger=fallback_trigger))}</b>",
            f"Next run: <b>{escape_html(next_run_text)}</b>",
            f"AI: <b>{self._format_provider_display(provider)}</b>",
        ]
        if workspace_path:
            lines.append(f"Path: <code>{escape_html(workspace_path)}</code>")
        lines.append(
            f"Message: <i>{escape_html(message[:50])}{'...' if len(message) > 50 else ''}</i>"
        )
        return "\n".join(lines)

    def _build_plugin_registered_text(self, schedule) -> str:
        """Build the success card for plugin schedules."""
        time_str = self._string_attr(schedule, "time_str")
        next_run_text = self._string_attr(schedule, "next_run_text", fallback=time_str)
        return "\n".join([
            "<b>Plugin Schedule Registered!</b>",
            "",
            f"🔌 <b>{escape_html(schedule.name)}</b> (plugin)",
            f"Time: <b>{escape_html(time_str)}</b>",
            f"Schedule: <b>{escape_html(self._resolve_schedule_summary(schedule))}</b>",
            f"Next run: <b>{escape_html(next_run_text)}</b>",
            f"Plugin: <b>{escape_html(getattr(schedule, 'plugin_name', '') or '-')}</b>",
            f"Action: <b>{escape_html(getattr(schedule, 'action_name', '') or '-')}</b>",
        ])

    def _resolve_schedule_summary(self, schedule, *, fallback_trigger: str | None = None) -> str:
        """Resolve one UI-friendly summary for daily/once schedules."""
        trigger_type = normalize_trigger_type(getattr(schedule, "trigger_type", None) or fallback_trigger)
        hour = getattr(schedule, "hour", 0)
        minute = getattr(schedule, "minute", 0)
        if trigger_type == "once":
            run_at_local = getattr(schedule, "run_at_local", None)
            if run_at_local:
                return f"Once at {format_local_datetime(run_at_local)}"
            return f"Once at {hour:02d}:{minute:02d}"

        cron_expr = getattr(schedule, "cron_expr", None)
        if cron_expr == build_daily_cron(hour, minute) or cron_expr is None:
            return f"Daily at {hour:02d}:{minute:02d}"
        trigger_summary = getattr(schedule, "trigger_summary", None)
        return trigger_summary if isinstance(trigger_summary, str) and trigger_summary else "Daily"

    @staticmethod
    def _resolve_schedule_type(schedule, *, fallback: str | None = None) -> str:
        """Resolve schedule type while tolerating MagicMock attributes in tests."""
        schedule_type = getattr(schedule, "schedule_type", None)
        if not isinstance(schedule_type, str) or not schedule_type:
            schedule_type = getattr(schedule, "type", None)
        if not isinstance(schedule_type, str) or not schedule_type:
            schedule_type = fallback or "chat"
        return normalize_schedule_type(schedule_type)

    @staticmethod
    def _resolve_provider(schedule, *, fallback: str = "claude") -> str:
        """Resolve provider while tolerating MagicMock attributes in tests."""
        provider = getattr(schedule, "ai_provider", None)
        if not isinstance(provider, str) or provider not in {"claude", "codex"}:
            return fallback
        return provider

    @staticmethod
    def _string_attr(schedule, attr: str, *, fallback: str = "") -> str:
        """Return a string attribute or a safe fallback."""
        value = getattr(schedule, attr, None)
        return value if isinstance(value, str) and value else fallback
