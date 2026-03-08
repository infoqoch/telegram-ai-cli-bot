"""Configuration management using Pydantic Settings."""

import fnmatch
from functools import lru_cache
from pathlib import Path
from typing import Optional

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""
    
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )
    
    # Telegram
    telegram_token: str = Field(..., description="Telegram Bot API token")
    allowed_chat_ids: list[int] = Field(default_factory=list)
    admin_chat_id: int = Field(default=0, description="Chat ID for admin notifications")

    # AI
    ai_command: str = Field(default="claude", alias="AI_COMMAND")
    session_timeout_hours: int = Field(default=24)
    
    # Authentication
    require_auth: bool = Field(default=True)
    auth_secret_key: str = Field(default="")
    auth_timeout_minutes: int = Field(default=30)
    
    # Paths
    base_dir: Path = Field(default_factory=lambda: Path(__file__).parent.parent)
    working_dir: Optional[Path] = Field(default=None, description="봇이 작업할 디렉토리")

    # Project Sessions (쉼표로 구분된 문자열로 저장, 프로퍼티로 리스트 반환)
    allowed_project_paths_raw: str = Field(
        default="/Users/bae/AiSandbox/*,/Users/bae/Projects/*",
        alias="ALLOWED_PROJECT_PATHS",
        description="프로젝트 세션 허용 디렉토리 (glob 패턴, 쉼표 구분)"
    )

    @property
    def allowed_project_paths(self) -> list[str]:
        """Parse comma-separated paths into list."""
        if not self.allowed_project_paths_raw or not self.allowed_project_paths_raw.strip():
            return []
        return [p.strip() for p in self.allowed_project_paths_raw.split(",") if p.strip()]

    @field_validator("working_dir", mode="before")
    @classmethod
    def expand_working_dir(cls, v):
        """~ 경로 확장 및 Path 변환."""
        if v is None or v == "":
            return None
        path = Path(v).expanduser()
        return path

    @property
    def effective_working_dir(self) -> Path:
        """실제 작업 디렉토리 반환. 설정 없으면 base_dir 사용."""
        if self.working_dir:
            return self.working_dir
        return self.base_dir
    
    @field_validator("auth_secret_key", mode="after")
    @classmethod
    def validate_auth_secret_key(cls, v, info):
        """REQUIRE_AUTH=true일 때 빈 AUTH_SECRET_KEY 방지."""
        # info.data에서 require_auth 값 확인
        require_auth = info.data.get("require_auth", True)
        if require_auth and not v:
            raise ValueError(
                "AUTH_SECRET_KEY is required when REQUIRE_AUTH=true. "
                "Set AUTH_SECRET_KEY in .env or set REQUIRE_AUTH=false."
            )
        return v

    @field_validator("allowed_chat_ids", mode="before")
    @classmethod
    def parse_chat_ids(cls, v):
        if isinstance(v, str):
            if not v.strip():
                return []
            return [int(x.strip()) for x in v.split(",") if x.strip()]
        if isinstance(v, int):
            return [v]
        if isinstance(v, list):
            return v
        return []

    @property
    def data_dir(self) -> Path:
        return self.base_dir / ".data"

    @property
    def db_path(self) -> Path:
        return self.data_dir / "bot.db"

    
    @property
    def prompts_dir(self) -> Path:
        return self.base_dir / "prompts"
    
    @property
    def telegram_prompt_file(self) -> Path:
        return self.prompts_dir / "telegram.md"

    def is_allowed_project_path(self, path: str) -> bool:
        """Check if path is allowed for project sessions."""
        expanded_path = Path(path).expanduser().resolve()
        if not expanded_path.exists() or not expanded_path.is_dir():
            return False

        path_str = str(expanded_path)
        for pattern in self.allowed_project_paths:
            expanded_pattern = str(Path(pattern).expanduser())
            if fnmatch.fnmatch(path_str, expanded_pattern):
                return True
            # 패턴이 /path/* 형태면 하위 디렉토리 체크
            if expanded_pattern.endswith("/*"):
                parent = expanded_pattern[:-2]
                if path_str.startswith(parent + "/"):
                    return True
        return False

    def list_available_projects(self) -> list[dict]:
        """Scan allowed directories and return list of available projects."""
        projects = []
        for pattern in self.allowed_project_paths:
            expanded_pattern = Path(pattern).expanduser()
            # /path/* 패턴 처리
            if str(expanded_pattern).endswith("/*"):
                parent_dir = Path(str(expanded_pattern)[:-2])
                if parent_dir.exists() and parent_dir.is_dir():
                    for child in sorted(parent_dir.iterdir()):
                        if child.is_dir() and not child.name.startswith("."):
                            has_claude = (child / "CLAUDE.md").exists() or (child / ".claude").exists()
                            projects.append({
                                "path": str(child),
                                "name": child.name,
                                "has_claude": has_claude,
                            })
        return projects

    def validate_project_path(self, path: str) -> tuple[bool, str]:
        """Validate project path and return (is_valid, error_message)."""
        expanded = Path(path).expanduser().resolve()

        if not expanded.exists():
            return False, f"Path does not exist: {path}"

        if not expanded.is_dir():
            return False, f"Not a directory: {path}"

        if not self.is_allowed_project_path(path):
            return False, f"Path not allowed: {path}"

        # CLAUDE.md 또는 .claude 디렉토리 존재 확인 (권장사항)
        has_claude_config = (
            (expanded / "CLAUDE.md").exists() or
            (expanded / ".claude").exists()
        )

        return True, "" if has_claude_config else "⚠️ No CLAUDE.md (optional)"


@lru_cache
def get_settings() -> Settings:
    """Get cached settings instance."""
    return Settings()
