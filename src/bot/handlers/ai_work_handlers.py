"""AI Work handlers - contextual AI assistance for each domain."""

from pathlib import Path

from telegram import ForceReply

from src.ai import get_default_model
from src.logging_config import logger
from .base import BaseHandler


# Core domain labels (only non-plugin domains)
CORE_DOMAIN_LABELS = {
    "scheduler": "Scheduler",
    "workspace": "Workspace",
    "tasks": "Tasks",
    "sessions": "Sessions",
}

# Core domains with static md context files
CORE_DOMAINS = {"scheduler", "workspace", "tasks", "sessions"}


class AiWorkHandlers(BaseHandler):
    """Contextual AI assistance - '✨ AI와 작업하기' feature."""

    def _get_domain_label(self, domain: str) -> str:
        """Get display label for a domain. Plugins provide their own, core uses constant."""
        if self.plugins:
            plugin = self.plugins.get_plugin_by_name(domain)
            if plugin:
                return plugin.display_name or plugin.name.capitalize()
        return CORE_DOMAIN_LABELS.get(domain, domain.capitalize())

    def _load_core_context(self, domain: str) -> str:
        """Load static AI context markdown for a core domain."""
        context_dir = Path(__file__).parent.parent / "ai_contexts"
        context_path = context_dir / f"{domain}.md"
        if context_path.exists():
            return context_path.read_text(encoding="utf-8")
        return ""

    async def _handle_aiwork_callback(self, query, chat_id: int, callback_data: str) -> None:
        """Handle aiwork:{domain} callback - show ForceReply prompt."""
        domain = callback_data.split(":", 1)[1] if ":" in callback_data else ""
        label = self._get_domain_label(domain)

        await query.message.reply_text(
            f"✨ <b>{label} - AI Work</b>\n\n"
            f"What would you like help with?\n"
            f"<i>Current {label} data will be sent to AI.</i>\n\n"
            f"<code>aiwork:{domain}</code>",
            parse_mode="HTML",
            reply_markup=ForceReply(
                selective=True,
                input_field_placeholder=f"Ask about {label}...",
            ),
        )

    async def _handle_aiwork_force_reply(
        self, update, chat_id: int, message: str, domain: str
    ) -> None:
        """Create a new session, gather domain context, and dispatch to AI."""
        user_id = str(chat_id)
        label = self._get_domain_label(domain)

        # Create a dedicated session for this AI work
        provider = self._get_selected_ai_provider(user_id)
        model = get_default_model(provider)
        session_name = f"✨ {label} AI"

        session_id = self.sessions.create_session(
            user_id=user_id,
            ai_provider=provider,
            model=model,
            name=session_name,
            first_message=f"(AI Work: {domain})",
        )

        await update.message.reply_text(
            f"✨ Switched to new session: <b>{session_name}</b>\n"
            f"<code>{session_id[:8]}</code>",
            parse_mode="HTML",
        )

        # Gather context and dispatch
        context_text = await self._get_static_context(domain)

        augmented_message = (
            f"[Context - {label}]\n"
            f"{context_text}\n\n"
            f"Based on the above context, answer the following request:\n"
            f"{message}"
        )

        await self._dispatch_to_ai(update, chat_id, user_id, augmented_message)

    async def _get_static_context(self, domain: str) -> str:
        """Load static context description for a domain."""
        plugin = self.plugins.get_plugin_by_name(domain) if self.plugins else None
        if plugin:
            return plugin._load_ai_context_file()
        return self._load_core_context(domain)
