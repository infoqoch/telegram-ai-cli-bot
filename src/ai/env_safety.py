"""Environment sanitization for subscription-authenticated CLI runs."""

from __future__ import annotations

from collections.abc import Mapping
import os


CLI_BLOCKED_ENV_KEYS = frozenset(
    {
        "ANTHROPIC_API_KEY",
        "ANTHROPIC_AUTH_TOKEN",
        "ANTHROPIC_OAUTH_TOKEN",
        "CLAUDE_CODE_PROVIDER_MANAGED_BY_HOST",
        "CLAUDECODE",
        "CODEX_API_KEY",
        "GEMINI_API_KEY",
        "GOOGLE_API_KEY",
        "GOOGLE_APPLICATION_CREDENTIALS",
        "OPENAI_API_KEY",
        "OPENAI_OAUTH_TOKEN",
        "OPENROUTER_API_KEY",
    }
)


def build_cli_subprocess_env(base_env: Mapping[str, str] | None = None) -> dict[str, str]:
    """Return an env for local subscription CLI runs without API-key routes."""
    env = dict(os.environ if base_env is None else base_env)
    for key in CLI_BLOCKED_ENV_KEYS:
        env.pop(key, None)
    return env
