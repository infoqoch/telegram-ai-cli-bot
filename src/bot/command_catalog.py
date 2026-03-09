"""Shared slash-command metadata for Telegram sync and the in-chat launcher."""

from __future__ import annotations

from dataclasses import dataclass

from telegram import BotCommand


@dataclass(frozen=True)
class CommandSpec:
    """One user-visible slash command."""

    name: str
    description: str
    menu_label: str | None = None
    callback_data: str | None = None
    admin_only: bool = False
    requires_plugins: bool = False


BASE_COMMAND_SPECS: tuple[CommandSpec, ...] = (
    CommandSpec("menu", "Open the main service menu", menu_label="📋 Menu", callback_data="menu:open"),
    CommandSpec("help", "Show help and guides", menu_label="❓ Help", callback_data="menu:help"),
    CommandSpec("session", "Show the current session"),
    CommandSpec("new", "Start a new AI session", menu_label="🆕 New Session", callback_data="menu:new"),
    CommandSpec("sl", "Show the session list", menu_label="💬 Sessions", callback_data="menu:sessions"),
    CommandSpec("workspace", "Open the workspace hub", menu_label="📂 Workspace", callback_data="menu:workspace"),
    CommandSpec("scheduler", "Open the scheduler hub", menu_label="📅 Scheduler", callback_data="menu:scheduler"),
    CommandSpec("tasks", "Show active tasks", menu_label="📌 Tasks", callback_data="menu:tasks"),
    CommandSpec("select_ai", "Choose Claude or Codex", menu_label="🔀 AI", callback_data="menu:ai"),
    CommandSpec("plugins", "Browse the plugin catalog", menu_label="🔌 Plugins", callback_data="menu:plugins", requires_plugins=True),
    CommandSpec("reload", "Reload plugins", admin_only=True),
)

PUBLISHED_BOT_COMMAND_NAMES: tuple[str, ...] = (
    "menu",
    "session",
    "new",
    "sl",
    "tasks",
)


def iter_command_specs(*, has_plugins: bool, is_admin: bool = False) -> list[CommandSpec]:
    """Return filtered command metadata for the current runtime."""
    specs: list[CommandSpec] = []
    for spec in BASE_COMMAND_SPECS:
        if spec.requires_plugins and not has_plugins:
            continue
        if spec.admin_only and not is_admin:
            continue
        specs.append(spec)
    return specs


def build_bot_commands(*, has_plugins: bool, is_admin: bool = False) -> list[BotCommand]:
    """Convert command specs into Telegram BotCommand payloads."""
    specs_by_name = {
        spec.name: spec
        for spec in iter_command_specs(has_plugins=has_plugins, is_admin=is_admin)
    }
    return [
        BotCommand(specs_by_name[name].name, specs_by_name[name].description)
        for name in PUBLISHED_BOT_COMMAND_NAMES
        if name in specs_by_name
    ]


def build_menu_specs(*, has_plugins: bool, is_admin: bool = False) -> list[CommandSpec]:
    """Return command specs that should appear in the in-chat launcher."""
    return [
        spec
        for spec in iter_command_specs(has_plugins=has_plugins, is_admin=is_admin)
        if spec.menu_label and spec.callback_data
    ]
