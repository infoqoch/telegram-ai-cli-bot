"""Claude CLI 클라이언트 테스트.

ClaudeClient 클래스의 핵심 기능 검증:
- 명령어 빌드
- 시스템 프롬프트 로딩
- 세션 생성
"""

import asyncio
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.claude.client import ChatError, ChatResponse, ClaudeClient


@pytest.fixture
def client():
    """기본 클라이언트 생성."""
    return ClaudeClient(command="claude", timeout=60)


@pytest.fixture
def client_with_prompt():
    """시스템 프롬프트 포함 클라이언트."""
    with tempfile.NamedTemporaryFile(mode='w', suffix='.md', delete=False) as f:
        f.write("You are a helpful assistant.")
        prompt_path = Path(f.name)

    client = ClaudeClient(
        command="claude --dangerously-skip-permissions",
        system_prompt_file=prompt_path,
        timeout=60,
    )
    yield client
    prompt_path.unlink()


class TestClaudeClient:
    """ClaudeClient 단위 테스트."""

    def test_build_command_basic(self, client):
        """기본 명령어 빌드 확인."""
        cmd = client._build_command("Hello")

        assert cmd[0] == "claude"
        assert "--print" in cmd
        assert "--output-format" in cmd
        assert "json" in cmd
        assert cmd[-1] == "Hello"

    def test_build_command_with_system_prompt(self, client_with_prompt):
        """시스템 프롬프트 포함 명령어 빌드."""
        cmd = client_with_prompt._build_command("Hello")

        assert "--system-prompt" in cmd
        assert "You are a helpful assistant." in cmd

    def test_build_command_with_resume(self, client):
        """세션 재개 명령어 빌드 확인."""
        cmd = client._build_command("Hello", session_id="abc-123")

        assert "--resume" in cmd
        assert "abc-123" in cmd

    def test_command_parsing(self):
        """복잡한 명령어 파싱 확인."""
        client = ClaudeClient(
            command="claude --dangerously-skip-permissions --verbose"
        )

        assert client.command_parts == [
            "claude", "--dangerously-skip-permissions", "--verbose"
        ]

    def test_load_system_prompt_nonexistent(self):
        """존재하지 않는 프롬프트 파일 처리."""
        client = ClaudeClient(
            system_prompt_file=Path("/nonexistent/path.md")
        )

        assert client.system_prompt is None

    @pytest.mark.asyncio
    async def test_summarize_empty_questions(self, client):
        """빈 질문 목록 요약."""
        result = await client.summarize([])
        assert result == "(내용 없음)"


class TestChatError:
    """ChatError Enum 테스트."""

    def test_chat_error_enum_values(self):
        """ChatError enum 값 검증."""
        assert ChatError.TIMEOUT.value == "TIMEOUT"
        assert ChatError.SESSION_NOT_FOUND.value == "SESSION_NOT_FOUND"
        assert ChatError.CLI_ERROR.value == "CLI_ERROR"


class TestChatResponse:
    """ChatResponse Dataclass 테스트."""

    def test_chat_response_creation(self):
        """모든 필드를 포함한 ChatResponse 생성."""
        from src.claude.client import ChatError, ChatResponse

        response = ChatResponse(
            text="Hello, world!",
            error=ChatError.CLI_ERROR,
            session_id="session-123"
        )

        assert response.text == "Hello, world!"
        assert response.error == ChatError.CLI_ERROR
        assert response.session_id == "session-123"

    def test_chat_response_defaults(self):
        """기본값 검증 (error=None, session_id=None)."""
        from src.claude.client import ChatResponse

        response = ChatResponse(text="Test")

        assert response.text == "Test"
        assert response.error is None
        assert response.session_id is None

    def test_chat_response_tuple_unpacking(self):
        """하위 호환성을 위한 tuple 언패킹 지원."""
        from src.claude.client import ChatResponse

        response = ChatResponse(
            text="Success",
            error=None,
            session_id="abc-123"
        )

        text, error, session_id = response

        assert text == "Success"
        assert error is None
        assert session_id == "abc-123"

    def test_chat_response_error_value_in_tuple(self):
        """tuple 언패킹 시 error.value 반환 검증."""
        from src.claude.client import ChatError, ChatResponse

        response = ChatResponse(
            text="",
            error=ChatError.TIMEOUT,
            session_id=None
        )

        text, error, session_id = response

        assert text == ""
        assert error == "TIMEOUT"
        assert session_id is None


class TestRunCommand:
    """_run_command 메서드 테스트."""

    @pytest.mark.asyncio
    async def test_run_command_success(self, client):
        """정상적인 명령어 실행."""
        with patch('asyncio.create_subprocess_exec') as mock_exec:
            mock_process = AsyncMock()
            mock_process.communicate = AsyncMock(
                return_value=(b"output text", b"")
            )
            mock_process.returncode = 0
            mock_exec.return_value = mock_process

            stdout, stderr, returncode = await client._run_command(
                ["echo", "test"],
                timeout=60
            )

            assert stdout == "output text"
            assert stderr == ""
            assert returncode == 0

    @pytest.mark.asyncio
    async def test_run_command_returns_tuple(self, client):
        """_run_command가 (stdout, stderr, returncode) 반환 검증."""
        with patch('asyncio.create_subprocess_exec') as mock_exec:
            mock_process = AsyncMock()
            mock_process.communicate = AsyncMock(
                return_value=(b"out", b"err")
            )
            mock_process.returncode = 1
            mock_exec.return_value = mock_process

            result = await client._run_command(["test"], timeout=60)

            assert isinstance(result, tuple)
            assert len(result) == 3
            assert result == ("out", "err", 1)

    @pytest.mark.asyncio
    async def test_run_command_kills_subprocess_on_timeout(self, client):
        """타임아웃 발생 시 subprocess를 종료한다."""
        with patch('asyncio.create_subprocess_exec') as mock_exec:
            mock_process = AsyncMock()
            mock_process.communicate = AsyncMock(side_effect=asyncio.TimeoutError)
            mock_process.kill = MagicMock()
            mock_exec.return_value = mock_process

            with pytest.raises(asyncio.TimeoutError):
                await client._run_command(["claude", "--print"], timeout=1)

            assert mock_process.kill.call_count == 1
            assert mock_process.communicate.await_count >= 1


class TestChatMethod:
    """chat() 메서드 테스트."""

    @pytest.mark.asyncio
    async def test_chat_returns_chat_response(self, client):
        """chat()이 ChatResponse 반환 검증."""
        from src.claude.client import ChatResponse

        with patch('asyncio.create_subprocess_exec') as mock_exec:
            mock_process = AsyncMock()
            mock_process.communicate = AsyncMock(
                return_value=(
                    b'{"result": "Hi!", "session_id": "s1"}',
                    b""
                )
            )
            mock_process.returncode = 0
            mock_exec.return_value = mock_process

            response = await client.chat("Hello")

            assert isinstance(response, ChatResponse)
            assert response.text == "Hi!"
            assert response.error is None
            assert response.session_id == "s1"

    @pytest.mark.asyncio
    async def test_chat_session_not_found(self, client):
        """SESSION_NOT_FOUND 에러 검증 (stderr에 'not found')."""
        from src.claude.client import ChatError

        with patch('asyncio.create_subprocess_exec') as mock_exec:
            mock_process = AsyncMock()
            mock_process.communicate = AsyncMock(
                return_value=(b"", b"Session not found")
            )
            mock_process.returncode = 1
            mock_exec.return_value = mock_process

            response = await client.chat("Hello", "invalid-session")

            assert response.error == ChatError.SESSION_NOT_FOUND
            assert response.text == ""
            assert response.session_id is None

    @pytest.mark.asyncio
    async def test_chat_timeout(self, client):
        """subprocess timeout은 TIMEOUT 에러로 노출된다."""
        from src.claude.client import ChatError

        with patch.object(client, "_run_command", side_effect=asyncio.TimeoutError):
            response = await client.chat("Hello", "session-123")

        assert response.error == ChatError.TIMEOUT
        assert response.text == ""
        assert response.session_id == "session-123"

    @pytest.mark.asyncio
    async def test_chat_cli_error(self, client):
        """CLI_ERROR 검증 (non-zero return code)."""
        from src.claude.client import ChatError

        with patch('asyncio.create_subprocess_exec') as mock_exec:
            mock_process = AsyncMock()
            mock_process.communicate = AsyncMock(
                return_value=(b"", b"Some error")
            )
            mock_process.returncode = 1
            mock_exec.return_value = mock_process

            response = await client.chat("Hello")

            assert response.error == ChatError.CLI_ERROR
            assert "Some error" in response.text or response.text == "(오류)"

    @pytest.mark.asyncio
    async def test_chat_json_parse_success(self, client):
        """JSON 파싱 성공 및 session_id 추출 검증."""
        with patch('asyncio.create_subprocess_exec') as mock_exec:
            mock_process = AsyncMock()
            mock_process.communicate = AsyncMock(
                return_value=(
                    b'{"result": "Response text", "session_id": "xyz-789"}',
                    b""
                )
            )
            mock_process.returncode = 0
            mock_exec.return_value = mock_process

            response = await client.chat("Test")

            assert response.text == "Response text"
            assert response.session_id == "xyz-789"
            assert response.error is None

    @pytest.mark.asyncio
    async def test_chat_json_parse_failure(self, client):
        """JSON 파싱 실패 시 원본 출력 반환 검증."""
        with patch('asyncio.create_subprocess_exec') as mock_exec:
            mock_process = AsyncMock()
            mock_process.communicate = AsyncMock(
                return_value=(b"Plain text response", b"")
            )
            mock_process.returncode = 0
            mock_exec.return_value = mock_process

            response = await client.chat("Test")

            assert response.text == "Plain text response"
            assert response.error is None
            assert response.session_id is None

class TestCreateSession:
    """create_session() 메서드 테스트."""

    @pytest.mark.asyncio
    async def test_create_session_success(self, client):
        """성공 시 session_id 반환."""
        with patch('asyncio.create_subprocess_exec') as mock_exec:
            mock_process = AsyncMock()
            mock_process.communicate = AsyncMock(
                return_value=(
                    b'{"result": "hi", "session_id": "new-session-123"}',
                    b""
                )
            )
            mock_process.returncode = 0
            mock_exec.return_value = mock_process

            session_id = await client.create_session()

            assert session_id == "new-session-123"

    @pytest.mark.asyncio
    async def test_create_session_failure(self, client):
        """실패 시 None 반환."""
        from src.claude.client import ChatError

        with patch('asyncio.create_subprocess_exec') as mock_exec:
            mock_process = AsyncMock()
            mock_process.communicate = AsyncMock(
                return_value=(b"", b"Error creating session")
            )
            mock_process.returncode = 1
            mock_exec.return_value = mock_process

            session_id = await client.create_session()

            assert session_id is None
