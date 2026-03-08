"""Registry for provider-specific CLI clients."""

from src.ai.catalog import DEFAULT_PROVIDER


class AIRegistry:
    """Simple provider → client registry."""

    def __init__(self, clients: dict[str, object]):
        self._clients = dict(clients)

    def get_client(self, provider: str):
        """Return provider client or raise KeyError."""
        return self._clients[provider]

    def get_default_client(self):
        """Return default provider client."""
        return self._clients[DEFAULT_PROVIDER]

    def supported_providers(self) -> list[str]:
        """Return provider keys available in this registry."""
        return list(self._clients.keys())


def build_default_registry(settings) -> AIRegistry:
    """Build the standard Claude/Codex registry from settings."""
    from src.claude.client import ClaudeClient
    from src.codex.client import CodexClient

    return AIRegistry(
        {
            "claude": ClaudeClient(
                command=settings.ai_command,
                system_prompt_file=settings.telegram_prompt_file,
                timeout=None,
            ),
            "codex": CodexClient(
                command="codex",
                system_prompt_file=settings.telegram_prompt_file,
                timeout=None,
            ),
        }
    )
