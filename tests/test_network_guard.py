"""Network guard circuit-breaker tests."""

from __future__ import annotations

import socket

import pytest

from src.network_guard import CircuitOpen, NetworkGuard, NetworkUnavailable, is_transient_network_error


def test_transient_network_errors_are_classified():
    assert is_transient_network_error(socket.gaierror("nodename nor servname provided"))
    assert is_transient_network_error(OSError(54, "Connection reset by peer"))


def test_non_network_errors_are_not_classified():
    assert not is_transient_network_error(ValueError("bad input"))


def test_run_sync_opens_circuit_after_threshold():
    now = 100.0
    guard = NetworkGuard(failure_threshold=2, reset_timeout_seconds=30, clock=lambda: now)

    def fail():
        raise socket.gaierror("temporary failure")

    with pytest.raises(NetworkUnavailable):
        guard.run_sync("telegram", fail)

    with pytest.raises(NetworkUnavailable):
        guard.run_sync("telegram", fail)

    snapshot = guard.snapshot("telegram")
    assert snapshot.open is True
    assert snapshot.failures == 2

    with pytest.raises(CircuitOpen):
        guard.run_sync("telegram", lambda: "ok")


def test_run_sync_resets_after_success_when_circuit_allows_retry():
    now = 100.0
    guard = NetworkGuard(failure_threshold=1, reset_timeout_seconds=30, clock=lambda: now)

    with pytest.raises(NetworkUnavailable):
        guard.run_sync("google_calendar", lambda: (_ for _ in ()).throw(OSError(54, "reset")))

    now = 131.0
    assert guard.run_sync("google_calendar", lambda: "ok") == "ok"

    snapshot = guard.snapshot("google_calendar")
    assert snapshot.open is False
    assert snapshot.failures == 0


@pytest.mark.asyncio
async def test_run_async_wraps_transient_errors():
    guard = NetworkGuard(failure_threshold=3, reset_timeout_seconds=30)

    async def fail():
        raise TimeoutError("timed out")

    with pytest.raises(NetworkUnavailable) as exc_info:
        await guard.run_async("weather", fail)

    assert exc_info.value.dependency == "weather"
