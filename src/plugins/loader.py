"""Plugin loader with safe loading and hot reload support."""

import importlib.util
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


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
        self.base_dir = base_dir
        self.plugins: list[Plugin] = []
        self._loaded_modules: dict[str, any] = {}

    def load_all(self) -> list[str]:
        """모든 플러그인 로드 (builtin + custom).

        Returns:
            로드된 플러그인 이름 목록
        """
        loaded = []

        # builtin 먼저, custom 나중에 (덮어쓰기 가능)
        for plugin_dir in ["builtin", "custom"]:
            dir_path = self.base_dir / "plugins" / plugin_dir
            if not dir_path.exists():
                continue

            for py_file in dir_path.glob("*.py"):
                if py_file.name.startswith("_"):
                    continue

                plugin = self._load_plugin_safe(py_file)
                if plugin:
                    self.plugins.append(plugin)
                    loaded.append(f"{plugin_dir}/{plugin.name}")
                    logger.info(f"플러그인 로드됨: {plugin_dir}/{plugin.name}")

        return loaded

    def _load_plugin_safe(self, file_path: Path) -> Optional[Plugin]:
        """안전하게 플러그인 로드 (실패해도 봇 계속 동작).

        Args:
            file_path: 플러그인 파일 경로

        Returns:
            Plugin 인스턴스 또는 None (실패 시)
        """
        try:
            # 모듈 로드
            spec = importlib.util.spec_from_file_location(
                file_path.stem, file_path
            )
            if not spec or not spec.loader:
                logger.warning(f"플러그인 로드 실패 (spec 없음): {file_path}")
                return None

            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)

            # Plugin 클래스 찾기
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
                    return plugin

            logger.warning(f"플러그인 클래스 없음: {file_path}")
            return None

        except SyntaxError as e:
            logger.error(f"플러그인 문법 오류: {file_path} - {e}")
            return None
        except Exception as e:
            logger.error(f"플러그인 로드 실패: {file_path} - {e}")
            return None

    def reload_plugin(self, name: str) -> bool:
        """특정 플러그인 핫 리로드.

        Args:
            name: 플러그인 이름

        Returns:
            성공 여부
        """
        # 기존 플러그인 제거
        self.plugins = [p for p in self.plugins if p.name != name]

        # 다시 로드 시도
        for plugin_dir in ["builtin", "custom"]:
            file_path = self.base_dir / "plugins" / plugin_dir / f"{name}.py"
            if file_path.exists():
                plugin = self._load_plugin_safe(file_path)
                if plugin:
                    self.plugins.append(plugin)
                    logger.info(f"플러그인 리로드됨: {name}")
                    return True

        return False

    async def process_message(self, message: str, chat_id: int) -> Optional[PluginResult]:
        """메시지를 플러그인으로 처리 시도.

        Args:
            message: 사용자 메시지
            chat_id: 채팅 ID

        Returns:
            PluginResult (처리됨) 또는 None (처리 안됨)
        """
        for plugin in self.plugins:
            try:
                if await plugin.can_handle(message, chat_id):
                    result = await plugin.handle(message, chat_id)
                    if result.handled:
                        return result
            except Exception as e:
                logger.error(f"플러그인 실행 오류 ({plugin.name}): {e}")
                # 플러그인 오류가 봇 전체에 영향 주지 않도록
                continue

        return None

    def get_plugin_list(self) -> list[dict]:
        """로드된 플러그인 목록 반환."""
        return [
            {"name": p.name, "description": p.description}
            for p in self.plugins
        ]
