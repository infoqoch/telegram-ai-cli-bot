"""Async Claude Code CLI client."""

import asyncio
import json
import logging
import shlex
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


class ChatError(Enum):
    """Claude CLI 에러 타입."""

    TIMEOUT = "TIMEOUT"
    SESSION_NOT_FOUND = "SESSION_NOT_FOUND"
    CLI_ERROR = "CLI_ERROR"


@dataclass
class ChatResponse:
    """Claude CLI 응답."""

    text: str
    error: Optional[ChatError] = None
    session_id: Optional[str] = None

    def __iter__(self):
        """하위 호환성을 위한 tuple 언패킹 지원."""
        error_str = self.error.value if self.error else None
        return iter((self.text, error_str, self.session_id))


class ClaudeClient:
    """Async wrapper for Claude Code CLI."""

    def __init__(
        self,
        command: str = "claude",
        system_prompt_file: Optional[Path] = None,
        timeout: int = 300,
    ):
        self.command_parts = shlex.split(command)
        self.system_prompt = self._load_system_prompt(system_prompt_file)
        self.timeout = timeout

    def _load_system_prompt(self, path: Optional[Path]) -> Optional[str]:
        if path and path.exists():
            return path.read_text(encoding="utf-8")
        return None

    async def _run_command(
        self,
        cmd: list[str],
        timeout: int,
    ) -> tuple[str, str, int]:
        """Execute command and return (stdout, stderr, returncode)."""
        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(
            process.communicate(),
            timeout=timeout,
        )
        return (
            stdout.decode("utf-8").strip(),
            stderr.decode("utf-8").strip(),
            process.returncode,
        )

    async def create_session(self) -> Optional[str]:
        """Create a new Claude session and return session_id."""
        response = await self.chat("answer 'hi'", None)
        if response.error:
            logger.error(f"Failed to create Claude session: {response.error.value}")
            return None
        logger.info(f"Created new Claude session: {response.session_id}")
        return response.session_id

    async def chat(
        self,
        message: str,
        session_id: Optional[str] = None,
    ) -> ChatResponse:
        """
        Send a message to Claude.

        Args:
            message: User message
            session_id: Claude's session ID (always use --resume if provided)

        Returns:
            ChatResponse with text, error, and session_id
        """
        cmd = self._build_command(message, session_id)

        try:
            output, error, returncode = await self._run_command(cmd, self.timeout)

            if returncode != 0:
                if "not found" in error.lower() or "invalid" in error.lower():
                    return ChatResponse("", ChatError.SESSION_NOT_FOUND, None)
                logger.warning(f"Claude CLI error: {error}")
                return ChatResponse(error or "(오류)", ChatError.CLI_ERROR, None)

            # JSON 파싱
            try:
                data = json.loads(output)
                result = data.get("result", "(응답 없음)")
                new_session_id = data.get("session_id")
                logger.info(f"Claude response - session_id: {new_session_id}")
                return ChatResponse(result, None, new_session_id)
            except json.JSONDecodeError:
                # JSON 파싱 실패 시 원본 반환
                logger.warning(f"JSON parse failed, output: {output[:200]}")
                return ChatResponse(output or "(응답 없음)", None, None)

        except asyncio.TimeoutError:
            logger.error(f"Claude CLI timeout after {self.timeout}s")
            return ChatResponse("", ChatError.TIMEOUT, None)
        except Exception as e:
            logger.exception("Claude CLI error")
            return ChatResponse("", ChatError.CLI_ERROR, None)

    def _build_command(
        self,
        message: str,
        session_id: Optional[str] = None,
    ) -> list[str]:
        """Build Claude CLI command."""
        cmd = list(self.command_parts)

        # 세션이 있으면 항상 resume 사용
        if session_id:
            cmd.extend(["--resume", session_id])

        # JSON 출력 (session_id 파싱용)
        cmd.extend(["--print", "--output-format", "json"])

        if self.system_prompt:
            cmd.extend(["--system-prompt", self.system_prompt])

        cmd.append(message)
        return cmd

    async def summarize(self, questions: list[str], max_questions: int = 10) -> str:
        """Generate a summary of conversation questions."""
        if not questions:
            return "(내용 없음)"

        history_text = "\n".join(f"- {q[:100]}" for q in questions[:max_questions])
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

        try:
            output, _, _ = await self._run_command(cmd, timeout=60)
            return output[:300] if output else "(요약 실패)"

        except Exception:
            first_q = questions[0][:50]
            return f'"{first_q}..."'
