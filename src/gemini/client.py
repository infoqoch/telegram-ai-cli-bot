"""Async Gemini CLI client."""

import asyncio
import json
import re
import sys
from pathlib import Path
from typing import Optional

from src.ai.base_client import BaseCLIClient, PromptConfig
from src.ai.catalog import get_profile
from src.ai.client_types import ChatError, ChatResponse
from src.logging_config import logger

_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.IGNORECASE
)

_MCP_SERVER_NAME = "bot-plugins"


class GeminiClient(BaseCLIClient):
    """Async wrapper for Gemini CLI.

    Key differences from ClaudeClient:
    - System prompt via GEMINI.md file (no --system-prompt flag)
    - MCP via .gemini/settings.json in cwd (no --mcp-config flag)
    - JSON response field is "response" (Claude uses "result")
    - Session resume: --resume <uuid> (same flag name as Claude)
    - Model selection: -m <provider_model>
    - Auto-approve: --approval-mode yolo
    """

    def __init__(
        self,
        command: str = "gemini",
        system_prompt_file: Optional[Path] = None,
        timeout: Optional[int] = None,
    ):
        super().__init__(command, system_prompt_file, timeout)
        # Write system prompt to GEMINI.md at project root (used for non-workspace sessions)
        self._ensure_gemini_md(self._project_root())

    def _inject_prompt_args(self, cmd: list[str], prompts: PromptConfig) -> None:
        # Gemini uses GEMINI.md for system prompts — no CLI flag available.
        pass

    # ------------------------------------------------------------------
    # Setup helpers
    # ------------------------------------------------------------------

    def _ensure_gemini_md(self, directory: Path) -> None:
        """Write system prompt to GEMINI.md in the given directory.

        Only writes if system_prompt is set. Does not overwrite existing
        content that is already identical (avoids unnecessary disk writes).
        """
        if not self.system_prompt:
            return
        gemini_md = directory / "GEMINI.md"
        if gemini_md.exists() and gemini_md.read_text(encoding="utf-8") == self.system_prompt:
            return
        try:
            gemini_md.write_text(self.system_prompt, encoding="utf-8")
            logger.debug(f"GEMINI.md written to {directory}")
        except OSError as exc:
            logger.warning(f"Could not write GEMINI.md to {directory}: {exc}")

    def _ensure_mcp_settings(self, directory: Path) -> None:
        """Ensure .gemini/settings.json in the given directory exposes bot-plugins MCP.

        Only creates if the file does not exist — does not overwrite user-customized config.
        """
        bridge_script = self._project_root() / "mcp_servers" / "plugin_bridge_server.py"
        if not bridge_script.exists():
            return

        settings_path = directory / ".gemini" / "settings.json"
        if settings_path.exists():
            return

        config = {
            "mcpServers": {
                _MCP_SERVER_NAME: {
                    "command": sys.executable,
                    "args": [str(bridge_script)],
                }
            }
        }
        try:
            settings_path.parent.mkdir(parents=True, exist_ok=True)
            settings_path.write_text(json.dumps(config, indent=2), encoding="utf-8")
            logger.debug(f".gemini/settings.json written to {directory}")
        except OSError as exc:
            logger.warning(f"Could not write .gemini/settings.json to {directory}: {exc}")

    def _prepare_workspace(self, workspace_path: str) -> None:
        """Ensure workspace directory has GEMINI.md and MCP settings."""
        workspace = Path(workspace_path)
        self._ensure_gemini_md(workspace)
        self._ensure_mcp_settings(workspace)

    # ------------------------------------------------------------------
    # Command building
    # ------------------------------------------------------------------

    def _build_command(
        self,
        message: str,
        session_id: Optional[str] = None,
        model: Optional[str] = None,
        workspace_path: Optional[str] = None,
    ) -> list[str]:
        cmd = list(self.command_parts)

        if model:
            profile = get_profile("gemini", model)
            cmd.extend(["-m", profile.provider_model])

        if session_id and _UUID_RE.match(session_id):
            cmd.extend(["--resume", session_id])
        elif session_id:
            logger.warning(f"Invalid UUID for --resume, starting new session: {session_id[:16]}")

        cmd.extend(["--output-format", "json"])
        cmd.extend(["--approval-mode", "yolo"])
        cmd.extend(["--prompt", message])

        return cmd

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def create_session(self, workspace_path: Optional[str] = None) -> Optional[str]:
        """Create a new Gemini session and return session_id."""
        logger.info("creating new Gemini session")
        response = await self.chat("answer 'hi'", None, workspace_path=workspace_path)
        if response.error:
            logger.error(f"Gemini session creation failed: {response.error.value}")
            return None
        logger.info(f"new Gemini session created: {response.session_id}")
        return response.session_id

    async def chat(
        self,
        message: str,
        session_id: Optional[str] = None,
        model: Optional[str] = None,
        workspace_path: Optional[str] = None,
    ) -> ChatResponse:
        """Send a message to Gemini.

        Args:
            message: User message
            session_id: Gemini session UUID (uses --resume if provided)
            model: Model profile key (gemini-pro / gemini-flash / gemini-flash-lite)
            workspace_path: Workspace directory (cwd for the CLI call)
        """
        short_msg = message[:50] + "..." if len(message) > 50 else message
        logger.trace(f"GeminiClient.chat() - msg='{short_msg}', session={session_id[:8] if session_id else 'None'}, model={model}")

        if workspace_path:
            await asyncio.to_thread(self._prepare_workspace, workspace_path)

        normalized_model = get_profile("gemini", model).key if model else None
        cmd = self._build_command(message, session_id, normalized_model, workspace_path)

        try:
            output, error, returncode = await self._run_command(
                cmd, timeout=self.timeout, cwd=workspace_path
            )
        except asyncio.TimeoutError:
            logger.warning(f"Gemini CLI timed out - session={session_id[:8] if session_id else 'None'}")
            return ChatResponse("", ChatError.TIMEOUT, session_id)
        except Exception as exc:
            logger.exception(f"Gemini CLI exception: {exc}")
            return ChatResponse(str(exc), ChatError.CLI_ERROR, None)

        if returncode != 0:
            logger.error(f"Gemini CLI abnormal exit - returncode={returncode}")
            logger.error(f"  stderr: {error or '(empty)'}")
            logger.error(f"  stdout: {output[:500] if output else '(empty)'}")

            if error and ("session" in error.lower() and ("not found" in error.lower() or "invalid" in error.lower())):
                return ChatResponse(error, ChatError.SESSION_NOT_FOUND, None)

            return ChatResponse(error or output or "Gemini CLI error", ChatError.CLI_ERROR, session_id)

        logger.debug(f"[GEMINI RAW] length={len(output)}, preview={repr(output[:300]) if output else 'EMPTY'}")

        try:
            data = json.loads(output)
        except json.JSONDecodeError:
            logger.warning(f"Gemini JSON parse failed, returning raw output")
            return ChatResponse(output or "(no response)", None, session_id)

        result = data.get("response", "")
        new_session_id = data.get("session_id")

        if not result or not result.strip():
            logger.warning(f"[GEMINI EMPTY RESULT] data keys: {list(data.keys())}")

        logger.info(f"Gemini response - session_id={new_session_id}")
        return ChatResponse(result, None, new_session_id or session_id)
