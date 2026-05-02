"""AI model catalog tests."""

from src.ai.catalog import (
    get_default_model,
    get_profile,
    infer_provider_from_model,
    is_supported_model,
    normalize_model,
)


def test_codex_profile_uses_stable_key_with_current_provider_model():
    """Codex profile keys should not need to change for each model upgrade."""
    profile = get_profile("codex", "high")

    assert profile.key == "high"
    assert profile.provider_model == "gpt-5.5"
    assert profile.reasoning_effort == "high"


def test_codex_legacy_gpt54_key_resolves_to_current_profile():
    """Existing sessions/env overrides using gpt54_* should keep working."""
    profile = get_profile("codex", "gpt54_xhigh")

    assert profile.key == "xhigh"
    assert profile.provider_model == "gpt-5.5"
    assert normalize_model("codex", "gpt54_xhigh") == "xhigh"
    assert is_supported_model("codex", "gpt54_xhigh")


def test_codex_stable_key_provider_inference():
    """Command parsing should recognize stable Codex profile keys."""
    assert infer_provider_from_model("high") == "codex"
    assert get_default_model("codex") == "xhigh"


def test_codex_legacy_codex_prefixed_key_resolves_to_provider_local_profile():
    """The previous codex_* abstraction remains accepted during migration."""
    assert normalize_model("codex", "codex_high") == "high"
    assert is_supported_model("codex", "codex_xhigh")
