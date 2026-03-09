"""AI provider helpers."""

from .catalog import (
    DEFAULT_PROVIDER,
    SUPPORTED_PROVIDERS,
    get_default_model,
    get_profile,
    get_profile_badge,
    get_profile_label,
    get_profile_short_label,
    get_provider_button,
    get_provider_icon,
    get_provider_label,
    get_provider_profiles,
    infer_provider_from_model,
    is_supported_model,
    is_supported_provider,
    normalize_model,
)
from .client_types import AIClient, ChatError, ChatResponse
from .registry import AIRegistry, build_default_registry

__all__ = [
    "AIClient",
    "AIRegistry",
    "build_default_registry",
    "ChatError",
    "ChatResponse",
    "DEFAULT_PROVIDER",
    "SUPPORTED_PROVIDERS",
    "get_default_model",
    "get_profile",
    "get_profile_badge",
    "get_profile_label",
    "get_profile_short_label",
    "get_provider_button",
    "get_provider_icon",
    "get_provider_label",
    "get_provider_profiles",
    "infer_provider_from_model",
    "is_supported_model",
    "is_supported_provider",
    "normalize_model",
]
