"""Tests for supervisor startup and crash-loop helpers."""

from __future__ import annotations

from collections import deque
from types import SimpleNamespace
from unittest.mock import MagicMock

import src.supervisor as supervisor


def test_run_preflight_rejects_invalid_settings(monkeypatch):
    monkeypatch.setattr(supervisor, "get_settings", MagicMock(side_effect=RuntimeError("bad config")))
    notify = MagicMock()
    monkeypatch.setattr(supervisor, "notify_admin", notify)

    assert supervisor._run_preflight() is False
    notify.assert_called_once()


def test_run_preflight_rejects_empty_telegram_token(monkeypatch):
    monkeypatch.setattr(
        supervisor,
        "get_settings",
        MagicMock(return_value=SimpleNamespace(telegram_token="")),
    )
    notify = MagicMock()
    monkeypatch.setattr(supervisor, "notify_admin", notify)

    assert supervisor._run_preflight() is False
    notify.assert_called_once()


def test_run_preflight_rejects_missing_ai_provider(monkeypatch):
    monkeypatch.setattr(
        supervisor,
        "get_settings",
        MagicMock(return_value=SimpleNamespace(telegram_token="token")),
    )
    monkeypatch.setattr(
        supervisor,
        "build_default_registry",
        MagicMock(side_effect=RuntimeError("No supported AI CLI provider found")),
    )
    notify = MagicMock()
    monkeypatch.setattr(supervisor, "notify_admin", notify)

    assert supervisor._run_preflight() is False
    notify.assert_called_once()


def test_record_crash_time_prunes_outside_window():
    crash_times = deque([100.0, 150.0])

    count = supervisor._record_crash_time(crash_times, 450.0, window_seconds=200)

    assert count == 1
    assert list(crash_times) == [450.0]
