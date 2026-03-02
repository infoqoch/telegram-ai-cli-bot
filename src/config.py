"""Configuration management using Pydantic Settings."""

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
    )
    
    # Telegram
    telegram_token: str = Field(..., description="Telegram Bot API token")
    allowed_chat_ids: list[int] = Field(default_factory=list)
    
    # Claude
    claude_command: str = Field(default="claude")
    session_timeout_hours: int = Field(default=24)
    
    # Authentication
    require_auth: bool = Field(default=True)
    auth_secret_key: str = Field(default="")
    auth_timeout_minutes: int = Field(default=30)
    
    # Paths
    base_dir: Path = Field(default_factory=lambda: Path(__file__).parent.parent)
    
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
    def sessions_file(self) -> Path:
        return self.data_dir / "sessions.json"
    
    @property
    def prompts_dir(self) -> Path:
        return self.base_dir / "prompts"
    
    @property
    def telegram_prompt_file(self) -> Path:
        return self.prompts_dir / "telegram.md"


@lru_cache
def get_settings() -> Settings:
    """Get cached settings instance."""
    return Settings()
