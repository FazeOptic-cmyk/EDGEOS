"""Async provider interface plus resilient HTTP, cache, and diagnostics plumbing."""

from __future__ import annotations

import asyncio
import json
import math
import random
import time
from abc import ABC, abstractmethod
from collections import OrderedDict
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Any, Awaitable, Callable, Mapping

import httpx

from .models import (
    DataOrigin,
    EventQuery,
    MarketQuery,
    NormalizedEvent,
    NormalizedMarket,
    NormalizedOrderBook,
    OrderBookQuery,
    ProviderDescriptor,
    ProviderHealth,
    ProviderPage,
    ProviderState,
    RateLimitInfo,
)
from .parsing import integer_or_none, parse_datetime, utc_now


class ProviderError(RuntimeError):
    """Base class for provider failures safe to expose to API callers."""


class MissingCredentialError(ProviderError):
    pass


class UnsupportedCapabilityError(ProviderError):
    pass


class ProviderUnavailableError(ProviderError):
    pass


class ProviderHTTPError(ProviderError):
    def __init__(self, provider: str, status_code: int, retryable: bool) -> None:
        super().__init__(f"{provider} returned HTTP {status_code}")
        self.status_code = status_code
        self.retryable = retryable


class ProviderProtocolError(ProviderError):
    pass


@dataclass(frozen=True, slots=True)
class RetryPolicy:
    max_attempts: int = 3
    base_delay_seconds: float = 0.25
    max_delay_seconds: float = 4.0
    jitter_ratio: float = 0.15

    def __post_init__(self) -> None:
        if self.max_attempts < 1:
            raise ValueError("max_attempts must be at least one")


@dataclass(frozen=True, slots=True)
class HTTPPayload:
    data: Any
    headers: Mapping[str, str]
    status_code: int
    received_at: datetime
    from_cache: bool = False

    @property
    def data_origin(self) -> DataOrigin:
        return DataOrigin.CACHE if self.from_cache else DataOrigin.LIVE_POLL


@dataclass(slots=True)
class _Metrics:
    requests_attempted: int = 0
    errors: int = 0
    consecutive_failures: int = 0
    rate_limit_events: int = 0
    last_success_at: datetime | None = None
    last_error_at: datetime | None = None
    last_error: str | None = None
    last_http_status: int | None = None
    last_latency_ms: float | None = None
    rate_limit: RateLimitInfo | None = None


class AsyncTTLCache:
    """Small in-process TTL cache; durable cache belongs in the ingestion layer."""

    def __init__(self, max_entries: int = 512) -> None:
        self._max_entries = max_entries
        self._values: OrderedDict[str, tuple[float, HTTPPayload]] = OrderedDict()
        self._lock = asyncio.Lock()

    async def get(self, key: str) -> HTTPPayload | None:
        async with self._lock:
            entry = self._values.get(key)
            if entry is None:
                return None
            expires_at, value = entry
            if expires_at <= time.monotonic():
                self._values.pop(key, None)
                return None
            self._values.move_to_end(key)
            return replace(value, from_cache=True)

    async def set(self, key: str, value: HTTPPayload, ttl_seconds: float) -> None:
        if ttl_seconds <= 0:
            return
        async with self._lock:
            self._values[key] = (time.monotonic() + ttl_seconds, value)
            self._values.move_to_end(key)
            while len(self._values) > self._max_entries:
                self._values.popitem(last=False)

    async def clear(self) -> None:
        async with self._lock:
            self._values.clear()


class MarketDataAdapter(ABC):
    descriptor: ProviderDescriptor
    RETRYABLE_STATUS_CODES = frozenset({408, 425, 429, 500, 502, 503, 504})

    def __init__(
        self,
        *,
        client: httpx.AsyncClient | None = None,
        retry_policy: RetryPolicy | None = None,
        cache: AsyncTTLCache | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        now: Callable[[], datetime] = utc_now,
        timeout_seconds: float = 10.0,
    ) -> None:
        self._client = client or httpx.AsyncClient(
            timeout=httpx.Timeout(timeout_seconds),
            follow_redirects=False,
            headers={"User-Agent": "EDGEOS/0.1 read-only-market-research"},
        )
        self._owns_client = client is None
        self._retry_policy = retry_policy or RetryPolicy()
        self._cache = cache or AsyncTTLCache()
        self._sleep = sleep
        self._now = now
        self._metrics = _Metrics()
        self._inflight: dict[str, asyncio.Task[HTTPPayload]] = {}
        self._inflight_lock = asyncio.Lock()
        # Shared by all routes on this adapter and retained across worker cycles.
        # A provider's Retry-After must not be shortened to our local sleep cap.
        self._retry_not_before = 0.0

    @abstractmethod
    async def list_events(
        self, query: EventQuery | None = None
    ) -> ProviderPage[NormalizedEvent]:
        raise NotImplementedError

    @abstractmethod
    async def list_markets(
        self, query: MarketQuery | None = None
    ) -> ProviderPage[NormalizedMarket]:
        raise NotImplementedError

    @abstractmethod
    async def get_order_book(self, query: OrderBookQuery) -> NormalizedOrderBook:
        raise NotImplementedError

    @abstractmethod
    async def check_health(self) -> ProviderHealth:
        raise NotImplementedError

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def __aenter__(self) -> MarketDataAdapter:
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.aclose()

    @property
    def maximum_request_attempts(self) -> int:
        """Upper bound used by the worker's durable request reservation."""
        return self._retry_policy.max_attempts

    def health_snapshot(
        self,
        *,
        state: ProviderState | None = None,
        message: str | None = None,
        known_refresh_interval_seconds: float | None = None,
        measured_data_delay_seconds: float | None = None,
    ) -> ProviderHealth:
        cooling_down = self._retry_not_before > time.monotonic()
        if cooling_down and state in (
            None, ProviderState.CONNECTED, ProviderState.DISCONNECTED, ProviderState.DELAYED
        ):
            state = ProviderState.DELAYED
        elif state is None:
            if self._metrics.consecutive_failures > 0:
                state = ProviderState.DISCONNECTED
            elif self._metrics.last_success_at is not None:
                state = ProviderState.CONNECTED
            elif self._metrics.last_error_at is not None:
                state = ProviderState.DISCONNECTED
            else:
                state = ProviderState.UNKNOWN
        return ProviderHealth(
            provider=self.descriptor.code,
            state=state,
            checked_at=self._now(),
            last_success_at=self._metrics.last_success_at,
            last_error_at=self._metrics.last_error_at,
            last_error=self._metrics.last_error,
            last_http_status=self._metrics.last_http_status,
            last_latency_ms=self._metrics.last_latency_ms,
            requests_attempted=self._metrics.requests_attempted,
            errors=self._metrics.errors,
            consecutive_failures=self._metrics.consecutive_failures,
            rate_limit_events=self._metrics.rate_limit_events,
            rate_limit=self._metrics.rate_limit,
            known_refresh_interval_seconds=known_refresh_interval_seconds,
            measured_data_delay_seconds=measured_data_delay_seconds,
            message=message,
        )

    async def _request_json(
        self,
        method: str,
        url: str,
        *,
        params: Mapping[str, Any] | None = None,
        json_body: Any = None,
        headers: Mapping[str, str] | None = None,
        cache_ttl_seconds: float = 0,
        deduplicate: bool = True,
    ) -> HTTPPayload:
        self._raise_if_cooling_down()
        method = method.upper()
        key = self._request_key(method, url, params, json_body)
        if method == "GET" and cache_ttl_seconds > 0:
            cached = await self._cache.get(key)
            if cached is not None:
                return cached

        if not deduplicate:
            payload = await self._request_json_uncached(
                method, url, params=params, json_body=json_body, headers=headers
            )
            if method == "GET":
                await self._cache.set(key, payload, cache_ttl_seconds)
            return payload

        created = False
        async with self._inflight_lock:
            task = self._inflight.get(key)
            if task is None:
                task = asyncio.create_task(
                    self._request_json_uncached(
                        method,
                        url,
                        params=params,
                        json_body=json_body,
                        headers=headers,
                    )
                )
                self._inflight[key] = task
                created = True
        try:
            payload = await task
            if created and method == "GET":
                await self._cache.set(key, payload, cache_ttl_seconds)
            return payload
        finally:
            if created:
                async with self._inflight_lock:
                    if self._inflight.get(key) is task:
                        self._inflight.pop(key, None)

    async def _request_json_uncached(
        self,
        method: str,
        url: str,
        *,
        params: Mapping[str, Any] | None,
        json_body: Any,
        headers: Mapping[str, str] | None,
    ) -> HTTPPayload:
        policy = self._retry_policy
        for attempt in range(1, policy.max_attempts + 1):
            self._raise_if_cooling_down()
            started = time.perf_counter()
            self._metrics.requests_attempted += 1
            try:
                response = await self._client.request(
                    method,
                    url,
                    params=params,
                    json=json_body,
                    headers=headers,
                )
            except httpx.RequestError as error:
                self._record_failure(type(error).__name__, None, started)
                if attempt >= policy.max_attempts:
                    raise ProviderUnavailableError(
                        f"{self.descriptor.display_name} could not be reached"
                    ) from None
                await self._sleep(self._backoff(attempt, None))
                continue

            self._metrics.last_http_status = response.status_code
            rate_limit = self._parse_rate_limit(response.headers)
            self._metrics.rate_limit = rate_limit
            if response.status_code in self.RETRYABLE_STATUS_CODES:
                if response.status_code == 429:
                    self._metrics.rate_limit_events += 1
                self._record_failure(
                    f"HTTP {response.status_code}", response.status_code, started
                )
                retry_after = (
                    rate_limit.retry_after_seconds if rate_limit is not None else None
                )
                if retry_after is not None and retry_after > 0:
                    self._retry_not_before = max(
                        self._retry_not_before, time.monotonic() + retry_after
                    )
                if attempt < policy.max_attempts and (
                    retry_after is None or retry_after <= policy.max_delay_seconds
                ):
                    await self._sleep(self._backoff(attempt, retry_after))
                    continue
                raise ProviderHTTPError(
                    self.descriptor.display_name,
                    response.status_code,
                    retryable=True,
                )
            if response.is_error:
                self._record_failure(
                    f"HTTP {response.status_code}", response.status_code, started
                )
                raise ProviderHTTPError(
                    self.descriptor.display_name,
                    response.status_code,
                    retryable=False,
                )

            try:
                data = response.json()
            except (json.JSONDecodeError, ValueError) as exc:
                self._record_failure("invalid JSON response", response.status_code, started)
                raise ProviderProtocolError(
                    f"{self.descriptor.display_name} returned invalid JSON"
                ) from exc

            received_at = self._now()
            self._metrics.last_success_at = received_at
            self._metrics.last_latency_ms = (time.perf_counter() - started) * 1000
            self._metrics.consecutive_failures = 0
            self._metrics.last_error = None
            return HTTPPayload(
                data=data,
                headers=dict(response.headers),
                status_code=response.status_code,
                received_at=received_at,
            )
        raise AssertionError("retry loop exhausted")

    def _raise_if_cooling_down(self) -> None:
        remaining = self._retry_not_before - time.monotonic()
        if remaining > 0:
            raise ProviderUnavailableError(
                f"{self.descriptor.display_name} requests delayed by provider "
                f"Retry-After ({remaining:.1f}s remaining)"
            )

    def _record_failure(
        self, message: str, status_code: int | None, started: float
    ) -> None:
        self._metrics.errors += 1
        self._metrics.consecutive_failures += 1
        self._metrics.last_error_at = self._now()
        self._metrics.last_error = message
        self._metrics.last_http_status = status_code
        self._metrics.last_latency_ms = (time.perf_counter() - started) * 1000

    def _backoff(self, attempt: int, retry_after: float | None) -> float:
        if retry_after is not None:
            return max(retry_after, 0)
        delay = min(
            self._retry_policy.base_delay_seconds * (2 ** (attempt - 1)),
            self._retry_policy.max_delay_seconds,
        )
        jitter = delay * self._retry_policy.jitter_ratio * random.random()
        return delay + jitter

    @staticmethod
    def _request_key(
        method: str,
        url: str,
        params: Mapping[str, Any] | None,
        body: Any,
    ) -> str:
        return json.dumps(
            [method, url, params or {}, body],
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        )

    @staticmethod
    def _parse_rate_limit(headers: Mapping[str, str]) -> RateLimitInfo | None:
        lower = {key.lower(): value for key, value in headers.items()}
        retry_after = MarketDataAdapter._retry_after_seconds(lower.get("retry-after"))
        result = RateLimitInfo(
            limit=integer_or_none(lower.get("x-ratelimit-limit")),
            remaining=integer_or_none(
                lower.get("x-requests-remaining")
                or lower.get("x-ratelimit-remaining")
            ),
            used=integer_or_none(lower.get("x-requests-used")),
            last_request_cost=integer_or_none(lower.get("x-requests-last")),
            retry_after_seconds=retry_after,
            reset_at=parse_datetime(lower.get("x-ratelimit-reset")),
        )
        if all(
            value is None
            for value in (
                result.limit,
                result.remaining,
                result.used,
                result.last_request_cost,
                result.retry_after_seconds,
                result.reset_at,
            )
        ):
            return None
        return result

    @staticmethod
    def _retry_after_seconds(value: str | None) -> float | None:
        if value is None:
            return None
        try:
            seconds = float(value)
            return max(seconds, 0) if math.isfinite(seconds) else None
        except ValueError:
            try:
                retry_at = parsedate_to_datetime(value)
            except (TypeError, ValueError):
                return None
            if retry_at.tzinfo is None:
                retry_at = retry_at.replace(tzinfo=UTC)
            return max((retry_at - datetime.now(UTC)).total_seconds(), 0)

