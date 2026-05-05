"""Global pytest configuration for deterministic local test runs."""

from __future__ import annotations

import os

import pytest


# Keep the suite independent from the developer's shell and local `.env`.
os.environ["TELEGRAM_TOKEN"] = "test-token"
os.environ["ALLOWED_CHAT_IDS"] = "[]"
os.environ["ADMIN_CHAT_ID"] = "1"
os.environ["REQUIRE_AUTH"] = "false"
os.environ.setdefault("APP_TIMEZONE", "Asia/Seoul")

try:
    from src.config import get_settings

    get_settings.cache_clear()
except Exception:
    # Some tests import config lazily; failing here would be worse than skipping.
    pass


@pytest.fixture(autouse=True)
def _reset_network_guard():
    """Keep circuit-breaker state from leaking between tests."""
    try:
        from src.network_guard import network_guard

        network_guard.reset()
        yield
        network_guard.reset()
    except Exception:
        yield
