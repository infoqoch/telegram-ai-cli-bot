"""Plugin loader with safe loading and hot reload support."""

import importlib.util
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Optional

from src.logging_config import logger

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


class Plugin(ABC):
    """플러그인 기본 클래스."""

    name: str = "base"
    description: str = "Base plugin"
    usage: str = "Usage not defined."

    CALLBACK_PREFIX: str = ""  # 빈 문자열이면 콜백 미지원
    FORCE_REPLY_MARKER: str = ""  # 빈 문자열이면 ForceReply 미지원

    # Repository 인스턴스 (PluginLoader가 주입)
    _repository: Optional["Repository"] = None

    @property
    def repository(self):
        """Repository 인스턴스 반환."""
        return self._repository

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

    def handle_callback(self, callback_data: str, chat_id: int) -> dict:
        """콜백 처리. 오버라이드하여 사용."""
        raise NotImplementedError

    async def handle_callback_async(self, callback_data: str, chat_id: int) -> dict:
        """비동기 콜백 처리. 기본적으로 sync handle_callback을 호출."""
        return self.handle_callback(callback_data, chat_id)

    def handle_force_reply(self, message: str, chat_id: int) -> dict:
        """ForceReply 응답 처리. 오버라이드하여 사용."""
        raise NotImplementedError

    def get_scheduled_actions(self) -> list[ScheduledAction]:
        """스케줄 가능한 액션 목록. 오버라이드하여 사용."""
        return []

    async def execute_scheduled_action(self, action_name: str, chat_id: int) -> str:
        """스케줄된 액션 실행. 결과 텍스트(HTML) 반환."""
        raise NotImplementedError(f"Action '{action_name}' not implemented")



class PluginLoader:
    """안전한 플러그인 로더."""

    def __init__(self, base_dir: Path, repository: any = None):
        logger.trace(f"PluginLoader.__init__() - base_dir={base_dir}")
        self.base_dir = base_dir
        self.plugins: list[Plugin] = []
        self._loaded_modules: dict[str, any] = {}
        self._repository = repository

    def set_repository(self, repository: any) -> None:
        """Repository 설정 및 모든 플러그인에 주입."""
        self._repository = repository
        for plugin in self.plugins:
            plugin._repository = repository

    def load_all(self) -> list[str]:
        """모든 플러그인 로드 (builtin + custom).

        Returns:
            로드된 플러그인 이름 목록
        """
        loaded = []
        logger.trace(f"load_all() 시작 - base_dir={self.base_dir}")

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
            logger.info(f"플러그인 로드됨: {location}")
            return True

        # builtin 먼저, custom 나중에 (덮어쓰기 가능)
        for plugin_dir in ["builtin", "custom"]:
            dir_path = self.base_dir / "plugins" / plugin_dir
            if not dir_path.exists():
                logger.trace(f"플러그인 디렉토리 없음: {dir_path}")
                continue

            logger.trace(f"플러그인 디렉토리 스캔: {dir_path}")

            # 1. 디렉토리 기반 플러그인 로드 (우선)
            for item in dir_path.iterdir():
                if item.is_dir() and not item.name.startswith("_"):
                    init_file = item / "__init__.py"
                    plugin_file = item / "plugin.py"
                    if init_file.exists() or plugin_file.exists():
                        logger.trace(f"플러그인 패키지 발견: {item.name}")
                        plugin = self._load_plugin_from_package(item)
                        if plugin:
                            if not try_register(plugin, f"{plugin_dir}/{plugin.name}"):
                                pass  # 충돌 경고는 try_register 내부에서 출력
                        else:
                            logger.warning(f"플러그인 로드 실패: {item.name}")

            # 2. 파일 기반 플러그인 로드
            for py_file in dir_path.glob("*.py"):
                if py_file.name.startswith("_"):
                    continue

                logger.trace(f"플러그인 파일 발견: {py_file.name}")
                plugin = self._load_plugin_safe(py_file)
                if plugin:
                    try_register(plugin, f"{plugin_dir}/{plugin.name}")

        logger.info(f"플러그인 로드 완료: {len(loaded)}개")
        logger.trace(f"로드된 플러그인: {loaded}")

        # 플러그인 스키마 초기화
        self._init_plugin_schemas()

        return loaded

    def _init_plugin_schemas(self) -> None:
        """로드된 플러그인의 DDL을 실행하여 테이블 생성."""
        if not self._repository or not hasattr(self._repository, '_conn'):
            logger.trace("Repository 없음 - 플러그인 스키마 초기화 스킵")
            return

        conn = self._repository._conn
        for plugin in self.plugins:
            schema = plugin.get_schema()
            if schema:
                try:
                    conn.executescript(schema)
                    conn.commit()
                    logger.trace(f"플러그인 스키마 초기화: {plugin.name}")
                except Exception as e:
                    logger.error(f"플러그인 스키마 실패 ({plugin.name}): {e}")

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
            logger.trace(f"plugin.py 발견 - 로드 시도")
            return self._load_plugin_safe(plugin_file)

        # plugin.py가 없으면 __init__.py에서 직접 정의된 경우 시도
        init_file = package_path / "__init__.py"
        logger.trace(f"__init__.py에서 로드 시도")

        try:
            spec = importlib.util.spec_from_file_location(
                package_path.name, init_file
            )
            if not spec or not spec.loader:
                logger.warning(f"spec 생성 실패: {package_path}")
                return None

            logger.trace("모듈 로드 중")
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)

            # Plugin 클래스 찾기
            logger.trace("Plugin 클래스 검색 중")
            for attr_name in dir(module):
                attr = getattr(module, attr_name)
                if (
                    isinstance(attr, type)
                    and issubclass(attr, Plugin)
                    and attr is not Plugin
                ):
                    plugin = attr()
                    plugin._base_dir = self.base_dir
                    plugin._repository = self._repository
                    self._loaded_modules[package_path.name] = module
                    logger.trace(f"Plugin 클래스 발견: {attr_name} -> {plugin.name}")
                    return plugin

            logger.warning(f"Plugin 클래스 없음: {package_path}")
            return None

        except SyntaxError as e:
            logger.error(f"플러그인 문법 오류: {package_path} - {e}")
            return None
        except Exception as e:
            logger.error(f"플러그인 로드 실패: {package_path} - {e}", exc_info=True)
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
            # 모듈 로드
            spec = importlib.util.spec_from_file_location(
                file_path.stem, file_path
            )
            if not spec or not spec.loader:
                logger.warning(f"spec 생성 실패: {file_path}")
                return None

            logger.trace("모듈 실행 중")
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            logger.trace(f"모듈 실행 완료: {file_path.stem}")

            # Plugin 클래스 찾기
            logger.trace("Plugin 클래스 검색 중")
            for attr_name in dir(module):
                attr = getattr(module, attr_name)
                if (
                    isinstance(attr, type)
                    and issubclass(attr, Plugin)
                    and attr is not Plugin
                ):
                    plugin = attr()
                    plugin._base_dir = self.base_dir  # 데이터 디렉토리용
                    plugin._repository = self._repository
                    self._loaded_modules[file_path.stem] = module
                    logger.trace(f"Plugin 인스턴스 생성: {plugin.name} (class: {attr_name})")
                    return plugin

            logger.warning(f"Plugin 클래스 없음: {file_path}")
            return None

        except SyntaxError as e:
            logger.error(f"플러그인 문법 오류: {file_path} - {e}")
            return None
        except Exception as e:
            logger.error(f"플러그인 로드 실패: {file_path} - {e}", exc_info=True)
            return None

    def reload_plugin(self, name: str) -> bool:
        """특정 플러그인 핫 리로드 (파일 + 패키지 지원).

        Args:
            name: 플러그인 이름

        Returns:
            성공 여부
        """
        logger.trace(f"reload_plugin() - name={name}")

        # 기존 플러그인 제거
        self.plugins = [p for p in self.plugins if p.name != name]
        logger.trace("기존 플러그인 제거됨")

        # 다시 로드 시도
        for plugin_dir in ["builtin", "custom"]:
            dir_base = self.base_dir / "plugins" / plugin_dir

            # 1. 디렉토리 패키지 기반 시도
            package_path = dir_base / name
            if package_path.is_dir():
                self._invalidate_module_cache(name)
                plugin = self._load_plugin_from_package(package_path)
                if plugin:
                    plugin._source_group = plugin_dir
                    plugin._source_location = f"{plugin_dir}/{plugin.name}"
                    self.plugins.append(plugin)
                    logger.info(f"플러그인 리로드됨 (패키지): {name}")
                    return True

            # 2. 단일 파일 기반 시도
            file_path = dir_base / f"{name}.py"
            if file_path.exists():
                self._invalidate_module_cache(name)
                plugin = self._load_plugin_safe(file_path)
                if plugin:
                    plugin._source_group = plugin_dir
                    plugin._source_location = f"{plugin_dir}/{plugin.name}"
                    self.plugins.append(plugin)
                    logger.info(f"플러그인 리로드됨 (파일): {name}")
                    return True

        logger.warning(f"플러그인 리로드 실패: {name}")
        return False

    def _invalidate_module_cache(self, name: str) -> None:
        """모듈 캐시 제거 (핫 리로드 시 이전 코드가 남는 문제 방지)."""
        import sys

        # _loaded_modules에서 제거
        self._loaded_modules.pop(name, None)
        self._loaded_modules.pop("plugin", None)

        # sys.modules에서 관련 모듈 제거 (핫 리로드 핵심)
        modules_to_remove = [
            key for key in sys.modules
            if key == name or key == "plugin" or key.startswith(f"{name}.")
        ]
        for mod_key in modules_to_remove:
            del sys.modules[mod_key]

        if modules_to_remove:
            logger.trace(f"모듈 캐시 제거: {modules_to_remove}")

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
        logger.trace(f"플러그인 수: {len(self.plugins)}")

        for plugin in self.plugins:
            try:
                logger.trace(f"플러그인 체크: {plugin.name}")
                can_handle = await plugin.can_handle(message, chat_id)
                logger.trace(f"{plugin.name}.can_handle() = {can_handle}")

                if can_handle:
                    logger.trace(f"{plugin.name}.handle() 호출")
                    result = await plugin.handle(message, chat_id)
                    logger.trace(f"handle 결과 - handled={result.handled}, response_len={len(result.response) if result.response else 0}")

                    if result.handled:
                        logger.info(f"플러그인 처리 완료: {plugin.name}")
                        return result

            except Exception as e:
                logger.error(f"플러그인 실행 오류 ({plugin.name}): {e}", exc_info=True)
                # 플러그인 오류가 봇 전체에 영향 주지 않도록
                continue

        logger.trace("플러그인 매칭 없음 - Claude로 전달")
        return None

    def get_plugin_list(self) -> list[dict]:
        """로드된 플러그인 목록 반환."""
        logger.trace("get_plugin_list()")
        return [
            {"name": p.name, "description": p.description}
            for p in self.plugins
        ]

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
                logger.trace(f"플러그인 찾음: {name}")
                return plugin

        logger.trace(f"플러그인 없음: {name}")
        return None
