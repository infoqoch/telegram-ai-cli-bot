"""Environment safety tests for local CLI subprocesses."""

from src.ai.env_safety import CLI_BLOCKED_ENV_KEYS, build_cli_subprocess_env


def test_build_cli_subprocess_env_removes_api_and_host_managed_keys():
    base_env = {
        "PATH": "/usr/bin",
        "OPENAI_API_KEY": "sk-test",
        "CODEX_API_KEY": "codex-test",
        "ANTHROPIC_API_KEY": "anthropic-test",
        "CLAUDE_CODE_PROVIDER_MANAGED_BY_HOST": "1",
        "GEMINI_API_KEY": "gemini-test",
        "GOOGLE_APPLICATION_CREDENTIALS": "/tmp/service-account.json",
    }

    env = build_cli_subprocess_env(base_env)

    assert env["PATH"] == "/usr/bin"
    for key in CLI_BLOCKED_ENV_KEYS:
        assert key not in env
