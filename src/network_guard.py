"""Central network call guard with circuit-breaker state."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
import errno
import os
import socket
import time
from threading import Lock
from typing import Any, TypeVar
from urllib.error import URLError

from src.logging_config import logger

try:  # Optional in tests that do not install Telegram extras.
    from telegram.error import BadRequest, NetworkError as TelegramNetworkError, TimedOut
except Exception:  # pragma: no cover - import guard for optional runtime dependency
    BadRequest = None
    TelegramNetworkError = None
    TimedOut = None

try:
    import httpx
except Exception:  # pragma: no cover
    httpx = None

try:
    import httpcore
except Exception:  # pragma: no cover
    httpcore = None

try:
    from googleapiclient.errors import HttpError
except Exception:  # pragma: no cover
    HttpError = None


T = TypeVar("T")


_TRANSIENT_OS_ERRNOS = {
    errno.ECONNABORTED,
    errno.ECONNREFUSED,
    errno.ECONNRESET,
    errno.EHOSTDOWN,
    errno.EHOSTUNREACH,
    errno.ENETDOWN,
    errno.ENETRESET,
    errno.ENETUNREACH,
    errno.ETIMEDOUT,
    errno.EPIPE,
}

_TRANSIENT_TEXT_MARKERS = (
    "all connection attempts failed",
    "broken pipe",
    "cannot assign requested address",
    "connection reset",
    "connection reset by peer",
    "connection refused",
    "network is down",
    "network is unreachable",
    "name or service not known",
    "nodename nor servname",
    "temporary failure",
    "timed out",
    "unable to find the server",
)


class NetworkUnavailable(RuntimeError):
    """Raised when one guarded network dependency is currently unavailable."""

    def __init__(self, dependency: str, message: str):
        self.dependency = dependency
        super().__init__(f"{dependency}: {message}")


class CircuitOpen(NetworkUnavailable):
    """Raised when a dependency circuit is open and calls are being skipped."""


@dataclass
class _CircuitState:
    failures: int = 0
    opened_until: float = 0.0


@dataclass(frozen=True)
class CircuitSnapshot:
    """Read-only circuit state for diagnostics/tests."""

    dependency: str
    failures: int
    open: bool
    seconds_until_retry: float


class NetworkGuard:
    """Wrap sync/async network calls with transient error classification."""

    def __init__(
        self,
        *,
        failure_threshold: int = 3,
        reset_timeout_seconds: float = 120.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.failure_threshold = max(1, failure_threshold)
        self.reset_timeout_seconds = max(0.1, reset_timeout_seconds)
        self._clock = clock
        self._states: dict[str, _CircuitState] = {}
        self._lock = Lock()

    def run_sync(self, dependency: str, func: Callable[..., T], *args: Any, **kwargs: Any) -> T:
        """Run one blocking network call through a dependency circuit."""
        self._raise_if_open(dependency)
        try:
            result = func(*args, **kwargs)
        except Exception as exc:
            self._handle_exception(dependency, exc)
        self._record_success(dependency)
        return result

    async def run_async(self, dependency: str, func: Callable[..., T], *args: Any, **kwargs: Any) -> T:
        """Run one async network call through a dependency circuit."""
        self._raise_if_open(dependency)
        try:
            result = func(*args, **kwargs)
            if asyncio.iscoroutine(result):
                result = await result
        except Exception as exc:
            self._handle_exception(dependency, exc)
        self._record_success(dependency)
        return result

    def snapshot(self, dependency: str) -> CircuitSnapshot:
        """Return current circuit state for one dependency."""
        now = self._clock()
        with self._lock:
            state = self._states.get(dependency, _CircuitState())
            seconds = max(0.0, state.opened_until - now)
            return CircuitSnapshot(
                dependency=dependency,
                failures=state.failures,
                open=seconds > 0,
                seconds_until_retry=seconds,
            )

    def reset(self, dependency: str | None = None) -> None:
        """Reset one dependency circuit, or all circuits when dependency is omitted."""
        with self._lock:
            if dependency is None:
                self._states.clear()
            else:
                self._states.pop(dependency, None)

    def _raise_if_open(self, dependency: str) -> None:
        now = self._clock()
        with self._lock:
            state = self._states.setdefault(dependency, _CircuitState())
            if state.opened_until <= now:
                return
            retry_in = state.opened_until - now
        raise CircuitOpen(dependency, f"circuit open; retry in {retry_in:.0f}s")

    def _handle_exception(self, dependency: str, exc: Exception) -> None:
        if isinstance(exc, NetworkUnavailable):
            raise exc

        if not is_transient_network_error(exc):
            raise exc

        self._record_failure(dependency, exc)
        raise NetworkUnavailable(dependency, _compact_exception(exc)) from exc

    def _record_success(self, dependency: str) -> None:
        with self._lock:
            state = self._states.setdefault(dependency, _CircuitState())
            if state.failures or state.opened_until:
                logger.info(f"[NetworkGuard] recovered: dependency={dependency}")
            state.failures = 0
            state.opened_until = 0.0

    def _record_failure(self, dependency: str, exc: Exception) -> None:
        now = self._clock()
        with self._lock:
            state = self._states.setdefault(dependency, _CircuitState())
            state.failures += 1
            failures = state.failures
            if failures >= self.failure_threshold:
                state.opened_until = now + self.reset_timeout_seconds
                logger.warning(
                    f"[NetworkGuard] circuit opened: dependency={dependency}, "
                    f"failures={failures}, reset_in={self.reset_timeout_seconds:.0f}s, "
                    f"error={_compact_exception(exc)}"
                )
                return

        logger.warning(
            f"[NetworkGuard] transient network failure: dependency={dependency}, "
            f"failures={failures}/{self.failure_threshold}, error={_compact_exception(exc)}"
        )


def is_transient_network_error(exc: BaseException) -> bool:
    """Return whether an exception represents a retryable network failure."""
    for item in _exception_chain(exc):
        if _is_transient_exception(item):
            return True
    return False


def _exception_chain(exc: BaseException):
    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        yield current
        current = current.__cause__ or current.__context__


def _is_transient_exception(exc: BaseException) -> bool:
    if isinstance(exc, NetworkUnavailable):
        return True

    if TimedOut is not None and isinstance(exc, TimedOut):
        return True

    if BadRequest is not None and isinstance(exc, BadRequest):
        return False

    if TelegramNetworkError is not None and isinstance(exc, TelegramNetworkError):
        return True

    if httpx is not None and isinstance(
        exc,
        (
            httpx.ConnectError,
            httpx.ConnectTimeout,
            httpx.NetworkError,
            httpx.PoolTimeout,
            httpx.ReadTimeout,
            httpx.RemoteProtocolError,
            httpx.TimeoutException,
            httpx.WriteTimeout,
        ),
    ):
        return True

    if httpcore is not None and isinstance(
        exc,
        (
            httpcore.ConnectError,
            httpcore.ConnectTimeout,
            httpcore.NetworkError,
            httpcore.PoolTimeout,
            httpcore.ReadTimeout,
            httpcore.RemoteProtocolError,
            httpcore.TimeoutException,
            httpcore.WriteTimeout,
        ),
    ):
        return True

    if HttpError is not None and isinstance(exc, HttpError):
        status = getattr(getattr(exc, "resp", None), "status", None)
        return status == 408 or status == 429 or (isinstance(status, int) and status >= 500)

    if isinstance(exc, socket.gaierror):
        return True

    if isinstance(exc, TimeoutError | ConnectionError | URLError):
        return True

    if isinstance(exc, OSError):
        err_no = getattr(exc, "errno", None)
        if err_no in _TRANSIENT_OS_ERRNOS:
            return True

    text = str(exc).lower()
    return any(marker in text for marker in _TRANSIENT_TEXT_MARKERS)


def _compact_exception(exc: BaseException) -> str:
    text = str(exc).strip()
    if not text:
        return type(exc).__name__
    return f"{type(exc).__name__}: {text}"


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        logger.warning(f"[NetworkGuard] invalid integer env: {name}={raw!r}, default={default}")
        return default


network_guard = NetworkGuard(
    failure_threshold=_env_int("NETWORK_GUARD_FAILURE_THRESHOLD", 3),
    reset_timeout_seconds=_env_int("NETWORK_GUARD_RESET_SECONDS", 120),
)


__all__ = [
    "CircuitOpen",
    "CircuitSnapshot",
    "NetworkGuard",
    "NetworkUnavailable",
    "is_transient_network_error",
    "network_guard",
]
