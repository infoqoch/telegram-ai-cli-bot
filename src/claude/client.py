"""Async Claude Code CLI client."""

import asyncio
from contextlib import suppress
from datetime import datetime, timezone
import json
import os
import pty
import re
import select
import shlex
import signal
import subprocess
import time
from pathlib import Path
from typing import Optional

_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.IGNORECASE
)
_ANSI_RE = re.compile(r"\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])")
_USAGE_RE = re.compile(
    r"5h:\s*(?P<five_hour_percent>\d+)%\s*\((?P<five_hour_reset>[^)]+)\).*?"
    r"wk:\s*(?P<weekly_percent>\d+)%\s*\((?P<weekly_reset>[^)]+)\)",
    re.IGNORECASE | re.DOTALL,
)

from src.ai.base_client import BaseCLIClient
from src.ai.catalog import get_profile
from src.ai.client_types import ChatError, ChatResponse
from src.logging_config import logger


class ClaudeClient(BaseCLIClient):
    """Async wrapper for Claude Code CLI."""

    def __init__(
        self,
        command: str = "claude",
        system_prompt_file: Optional[Path] = None,
        timeout: Optional[int] = None,
    ):
        logger.trace(f"ClaudeClient.__init__() - command='{command}', timeout={timeout}")
        self.command_parts = shlex.split(command)
        self.system_prompt = self._load_system_prompt(system_prompt_file)
        self.timeout = timeout
        logger.trace(f"command_parts={self.command_parts}")
        logger.trace(f"system_prompt loaded={self.system_prompt is not None}")

    def _load_system_prompt(self, path: Optional[Path]) -> Optional[str]:
        logger.trace(f"_load_system_prompt() - path={path}")
        if path and path.exists():
            content = path.read_text(encoding="utf-8")
            logger.trace(f"system prompt loaded - length={len(content)}")
            return content
        logger.trace("system prompt none")
        return None

    async def create_session(self, workspace_path: Optional[str] = None) -> Optional[str]:
        """Create a new Claude session and return session_id.

        Args:
            workspace_path: Workspace directory path (for workspace sessions)
        """
        logger.trace(f"create_session() start - workspace_path={workspace_path or 'none'}")
        logger.info("creating new Claude session")

        response = await self.chat("answer 'hi'", None, workspace_path=workspace_path)

        if response.error:
            logger.error(f"session creation failed: {response.error.value}")
            return None

        logger.info(f"new session created: {response.session_id}")
        logger.trace(f"response: {response.text[:100] if response.text else 'none'}")
        return response.session_id

    async def chat(
        self,
        message: str,
        session_id: Optional[str] = None,
        model: Optional[str] = None,
        workspace_path: Optional[str] = None,
    ) -> ChatResponse:
        """
        Send a message to Claude.

        Args:
            message: User message
            session_id: Claude's session ID (always use --resume if provided)
            model: Model to use (opus, sonnet, haiku)
            workspace_path: Workspace directory path (for workspace sessions)

        Returns:
            ChatResponse with text, error, and session_id
        """
        short_msg = message[:50] + "..." if len(message) > 50 else message
        logger.trace(f"chat() start - msg='{short_msg}'")
        logger.trace(f"session_id={session_id[:8] if session_id else 'None'}, model={model}, workspace={workspace_path or 'none'}")

        normalized_model = get_profile("claude", model).key if model else None
        cmd = self._build_command(message, session_id, normalized_model, workspace_path)
        logger.trace(f"command built - {len(cmd)} parts")

        try:
            logger.trace("CLI execution start")
            output, error, returncode = await self._run_command(cmd, timeout=self.timeout, cwd=workspace_path)

            logger.trace(f"CLI result - returncode={returncode}")

            if returncode != 0:
                # detailed error logging - both stdout and stderr
                logger.error(f"Claude CLI abnormal exit - returncode={returncode}")
                logger.error(f"  stderr: {error if error else '(empty)'}")
                logger.error(f"  stdout: {output[:500] if output else '(empty)'}")
                logger.error(f"  session_id: {session_id[:8] if session_id else 'None'}")
                logger.error(f"  message: {short_msg}")

                # command used (excluding message content)
                cmd_preview = " ".join(cmd[:-1])  # exclude last arg (message)
                logger.debug(f"  command: {cmd_preview} <message>")

                if error and ("not found" in error.lower() or "no conversation found" in error.lower() or "invalid" in error.lower()):
                    logger.warning(f"session not found: {error[:100]}")
                    return ChatResponse("", ChatError.SESSION_NOT_FOUND, None)

                # combine error messages (merge if both present)
                error_detail = error or output or "(no error content)"
                return ChatResponse(error_detail, ChatError.CLI_ERROR, None)

            # JSON parsing
            logger.trace("JSON parse attempt")
            logger.debug(f"[RAW OUTPUT] length={len(output)}, preview={repr(output[:300]) if output else 'EMPTY'}")
            try:
                data = json.loads(output)
                result = data.get("result", "")
                new_session_id = data.get("session_id")

                logger.trace(f"parse success - session_id={new_session_id}")
                logger.debug(f"[PARSED] result type={type(result)}, length={len(result) if result else 0}")
                logger.debug(f"[PARSED] result preview={repr(result[:200]) if result else 'EMPTY/NONE'}")
                logger.debug(f"[PARSED] all keys={list(data.keys())}")

                # detect empty result - trace cause
                if not result or not result.strip():
                    logger.warning(f"[EMPTY RESULT] Claude returned empty result!")
                    logger.warning(f"  raw data keys: {list(data.keys())}")
                    logger.warning(f"  raw data: {json.dumps(data, ensure_ascii=False)[:500]}")

                logger.info(f"Claude response - session_id={new_session_id}")

                return ChatResponse(result, None, new_session_id)

            except json.JSONDecodeError as e:
                # return raw output on JSON parse failure
                logger.warning(f"JSON parse failed: {e}")
                logger.warning(f"[JSON ERROR] raw output: {repr(output[:500]) if output else 'EMPTY'}")
                return ChatResponse(output or "(no response)", None, None)

        except asyncio.TimeoutError:
            logger.warning(
                f"Claude CLI timed out - session={session_id[:8] if session_id else 'None'}, "
                f"timeout={self.timeout}"
            )
            return ChatResponse("", ChatError.TIMEOUT, session_id)
        except Exception as e:
            logger.exception(f"Claude CLI error: {e}")
            return ChatResponse("", ChatError.CLI_ERROR, None)

    async def get_usage_snapshot(self) -> Optional[dict[str, str]]:
        """Return the current Claude Code subscription usage snapshot."""
        auth_snapshot = await self._get_auth_snapshot()
        if not auth_snapshot:
            return None

        snapshot = {
            "subscription_type": auth_snapshot["subscription_type"],
            "checked_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }

        omc_snapshot = await self._get_usage_snapshot_from_omc()
        if omc_snapshot:
            snapshot.update(omc_snapshot)
            return snapshot

        raw_screen = await asyncio.to_thread(self._capture_usage_screen)
        if raw_screen:
            cleaned = self._strip_ansi(raw_screen)
            match = _USAGE_RE.search(cleaned)
            if match:
                snapshot.update({key: value.strip() for key, value in match.groupdict().items()})
                return snapshot
            logger.warning(f"Claude usage screen parse failed: {cleaned[:300]!r}")
        else:
            logger.warning("Claude usage screen capture returned empty output")

        unavailable_reason = await self._get_usage_unavailable_reason()
        snapshot["unavailable_reason"] = unavailable_reason or "Usage endpoint temporarily unavailable"
        return snapshot

    async def _get_auth_snapshot(self) -> Optional[dict[str, str]]:
        """Read Claude auth status and return plan details."""
        auth_stdout, auth_stderr, auth_returncode = await self._run_command(
            [*self.command_parts, "auth", "status"],
            timeout=10,
        )
        if auth_returncode != 0:
            logger.warning(f"Claude auth status failed: {auth_stderr or auth_stdout}")
            return None

        subscription_type = "unknown"
        try:
            auth_data = json.loads(auth_stdout)
            if not auth_data.get("loggedIn"):
                logger.warning("Claude auth status reports loggedOut state")
                return None
            subscription_type = str(auth_data.get("subscriptionType") or "unknown")
        except json.JSONDecodeError:
            logger.debug("Claude auth status was not JSON; continuing without subscription_type")

        return {"subscription_type": subscription_type}

    async def _get_usage_snapshot_from_omc(self) -> Optional[dict[str, str]]:
        """Best-effort usage lookup via oh-my-claudecode's usage API."""
        usage_api_path = self._find_omc_usage_api_path()
        if not usage_api_path:
            logger.debug("OMC usage-api.js not found; skipping plugin usage lookup")
            return None

        script = """
import { pathToFileURL } from "node:url";

const usageApiPath = process.argv[1];

try {
  const mod = await import(pathToFileURL(usageApiPath).href);
  const data = await mod.getUsage();
  process.stdout.write(JSON.stringify({ data }));
} catch (error) {
  process.stdout.write(JSON.stringify({ error: String(error) }));
}
"""
        try:
            stdout, stderr, returncode = await self._run_command(
                ["node", "--input-type=module", "-e", script, str(usage_api_path)],
                timeout=15,
            )
        except Exception as exc:
            logger.debug(f"OMC usage lookup failed to execute: {exc}")
            return None

        if returncode != 0:
            logger.warning(f"OMC usage lookup failed: {stderr or stdout}")
            return None

        try:
            payload = json.loads(stdout) if stdout else {}
        except json.JSONDecodeError:
            logger.warning(f"OMC usage lookup returned non-JSON: {stdout[:200]!r}")
            return None

        if payload.get("error"):
            logger.warning(f"OMC usage lookup errored: {payload['error']}")
            return None

        data = payload.get("data")
        if not isinstance(data, dict):
            return None

        five_hour_percent = self._format_usage_percent(data.get("fiveHourPercent"))
        weekly_percent = self._format_usage_percent(data.get("weeklyPercent"))
        if five_hour_percent is None or weekly_percent is None:
            return None

        return {
            "five_hour_percent": five_hour_percent,
            "five_hour_reset": self._format_reset_window(data.get("fiveHourResetsAt")),
            "weekly_percent": weekly_percent,
            "weekly_reset": self._format_reset_window(data.get("weeklyResetsAt")),
        }

    async def _get_usage_unavailable_reason(self) -> Optional[str]:
        """Best-effort detail for why usage data is currently unavailable."""
        script = """
import { execSync } from "node:child_process";
import { existsSync, readFileSync } from "node:fs";
import https from "node:https";
import { homedir } from "node:os";
import { join } from "node:path";

function readKeychainCredentials() {
  if (process.platform !== "darwin") return null;
  try {
    const raw = execSync('/usr/bin/security find-generic-password -s "Claude Code-credentials" -w 2>/dev/null', {
      encoding: "utf8",
      timeout: 2000,
    }).trim();
    const parsed = JSON.parse(raw);
    const creds = parsed.claudeAiOauth || parsed;
    return creds.accessToken ? creds : null;
  } catch {
    return null;
  }
}

function readFileCredentials() {
  try {
    const credPath = join(homedir(), ".claude/.credentials.json");
    if (!existsSync(credPath)) return null;
    const parsed = JSON.parse(readFileSync(credPath, "utf8"));
    const creds = parsed.claudeAiOauth || parsed;
    return creds.accessToken ? creds : null;
  } catch {
    return null;
  }
}

const creds = readKeychainCredentials() || readFileCredentials();
if (!creds?.accessToken) {
  process.stdout.write(JSON.stringify({ reason: "Claude credentials unavailable" }));
  process.exit(0);
}

const req = https.request({
  hostname: "api.anthropic.com",
  path: "/api/oauth/usage",
  method: "GET",
  headers: {
    Authorization: `Bearer ${creds.accessToken}`,
    "anthropic-beta": "oauth-2025-04-20",
    "Content-Type": "application/json",
  },
  timeout: 10000,
}, (res) => {
  let data = "";
  res.on("data", (chunk) => { data += chunk; });
  res.on("end", () => {
    if (res.statusCode === 200) {
      process.stdout.write(JSON.stringify({ reason: null }));
      return;
    }

    let parsed = null;
    try {
      parsed = JSON.parse(data);
    } catch {}

    const error = parsed?.error || {};
    process.stdout.write(JSON.stringify({
      reason: error.message || `HTTP ${res.statusCode}`,
      statusCode: res.statusCode,
      errorType: error.type || null,
    }));
  });
});

req.on("error", (error) => {
  process.stdout.write(JSON.stringify({ reason: String(error) }));
});

req.on("timeout", () => {
  req.destroy(new Error("timeout"));
});

req.end();
"""
        try:
            stdout, stderr, returncode = await self._run_command(
                ["node", "--input-type=module", "-e", script],
                timeout=15,
            )
        except Exception as exc:
            logger.debug(f"Usage unavailable-reason lookup failed to execute: {exc}")
            return None

        if returncode != 0:
            logger.warning(f"Usage unavailable-reason lookup failed: {stderr or stdout}")
            return None

        try:
            payload = json.loads(stdout) if stdout else {}
        except json.JSONDecodeError:
            logger.warning(f"Usage unavailable-reason lookup returned non-JSON: {stdout[:200]!r}")
            return None

        reason = payload.get("reason")
        if not reason:
            return None

        status_code = payload.get("statusCode")
        error_type = payload.get("errorType")
        suffix_parts = [str(part) for part in (status_code, error_type) if part]
        if suffix_parts:
            return f"{reason} ({', '.join(suffix_parts)})"
        return str(reason)

    @staticmethod
    def _find_omc_usage_api_path() -> Optional[Path]:
        """Return the newest installed oh-my-claudecode usage API path."""
        cache_root = Path.home() / ".claude" / "plugins" / "cache" / "omc" / "oh-my-claudecode"
        if not cache_root.exists():
            return None

        candidates = list(cache_root.glob("*/dist/hud/usage-api.js"))
        if not candidates:
            return None

        return max(candidates, key=lambda path: path.stat().st_mtime)

    @staticmethod
    def _format_usage_percent(value) -> Optional[str]:
        """Normalize one percentage value to a whole-number string."""
        try:
            return str(int(round(float(value))))
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _format_reset_window(value) -> str:
        """Format an ISO reset timestamp as a compact relative window."""
        if not value:
            return "unknown"

        try:
            reset_at = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError:
            return str(value)

        if reset_at.tzinfo is None:
            reset_at = reset_at.replace(tzinfo=timezone.utc)

        remaining = int((reset_at - datetime.now(timezone.utc)).total_seconds())
        if remaining <= 0:
            return "soon"

        days, rem = divmod(remaining, 86400)
        hours, rem = divmod(rem, 3600)
        minutes, _ = divmod(rem, 60)

        parts: list[str] = []
        if days:
            parts.append(f"{days}d")
        if hours:
            parts.append(f"{hours}h")
        if minutes or not parts:
            parts.append(f"{minutes}m")
        return "".join(parts)

    def _capture_usage_screen(self, startup_timeout: float = 3.0) -> str:
        """Launch Claude in a PTY briefly and capture the startup status line."""
        master_fd, slave_fd = pty.openpty()
        process = subprocess.Popen(
            self.command_parts,
            stdin=slave_fd,
            stdout=slave_fd,
            stderr=slave_fd,
            close_fds=True,
        )
        os.close(slave_fd)

        chunks: list[bytes] = []
        deadline = time.monotonic() + startup_timeout
        try:
            while time.monotonic() < deadline:
                ready, _, _ = select.select([master_fd], [], [], 0.25)
                if not ready:
                    continue

                try:
                    chunk = os.read(master_fd, 4096)
                except OSError:
                    break

                if not chunk:
                    break

                chunks.append(chunk)
                if b"5h:" in chunk and b"wk:" in chunk:
                    # Collect one extra frame so reset strings are included before shutdown.
                    settle_deadline = time.monotonic() + 0.35
                    while time.monotonic() < settle_deadline:
                        ready, _, _ = select.select([master_fd], [], [], 0.05)
                        if not ready:
                            continue
                        try:
                            extra = os.read(master_fd, 4096)
                        except OSError:
                            extra = b""
                        if not extra:
                            break
                        chunks.append(extra)
                    break
        finally:
            for sig in (signal.SIGINT, signal.SIGINT, signal.SIGTERM):
                if process.poll() is not None:
                    break
                with suppress(Exception):
                    process.send_signal(sig)
                time.sleep(0.05)

            if process.poll() is None:
                with suppress(Exception):
                    process.kill()
            with suppress(Exception):
                process.wait(timeout=1)
            with suppress(OSError):
                os.close(master_fd)

        return b"".join(chunks).decode("utf-8", errors="ignore")

    @staticmethod
    def _strip_ansi(text: str) -> str:
        """Remove ANSI control codes from terminal output."""
        return _ANSI_RE.sub("", text).replace("\r", "")

    def _build_command(
        self,
        message: str,
        session_id: Optional[str] = None,
        model: Optional[str] = None,
        workspace_path: Optional[str] = None,
    ) -> list[str]:
        """Build Claude CLI command."""
        logger.trace(f"_build_command() - session={session_id[:8] if session_id else 'None'}, model={model}, workspace={workspace_path or 'none'}")

        cmd = list(self.command_parts)

        # model selection
        if model:
            cmd.extend(["--model", model])
            logger.trace(f"--model {model} option added")

        # use resume if session exists (valid UUID only)
        if session_id and _UUID_RE.match(session_id):
            cmd.extend(["--resume", session_id])
            logger.trace("--resume option added")
        elif session_id:
            logger.warning(f"Invalid UUID for --resume, starting new session: {session_id[:16]}")

        # JSON output (for session_id parsing)
        cmd.extend(["--print", "--output-format", "json"])
        logger.trace("JSON output option added")

        # auto-approve tool permissions (needed by scheduler for WebSearch etc.)
        cmd.append("--dangerously-skip-permissions")
        logger.trace("--dangerously-skip-permissions option added")

        if self.system_prompt:
            cmd.extend(["--system-prompt", self.system_prompt])
            logger.trace("system prompt option added")

        # workspace session: append telegram response format (workspace CLAUDE.md + telegram format)
        if workspace_path:
            telegram_format_prompt = (
                "응답 포맷 규칙: "
                "1) Telegram HTML 사용 (<b>, <i>, <code>, <pre>) "
                "2) 마크다운 금지 (**, *, #, ```) "
                "3) 모바일 최적화 (간결하게) "
                "4) 한국어로 응답"
            )
            cmd.extend(["--append-system-prompt", telegram_format_prompt])
            logger.trace("workspace session - telegram format prompt added")

        cmd.append(message)
        logger.trace(f"final command length: {len(cmd)} parts")

        return cmd

    async def summarize(self, questions: list[str], max_questions: int = 10) -> str:
        """Generate a summary of conversation questions."""
        logger.trace(f"summarize() - questions={len(questions)}, max={max_questions}")

        if not questions:
            logger.trace("no questions")
            return "(no content)"

        history_text = "\n".join(f"- {q[:100]}" for q in questions[:max_questions])
        logger.trace(f"history text built - length={len(history_text)}")

        prompt = f"""다음 질문들을 보고 이 대화 세션을 2-3문장으로 요약해주세요.
- 무엇을 하려고 했는지
- 주요 주제나 작업 내용
질문 없이 요약만 답변하세요.

질문들:
{history_text}"""

        cmd = list(self.command_parts) + [
            "--print",
            "--output-format", "text",
            "-p", prompt,
        ]

        logger.trace("running summarize command")

        try:
            output, _, _ = await self._run_command(cmd, timeout=60)
            result = output[:300] if output else "(summarize failed)"
            logger.trace(f"summarize complete - length={len(result)}")
            return result

        except Exception as e:
            logger.warning(f"summarize failed: {e}")
            first_q = questions[0][:50]
            return f'"{first_q}..."'

    async def compact(self, session_id: str) -> ChatResponse:
        """Compact a Claude session to reduce context size.

        Args:
            session_id: Claude's session ID to compact

        Returns:
            ChatResponse with compact result
        """
        logger.trace(f"compact() - session_id={session_id[:8]}")
        logger.info(f"session compact start: {session_id[:8]}")

        # Claude CLI compact command: claude --resume <session_id> /compact
        cmd = list(self.command_parts) + [
            "--resume", session_id,
            "--print",
            "--output-format", "json",
            "/compact",
        ]

        try:
            output, error, returncode = await self._run_command(cmd, timeout=120)

            if returncode != 0:
                logger.error(f"Compact failed - returncode={returncode}, error={error}")
                return ChatResponse(error or "(compact failed)", ChatError.CLI_ERROR, session_id)

            # JSON parse attempt
            try:
                data = json.loads(output)
                result = data.get("result", "(no response)")
                logger.info(f"Compact complete: {session_id[:8]}")
                return ChatResponse(result, None, session_id)
            except json.JSONDecodeError:
                # return raw output on JSON parse failure
                logger.info(f"Compact complete (raw): {session_id[:8]}")
                return ChatResponse(output or "(compact complete)", None, session_id)

        except asyncio.TimeoutError:
            logger.error(f"Compact timeout: {session_id[:8]}")
            return ChatResponse("", ChatError.TIMEOUT, session_id)
        except Exception as e:
            logger.exception(f"Compact error: {e}")
            return ChatResponse(str(e), ChatError.CLI_ERROR, session_id)
