"""Async Codex CLI client."""

import asyncio
import json
import shlex
from pathlib import Path
from typing import Optional

from src.ai.base_client import BaseCLIClient
from src.ai.catalog import get_profile
from src.ai.client_types import ChatError, ChatResponse
from src.logging_config import logger


class CodexClient(BaseCLIClient):
    """Async wrapper for Codex CLI."""

    def __init__(
        self,
        command: str = "codex",
        system_prompt_file: Optional[Path] = None,
        timeout: Optional[int] = None,
    ):
        self.command_parts = shlex.split(command)
        self.system_prompt = self._load_system_prompt(system_prompt_file)
        self.timeout = timeout

    def _load_system_prompt(self, path: Optional[Path]) -> Optional[str]:
        if path and path.exists():
            return path.read_text(encoding="utf-8")
        return None

    async def chat(
        self,
        message: str,
        session_id: Optional[str] = None,
        model: Optional[str] = None,
        workspace_path: Optional[str] = None,
    ) -> ChatResponse:
        """Send one message via codex exec/exec resume."""
        profile = get_profile("codex", model)
        cmd = self._build_command(
            message=message,
            session_id=session_id,
            model=profile.key,
            workspace_path=workspace_path,
        )

        try:
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

        if returncode != 0 and not output:
            return ChatResponse(error or "Codex CLI failed", ChatError.CLI_ERROR, session_id)

        return self._parse_jsonl(output, error, fallback_session_id=session_id)

    def _build_command(
        self,
        message: str,
        session_id: Optional[str],
        model: str,
        workspace_path: Optional[str],
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

        if self.system_prompt:
            common.extend(["-c", f'instructions="{self.system_prompt}"'])

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
