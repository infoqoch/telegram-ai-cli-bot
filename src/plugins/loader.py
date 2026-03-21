"""Plugin loader with safe loading and hot reload support."""

import importlib.util
import os
import re
import sys
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, Optional

from src.logging_config import logger
from src.plugins.storage import PluginDatabase
from src.repository.adapters import RepositoryPluginDatabase

if TYPE_CHECKING:
    from src.repository import Repository


@dataclass
class PluginResult:
    """플러그인 실행 결과."""
    handled: bool  # 플러그인이 메시지를 처리했는지
    response: Optional[str] = None  # 응답 메시지
    error: Optional[str] = None  # 에러 메시지
    reply_markup: Optional[any] = None  # InlineKeyboardMarkup 등


@dataclass
class ScheduledAction:
    """플러그인 스케줄 가능 액션."""
    name: str  # 액션 식별자 (e.g., "morning_check")
    description: str  # 표시용 설명 (e.g., "오전 할일 체크")
    recommended_hour: int | None = None  # 추천 시간 (None이면 interval 모드)
    recommended_minute: int | None = None  # 추천 분 (hour=None이면 */minute 간격)


@dataclass
class PluginInteraction:
    """Ephemeral plugin-owned interaction captured via a ForceReply prompt."""

    plugin_name: str
    chat_id: int
    action: str = "force_reply"
    state: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class PluginSystemJobContext:
    """Runtime services exposed to plugins that register system jobs."""

    app: Any
    maintainer_chat_id: Optional[int] = None


PLUGIN_SURFACE_CATALOG = "catalog"
PLUGIN_SURFACE_MAIN_MENU = "main_menu"
PluginSurface = Literal["catalog", "main_menu"]


@dataclass(frozen=True)
class PluginMenuEntry:
    """Declarative menu placement metadata for one plugin launcher."""

    label: str
    surfaces: tuple[PluginSurface, ...] = (PLUGIN_SURFACE_CATALOG,)
    priority: int = 100
    default_promoted: bool = False

    def supports(self, surface: PluginSurface) -> bool:
        """Return whether this entry should appear on one UI surface."""
        return surface in self.surfaces


class Plugin(ABC):
    """플러그인 기본 클래스."""

    name: str = "base"
    description: str = "Base plugin"
    display_name: str = ""  # Human-readable label for AI Work UI (default: name.capitalize())
    usage: str = "Usage not defined."
    MENU_ENTRY: PluginMenuEntry | None = None

    CALLBACK_PREFIX: str = ""  # 빈 문자열이면 콜백 미지원
    FORCE_REPLY_MARKER: str = ""  # 빈 문자열이면 ForceReply 미지원

    # Repository 인스턴스 (PluginLoader가 주입)
    _repository: Optional["Repository"] = None
    _storage: Any = None

    @property
    def repository(self):
        """Repository 인스턴스 반환."""
        return self._repository

    @property
    def storage(self) -> Any:
        """Plugin-scoped storage adapter lazily built from the runtime repository."""
        if self._storage is None and self._repository is not None:
            self._storage = self.build_storage(self._repository)
        return self._storage

    def bind_runtime(self, repository: Optional["Repository"]) -> None:
        """Bind or rebind runtime services injected by the plugin loader."""
        self._repository = repository
        self._storage = None

    def build_storage(self, repository: "Repository") -> Any:
        """Create a plugin-owned persistence adapter."""
        return repository

    def get_schema(self) -> str:
        """플러그인 전용 DDL을 반환. 오버라이드하여 사용."""
        return ""

    @abstractmethod
    async def can_handle(self, message: str, chat_id: int) -> bool:
        """이 플러그인이 메시지를 처리할 수 있는지 확인."""
        pass

    @abstractmethod
    async def handle(self, message: str, chat_id: int) -> PluginResult:
        """메시지 처리."""
        pass

    async def open_launcher(self, chat_id: int) -> PluginResult:
        """Open the plugin's root launcher screen."""
        return await self.handle(self.name, chat_id)

    def handle_callback(self, callback_data: str, chat_id: int) -> dict:
        """콜백 처리. 오버라이드하여 사용."""
        raise NotImplementedError

    async def handle_callback_async(self, callback_data: str, chat_id: int) -> dict:
        """비동기 콜백 처리. 기본적으로 sync handle_callback을 호출."""
        return self.handle_callback(callback_data, chat_id)

    def handle_force_reply(self, message: str, chat_id: int) -> dict:
        """ForceReply 응답 처리. 오버라이드하여 사용."""
        raise NotImplementedError

    def handle_interaction(
        self,
        message: str,
        chat_id: int,
        interaction: Optional[PluginInteraction] = None,
    ) -> dict:
        """Handle one plugin-owned interaction started by the core runtime."""
        del interaction
        return self.handle_force_reply(message, chat_id)

    def get_scheduled_actions(self) -> list[ScheduledAction]:
        """스케줄 가능한 액션 목록. 오버라이드하여 사용."""
        return []

    def get_menu_entry(self) -> PluginMenuEntry:
        """Return menu placement metadata for launcher surfaces."""
        if self.MENU_ENTRY is not None:
            return self.MENU_ENTRY

        label = self.display_name or self.name.replace("_", " ").title()
        return PluginMenuEntry(label=label)

    async def execute_scheduled_action(self, action_name: str, chat_id: int) -> str | dict | None:
        """스케줄된 액션 실행. str(HTML), dict(text, reply_markup), 또는 None(전송 스킵) 반환."""
        raise NotImplementedError(f"Action '{action_name}' not implemented")

    def register_system_jobs(self, context: PluginSystemJobContext) -> None:
        """Register plugin-owned system jobs into the shared app runtime."""
        del context

    # AI context
    ai_context_file: str = "ai_context.md"

    def _load_ai_context_file(self) -> str:
        """Load static AI context description from markdown file."""
        module_file = getattr(self, "_module_file", None)
        if module_file:
            plugin_dir = Path(module_file).parent
        else:
            import inspect

            plugin_dir = Path(inspect.getfile(self.__class__)).parent
        context_path = plugin_dir / self.ai_context_file
        if context_path.exists():
            return context_path.read_text(encoding="utf-8")
        return ""

    async def get_ai_context(self, chat_id: int) -> str:
        """Return full AI context: static description + dynamic data.

        Override get_ai_dynamic_context() to provide live data.
        """
        static = self._load_ai_context_file()
        dynamic = await self.get_ai_dynamic_context(chat_id)
        if dynamic:
            return f"{static}\n\n[현재 데이터]\n{dynamic}"
        return static

    async def get_ai_dynamic_context(self, chat_id: int) -> str:
        """Override to provide dynamic context data from DB. Default: empty."""
        return ""


class PluginLoader:
    """안전한 플러그인 로더."""

    MAIN_MENU_OVERRIDE_ENV = "BOT_MAIN_MENU_PLUGINS"

    def __init__(self, base_dir: Path, repository: any = None):
        logger.trace(f"PluginLoader.__init__() - base_dir={base_dir}")
        self.base_dir = base_dir
        self.plugins: list[Plugin] = []
        self._loaded_modules: dict[str, any] = {}
        self._repository = repository
        self._database: Optional[PluginDatabase] = None
        self.set_repository(repository)

    def set_repository(self, repository: any) -> None:
        """Repository 설정 및 모든 플러그인에 주입."""
        self._repository = repository
        self._database = RepositoryPluginDatabase(repository) if repository is not None else None
        for plugin in self.plugins:
            plugin.bind_runtime(repository)

    def load_all(self) -> list[str]:
        """모든 플러그인 로드 (builtin + custom).

        Returns:
            로드된 플러그인 이름 목록
        """
        loaded = []
        logger.trace(f"load_all() start - base_dir={self.base_dir}")

        # 충돌 감지용 레지스트리 (name/prefix/marker → 등록한 플러그인 name)
        registered_names: dict[str, str] = {}
        registered_callback_prefixes: dict[str, str] = {}
        registered_force_reply_markers: dict[str, str] = {}

        def try_register(plugin: Plugin, location: str) -> bool:
            """플러그인 충돌 여부 확인 후 등록. 충돌 시 False 반환."""
            source_group = location.split("/", 1)[0]

            # name 중복 체크
            if plugin.name in registered_names:
                logger.warning(
                    f"Plugin '{plugin.name}' skipped: name '{plugin.name}' already registered by '{registered_names[plugin.name]}'"
                )
                return False

            # CALLBACK_PREFIX 중복 체크 (빈 문자열 제외)
            if plugin.CALLBACK_PREFIX and plugin.CALLBACK_PREFIX in registered_callback_prefixes:
                existing = registered_callback_prefixes[plugin.CALLBACK_PREFIX]
                logger.warning(
                    f"Plugin '{plugin.name}' skipped: CALLBACK_PREFIX '{plugin.CALLBACK_PREFIX}' already registered by '{existing}'"
                )
                return False

            # FORCE_REPLY_MARKER 중복 체크 (빈 문자열 제외)
            if plugin.FORCE_REPLY_MARKER and plugin.FORCE_REPLY_MARKER in registered_force_reply_markers:
                existing = registered_force_reply_markers[plugin.FORCE_REPLY_MARKER]
                logger.warning(
                    f"Plugin '{plugin.name}' skipped: FORCE_REPLY_MARKER '{plugin.FORCE_REPLY_MARKER}' already registered by '{existing}'"
                )
                return False

            # 충돌 없음 → 등록
            registered_names[plugin.name] = plugin.name
            if plugin.CALLBACK_PREFIX:
                registered_callback_prefixes[plugin.CALLBACK_PREFIX] = plugin.name
            if plugin.FORCE_REPLY_MARKER:
                registered_force_reply_markers[plugin.FORCE_REPLY_MARKER] = plugin.name

            plugin._source_group = source_group
            plugin._source_location = location
            self.plugins.append(plugin)
            loaded.append(location)
            logger.info(f"plugin loaded: {location}")
            return True

        # builtin 먼저, custom 나중에 (덮어쓰기 가능)
        for plugin_dir in ["builtin", "custom"]:
            dir_path = self.base_dir / "plugins" / plugin_dir
            if not dir_path.exists():
                logger.trace(f"plugin directory not found: {dir_path}")
                continue

            logger.trace(f"scanning plugin directory: {dir_path}")

            # 1. 디렉토리 기반 플러그인 로드 (우선)
            for item in dir_path.iterdir():
                if item.is_dir() and not item.name.startswith("_"):
                    init_file = item / "__init__.py"
                    plugin_file = item / "plugin.py"
                    if init_file.exists() or plugin_file.exists():
                        logger.trace(f"plugin package found: {item.name}")
                        plugin = self._load_plugin_from_package(item)
                        if plugin:
                            if not try_register(plugin, f"{plugin_dir}/{plugin.name}"):
                                pass  # 충돌 경고는 try_register 내부에서 출력
                        else:
                            logger.warning(f"plugin load failed: {item.name}")

            # 2. 파일 기반 플러그인 로드
            for py_file in dir_path.glob("*.py"):
                if py_file.name.startswith("_"):
                    continue

                logger.trace(f"plugin file found: {py_file.name}")
                plugin = self._load_plugin_safe(py_file)
                if plugin:
                    try_register(plugin, f"{plugin_dir}/{plugin.name}")

        logger.info(f"plugin load complete: {len(loaded)} plugins")
        logger.trace(f"loaded plugins: {loaded}")

        # 플러그인 스키마 초기화
        self._init_plugin_schemas()

        return loaded

    def _init_plugin_schemas(self) -> None:
        """로드된 플러그인의 DDL을 실행하여 테이블 생성."""
        if not self._database:
            logger.trace("no repository - skipping plugin schema init")
            return

        for plugin in self.plugins:
            schema = plugin.get_schema()
            if schema:
                try:
                    self._database.executescript(schema)
                    logger.trace(f"plugin schema init: {plugin.name}")
                except Exception as e:
                    logger.error(f"plugin schema failed ({plugin.name}): {e}")

    def _load_plugin_from_package(self, package_path: Path) -> Optional[Plugin]:
        """디렉토리 기반 플러그인 로드.

        Args:
            package_path: 플러그인 패키지 디렉토리 (예: plugins/builtin/memo/)

        Returns:
            Plugin 인스턴스 또는 None (실패 시)
        """
        logger.trace(f"_load_plugin_from_package() - path={package_path}")

        # plugin.py 직접 로드 (상대 import 문제 회피)
        plugin_file = package_path / "plugin.py"
        if plugin_file.exists():
            logger.trace(f"plugin.py found - attempting load")
            return self._load_plugin_safe(plugin_file)

        # plugin.py가 없으면 __init__.py에서 직접 정의된 경우 시도
        init_file = package_path / "__init__.py"
        logger.trace(f"attempting load from __init__.py")

        try:
            module_name, module = self._load_module_from_file(init_file)

            # Plugin 클래스 찾기
            logger.trace("searching for Plugin class")
            for attr_name in dir(module):
                attr = getattr(module, attr_name)
                if (
                    isinstance(attr, type)
                    and issubclass(attr, Plugin)
                    and attr is not Plugin
                ):
                    plugin = attr()
                    plugin._base_dir = self.base_dir
                    plugin._module_name = module_name
                    plugin._module_file = init_file
                    plugin.bind_runtime(self._repository)
                    logger.trace(f"Plugin class found: {attr_name} -> {plugin.name}")
                    return plugin

            logger.warning(f"no Plugin class found: {package_path}")
            return None

        except SyntaxError as e:
            logger.error(f"plugin syntax error: {package_path} - {e}")
            return None
        except Exception as e:
            logger.error(f"plugin load failed: {package_path} - {e}", exc_info=True)
            return None

    def _load_plugin_safe(self, file_path: Path) -> Optional[Plugin]:
        """안전하게 플러그인 로드 (실패해도 봇 계속 동작).

        Args:
            file_path: 플러그인 파일 경로

        Returns:
            Plugin 인스턴스 또는 None (실패 시)
        """
        logger.trace(f"_load_plugin_safe() - file={file_path}")

        try:
            module_name, module = self._load_module_from_file(file_path)

            # Plugin 클래스 찾기
            logger.trace("searching for Plugin class")
            for attr_name in dir(module):
                attr = getattr(module, attr_name)
                if (
                    isinstance(attr, type)
                    and issubclass(attr, Plugin)
                    and attr is not Plugin
                ):
                    plugin = attr()
                    plugin._base_dir = self.base_dir  # 데이터 디렉토리용
                    plugin._module_name = module_name
                    plugin._module_file = file_path
                    plugin.bind_runtime(self._repository)
                    logger.trace(f"Plugin instance created: {plugin.name} (class: {attr_name})")
                    return plugin

            logger.warning(f"no Plugin class found: {file_path}")
            return None

        except SyntaxError as e:
            logger.error(f"plugin syntax error: {file_path} - {e}")
            return None
        except Exception as e:
            logger.error(f"plugin load failed: {file_path} - {e}", exc_info=True)
            return None

    def _build_module_name(self, file_path: Path) -> str:
        """Build a deterministic unique module name for a plugin file."""
        resolved = file_path.resolve()
        try:
            relative = resolved.relative_to(self.base_dir.resolve())
            stem = "_".join(relative.with_suffix("").parts)
        except ValueError:
            stem = resolved.stem
        sanitized = re.sub(r"[^0-9a-zA-Z_]", "_", stem)
        return f"_dynamic_plugin_{sanitized}"

    def _load_module_from_file(self, file_path: Path) -> tuple[str, Any]:
        """Load a module from file and register it in sys.modules for inspect/reload."""
        module_name = self._build_module_name(file_path)
        spec = importlib.util.spec_from_file_location(module_name, file_path)
        if not spec or not spec.loader:
            raise ImportError(f"spec creation failed: {file_path}")

        logger.trace(f"executing module: {module_name}")
        module = importlib.util.module_from_spec(spec)
        sys.modules.pop(module_name, None)
        sys.modules[module_name] = module

        try:
            spec.loader.exec_module(module)
        except Exception:
            sys.modules.pop(module_name, None)
            self._loaded_modules.pop(module_name, None)
            raise

        self._loaded_modules[module_name] = module
        logger.trace(f"module executed: {module_name}")
        return module_name, module

    def reload_plugin(self, name: str) -> bool:
        """특정 플러그인 핫 리로드 (파일 + 패키지 지원).

        Args:
            name: 플러그인 이름

        Returns:
            성공 여부
        """
        logger.trace(f"reload_plugin() - name={name}")

        existing_plugins = [p for p in self.plugins if p.name == name]
        self._invalidate_module_cache(name, existing_plugins)

        # 기존 플러그인 제거
        self.plugins = [p for p in self.plugins if p.name != name]
        logger.trace("existing plugin removed")

        # 다시 로드 시도
        for plugin_dir in ["builtin", "custom"]:
            dir_base = self.base_dir / "plugins" / plugin_dir

            # 1. 디렉토리 패키지 기반 시도
            package_path = dir_base / name
            if package_path.is_dir():
                plugin = self._load_plugin_from_package(package_path)
                if plugin:
                    plugin._source_group = plugin_dir
                    plugin._source_location = f"{plugin_dir}/{plugin.name}"
                    self.plugins.append(plugin)
                    logger.info(f"plugin reloaded (package): {name}")
                    return True

            # 2. 단일 파일 기반 시도
            file_path = dir_base / f"{name}.py"
            if file_path.exists():
                plugin = self._load_plugin_safe(file_path)
                if plugin:
                    plugin._source_group = plugin_dir
                    plugin._source_location = f"{plugin_dir}/{plugin.name}"
                    self.plugins.append(plugin)
                    logger.info(f"plugin reloaded (file): {name}")
                    return True

        logger.warning(f"plugin reload failed: {name}")
        return False

    def _invalidate_module_cache(self, name: str, existing_plugins: Optional[list[Plugin]] = None) -> None:
        """모듈 캐시 제거 (핫 리로드 시 이전 코드가 남는 문제 방지)."""
        plugins = existing_plugins if existing_plugins is not None else [p for p in self.plugins if p.name == name]
        module_names = {name, "plugin"}
        module_names.update(
            module_name
            for module_name in (getattr(plugin, "_module_name", None) for plugin in plugins)
            if module_name
        )

        for module_name in module_names:
            self._loaded_modules.pop(module_name, None)

        modules_to_remove = [
            key for key in sys.modules
            if key in module_names or any(key.startswith(f"{module_name}.") for module_name in module_names)
        ]
        for mod_key in modules_to_remove:
            del sys.modules[mod_key]

        if modules_to_remove:
            logger.trace(f"module cache cleared: {modules_to_remove}")

    def reload_all(self) -> tuple[list[str], list[str]]:
        """모든 플러그인 리로드.

        Returns:
            (성공 목록, 실패 목록)
        """
        plugin_names = [p.name for p in self.plugins]
        success = []
        failed = []

        for name in plugin_names:
            if self.reload_plugin(name):
                success.append(name)
            else:
                failed.append(name)

        return success, failed

    def register_system_jobs(self, app: Any, maintainer_chat_id: Optional[int]) -> None:
        """Let plugins register their own background jobs without core special-casing."""
        context = PluginSystemJobContext(app=app, maintainer_chat_id=maintainer_chat_id)
        for plugin in self.plugins:
            try:
                plugin.register_system_jobs(context)
            except Exception as exc:
                logger.error(f"plugin system job registration failed ({plugin.name}): {exc}", exc_info=True)

    async def process_message(self, message: str, chat_id: int) -> Optional[PluginResult]:
        """메시지를 플러그인으로 처리 시도.

        Args:
            message: 사용자 메시지
            chat_id: 채팅 ID

        Returns:
            PluginResult (처리됨) 또는 None (처리 안됨)
        """
        short_msg = message[:50] + "..." if len(message) > 50 else message
        logger.trace(f"process_message() - msg='{short_msg}', chat_id={chat_id}")
        logger.trace(f"plugin count: {len(self.plugins)}")

        for plugin in self.plugins:
            try:
                logger.trace(f"checking plugin: {plugin.name}")
                can_handle = await plugin.can_handle(message, chat_id)
                logger.trace(f"{plugin.name}.can_handle() = {can_handle}")

                if can_handle:
                    logger.trace(f"{plugin.name}.handle() called")
                    result = await plugin.handle(message, chat_id)
                    logger.trace(f"handle result - handled={result.handled}, response_len={len(result.response) if result.response else 0}")

                    if result.handled:
                        logger.info(f"plugin handled: {plugin.name}")
                        return result

            except Exception as e:
                logger.error(f"plugin execution error ({plugin.name}): {e}", exc_info=True)
                # 플러그인 오류가 봇 전체에 영향 주지 않도록
                continue

        logger.trace("no plugin match - passing to Claude")
        return None

    def get_plugin_list(self) -> list[dict]:
        """로드된 플러그인 목록 반환."""
        logger.trace("get_plugin_list()")
        return [
            {"name": p.name, "description": p.description}
            for p in self.plugins
        ]

    @classmethod
    def _parse_main_menu_override(cls) -> list[str] | None:
        """Return one ordered main-menu override list from env, if configured."""
        raw = os.getenv(cls.MAIN_MENU_OVERRIDE_ENV)
        if raw is None:
            return None
        return [name.strip() for name in raw.split(",") if name.strip()]

    def get_plugins_for_surface(self, surface: PluginSurface) -> list[Plugin]:
        """Return plugins eligible for one launcher surface."""
        eligible = [plugin for plugin in self.plugins if plugin.get_menu_entry().supports(surface)]

        if surface == PLUGIN_SURFACE_MAIN_MENU:
            override_names = self._parse_main_menu_override()
            if override_names is not None:
                by_name = {plugin.name: plugin for plugin in eligible}
                ordered: list[Plugin] = []
                missing: list[str] = []
                seen: set[str] = set()

                for name in override_names:
                    if name in seen:
                        continue
                    seen.add(name)
                    plugin = by_name.get(name)
                    if plugin is None:
                        missing.append(name)
                        continue
                    ordered.append(plugin)

                if missing:
                    logger.warning(
                        f"{self.MAIN_MENU_OVERRIDE_ENV} ignored unknown/ineligible plugins: {', '.join(missing)}"
                    )
                return ordered

            eligible = [plugin for plugin in eligible if plugin.get_menu_entry().default_promoted]

        return sorted(
            eligible,
            key=lambda plugin: (plugin.get_menu_entry().priority, plugin.name),
        )

    def get_plugin_for_callback(self, callback_data: str) -> Optional[Plugin]:
        """callback_data의 prefix로 플러그인 찾기."""
        for plugin in self.plugins:
            if plugin.CALLBACK_PREFIX and callback_data.startswith(plugin.CALLBACK_PREFIX):
                return plugin
        return None

    def get_plugin_by_name(self, name: str) -> Optional[Plugin]:
        """이름으로 플러그인 찾기."""
        logger.trace(f"get_plugin_by_name() - name={name}")

        for plugin in self.plugins:
            if plugin.name == name:
                logger.trace(f"plugin found: {name}")
                return plugin

        logger.trace(f"plugin not found: {name}")
        return None
