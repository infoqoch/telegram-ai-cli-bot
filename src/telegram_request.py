"""Telegram Bot API request wrapper with network circuit-breaker support."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

from telegram.request import BaseRequest, HTTPXRequest

from src.network_guard import CircuitOpen, NetworkGuard, network_guard


class GuardedTelegramRequest(BaseRequest):
    """Delegate PTB HTTP requests through the shared network guard."""

    def __init__(
        self,
        inner: BaseRequest | None = None,
        *,
        dependency: str = "telegram",
        guard: NetworkGuard = network_guard,
        wait_for_open_circuit: bool = False,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._inner = inner or HTTPXRequest()
        self._dependency = dependency
        self._guard = guard
        self._wait_for_open_circuit = wait_for_open_circuit
        self._sleep = sleep

    @property
    def read_timeout(self) -> float | None:
        return self._inner.read_timeout

    async def initialize(self) -> None:
        await self._inner.initialize()

    async def shutdown(self) -> None:
        await self._inner.shutdown()

    async def post(self, *args: Any, **kwargs: Any) -> Any:
        return await self._run_guarded(self._inner.post, *args, **kwargs)

    async def retrieve(self, *args: Any, **kwargs: Any) -> bytes:
        return await self._run_guarded(self._inner.retrieve, *args, **kwargs)

    async def _run_guarded(self, func: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
        while True:
            try:
                return await self._guard.run_async(self._dependency, func, *args, **kwargs)
            except CircuitOpen:
                if not self._wait_for_open_circuit:
                    raise
                snapshot = self._guard.snapshot(self._dependency)
                await self._sleep(max(snapshot.seconds_until_retry, 0.1))

    async def do_request(
        self,
        url: str,
        method: str,
        request_data: Any = None,
        read_timeout: Any = None,
        write_timeout: Any = None,
        connect_timeout: Any = None,
        pool_timeout: Any = None,
    ) -> tuple[int, bytes]:
        return await self._run_guarded(
            self._inner.do_request,
            url,
            method,
            request_data,
            read_timeout,
            write_timeout,
            connect_timeout,
            pool_timeout,
        )


def build_guarded_telegram_request(
    *,
    read_timeout: float | None = 15,
    write_timeout: float | None = 15,
    connect_timeout: float | None = 10,
    pool_timeout: float | None = 5,
    wait_for_open_circuit: bool = False,
) -> GuardedTelegramRequest:
    """Create the guarded PTB request used by runtime Bot instances."""
    return GuardedTelegramRequest(
        HTTPXRequest(
            read_timeout=read_timeout,
            write_timeout=write_timeout,
            connect_timeout=connect_timeout,
            pool_timeout=pool_timeout,
        ),
        wait_for_open_circuit=wait_for_open_circuit,
    )


__all__ = ["GuardedTelegramRequest", "build_guarded_telegram_request"]
