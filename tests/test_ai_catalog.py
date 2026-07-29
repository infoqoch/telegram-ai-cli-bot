"""AI model catalog tests."""

from src.ai.catalog import (
    get_default_model,
    get_profile,
    infer_provider_from_model,
    is_supported_model,
    normalize_model,
)


def test_codex_sol_profile_uses_requested_model_and_reasoning():
    """Sol profiles should use GPT-5.6 Sol with their requested reasoning."""
    profile = get_profile("codex", "sol-high")

    assert profile.key == "sol-high"
    assert profile.provider_model == "gpt-5.6-sol"
    assert profile.reasoning_effort == "high"


def test_codex_terra_profile_uses_xhigh_reasoning():
    """Terra profile should use GPT-5.6 Terra with xhigh reasoning."""
    profile = get_profile("codex", "terra-xhigh")

    assert profile.key == "terra-xhigh"
    assert profile.provider_model == "gpt-5.6-terra"
    assert profile.reasoning_effort == "xhigh"


def test_codex_legacy_gpt54_key_resolves_to_current_profile():
    """Existing sessions/env overrides using gpt54_* should keep working."""
    profile = get_profile("codex", "gpt54_xhigh")

    assert profile.key == "sol-xhigh"
    assert profile.provider_model == "gpt-5.6-sol"
    assert normalize_model("codex", "gpt54_xhigh") == "sol-xhigh"
    assert is_supported_model("codex", "gpt54_xhigh")


def test_codex_stable_key_provider_inference():
    """Command parsing should recognize stable Codex profile keys."""
    assert infer_provider_from_model("sol-high") == "codex"
    assert infer_provider_from_model("terra-xhigh") == "codex"
    assert get_default_model("codex") == "sol-xhigh"


def test_codex_legacy_codex_prefixed_key_resolves_to_provider_local_profile():
    """The previous codex_* abstraction remains accepted during migration."""
    assert normalize_model("codex", "codex_high") == "sol-high"
    assert is_supported_model("codex", "codex_xhigh")


def test_codex_previous_stable_keys_resolve_to_new_profiles():
    """Previously persisted provider-local keys should keep working."""
    assert normalize_model("codex", "xhigh") == "sol-xhigh"
    assert normalize_model("codex", "high") == "sol-high"
    assert normalize_model("codex", "medium") == "terra-xhigh"


def test_agy_profiles_use_stable_keys_with_exact_cli_model_names():
    """Agy profile keys should be provider-local and map to exact CLI labels."""
    profile = get_profile("agy", "agy-pro-high")

    assert profile.key == "agy-pro-high"
    assert profile.provider_model == "Gemini 3.1 Pro (High)"
    assert infer_provider_from_model("agy-pro-high") == "agy"


def test_agy_legacy_gemini_35_keys_resolve_to_stable_profiles():
    """Early Agy keys remain accepted as aliases."""
    assert normalize_model("agy", "gemini-3.5-pro") == "agy-pro-high"
    assert normalize_model("agy", "gemini-3.5-flash") == "agy-flash-high"
    assert normalize_model("agy", "gemini-3.5-flash-lite") == "agy-flash-low"
    assert is_supported_model("agy", "gemini-3.5-pro")
