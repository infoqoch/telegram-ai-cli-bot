"""플러그인 로더 충돌 감지 테스트.

PluginLoader.load_all() 시점에 name, CALLBACK_PREFIX, FORCE_REPLY_MARKER
중복을 올바르게 감지하고 두 번째 플러그인을 거부하는지 검증한다.
"""

import io
import textwrap
import tempfile
from pathlib import Path

import pytest
from loguru import logger

from src.plugins.loader import Plugin, PluginLoader, PluginResult


@pytest.fixture
def log_capture():
    """loguru 경고 로그를 캡처하는 fixture."""
    buf = io.StringIO()
    handler_id = logger.add(buf, level="WARNING", format="{message}")
    yield buf
    logger.remove(handler_id)


# ---------------------------------------------------------------------------
# 헬퍼: 임시 디렉토리에 플러그인 파일 작성
# ---------------------------------------------------------------------------

def _write_plugin(base_dir: Path, plugin_dir: str, filename: str, content: str) -> None:
    """base_dir/plugins/{plugin_dir}/{filename} 에 플러그인 소스를 작성한다."""
    target_dir = base_dir / "plugins" / plugin_dir
    target_dir.mkdir(parents=True, exist_ok=True)
    (target_dir / filename).write_text(textwrap.dedent(content))


def _make_plugin_src(
    class_name: str,
    plugin_name: str,
    callback_prefix: str = "",
    force_reply_marker: str = "",
) -> str:
    """단순 Plugin 서브클래스 소스를 생성한다."""
    return f"""\
from src.plugins.loader import Plugin, PluginResult

class {class_name}(Plugin):
    name = "{plugin_name}"
    description = "Test plugin {plugin_name}"
    usage = "test"
    CALLBACK_PREFIX = "{callback_prefix}"
    FORCE_REPLY_MARKER = "{force_reply_marker}"

    async def can_handle(self, message: str, chat_id: int) -> bool:
        return False

    async def handle(self, message: str, chat_id: int) -> PluginResult:
        return PluginResult(handled=False)
"""


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def base_dir():
    """플러그인 디렉토리 구조가 포함된 임시 베이스 디렉토리."""
    with tempfile.TemporaryDirectory() as tmpdir:
        path = Path(tmpdir)
        # builtin / custom 디렉토리 사전 생성
        (path / "plugins" / "builtin").mkdir(parents=True)
        (path / "plugins" / "custom").mkdir(parents=True)
        yield path


# ---------------------------------------------------------------------------
# name 중복 테스트
# ---------------------------------------------------------------------------

class TestNameConflict:
    """같은 name을 가진 플러그인이 두 번 로드될 때 두 번째를 거부한다."""

    def test_duplicate_name_second_skipped(self, base_dir):
        """같은 name: 첫 번째만 로드되고 두 번째는 거부된다."""
        _write_plugin(base_dir, "builtin", "alpha.py",
                      _make_plugin_src("AlphaPlugin", "myname"))
        _write_plugin(base_dir, "custom", "beta.py",
                      _make_plugin_src("BetaPlugin", "myname"))  # 이름 충돌

        loader = PluginLoader(base_dir)
        loaded = loader.load_all()

        # 이름이 "myname"인 플러그인은 하나만 로드되어야 한다
        assert len([p for p in loader.plugins if p.name == "myname"]) == 1
        assert len(loaded) == 1

    def test_duplicate_name_warning_logged(self, base_dir, log_capture):
        """name 충돌 시 경고 로그에 두 플러그인 이름이 포함된다."""
        _write_plugin(base_dir, "builtin", "alpha.py",
                      _make_plugin_src("AlphaPlugin", "conflict_name"))
        _write_plugin(base_dir, "custom", "beta.py",
                      _make_plugin_src("BetaPlugin", "conflict_name"))

        loader = PluginLoader(base_dir)
        loader.load_all()

        logs = log_capture.getvalue()
        assert "conflict_name" in logs, f"충돌 경고 없음. 로그: {logs}"

    def test_different_names_both_loaded(self, base_dir):
        """이름이 다르면 둘 다 정상 로드된다."""
        _write_plugin(base_dir, "builtin", "alpha.py",
                      _make_plugin_src("AlphaPlugin", "alpha"))
        _write_plugin(base_dir, "builtin", "beta.py",
                      _make_plugin_src("BetaPlugin", "beta"))

        loader = PluginLoader(base_dir)
        loaded = loader.load_all()

        assert len(loader.plugins) == 2
        assert len(loaded) == 2


# ---------------------------------------------------------------------------
# CALLBACK_PREFIX 중복 테스트
# ---------------------------------------------------------------------------

class TestCallbackPrefixConflict:
    """같은 CALLBACK_PREFIX를 가진 플러그인이 두 번 로드될 때 두 번째를 거부한다."""

    def test_duplicate_callback_prefix_second_skipped(self, base_dir):
        """같은 CALLBACK_PREFIX: 첫 번째만 로드된다."""
        _write_plugin(base_dir, "builtin", "first.py",
                      _make_plugin_src("FirstPlugin", "first", callback_prefix="cb:"))
        _write_plugin(base_dir, "custom", "second.py",
                      _make_plugin_src("SecondPlugin", "second", callback_prefix="cb:"))

        loader = PluginLoader(base_dir)
        loaded = loader.load_all()

        assert len(loaded) == 1
        assert loader.plugins[0].name == "first"

    def test_duplicate_callback_prefix_warning_logged(self, base_dir, log_capture):
        """CALLBACK_PREFIX 충돌 시 경고 로그에 prefix 정보가 포함된다."""
        _write_plugin(base_dir, "builtin", "first.py",
                      _make_plugin_src("FirstPlugin", "first", callback_prefix="dup_cb:"))
        _write_plugin(base_dir, "custom", "second.py",
                      _make_plugin_src("SecondPlugin", "second", callback_prefix="dup_cb:"))

        loader = PluginLoader(base_dir)
        loader.load_all()

        logs = log_capture.getvalue()
        assert "dup_cb:" in logs, f"CALLBACK_PREFIX 충돌 경고 없음. 로그: {logs}"
        assert "first" in logs

    def test_different_callback_prefixes_both_loaded(self, base_dir):
        """CALLBACK_PREFIX가 다르면 둘 다 정상 로드된다."""
        _write_plugin(base_dir, "builtin", "first.py",
                      _make_plugin_src("FirstPlugin", "first", callback_prefix="a:"))
        _write_plugin(base_dir, "builtin", "second.py",
                      _make_plugin_src("SecondPlugin", "second", callback_prefix="b:"))

        loader = PluginLoader(base_dir)
        loaded = loader.load_all()

        assert len(loaded) == 2

    def test_empty_callback_prefix_no_conflict(self, base_dir):
        """빈 CALLBACK_PREFIX("")는 충돌 체크에서 제외된다."""
        _write_plugin(base_dir, "builtin", "first.py",
                      _make_plugin_src("FirstPlugin", "first", callback_prefix=""))
        _write_plugin(base_dir, "builtin", "second.py",
                      _make_plugin_src("SecondPlugin", "second", callback_prefix=""))

        loader = PluginLoader(base_dir)
        loaded = loader.load_all()

        # 빈 prefix는 충돌로 처리되지 않으므로 둘 다 로드
        assert len(loaded) == 2


# ---------------------------------------------------------------------------
# FORCE_REPLY_MARKER 중복 테스트
# ---------------------------------------------------------------------------

class TestForceReplyMarkerConflict:
    """같은 FORCE_REPLY_MARKER를 가진 플러그인이 두 번 로드될 때 두 번째를 거부한다."""

    def test_duplicate_force_reply_marker_second_skipped(self, base_dir):
        """같은 FORCE_REPLY_MARKER: 첫 번째만 로드된다."""
        _write_plugin(base_dir, "builtin", "first.py",
                      _make_plugin_src("FirstPlugin", "first", force_reply_marker="frm_marker"))
        _write_plugin(base_dir, "custom", "second.py",
                      _make_plugin_src("SecondPlugin", "second", force_reply_marker="frm_marker"))

        loader = PluginLoader(base_dir)
        loaded = loader.load_all()

        assert len(loaded) == 1
        assert loader.plugins[0].name == "first"

    def test_duplicate_force_reply_marker_warning_logged(self, base_dir, log_capture):
        """FORCE_REPLY_MARKER 충돌 시 경고 로그에 마커 정보가 포함된다."""
        _write_plugin(base_dir, "builtin", "first.py",
                      _make_plugin_src("FirstPlugin", "first", force_reply_marker="dup_marker"))
        _write_plugin(base_dir, "custom", "second.py",
                      _make_plugin_src("SecondPlugin", "second", force_reply_marker="dup_marker"))

        loader = PluginLoader(base_dir)
        loader.load_all()

        logs = log_capture.getvalue()
        assert "dup_marker" in logs, f"FORCE_REPLY_MARKER 충돌 경고 없음. 로그: {logs}"
        assert "first" in logs

    def test_different_force_reply_markers_both_loaded(self, base_dir):
        """FORCE_REPLY_MARKER가 다르면 둘 다 정상 로드된다."""
        _write_plugin(base_dir, "builtin", "first.py",
                      _make_plugin_src("FirstPlugin", "first", force_reply_marker="marker_a"))
        _write_plugin(base_dir, "builtin", "second.py",
                      _make_plugin_src("SecondPlugin", "second", force_reply_marker="marker_b"))

        loader = PluginLoader(base_dir)
        loaded = loader.load_all()

        assert len(loaded) == 2

    def test_empty_force_reply_marker_no_conflict(self, base_dir):
        """빈 FORCE_REPLY_MARKER("")는 충돌 체크에서 제외된다."""
        _write_plugin(base_dir, "builtin", "first.py",
                      _make_plugin_src("FirstPlugin", "first", force_reply_marker=""))
        _write_plugin(base_dir, "builtin", "second.py",
                      _make_plugin_src("SecondPlugin", "second", force_reply_marker=""))

        loader = PluginLoader(base_dir)
        loaded = loader.load_all()

        # 빈 marker는 충돌로 처리되지 않으므로 둘 다 로드
        assert len(loaded) == 2


# ---------------------------------------------------------------------------
# 복합 시나리오
# ---------------------------------------------------------------------------

class TestWarningMessageContent:
    """경고 메시지에 어떤 플러그인끼리 충돌하는지 명확히 표시하는지 확인한다."""

    def test_warning_contains_both_plugin_names(self, base_dir, log_capture):
        """경고 메시지에 충돌하는 두 플러그인 이름이 모두 포함된다."""
        _write_plugin(base_dir, "builtin", "owner.py",
                      _make_plugin_src("OwnerPlugin", "owner", callback_prefix="shared:"))
        _write_plugin(base_dir, "custom", "intruder.py",
                      _make_plugin_src("IntruderPlugin", "intruder", callback_prefix="shared:"))

        loader = PluginLoader(base_dir)
        loader.load_all()

        logs = log_capture.getvalue()
        # 충돌을 시도한 플러그인과 이미 등록된 플러그인 모두 언급되어야 한다
        assert "intruder" in logs
        assert "owner" in logs
        assert "shared:" in logs


class TestSystemJobRegistration:
    """플러그인 system job 등록은 이름 하드코딩 없이 generic하게 호출된다."""

    def test_loader_registers_system_jobs_for_all_plugins(self, base_dir):
        class JobPlugin(Plugin):
            name = "jobber"

            async def can_handle(self, message: str, chat_id: int) -> bool:
                return False

            async def handle(self, message: str, chat_id: int) -> PluginResult:
                return PluginResult(handled=False)

            def register_system_jobs(self, context) -> None:
                self.seen_context = context

        loader = PluginLoader(base_dir)
        plugin = JobPlugin()
        loader.plugins = [plugin]

        app = object()
        loader.register_system_jobs(app, 12345)

        assert plugin.seen_context.app is app
        assert plugin.seen_context.maintainer_chat_id == 12345
