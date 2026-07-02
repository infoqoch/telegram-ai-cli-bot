"""Async Antigravity (agy) CLI client."""

import asyncio
from contextlib import contextmanager
import fcntl
import json
import os
import re
import sys
import time
import uuid
from pathlib import Path
from typing import Iterator, Optional

from src.ai.base_client import BaseCLIClient, PromptConfig
from src.ai.catalog import get_profile
from src.ai.client_types import ChatError, ChatResponse
from src.logging_config import logger
from src.runtime_paths import get_data_dir, get_log_dir

_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.IGNORECASE
)
_SESSION_NOT_FOUND_RE = re.compile(
    r"conversation\s+\"?[0-9a-f-]+\"?\s+not found",
    re.IGNORECASE,
)

_MCP_SERVER_NAME = "bot-plugins"
_DEFAULT_PRINT_TIMEOUT = "30m"
_SUBPROCESS_TIMEOUT_GRACE_SECONDS = 60


class AgyClient(BaseCLIClient):
    """Async wrapper for Antigravity (agy) CLI."""

    def __init__(
        self,
        command: str = "agy",
        system_prompt_file: Optional[Path] = None,
        timeout: Optional[int] = None,
        print_timeout: str = _DEFAULT_PRINT_TIMEOUT,
        agy_root: Optional[Path] = None,
        session_create_lock_path: Optional[Path] = None,
        log_dir: Optional[Path] = None,
        prepare_project_mcp: bool = True,
    ):
        effective_timeout = timeout
        if effective_timeout is None:
            print_timeout_seconds = self._parse_duration_seconds(print_timeout)
            if print_timeout_seconds is not None:
                effective_timeout = print_timeout_seconds + _SUBPROCESS_TIMEOUT_GRACE_SECONDS

        super().__init__(command, system_prompt_file, effective_timeout)
        self.print_timeout = print_timeout
        self._agy_root = agy_root or Path.home() / ".gemini" / "antigravity-cli"
        self._brain_root = self._agy_root / "brain"
        self._conversations_root = self._agy_root / "conversations"
        self._last_conversations_path = self._agy_root / "cache" / "last_conversations.json"
        self._session_create_lock_path = (
            session_create_lock_path or get_data_dir() / "agy-session-create.lock"
        )
        self._log_dir = log_dir or get_log_dir() / "agy"
        if prepare_project_mcp:
            self._ensure_mcp_settings(self._project_root())

    def _inject_prompt_args(self, cmd: list[str], prompts: PromptConfig) -> None:
        pass

    # ------------------------------------------------------------------
    # Setup helpers
    # ------------------------------------------------------------------

    def _ensure_mcp_settings(self, directory: Path) -> None:
        """Ensure .agents/mcp.json in the given directory exposes bot-plugins MCP."""
        bridge_script = self._project_root() / "mcp_servers" / "plugin_bridge_server.py"
        if not bridge_script.exists():
            return

        settings_path = directory / ".agents" / "mcp.json"
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
            logger.debug(f".agents/mcp.json written to {directory}")
        except OSError as exc:
            logger.warning(f"Could not write .agents/mcp.json to {directory}: {exc}")

    def _prepare_workspace(self, workspace_path: str) -> None:
        """Ensure workspace directory has MCP settings."""
        workspace = Path(workspace_path)
        self._ensure_mcp_settings(workspace)

    # ------------------------------------------------------------------
    # Session helpers
    # ------------------------------------------------------------------

    @contextmanager
    def _session_creation_lock(self) -> Iterator[None]:
        """Serialize new-session discovery across detached worker processes."""
        self._session_create_lock_path.parent.mkdir(parents=True, exist_ok=True)
        with self._session_create_lock_path.open("w", encoding="utf-8") as handle:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            handle.seek(0)
            handle.truncate()
            handle.write(str(os.getpid()))
            handle.flush()
            try:
                yield
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    def _session_transcript_path(self, session_id: str) -> Path:
        return self._brain_root / session_id / ".system_generated" / "logs" / "transcript.jsonl"

    def _session_db_path(self, session_id: str) -> Path:
        return self._conversations_root / f"{session_id}.db"

    def _session_artifact_mtime(self, session_id: str) -> float:
        mtimes: list[float] = []
        for path in (self._session_transcript_path(session_id), self._session_db_path(session_id)):
            try:
                if path.exists():
                    mtimes.append(path.stat().st_mtime)
            except OSError:
                continue
        return max(mtimes) if mtimes else 0.0

    def _session_exists(self, session_id: str) -> bool:
        if not _UUID_RE.fullmatch(session_id):
            return False
        return self._session_artifact_mtime(session_id) > 0

    def _snapshot_sessions(self) -> dict[str, float]:
        """Return known Agy session IDs and their newest local artifact mtimes."""
        sessions: dict[str, float] = {}

        if self._brain_root.exists():
            transcript_glob = "*/.system_generated/logs/transcript.jsonl"
            for transcript_path in self._brain_root.glob(transcript_glob):
                session_id = transcript_path.parents[2].name
                if not _UUID_RE.fullmatch(session_id):
                    continue
                try:
                    sessions[session_id] = max(
                        sessions.get(session_id, 0.0),
                        transcript_path.stat().st_mtime,
                    )
                except OSError:
                    continue

        if self._conversations_root.exists():
            for db_path in self._conversations_root.glob("*.db"):
                session_id = db_path.stem
                if not _UUID_RE.fullmatch(session_id):
                    continue
                try:
                    sessions[session_id] = max(
                        sessions.get(session_id, 0.0),
                        db_path.stat().st_mtime,
                    )
                except OSError:
                    continue

        return sessions

    @staticmethod
    def _normalize_workspace_key(workspace_path: Optional[str]) -> str:
        path = (
            Path(workspace_path).expanduser()
            if workspace_path
            else BaseCLIClient._project_root()
        )
        return str(path.resolve(strict=False))

    def _read_last_conversation_id(self, workspace_path: Optional[str]) -> Optional[str]:
        """Read Agy's workspace -> conversation cache for the current cwd."""
        try:
            data = json.loads(self._last_conversations_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        if not isinstance(data, dict):
            return None

        workspace_key = self._normalize_workspace_key(workspace_path)
        candidates = (workspace_key, str(Path(workspace_key)))
        for key in candidates:
            value = data.get(key)
            if isinstance(value, str) and _UUID_RE.fullmatch(value):
                return value
        return None

    def _resolve_created_session_id(
        self,
        before: dict[str, float],
        *,
        workspace_path: Optional[str],
        started_at: float,
    ) -> Optional[str]:
        """Identify the session created or touched by a no-conversation Agy call."""
        after = self._snapshot_sessions()
        cached_id = self._read_last_conversation_id(workspace_path)
        if cached_id:
            cached_mtime = after.get(cached_id) or self._session_artifact_mtime(cached_id)
            previous_mtime = before.get(cached_id, 0.0)
            if cached_mtime and (cached_mtime > previous_mtime or cached_mtime >= started_at - 1):
                return cached_id

        changed: list[tuple[float, str]] = []
        for session_id, mtime in after.items():
            previous_mtime = before.get(session_id, 0.0)
            if previous_mtime == 0.0 or mtime > previous_mtime or mtime >= started_at - 1:
                changed.append((mtime, session_id))

        if not changed:
            return None

        changed.sort(reverse=True)
        return changed[0][1]

    @staticmethod
    def _parse_duration_seconds(value: str) -> Optional[int]:
        """Parse Agy duration strings such as 30m, 5m0s, 1h30m."""
        text = (value or "").strip()
        if not text:
            return None
        match = re.fullmatch(
            r"(?:(?P<hours>\d+)h)?(?:(?P<minutes>\d+)m)?(?:(?P<seconds>\d+)s)?",
            text,
        )
        if not match or not any(match.groupdict().values()):
            return None
        hours = int(match.group("hours") or 0)
        minutes = int(match.group("minutes") or 0)
        seconds = int(match.group("seconds") or 0)
        return hours * 3600 + minutes * 60 + seconds

    def _new_log_file(self) -> Path:
        self._log_dir.mkdir(parents=True, exist_ok=True)
        return self._log_dir / f"agy-{uuid.uuid4().hex}.log"

    def _format_message(self, message: str, workspace_path: Optional[str] = None) -> str:
        """Embed prompt and execution context because Agy has no system-prompt flag."""
        working_directory = self._normalize_workspace_key(workspace_path)
        execution_context = (
            "<EXECUTION_CONTEXT>\n"
            f"Intended working directory: {working_directory}\n"
            "When using shell commands, explicitly change to the intended working directory first "
            "(for example: cd \"<directory>\" && <command>). Do not rely on the Antigravity "
            "internal shell current directory.\n"
            "</EXECUTION_CONTEXT>"
        )

        parts = []
        if self.system_prompt:
            parts.append(self.system_prompt.strip())
        parts.append(execution_context)
        parts.append(
            "<USER_REQUEST>\n"
            f"{message}\n"
            "</USER_REQUEST>"
        )
        return "\n\n".join(parts)

    @staticmethod
    def _is_session_not_found(output: str, error: str) -> bool:
        combined = "\n".join(part for part in (error, output) if part)
        return bool(_SESSION_NOT_FOUND_RE.search(combined))

    @staticmethod
    def _summarize_error(output: str, error: str) -> str:
        return error or output or "Agy CLI error"

    # ------------------------------------------------------------------
    # Command building
    # ------------------------------------------------------------------

    def _build_command(
        self,
        message: str,
        session_id: Optional[str] = None,
        model: Optional[str] = None,
        workspace_path: Optional[str] = None,
        log_file: Optional[Path] = None,
    ) -> list[str]:
        cmd = list(self.command_parts)

        if model:
            profile = get_profile("agy", model)
            cmd.extend(["--model", profile.provider_model])

        if session_id and _UUID_RE.match(session_id):
            cmd.extend(["--conversation", session_id])
        elif session_id:
            logger.warning(f"Invalid UUID for --conversation: {session_id[:16]}")

        if workspace_path:
            cmd.extend(["--add-dir", workspace_path])

        cmd.extend(["--dangerously-skip-permissions"])
        cmd.extend(["--print-timeout", self.print_timeout])
        if log_file:
            cmd.extend(["--log-file", str(log_file)])
        cmd.extend(["--print", self._format_message(message, workspace_path)])

        return cmd

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def create_session(self, workspace_path: Optional[str] = None) -> Optional[str]:
        """Agy creates the provider session on first real prompt."""
        logger.info("Agy session envelope will be bound on first chat")
        return None

    async def chat(
        self,
        message: str,
        session_id: Optional[str] = None,
        model: Optional[str] = None,
        workspace_path: Optional[str] = None,
    ) -> ChatResponse:
        short_msg = message[:50] + "..." if len(message) > 50 else message
        logger.trace(
            f"AgyClient.chat() - msg='{short_msg}', "
            f"session={session_id[:8] if session_id else 'None'}, model={model}"
        )

        if workspace_path:
            await asyncio.to_thread(self._prepare_workspace, workspace_path)

        if session_id:
            if not _UUID_RE.fullmatch(session_id):
                logger.warning(f"Invalid Agy session id: {session_id[:16]}")
                return ChatResponse("Invalid Agy session id", ChatError.SESSION_NOT_FOUND, None)
            if not self._session_exists(session_id):
                logger.warning(f"Agy session not found locally: {session_id}")
                return ChatResponse(
                    "Agy conversation not found locally",
                    ChatError.SESSION_NOT_FOUND,
                    None,
                )

        log_file = self._new_log_file()
        cmd = self._build_command(
            message,
            session_id=session_id,
            model=model,
            workspace_path=workspace_path,
            log_file=log_file,
        )

        try:
            if session_id:
                output, error, returncode = await self._run_command(
                    cmd, timeout=self.timeout, cwd=workspace_path
                )
                next_session_id = session_id
            else:
                with self._session_creation_lock():
                    before = self._snapshot_sessions()
                    started_at = time.time()
                    output, error, returncode = await self._run_command(
                        cmd, timeout=self.timeout, cwd=workspace_path
                    )
                    next_session_id = self._resolve_created_session_id(
                        before,
                        workspace_path=workspace_path,
                        started_at=started_at,
                    )
        except asyncio.TimeoutError:
            logger.warning(
                f"Agy CLI timed out - session={session_id[:8] if session_id else 'new'}"
            )
            return ChatResponse("", ChatError.TIMEOUT, session_id)
        except Exception as exc:
            logger.exception(f"Agy CLI exception: {exc}")
            return ChatResponse(str(exc), ChatError.CLI_ERROR, None)

        if self._is_session_not_found(output, error):
            logger.warning(f"Agy conversation missing during CLI call - session={session_id}")
            return ChatResponse(
                self._summarize_error(output, error),
                ChatError.SESSION_NOT_FOUND,
                None,
            )

        if returncode != 0:
            logger.error(f"Agy CLI abnormal exit - returncode={returncode}")
            logger.error(f"  stderr: {error or '(empty)'}")
            return ChatResponse(
                self._summarize_error(output, error),
                ChatError.CLI_ERROR,
                session_id,
            )

        result = output.strip()

        if not result:
            logger.warning("[AGY EMPTY RESULT]")

        if not next_session_id:
            logger.warning(f"Agy response returned without discoverable session id; log={log_file}")

        logger.info(f"Agy response - session_id={next_session_id or '-'}")
        return ChatResponse(result, None, next_session_id)
