"""Async Claude Code CLI client."""

import asyncio
import json
import shlex
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Optional

from src.logging_config import logger


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
            logger.trace(f"시스템 프롬프트 로드됨 - length={len(content)}")
            return content
        logger.trace("시스템 프롬프트 없음")
        return None

    async def _run_command(
        self,
        cmd: list[str],
        timeout: int,
    ) -> tuple[str, str, int]:
        """Execute command and return (stdout, stderr, returncode)."""
        cmd_preview = " ".join(cmd[:5]) + f" ... ({len(cmd)} parts)"
        logger.trace(f"_run_command() - cmd={cmd_preview}")
        logger.trace(f"timeout={timeout}초")

        logger.trace("subprocess 생성 중")
        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        logger.trace(f"subprocess 생성됨 - pid={process.pid}")

        logger.trace("프로세스 실행 대기 중")
        stdout, stderr = await asyncio.wait_for(
            process.communicate(),
            timeout=timeout,
        )
        stdout_str = stdout.decode("utf-8").strip()
        stderr_str = stderr.decode("utf-8").strip()

        logger.trace(f"프로세스 완료 - returncode={process.returncode}")
        logger.trace(f"stdout length={len(stdout_str)}")
        logger.trace(f"stderr length={len(stderr_str)}")

        if stderr_str:
            logger.trace(f"stderr 내용: {stderr_str[:200]}")

        return (stdout_str, stderr_str, process.returncode)

    async def create_session(self) -> Optional[str]:
        """Create a new Claude session and return session_id."""
        logger.trace("create_session() 시작")
        logger.info("새 Claude 세션 생성 중")

        response = await self.chat("answer 'hi'", None)

        if response.error:
            logger.error(f"세션 생성 실패: {response.error.value}")
            return None

        logger.info(f"새 세션 생성됨: {response.session_id}")
        logger.trace(f"응답: {response.text[:100] if response.text else '(없음)'}")
        return response.session_id

    async def chat(
        self,
        message: str,
        session_id: Optional[str] = None,
        model: Optional[str] = None,
    ) -> ChatResponse:
        """
        Send a message to Claude.

        Args:
            message: User message
            session_id: Claude's session ID (always use --resume if provided)
            model: Model to use (opus, sonnet, haiku)

        Returns:
            ChatResponse with text, error, and session_id
        """
        short_msg = message[:50] + "..." if len(message) > 50 else message
        logger.trace(f"chat() 시작 - msg='{short_msg}'")
        logger.trace(f"session_id={session_id[:8] if session_id else 'None'}, model={model}")

        cmd = self._build_command(message, session_id, model)
        logger.trace(f"명령어 생성됨 - {len(cmd)} parts")

        try:
            logger.trace("CLI 실행 시작")
            output, error, returncode = await self._run_command(cmd, self.timeout)

            logger.trace(f"CLI 결과 - returncode={returncode}")

            if returncode != 0:
                # 에러 상세 로깅 - stdout, stderr 둘 다 출력
                logger.error(f"Claude CLI 비정상 종료 - returncode={returncode}")
                logger.error(f"  stderr: {error if error else '(비어있음)'}")
                logger.error(f"  stdout: {output[:500] if output else '(비어있음)'}")
                logger.error(f"  session_id: {session_id[:8] if session_id else 'None'}")
                logger.error(f"  message: {short_msg}")

                # 실행한 명령어 (메시지 내용 제외)
                cmd_preview = " ".join(cmd[:-1])  # 마지막 인자(메시지) 제외
                logger.debug(f"  command: {cmd_preview} <message>")

                if error and ("not found" in error.lower() or "invalid" in error.lower()):
                    logger.warning("세션을 찾을 수 없음")
                    return ChatResponse("", ChatError.SESSION_NOT_FOUND, None)

                # 에러 메시지 결합 (둘 다 있으면 합침)
                error_detail = error or output or "(오류 내용 없음)"
                return ChatResponse(error_detail, ChatError.CLI_ERROR, None)

            # JSON 파싱
            logger.trace("JSON 파싱 시도")
            try:
                data = json.loads(output)
                result = data.get("result", "(응답 없음)")
                new_session_id = data.get("session_id")

                logger.trace(f"파싱 성공 - session_id={new_session_id}")
                logger.trace(f"result length={len(result)}")
                logger.info(f"Claude 응답 - session_id={new_session_id}")

                return ChatResponse(result, None, new_session_id)

            except json.JSONDecodeError as e:
                # JSON 파싱 실패 시 원본 반환
                logger.warning(f"JSON 파싱 실패: {e}")
                logger.trace(f"원본 output: {output[:200]}")
                return ChatResponse(output or "(응답 없음)", None, None)

        except asyncio.TimeoutError:
            logger.error(f"Claude CLI 타임아웃 - {self.timeout}초 초과")
            return ChatResponse("", ChatError.TIMEOUT, None)

        except Exception as e:
            logger.exception(f"Claude CLI 오류: {e}")
            return ChatResponse("", ChatError.CLI_ERROR, None)

    def _build_command(
        self,
        message: str,
        session_id: Optional[str] = None,
        model: Optional[str] = None,
    ) -> list[str]:
        """Build Claude CLI command."""
        logger.trace(f"_build_command() - session={session_id[:8] if session_id else 'None'}, model={model}")

        cmd = list(self.command_parts)

        # 모델 지정
        if model:
            cmd.extend(["--model", model])
            logger.trace(f"--model {model} 옵션 추가됨")

        # 세션이 있으면 항상 resume 사용
        if session_id:
            cmd.extend(["--resume", session_id])
            logger.trace("--resume 옵션 추가됨")

        # JSON 출력 (session_id 파싱용)
        cmd.extend(["--print", "--output-format", "json"])
        logger.trace("JSON 출력 옵션 추가됨")

        if self.system_prompt:
            cmd.extend(["--system-prompt", self.system_prompt])
            logger.trace("시스템 프롬프트 옵션 추가됨")

        cmd.append(message)
        logger.trace(f"최종 명령어 길이: {len(cmd)} parts")

        return cmd

    async def summarize(self, questions: list[str], max_questions: int = 10) -> str:
        """Generate a summary of conversation questions."""
        logger.trace(f"summarize() - questions={len(questions)}, max={max_questions}")

        if not questions:
            logger.trace("질문 없음")
            return "(내용 없음)"

        history_text = "\n".join(f"- {q[:100]}" for q in questions[:max_questions])
        logger.trace(f"히스토리 텍스트 생성됨 - length={len(history_text)}")

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

        logger.trace("요약 명령어 실행")

        try:
            output, _, _ = await self._run_command(cmd, timeout=60)
            result = output[:300] if output else "(요약 실패)"
            logger.trace(f"요약 완료 - length={len(result)}")
            return result

        except Exception as e:
            logger.warning(f"요약 실패: {e}")
            first_q = questions[0][:50]
            return f'"{first_q}..."'
