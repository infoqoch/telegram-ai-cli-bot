"""Shared constants and provider/model helpers."""

from src.ai.catalog import (
    DEFAULT_PROVIDER,
    SUPPORTED_PROVIDERS,
    get_default_model,
    get_provider_profiles,
)

# 스케줄 가능 시간대 (00:00 ~ 23:00)
AVAILABLE_HOURS = list(range(24))

SUPPORTED_MODELS = [profile.key for profile in get_provider_profiles(DEFAULT_PROVIDER)]
DEFAULT_MODEL = get_default_model(DEFAULT_PROVIDER)

__all__ = [
    "AVAILABLE_HOURS",
    "DEFAULT_MODEL",
    "DEFAULT_PROVIDER",
    "SUPPORTED_MODELS",
    "SUPPORTED_PROVIDERS",
]
