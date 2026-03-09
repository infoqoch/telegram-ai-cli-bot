"""AI provider and model profile catalog."""

from dataclasses import dataclass

from src.ui_emoji import (
    MODEL_BADGE_LIGHT,
    MODEL_BADGE_MID,
    MODEL_BADGE_TOP,
    PROVIDER_BUTTON_CLAUDE,
    PROVIDER_BUTTON_CODEX,
    PROVIDER_ICON_CLAUDE,
    PROVIDER_ICON_CODEX,
)


SUPPORTED_PROVIDERS = ["claude", "codex"]
DEFAULT_PROVIDER = "claude"


@dataclass(frozen=True)
class ModelProfile:
    """Provider-specific model profile."""

    key: str
    provider: str
    label: str
    short_label: str
    button_label: str
    badge: str
    provider_model: str
    reasoning_effort: str | None = None


PROVIDER_LABELS = {
    "claude": "Claude",
    "codex": "Codex",
}


PROVIDER_ICONS = {
    "claude": PROVIDER_ICON_CLAUDE,
    "codex": PROVIDER_ICON_CODEX,
}


PROVIDER_BUTTONS = {
    "claude": PROVIDER_BUTTON_CLAUDE,
    "codex": PROVIDER_BUTTON_CODEX,
}


MODEL_PROFILES = {
    "claude": [
        ModelProfile(
            key="opus",
            provider="claude",
            label="Opus",
            short_label="Opus",
            button_label="Opus",
            badge=MODEL_BADGE_TOP,
            provider_model="opus",
        ),
        ModelProfile(
            key="sonnet",
            provider="claude",
            label="Sonnet",
            short_label="Sonnet",
            button_label="Sonnet",
            badge=MODEL_BADGE_MID,
            provider_model="sonnet",
        ),
        ModelProfile(
            key="haiku",
            provider="claude",
            label="Haiku",
            short_label="Haiku",
            button_label="Haiku",
            badge=MODEL_BADGE_LIGHT,
            provider_model="haiku",
        ),
    ],
    "codex": [
        ModelProfile(
            key="gpt54_xhigh",
            provider="codex",
            label="GPT-5.4 XHigh",
            short_label="5.4 XHigh",
            button_label="5.4 XHigh",
            badge=MODEL_BADGE_TOP,
            provider_model="gpt-5.4",
            reasoning_effort="xhigh",
        ),
        ModelProfile(
            key="gpt54_high",
            provider="codex",
            label="GPT-5.4 High",
            short_label="5.4 High",
            button_label="5.4 High",
            badge=MODEL_BADGE_MID,
            provider_model="gpt-5.4",
            reasoning_effort="high",
        ),
        ModelProfile(
            key="gpt53_codex_medium",
            provider="codex",
            label="GPT-5.3 Codex Medium",
            short_label="5.3 Codex",
            button_label="5.3 Codex",
            badge=MODEL_BADGE_LIGHT,
            provider_model="gpt-5.3-codex",
            reasoning_effort="medium",
        ),
    ],
}


DEFAULT_MODEL_BY_PROVIDER = {
    "claude": "sonnet",
    "codex": "gpt54_high",
}


MODEL_KEY_INDEX = {
    profile.key: profile
    for provider_profiles in MODEL_PROFILES.values()
    for profile in provider_profiles
}


def get_provider_label(provider: str) -> str:
    """Return human-readable provider label."""
    if provider not in SUPPORTED_PROVIDERS:
        provider = DEFAULT_PROVIDER
    return PROVIDER_LABELS.get(provider, provider.title())


def get_provider_button(provider: str) -> str:
    """Return short provider button label."""
    if provider not in SUPPORTED_PROVIDERS:
        provider = DEFAULT_PROVIDER
    return PROVIDER_BUTTONS.get(provider, provider.title())


def get_provider_icon(provider: str) -> str:
    """Return provider icon used in compact UI."""
    if provider not in SUPPORTED_PROVIDERS:
        provider = DEFAULT_PROVIDER
    return PROVIDER_ICONS.get(provider, PROVIDER_ICONS[DEFAULT_PROVIDER])


def get_provider_profiles(provider: str) -> list[ModelProfile]:
    """Return curated model profiles for a provider."""
    if provider not in SUPPORTED_PROVIDERS:
        provider = DEFAULT_PROVIDER
    return list(MODEL_PROFILES.get(provider, []))


def get_default_model(provider: str) -> str:
    """Return default model profile key for a provider."""
    return DEFAULT_MODEL_BY_PROVIDER.get(provider, DEFAULT_MODEL_BY_PROVIDER[DEFAULT_PROVIDER])


def get_profile(provider: str, model: str | None) -> ModelProfile:
    """Return one profile, falling back to the provider default."""
    model = model or get_default_model(provider)
    for profile in get_provider_profiles(provider):
        if profile.key == model:
            return profile
    return MODEL_KEY_INDEX[get_default_model(provider)]


def get_profile_label(provider: str, model: str | None) -> str:
    """Return display label for a provider/model pair."""
    return get_profile(provider, model).label


def get_profile_short_label(provider: str, model: str | None) -> str:
    """Return compact display label for a provider/model pair."""
    return get_profile(provider, model).short_label


def get_profile_badge(provider: str, model: str | None) -> str:
    """Return badge used in session/task lists."""
    return get_profile(provider, model).badge


def normalize_model(provider: str, model: str | None) -> str:
    """Normalize a profile key to a supported value."""
    return get_profile(provider, model).key


def is_supported_provider(provider: str) -> bool:
    """Whether the provider is supported by the bot."""
    return provider in SUPPORTED_PROVIDERS


def is_supported_model(provider: str, model: str) -> bool:
    """Whether the profile key is supported for a provider."""
    return any(profile.key == model for profile in get_provider_profiles(provider))


def infer_provider_from_model(model: str | None) -> str:
    """Best-effort provider inference used for DB cleanup/migration."""
    if not model:
        return DEFAULT_PROVIDER

    if model in ("opus", "sonnet", "haiku"):
        return "claude"
    if model.startswith("gpt") or "codex" in model:
        return "codex"
    if model.startswith("gemini"):
        return "gemini"
    return DEFAULT_PROVIDER
