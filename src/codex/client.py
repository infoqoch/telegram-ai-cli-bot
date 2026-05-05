"""Async Codex CLI client."""

import asyncio
from contextlib import suppress
import json
import re
import tempfile
from pathlib import Path
from typing import Optional

from src.ai.base_client import BaseCLIClient, PromptConfig
from src.ai.catalog import get_profile
from src.ai.client_types import ChatError, ChatResponse
from src.logging_config import logger
from src.runtime_paths import get_data_dir

_TOML_BARE_KEY_RE = re.compile(r"^[A-Za-z0-9_-]+$")
_CODEX_PROMPT_CONFIG_KEY = "model_instructions_file"


class CodexClient(BaseCLIClient):
    """Async wrapper for Codex CLI."""

    @staticmethod
    def _prompt_content(prompts: PromptConfig) -> Optional[str]:
        return prompts.system or prompts.append

    @classmethod
    def _write_prompt_file(cls, content: str) -> str:
        prompt_dir = get_data_dir() / "codex-prompts"
        prompt_dir.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            mode="w",
            suffix=".md",
            prefix="instructions_",
            delete=False,
            dir=prompt_dir,
            encoding="utf-8",
        ) as handle:
            handle.write(content)
            return handle.name

    @staticmethod
    def _remove_prompt_file(path: Optional[str]) -> None:
        if not path:
            return
        with suppress(OSError):
            Path(path).unlink()

    def _prepare_prompt_file(self, session_id: Optional[str], workspace_path: Optional[str]) -> Optional[str]:
        if session_id:
            return None

        content = self._prompt_content(self._resolve_prompts(workspace_path))
        if not content:
            return None
        return self._write_prompt_file(content)

    def _inject_prompt_args(
        self,
        cmd: list[str],
        prompts: PromptConfig,
        prompt_file_path: Optional[str] = None,
    ) -> None:
        """Inject prompt arguments using Codex CLI flags."""
        content = self._prompt_content(prompts)
        if not content:
            return
        if prompt_file_path:
            encoded = self._format_toml_value(prompt_file_path)
            cmd.extend(["-c", f"{_CODEX_PROMPT_CONFIG_KEY}={encoded}"])
            return
        cmd.extend(["-c", f"instructions={self._format_toml_value(content)}"])

    @classmethod
    def _load_project_mcp_servers(cls) -> dict[str, dict]:
        """Build project-local MCP server definitions dynamically."""
        import sys
        bridge_script = cls._project_root() / "mcp_servers" / "plugin_bridge_server.py"
        if not bridge_script.exists():
            return {}
        return {
            "bot-plugins": {
                "command": sys.executable,
                "args": [str(bridge_script)],
            }
        }

    @classmethod
    def _format_toml_key(cls, key: str) -> str:
        """Format one TOML bare/quoted key for `codex -c` overrides."""
        if _TOML_BARE_KEY_RE.match(key):
            return key
        return json.dumps(key)

    @classmethod
    def _format_toml_value(cls, value: object) -> str:
        """Serialize one Python value into a TOML-compatible inline literal."""
        if isinstance(value, str):
            return json.dumps(value)
        if isinstance(value, bool):
            return "true" if value else "false"
        if isinstance(value, int | float):
            return repr(value)
        if isinstance(value, list):
            return "[" + ", ".join(cls._format_toml_value(item) for item in value) + "]"
        if isinstance(value, dict):
            parts = [
                f"{cls._format_toml_key(str(key))} = {cls._format_toml_value(item)}"
                for key, item in value.items()
                if item is not None
            ]
            return "{ " + ", ".join(parts) + " }"
        raise TypeError(f"Unsupported TOML override value: {type(value).__name__}")

    @classmethod
    def _inject_project_mcp_args(cls, cmd: list[str]) -> None:
        """Expose project-local MCP servers to Codex without mutating ~/.codex/config.toml."""
        servers = cls._load_project_mcp_servers()
        if not servers:
            return

        for server_name, config in servers.items():
            prefix = f"mcp_servers.{server_name}"
            for field, value in config.items():
                if value is None:
                    continue
                try:
                    encoded = cls._format_toml_value(value)
                except TypeError as exc:
                    logger.warning(f"Codex MCP override skipped: {prefix}.{field} ({exc})")
                    continue
                cmd.extend(["-c", f"{prefix}.{field}={encoded}"])

        logger.trace(f"Codex MCP overrides added: {', '.join(sorted(servers))}")

    async def chat(
        self,
        message: str,
        session_id: Optional[str] = None,
        model: Optional[str] = None,
        workspace_path: Optional[str] = None,
    ) -> ChatResponse:
        """Send one message via codex exec/exec resume."""
        profile = get_profile("codex", model)
        prompt_file_path: Optional[str] = None

        try:
            prompt_file_path = self._prepare_prompt_file(session_id, workspace_path)
            cmd = self._build_command(
                message=message,
                session_id=session_id,
                model=profile.key,
                workspace_path=workspace_path,
                prompt_file_path=prompt_file_path,
            )
            output, error, returncode = await self._run_command(
                cmd,
                timeout=self.timeout,
                cwd=workspace_path,
            )
        except asyncio.TimeoutError:
            logger.warning("Codex CLI timed out")
            return ChatResponse("", ChatError.TIMEOUT, session_id)
        except Exception as e:
            logger.exception(f"Codex CLI exception: {e}")
            return ChatResponse(str(e), ChatError.CLI_ERROR, session_id)
        finally:
            self._remove_prompt_file(prompt_file_path)

        if returncode != 0 and not output:
            return ChatResponse(error or "Codex CLI failed", ChatError.CLI_ERROR, session_id)

        return self._parse_jsonl(output, error, fallback_session_id=session_id)

    def _build_command(
        self,
        message: str,
        session_id: Optional[str],
        model: str,
        workspace_path: Optional[str],
        prompt_file_path: Optional[str] = None,
    ) -> list[str]:
        """Build codex exec command."""
        profile = get_profile("codex", model)
        common = list(self.command_parts)

        if session_id:
            common.extend(["exec", "resume", "--json"])
        else:
            common.extend(["exec", "--json"])

        common.extend(["-m", profile.provider_model])
        if profile.reasoning_effort:
            common.extend(["-c", f'model_reasoning_effort="{profile.reasoning_effort}"'])

        common.append("--dangerously-bypass-approvals-and-sandbox")
        common.append("--skip-git-repo-check")

        prompts = PromptConfig() if session_id else self._resolve_prompts(workspace_path)
        self._inject_prompt_args(common, prompts, prompt_file_path)
        self._inject_project_mcp_args(common)

        if session_id:
            common.append(session_id)
        common.append(message)

        logger.debug(
            f"Codex command built: resume={bool(session_id)} model={profile.provider_model} effort={profile.reasoning_effort} cwd={workspace_path or '(default)'}"
        )
        return common

    def _parse_jsonl(
        self,
        output: str,
        stderr: str,
        fallback_session_id: Optional[str],
    ) -> ChatResponse:
        """Parse codex JSONL output into one normalized response."""
        final_text = ""
        thread_id = fallback_session_id

        for raw_line in output.splitlines():
            line = raw_line.strip()
            if not line:
                continue

            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue

            event_type = event.get("type")
            if event_type == "thread.started":
                thread_id = event.get("thread_id") or thread_id
            elif event_type == "item.completed":
                item = event.get("item", {})
                if item.get("type") == "agent_message":
                    final_text = item.get("text", final_text)
            elif event_type == "error":
                message = event.get("message") or stderr or "Codex CLI error"
                if "No such thread" in message or "not found" in message.lower():
                    return ChatResponse(message, ChatError.SESSION_NOT_FOUND, thread_id)
                return ChatResponse(message, ChatError.CLI_ERROR, thread_id)
            elif event_type == "turn.failed":
                failed = event.get("error", {})
                message = failed.get("message") or stderr or "Codex turn failed"
                if "No such thread" in message or "not found" in message.lower():
                    return ChatResponse(message, ChatError.SESSION_NOT_FOUND, thread_id)
                return ChatResponse(message, ChatError.CLI_ERROR, thread_id)

        if not final_text and stderr:
            return ChatResponse(stderr, ChatError.CLI_ERROR, thread_id)
        return ChatResponse(final_text, None, thread_id)
