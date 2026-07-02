"""Registry for provider-specific CLI clients."""

import shlex
import shutil

from src.ai.catalog import DEFAULT_PROVIDER


class AIRegistry:
    """Simple provider → client registry."""

    def __init__(self, clients: dict[str, object]):
        self._clients = dict(clients)

    def get_client(self, provider: str):
        """Return provider client or raise KeyError."""
        return self._clients[provider]

    def get_default_client(self):
        """Return the default provider client, falling back to the first available one."""
        if DEFAULT_PROVIDER in self._clients:
            return self._clients[DEFAULT_PROVIDER]
        return next(iter(self._clients.values()))

    def supported_providers(self) -> list[str]:
        """Return provider keys available in this registry."""
        return list(self._clients.keys())


def build_default_registry(settings) -> AIRegistry:
    """Build the standard Claude/Codex/Gemini registry from settings."""
    clients = {}

    if _command_available(settings.ai_command):
        from src.claude.client import ClaudeClient
        clients["claude"] = ClaudeClient(
            command=settings.ai_command,
            system_prompt_file=settings.telegram_prompt_file,
            timeout=None,
        )

    if _command_available("codex"):
        from src.codex.client import CodexClient
        clients["codex"] = CodexClient(
            command="codex",
            system_prompt_file=settings.telegram_prompt_file,
            timeout=None,
        )

    if shutil.which("gemini"):
        from src.gemini.client import GeminiClient
        clients["gemini"] = GeminiClient(
            command="gemini",
            system_prompt_file=settings.telegram_prompt_file,
            timeout=None,
        )

    if shutil.which("agy"):
        from src.agy.client import AgyClient
        clients["agy"] = AgyClient(
            command="agy",
            system_prompt_file=settings.telegram_prompt_file,
            timeout=None,
        )

    if not clients:
        raise RuntimeError(
            "No supported AI CLI provider found. Install claude, codex, gemini, or agy, "
            "or update AI_COMMAND to a command available on PATH."
        )

    return AIRegistry(clients)


def _command_available(command: str) -> bool:
    """Return whether the executable portion of a configured shell command exists."""
    try:
        parts = shlex.split(command or "")
    except ValueError:
        return False
    return bool(parts and shutil.which(parts[0]))
