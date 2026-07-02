"""Tests for main process startup exit semantics."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

import src.main as main
from src.runtime_exit_codes import RuntimeExitCode


def test_load_settings_or_exit_uses_config_error_code(monkeypatch):
    monkeypatch.setattr(main, "get_settings", MagicMock(side_effect=RuntimeError("bad config")))

    with pytest.raises(SystemExit) as exc_info:
        main._load_settings_or_exit()

    assert exc_info.value.code == int(RuntimeExitCode.CONFIG_ERROR)


def test_python_version_guard_exits_for_unsupported_version(capsys):
    with pytest.raises(SystemExit) as exc_info:
        main._ensure_supported_python_version((3, 10, 14))

    assert exc_info.value.code == int(RuntimeExitCode.CONFIG_ERROR)
    assert "Python 3.11+ is required" in capsys.readouterr().err


def test_python_version_guard_accepts_supported_version():
    main._ensure_supported_python_version((3, 11, 0))


def test_main_exits_with_lock_held_code(monkeypatch):
    monkeypatch.setattr(main, "setup_logging", lambda **kwargs: None)
    monkeypatch.setattr(main._process_lock, "acquire", lambda: False)

    with pytest.raises(SystemExit) as exc_info:
        main.main()

    assert exc_info.value.code == int(RuntimeExitCode.LOCK_HELD)


def test_main_exits_with_config_error_for_empty_token(monkeypatch):
    monkeypatch.setattr(main, "setup_logging", lambda **kwargs: None)
    monkeypatch.setattr(main._process_lock, "acquire", lambda: True)
    monkeypatch.setattr(main._process_lock, "release", lambda: None)
    monkeypatch.setattr(main.atexit, "register", lambda *args, **kwargs: None)
    monkeypatch.setattr(main.signal, "signal", lambda *args, **kwargs: None)
    monkeypatch.setattr(main, "_load_settings_or_exit", lambda: SimpleNamespace(telegram_token=""))

    with pytest.raises(SystemExit) as exc_info:
        main.main()

    assert exc_info.value.code == int(RuntimeExitCode.CONFIG_ERROR)
