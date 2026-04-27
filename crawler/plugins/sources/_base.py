"""BaseSource and BaseStreamingSource — base classes for all data source plugins.

Section B of plugins/sources/CLAUDE.md.
"""
from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import ClassVar, TypeVar

import httpx
from aiolimiter import AsyncLimiter
from tenacity import retry, retry_if_exception, stop_after_attempt, wait_exponential

from crawler.core.contracts import CostEstimate, SourceCapabilities
from crawler.core.models import RawMention, SourceQuery

ConfigT = TypeVar("ConfigT")


# ---------------------------------------------------------------------------
# Custom exceptions (B.7)
# ---------------------------------------------------------------------------


class SourceError(Exception):
    """Base exception for data source errors."""


class SourceAuthError(SourceError):
    """Authentication error — fail-fast, do not retry."""


class SourceRateLimitError(SourceError):
    """Rate limit exceeded — retry with backoff."""


class SourceFetchError(SourceError):
    """Network request error — retry."""


# ---------------------------------------------------------------------------
# Retry policy (B.5, F.3)
# ---------------------------------------------------------------------------


def _is_retryable(exc: BaseException) -> bool:
    """Return True if the exception should trigger a retry."""
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code in (429, 500, 502, 503, 504)
    if isinstance(exc, (httpx.ConnectError, httpx.TimeoutException)):
        return True
    if isinstance(exc, SourceRateLimitError):
        return True
    if isinstance(exc, SourceFetchError):
        return True
    return False


def _with_retry(func):  # type: ignore[no-untyped-def]
    """Decorator: exponential backoff, max 3 attempts, retry on network errors."""
    return retry(
        retry=retry_if_exception(_is_retryable),
        wait=wait_exponential(multiplier=1, min=1, max=60),
        stop=stop_after_attempt(3),
        reraise=True,
    )(func)


# ---------------------------------------------------------------------------
# BaseSource (B.1, B.2)
# ---------------------------------------------------------------------------


class BaseSource[ConfigT]:
    """
    Base class for all REST-pull sources.

    Subclasses MUST define:
      - id: ClassVar[str]                — unique plugin name
      - capabilities: ClassVar[SourceCapabilities]
      - __init__(self, config: ConfigT) -> None  (call super())
      - search(self, q: SourceQuery) -> AsyncIterator[RawMention]
      - health_check(self) -> bool
      - estimate_cost(self, q: SourceQuery) -> CostEstimate

    BaseSource provides:
      - _client: httpx.AsyncClient — shared HTTP session
      - _limiter: AsyncLimiter    — per-instance rate limiter
    """

    id: ClassVar[str]
    capabilities: ClassVar[SourceCapabilities]

    def __init__(self, config: ConfigT) -> None:
        self._config = config
        # Shared HTTP client for httpx-based sources (PRAW-based sources don't use it
        # directly, but it's available for future subclasses).
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(connect=5.0, read=30.0, write=10.0, pool=5.0),
        )
        # Rate limiter: 60 req/min default; subclass can override by setting
        # _rate_limit_per_minute class variable before calling super().__init__().
        rpm: int = getattr(self.__class__, "_rate_limit_per_minute", 60)
        self._limiter = AsyncLimiter(max_rate=rpm, time_period=60)

    async def close(self) -> None:
        """Close HTTP client. Call on shutdown."""
        await self._client.aclose()

    # ------------------------------------------------------------------
    # Abstract methods — subclass MUST override
    # ------------------------------------------------------------------

    async def search(self, q: SourceQuery) -> AsyncIterator[RawMention]:
        """Async generator yielding RawMention items."""
        raise NotImplementedError(f"{self.__class__.__name__} must implement search()")
        # Make this an async generator so the return type hint is valid
        # (unreachable, but needed for mypy)
        yield  # type: ignore[misc]

    async def health_check(self) -> bool:
        """Return True if source is reachable and authenticated."""
        raise NotImplementedError(f"{self.__class__.__name__} must implement health_check()")

    def estimate_cost(self, q: SourceQuery) -> CostEstimate:
        """Return cost estimate without performing any I/O."""
        raise NotImplementedError(f"{self.__class__.__name__} must implement estimate_cost()")


# ---------------------------------------------------------------------------
# BaseStreamingSource (B.2, E3 stub)
# ---------------------------------------------------------------------------


class BaseStreamingSource(BaseSource[ConfigT]):
    """
    Base class for long-lived streaming sources (Bluesky firehose, etc.).

    Subclasses must implement _connect() and _disconnect().
    Provides lifecycle: start/stop + async context manager.

    NOTE: Full implementation is E3. This stub exists so that the class
    hierarchy (BlueskySource → BaseStreamingSource → BaseSource) can be
    declared without breaking the E1 codebase.
    """

    def __init__(self, config: ConfigT) -> None:
        super().__init__(config)
        self._buffer: asyncio.Queue[RawMention] = asyncio.Queue(maxsize=10_000)
        self._running: bool = False

    async def start(self) -> None:
        """Open long-lived connection and begin buffering incoming messages."""
        self._running = True
        await self._connect()

    async def stop(self) -> None:
        """Close connection, drain buffer."""
        self._running = False
        await self._disconnect()

    async def __aenter__(self) -> BaseStreamingSource[ConfigT]:
        await self.start()
        return self

    async def __aexit__(self, exc_type: object, exc: object, tb: object) -> None:
        await self.stop()

    async def search(self, q: SourceQuery) -> AsyncIterator[RawMention]:  # type: ignore[override]
        """
        For streaming sources: drain everything accumulated in the buffer
        since since_cursor. Finishes when buffer is empty (does not block
        indefinitely).
        """
        while not self._buffer.empty():
            yield await self._buffer.get()

    async def _connect(self) -> None:
        """Establish long-lived connection. Subclass must override."""
        raise NotImplementedError(f"{self.__class__.__name__} must implement _connect()")

    async def _disconnect(self) -> None:
        """Close connection. Subclass must override."""
        raise NotImplementedError(f"{self.__class__.__name__} must implement _disconnect()")
