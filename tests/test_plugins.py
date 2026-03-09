"""플러그인 시스템 테스트.

플러그인 로더 및 개별 플러그인 검증:
- PluginLoader 로딩/리로딩
- 메시지 처리 흐름
- MemoPlugin 기능
"""

import tempfile
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from src.plugins.loader import Plugin, PluginLoader, PluginResult
from src.repository import init_repository, shutdown_repository, reset_connection


class MockPlugin(Plugin):
    """테스트용 모의 플러그인."""

    name = "mock"
    description = "Mock plugin for testing"
    usage = "Test usage"

    def __init__(self, can_handle_result: bool = True, handle_result: str = "handled"):
        self._can_handle_result = can_handle_result
        self._handle_result = handle_result

    async def can_handle(self, message: str, chat_id: int) -> bool:
        return self._can_handle_result

    async def handle(self, message: str, chat_id: int) -> PluginResult:
        return PluginResult(handled=True, response=self._handle_result)


class TestPluginResult:
    """PluginResult 데이터클래스 테스트."""

    def test_plugin_result_creation(self):
        """기본 생성."""
        result = PluginResult(handled=True, response="응답")

        assert result.handled is True
        assert result.response == "응답"
        assert result.error is None

    def test_plugin_result_with_error(self):
        """에러 포함 생성."""
        result = PluginResult(handled=False, error="오류 발생")

        assert result.handled is False
        assert result.response is None
        assert result.error == "오류 발생"

    def test_plugin_result_defaults(self):
        """기본값 확인."""
        result = PluginResult(handled=True)

        assert result.response is None
        assert result.error is None


class TestPluginLoader:
    """PluginLoader 테스트."""

    @pytest.fixture
    def temp_plugin_dir(self):
        """임시 플러그인 디렉토리 생성."""
        with tempfile.TemporaryDirectory() as tmpdir:
            base_dir = Path(tmpdir)

            # 플러그인 디렉토리 구조 생성
            builtin_dir = base_dir / "plugins" / "builtin"
            builtin_dir.mkdir(parents=True)

            custom_dir = base_dir / "plugins" / "custom"
            custom_dir.mkdir(parents=True)

            yield base_dir

    def test_loader_init(self, temp_plugin_dir):
        """로더 초기화."""
        loader = PluginLoader(temp_plugin_dir)

        assert loader.base_dir == temp_plugin_dir
        assert loader.plugins == []

    def test_load_all_empty(self, temp_plugin_dir):
        """플러그인 없을 때 빈 목록."""
        loader = PluginLoader(temp_plugin_dir)
        loaded = loader.load_all()

        assert loaded == []
        assert loader.plugins == []

    def test_get_plugin_list(self, temp_plugin_dir):
        """플러그인 목록 조회."""
        loader = PluginLoader(temp_plugin_dir)

        # 수동으로 플러그인 추가
        mock_plugin = MockPlugin()
        mock_plugin._base_dir = temp_plugin_dir
        loader.plugins.append(mock_plugin)

        plugin_list = loader.get_plugin_list()

        assert len(plugin_list) == 1
        assert plugin_list[0]["name"] == "mock"
        assert plugin_list[0]["description"] == "Mock plugin for testing"

    def test_get_plugin_by_name_found(self, temp_plugin_dir):
        """이름으로 플러그인 찾기 - 성공."""
        loader = PluginLoader(temp_plugin_dir)

        mock_plugin = MockPlugin()
        mock_plugin._base_dir = temp_plugin_dir
        loader.plugins.append(mock_plugin)

        result = loader.get_plugin_by_name("mock")

        assert result is not None
        assert result.name == "mock"

    def test_get_plugin_by_name_not_found(self, temp_plugin_dir):
        """이름으로 플러그인 찾기 - 실패."""
        loader = PluginLoader(temp_plugin_dir)

        result = loader.get_plugin_by_name("nonexistent")

        assert result is None

    @pytest.mark.asyncio
    async def test_process_message_handled(self, temp_plugin_dir):
        """메시지 처리 - 플러그인이 처리."""
        loader = PluginLoader(temp_plugin_dir)

        mock_plugin = MockPlugin(can_handle_result=True, handle_result="처리됨")
        mock_plugin._base_dir = temp_plugin_dir
        loader.plugins.append(mock_plugin)

        result = await loader.process_message("테스트", 12345)

        assert result is not None
        assert result.handled is True
        assert result.response == "처리됨"

    @pytest.mark.asyncio
    async def test_process_message_not_handled(self, temp_plugin_dir):
        """메시지 처리 - 플러그인이 처리 안 함."""
        loader = PluginLoader(temp_plugin_dir)

        mock_plugin = MockPlugin(can_handle_result=False)
        mock_plugin._base_dir = temp_plugin_dir
        loader.plugins.append(mock_plugin)

        result = await loader.process_message("테스트", 12345)

        assert result is None

    @pytest.mark.asyncio
    async def test_process_message_plugin_error(self, temp_plugin_dir):
        """메시지 처리 - 플러그인 오류 시 다음 플러그인 시도."""
        loader = PluginLoader(temp_plugin_dir)

        # 오류 발생하는 플러그인
        error_plugin = MockPlugin()
        error_plugin._base_dir = temp_plugin_dir
        error_plugin.can_handle = MagicMock(side_effect=Exception("테스트 오류"))
        loader.plugins.append(error_plugin)

        # 정상 플러그인
        normal_plugin = MockPlugin(can_handle_result=True, handle_result="정상")
        normal_plugin._base_dir = temp_plugin_dir
        loader.plugins.append(normal_plugin)

        result = await loader.process_message("테스트", 12345)

        # 오류 플러그인 스킵하고 정상 플러그인 처리
        assert result is not None
        assert result.response == "정상"

    @pytest.mark.asyncio
    async def test_process_message_no_plugins(self, temp_plugin_dir):
        """메시지 처리 - 플러그인 없음."""
        loader = PluginLoader(temp_plugin_dir)

        result = await loader.process_message("테스트", 12345)

        assert result is None


class TestMemoPluginPatterns:
    """MemoPlugin 패턴 테스트 - 단일 진입점 + 버튼 기반 UX."""

    @pytest.fixture
    def memo_plugin(self):
        """MemoPlugin 인스턴스 생성 (Repository 주입)."""
        from plugins.builtin.memo.plugin import MemoPlugin

        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test.db"
            repo = init_repository(db_path)

            plugin = MemoPlugin()
            plugin._base_dir = Path(tmpdir)
            plugin.bind_runtime(repo)
            # 플러그인 스키마 초기화
            repo._conn.executescript(plugin.get_schema())
            repo._conn.commit()

            yield plugin

            shutdown_repository()
            reset_connection()

    @pytest.mark.asyncio
    async def test_can_handle_single_entry_points(self, memo_plugin):
        """단일 진입점 인식 - '메모', 'memo'만."""
        # 정확히 키워드만 인식
        assert await memo_plugin.can_handle("메모", 12345) is True
        assert await memo_plugin.can_handle("memo", 12345) is True
        assert await memo_plugin.can_handle("MEMO", 12345) is True

    @pytest.mark.asyncio
    async def test_can_handle_not_match(self, memo_plugin):
        """단일 진입점 외 메시지는 처리 안함."""
        # 이전 명령어 스타일은 더 이상 처리 안함
        not_match = [
            "내일 회의 메모해줘",
            "메모해줘: 장보기",
            "메모 1 삭제",
            "메모 목록",
        ]
        for msg in not_match:
            result = await memo_plugin.can_handle(msg, 12345)
            assert result is False, f"Should NOT handle: {msg}"

    @pytest.mark.asyncio
    async def test_can_handle_exclude_patterns(self, memo_plugin):
        """제외 패턴 - AI에게 넘김."""
        exclude_messages = [
            "메모란 뭐야",
            "메모가 뭔가요",
            "메모 영어로",
            "메모 어떻게 해",
            "메모의 뜻",
        ]

        for msg in exclude_messages:
            result = await memo_plugin.can_handle(msg, 12345)
            assert result is False, f"Should NOT handle (exclude): {msg}"

    @pytest.mark.asyncio
    async def test_handle_main_menu(self, memo_plugin):
        """메인 메뉴 표시."""
        result = await memo_plugin.handle("메모", 12345)

        assert result.handled is True
        assert "Memo" in result.response
        assert "Saved" in result.response
        assert result.reply_markup is not None  # 버튼 있음

    @pytest.mark.asyncio
    async def test_callback_list_empty(self, memo_plugin):
        """콜백: 빈 메모 목록."""
        result = memo_plugin.handle_callback("memo:list", 12345)

        assert "No saved memos" in result["text"]

    @pytest.mark.asyncio
    async def test_callback_add_prompt(self, memo_plugin):
        """콜백: 메모 추가 ForceReply."""
        result = memo_plugin.handle_callback("memo:add", 12345)

        assert "Add Memo" in result["text"]
        assert result.get("force_reply") is not None

    @pytest.mark.asyncio
    async def test_force_reply_add_memo(self, memo_plugin):
        """ForceReply로 메모 추가."""
        result = memo_plugin.handle_force_reply("테스트 메모 내용", 12345)

        assert "saved" in result["text"]
        assert "테스트 메모 내용" in result["text"]

    @pytest.mark.asyncio
    async def test_callback_list_with_memos(self, memo_plugin):
        """콜백: 메모 있을 때 목록."""
        # ForceReply로 메모 추가
        memo_plugin.handle_force_reply("첫번째 메모", 12345)
        memo_plugin.handle_force_reply("두번째 메모", 12345)

        result = memo_plugin.handle_callback("memo:list", 12345)

        assert "Memo List" in result["text"]
        assert "#1" in result["text"]
        assert "#2" in result["text"]

    @pytest.mark.asyncio
    async def test_callback_delete_confirm(self, memo_plugin):
        """콜백: 삭제 확인."""
        memo_plugin.handle_force_reply("삭제할 메모", 12345)

        result = memo_plugin.handle_callback("memo:del:1", 12345)

        assert "Delete?" in result["text"]
        assert result.get("reply_markup") is not None

    @pytest.mark.asyncio
    async def test_callback_confirm_delete(self, memo_plugin):
        """콜백: 삭제 실행."""
        memo_plugin.handle_force_reply("삭제할 메모", 12345)

        result = memo_plugin.handle_callback("memo:confirm_del:1", 12345)

        assert "Deleted" in result["text"]

    @pytest.mark.asyncio
    async def test_callback_delete_not_found(self, memo_plugin):
        """콜백: 존재하지 않는 메모 삭제."""
        result = memo_plugin.handle_callback("memo:del:999", 12345)

        assert "not found" in result["text"]
