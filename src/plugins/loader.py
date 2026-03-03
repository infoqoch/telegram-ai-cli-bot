"""Plugin loader with safe loading and hot reload support."""

import importlib.util
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from src.logging_config import logger


@dataclass
class PluginResult:
    """플러그인 실행 결과."""
    handled: bool  # 플러그인이 메시지를 처리했는지
    response: Optional[str] = None  # 응답 메시지
    error: Optional[str] = None  # 에러 메시지


class Plugin(ABC):
    """플러그인 기본 클래스."""

    name: str = "base"
    description: str = "Base plugin"
    usage: str = "사용법이 정의되지 않았습니다."

    @abstractmethod
    async def can_handle(self, message: str, chat_id: int) -> bool:
        """이 플러그인이 메시지를 처리할 수 있는지 확인."""
        pass

    @abstractmethod
    async def handle(self, message: str, chat_id: int) -> PluginResult:
        """메시지 처리."""
        pass

    def get_data_dir(self, base_dir: Path) -> Path:
        """플러그인 데이터 디렉토리 반환."""
        data_dir = base_dir / ".data" / self.name
        data_dir.mkdir(parents=True, exist_ok=True)
        return data_dir


class PluginLoader:
    """안전한 플러그인 로더."""

    def __init__(self, base_dir: Path):
        logger.trace(f"PluginLoader.__init__() - base_dir={base_dir}")
        self.base_dir = base_dir
        self.plugins: list[Plugin] = []
        self._loaded_modules: dict[str, any] = {}

    def load_all(self) -> list[str]:
        """모든 플러그인 로드 (builtin + custom).

        플러그인 구조:
        - 디렉토리 기반: plugins/builtin/memo/__init__.py
        - 파일 기반 (레거시): plugins/builtin/memo.py

        Returns:
            로드된 플러그인 이름 목록
        """
        loaded = []
        logger.trace(f"load_all() 시작 - base_dir={self.base_dir}")

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
                            self.plugins.append(plugin)
                            loaded.append(f"{plugin_dir}/{plugin.name}")
                            logger.info(f"플러그인 로드됨: {plugin_dir}/{plugin.name}")
                        else:
                            logger.warning(f"플러그인 로드 실패: {item.name}")

            # 2. 파일 기반 플러그인 로드 (레거시)
            for py_file in dir_path.glob("*.py"):
                if py_file.name.startswith("_"):
                    continue

                logger.trace(f"레거시 플러그인 파일 발견: {py_file.name}")
                plugin = self._load_plugin_safe(py_file)
                if plugin:
                    self.plugins.append(plugin)
                    loaded.append(f"{plugin_dir}/{plugin.name}")
                    logger.info(f"플러그인 로드됨: {plugin_dir}/{plugin.name}")

        logger.info(f"플러그인 로드 완료: {len(loaded)}개")
        logger.trace(f"로드된 플러그인: {loaded}")
        return loaded

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
        """특정 플러그인 핫 리로드.

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
            file_path = self.base_dir / "plugins" / plugin_dir / f"{name}.py"
            if file_path.exists():
                logger.trace(f"플러그인 파일 발견: {file_path}")
                plugin = self._load_plugin_safe(file_path)
                if plugin:
                    self.plugins.append(plugin)
                    logger.info(f"플러그인 리로드됨: {name}")
                    return True

        logger.warning(f"플러그인 리로드 실패: {name}")
        return False

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

    def get_plugin_by_name(self, name: str) -> Optional[Plugin]:
        """이름으로 플러그인 찾기."""
        logger.trace(f"get_plugin_by_name() - name={name}")

        for plugin in self.plugins:
            if plugin.name == name:
                logger.trace(f"플러그인 찾음: {name}")
                return plugin

        logger.trace(f"플러그인 없음: {name}")
        return None
