"""Telegram Bot API request wrapper with network circuit-breaker support."""

from __future__ import annotations

from typing import Any

from telegram.request import BaseRequest, HTTPXRequest

from src.network_guard import network_guard


class GuardedTelegramRequest(BaseRequest):
    """Delegate PTB HTTP requests through the shared network guard."""

    def __init__(self, inner: BaseRequest | None = None, *, dependency: str = "telegram") -> None:
        self._inner = inner or HTTPXRequest()
        self._dependency = dependency

    @property
    def read_timeout(self) -> float | None:
        return self._inner.read_timeout

    async def initialize(self) -> None:
        await self._inner.initialize()

    async def shutdown(self) -> None:
        await self._inner.shutdown()

    async def post(self, *args: Any, **kwargs: Any) -> Any:
        return await network_guard.run_async(self._dependency, self._inner.post, *args, **kwargs)

    async def retrieve(self, *args: Any, **kwargs: Any) -> bytes:
        return await network_guard.run_async(self._dependency, self._inner.retrieve, *args, **kwargs)

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
        return await network_guard.run_async(
            self._dependency,
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
) -> GuardedTelegramRequest:
    """Create the guarded PTB request used by runtime Bot instances."""
    return GuardedTelegramRequest(
        HTTPXRequest(
            read_timeout=read_timeout,
            write_timeout=write_timeout,
            connect_timeout=connect_timeout,
            pool_timeout=pool_timeout,
        )
    )


__all__ = ["GuardedTelegramRequest", "build_guarded_telegram_request"]
