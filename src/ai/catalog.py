"""AI provider and model profile catalog."""

from dataclasses import dataclass

from src.ui_emoji import (
    MODEL_BADGE_LIGHT,
    MODEL_BADGE_MID,
    MODEL_BADGE_TOP,
    PROVIDER_BUTTON_CLAUDE,
    PROVIDER_BUTTON_CODEX,
    PROVIDER_BUTTON_GEMINI,
    PROVIDER_BUTTON_AGY,
    PROVIDER_ICON_CLAUDE,
    PROVIDER_ICON_CODEX,
    PROVIDER_ICON_GEMINI,
    PROVIDER_ICON_AGY,
)


SUPPORTED_PROVIDERS = ["claude", "codex", "gemini", "agy"]
DEFAULT_PROVIDER = "claude"
DEFAULT_MODEL_KEYS = {
    "claude": "opus",
    "codex": "sol-xhigh",
    "gemini": "gemini-pro",
    "agy": "agy-pro-high",
}


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
    "gemini": "Gemini",
    "agy": "Antigravity",
}


PROVIDER_ICONS = {
    "claude": PROVIDER_ICON_CLAUDE,
    "codex": PROVIDER_ICON_CODEX,
    "gemini": PROVIDER_ICON_GEMINI,
    "agy": PROVIDER_ICON_AGY,
}


PROVIDER_BUTTONS = {
    "claude": PROVIDER_BUTTON_CLAUDE,
    "codex": PROVIDER_BUTTON_CODEX,
    "gemini": PROVIDER_BUTTON_GEMINI,
    "agy": PROVIDER_BUTTON_AGY,
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
            provider_model="claude-opus-4-7",
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
            key="astra-xhigh",
            provider="codex",
            label="Astra XHigh",
            short_label="Astra XHigh",
            button_label="Astra XHigh",
            badge=MODEL_BADGE_TOP,
            provider_model="gpt-6-astra",
            reasoning_effort="xhigh",
        ),
        ModelProfile(
            key="sol-xhigh",
            provider="codex",
            label="Sol XHigh",
            short_label="Sol XHigh",
            button_label="Sol XHigh",
            badge=MODEL_BADGE_TOP,
            provider_model="gpt-5.6-sol",
            reasoning_effort="xhigh",
        ),
        ModelProfile(
            key="sol-high",
            provider="codex",
            label="Sol High",
            short_label="Sol High",
            button_label="Sol High",
            badge=MODEL_BADGE_MID,
            provider_model="gpt-5.6-sol",
            reasoning_effort="high",
        ),
    ],
    "gemini": [
        ModelProfile(
            key="gemini-pro",
            provider="gemini",
            label="Pro",
            short_label="Pro",
            button_label="Pro",
            badge=MODEL_BADGE_TOP,
            # Use Gemini CLI shorthand so it routes to the current latest
            # (e.g. 3.1-pro-preview today; auto-tracks future releases).
            provider_model="pro",
        ),
        ModelProfile(
            key="gemini-flash",
            provider="gemini",
            label="Flash",
            short_label="Flash",
            button_label="Flash",
            badge=MODEL_BADGE_MID,
            provider_model="flash",
        ),
        ModelProfile(
            key="gemini-flash-lite",
            provider="gemini",
            label="Flash Lite",
            short_label="Lite",
            button_label="Flash Lite",
            badge=MODEL_BADGE_LIGHT,
            provider_model="flash-lite",
        ),
    ],
    "agy": [
        ModelProfile(
            key="agy-pro-high",
            provider="agy",
            label="Pro High",
            short_label="Pro High",
            button_label="Pro High",
            badge=MODEL_BADGE_TOP,
            provider_model="Gemini 3.1 Pro (High)",
        ),
        ModelProfile(
            key="agy-flash-high",
            provider="agy",
            label="Flash High",
            short_label="Flash High",
            button_label="Flash High",
            badge=MODEL_BADGE_MID,
            provider_model="Gemini 3.5 Flash (High)",
        ),
        ModelProfile(
            key="agy-flash-low",
            provider="agy",
            label="Flash Low",
            short_label="Flash Low",
            button_label="Flash Low",
            badge=MODEL_BADGE_LIGHT,
            provider_model="Gemini 3.5 Flash (Low)",
        ),
    ],
}


def _get_default_model_overrides() -> dict[str, str]:
    """Read per-provider default model overrides from settings."""
    from src.config import get_settings
    settings = get_settings()
    overrides = {}
    if settings.default_model_claude:
        overrides["claude"] = settings.default_model_claude
    if settings.default_model_codex:
        overrides["codex"] = settings.default_model_codex
    if settings.default_model_gemini:
        overrides["gemini"] = settings.default_model_gemini
    if settings.default_model_agy:
        overrides["agy"] = settings.default_model_agy
    return overrides


MODEL_KEY_ALIASES = {
    "codex": {
        # Keep existing DB/session/env values working after moving Codex keys to
        # provider-local profiles. The concrete CLI model can now change independently.
        "xhigh": "sol-xhigh",
        "high": "sol-high",
        "medium": "astra-xhigh",
        "codex_xhigh": "sol-xhigh",
        "codex_high": "sol-high",
        "codex_medium": "astra-xhigh",
        "gpt54_xhigh": "sol-xhigh",
        "gpt54_high": "sol-high",
        "gpt55_xhigh": "sol-xhigh",
        "gpt55_high": "sol-high",
        "gpt53_codex_medium": "astra-xhigh",
        "terra-xhigh": "astra-xhigh",
    },
    "agy": {
        "gemini-3.5-pro": "agy-pro-high",
        "gemini-3.5-flash": "agy-flash-high",
        "gemini-3.5-flash-lite": "agy-flash-low",
        "agy-pro": "agy-pro-high",
        "pro-high": "agy-pro-high",
        "flash-high": "agy-flash-high",
        "flash-low": "agy-flash-low",
    },
}


def resolve_model_key(model: str | None, provider: str | None = None) -> str | None:
    """Return the canonical profile key for a user-facing or legacy model key."""
    if model is None:
        return None
    if provider:
        return MODEL_KEY_ALIASES.get(provider, {}).get(model, model)
    for provider_aliases in MODEL_KEY_ALIASES.values():
        if model in provider_aliases:
            return provider_aliases[model]
    return model


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


def _find_provider_profile(provider: str, model: str) -> ModelProfile | None:
    """Find a profile within one provider namespace."""
    for profile in get_provider_profiles(provider):
        if profile.key == model:
            return profile
    return None


def get_default_model(provider: str) -> str:
    """Return default model profile key for a provider.

    Priority: env var override > built-in provider default > first available profile.
    """
    overrides = _get_default_model_overrides()
    override = overrides.get(provider)
    resolved_override = resolve_model_key(override, provider)
    if resolved_override and _find_provider_profile(provider, resolved_override):
        return resolved_override
    built_in_default = DEFAULT_MODEL_KEYS.get(provider)
    if built_in_default and _find_provider_profile(provider, built_in_default):
        return built_in_default
    profiles = MODEL_PROFILES.get(provider, MODEL_PROFILES[DEFAULT_PROVIDER])
    return profiles[0].key


def get_profile(provider: str, model: str | None) -> ModelProfile:
    """Return one profile, falling back to the provider default."""
    model = resolve_model_key(model, provider) or get_default_model(provider)
    profile = _find_provider_profile(provider, model)
    if profile:
        return profile
    default_profile = _find_provider_profile(provider, get_default_model(provider))
    return default_profile or MODEL_PROFILES[DEFAULT_PROVIDER][0]


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
    model = resolve_model_key(model, provider) or model
    return any(profile.key == model for profile in get_provider_profiles(provider))


def infer_provider_from_model(model: str | None) -> str:
    """Best-effort provider inference used for DB cleanup/migration."""
    if not model:
        return DEFAULT_PROVIDER

    canonical_model = resolve_model_key(model) or model

    if canonical_model in ("opus", "sonnet", "haiku"):
        return "claude"
    if (
        model.startswith("gpt")
        or model.startswith("codex")
        or canonical_model in {"sol-xhigh", "sol-high", "astra-xhigh"}
    ):
        return "codex"
    if canonical_model.startswith("agy-") or canonical_model.startswith("gemini-3.5"):
        return "agy"
    if canonical_model.startswith("gemini"):
        return "gemini"
    return DEFAULT_PROVIDER
