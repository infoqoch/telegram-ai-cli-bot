from __future__ import annotations

from types import SimpleNamespace

import pytest
from telegram.error import NetworkError as TelegramNetworkError
from telegram.request import BaseRequest

from src.network_guard import CircuitOpen, NetworkUnavailable, network_guard
from src.telegram_request import GuardedTelegramRequest


class FakeRequest(BaseRequest):
    def __init__(self, exc: Exception | None = None) -> None:
        self.exc = exc
        self.called = False
        self.initialized = False
        self.shutdown_called = False

    @property
    def read_timeout(self) -> float:
        return 12.0

    async def initialize(self) -> None:
        self.initialized = True

    async def shutdown(self) -> None:
        self.shutdown_called = True

    async def do_request(
        self,
        url: str,
        method: str,
        request_data=None,
        read_timeout=None,
        write_timeout=None,
        connect_timeout=None,
        pool_timeout=None,
    ) -> tuple[int, bytes]:
        self.called = True
        if self.exc:
            raise self.exc
        return 200, b'{"ok":true,"result":true}'


@pytest.mark.asyncio
async def test_guarded_telegram_request_delegates_lifecycle():
    inner = FakeRequest()
    request = GuardedTelegramRequest(inner)

    await request.initialize()
    await request.shutdown()

    assert inner.initialized is True
    assert inner.shutdown_called is True
    assert request.read_timeout == 12.0


@pytest.mark.asyncio
async def test_guarded_telegram_request_wraps_network_errors():
    request = GuardedTelegramRequest(FakeRequest(TimeoutError("offline")))

    with pytest.raises(NetworkUnavailable):
        await request.post("https://example.invalid")

    snapshot = network_guard.snapshot("telegram")
    assert snapshot.failures == 1


@pytest.mark.asyncio
async def test_guarded_telegram_request_wraps_direct_do_request_errors():
    request = GuardedTelegramRequest(FakeRequest(TimeoutError("offline")))

    with pytest.raises(NetworkUnavailable):
        await request.do_request("https://example.invalid", "POST")

    snapshot = network_guard.snapshot("telegram")
    assert snapshot.failures == 1


@pytest.mark.asyncio
async def test_polling_request_waits_for_open_circuit_before_retrying():
    calls = 0
    waits: list[float] = []

    class FakeGuard:
        async def run_async(self, dependency, func, *args, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 1:
                raise CircuitOpen(dependency, "circuit open")
            return await func(*args, **kwargs)

        def snapshot(self, dependency):
            return SimpleNamespace(seconds_until_retry=7.0)

    async def fake_sleep(seconds: float) -> None:
        waits.append(seconds)

    inner = FakeRequest()
    request = GuardedTelegramRequest(
        inner,
        guard=FakeGuard(),
        wait_for_open_circuit=True,
        sleep=fake_sleep,
    )

    result = await request.do_request("https://example.invalid", "POST")

    assert result == (200, b'{"ok":true,"result":true}')
    assert calls == 2
    assert waits == [7.0]


@pytest.mark.asyncio
async def test_polling_request_translates_network_failure_for_ptb_retry_loop():
    request = GuardedTelegramRequest(
        FakeRequest(TimeoutError("offline")),
        wait_for_open_circuit=True,
    )

    with pytest.raises(TelegramNetworkError, match=r"telegram: NetworkError:.*offline"):
        await request.post("https://example.invalid")

    snapshot = network_guard.snapshot("telegram")
    assert snapshot.failures == 1
