"""Provider-neutral market-data contracts.

The models intentionally preserve missing values as ``None``.  In particular, a
retrieval timestamp is not a substitute for a provider quote timestamp and a
display price is not silently promoted to an executable ask.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any, Generic, Mapping, TypeVar


class ProviderCode(StrEnum):
    KALSHI = "kalshi"
    POLYMARKET_INTERNATIONAL = "polymarket_international"
    POLYMARKET_US = "polymarket_us"
    THE_ODDS_API = "the_odds_api"
    DEMO = "demo"


class ProviderState(StrEnum):
    UNKNOWN = "unknown"
    CONNECTED = "connected"
    DELAYED = "delayed"
    DISCONNECTED = "disconnected"
    REQUIRES_API_KEY = "requires_api_key"
    NOT_SUPPORTED = "not_supported"
    DEMO_ONLY = "demo_only"


class AccessMode(StrEnum):
    PUBLIC = "public"
    API_KEY_REQUIRED = "api_key_required"
    NOT_SUPPORTED = "not_supported"
    DEMO_ONLY = "demo_only"


class FeedType(StrEnum):
    REST_POLLING = "rest_polling"
    REALTIME_STREAM = "realtime_stream"
    HISTORICAL = "historical"
    DEMO = "demo"


class DataOrigin(StrEnum):
    LIVE_POLL = "live_poll"
    CACHE = "cache"
    HISTORICAL = "historical"
    DEMO = "demo"


class ProviderCapability(StrEnum):
    EVENT_DISCOVERY = "event_discovery"
    MARKET_DISCOVERY = "market_discovery"
    CURRENT_QUOTES = "current_quotes"
    BEST_BID_ASK = "best_bid_ask"
    ORDER_BOOK_DEPTH = "order_book_depth"
    QUOTE_SIZE = "quote_size"
    EVENT_START_TIME = "event_start_time"
    MARKET_STATUS = "market_status"
    PROVIDER_TIMESTAMPS = "provider_timestamps"
    RESOLUTION_RULES = "resolution_rules"
    SOURCE_REFERENCE = "source_reference"
    API_QUOTA = "api_quota"
    HEALTH_DIAGNOSTICS = "health_diagnostics"


class PriceType(StrEnum):
    CONTRACT_DOLLARS = "contract_dollars"
    DECIMAL_ODDS = "decimal_odds"


@dataclass(frozen=True, slots=True)
class ProviderDescriptor:
    code: ProviderCode
    display_name: str
    product_scope: str
    access_mode: AccessMode
    capabilities: frozenset[ProviderCapability]
    feed_types: frozenset[FeedType]
    api_base_urls: tuple[str, ...]
    official_docs_urls: tuple[str, ...]
    credential_environment_variables: tuple[str, ...] = ()
    notes: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class RateLimitInfo:
    limit: int | None = None
    remaining: int | None = None
    used: int | None = None
    last_request_cost: int | None = None
    retry_after_seconds: float | None = None
    reset_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class ProviderHealth:
    provider: ProviderCode
    state: ProviderState
    checked_at: datetime
    last_success_at: datetime | None
    last_error_at: datetime | None
    last_error: str | None
    last_http_status: int | None
    last_latency_ms: float | None
    requests_attempted: int
    errors: int
    consecutive_failures: int
    rate_limit_events: int
    rate_limit: RateLimitInfo | None
    known_refresh_interval_seconds: float | None = None
    measured_data_delay_seconds: float | None = None
    message: str | None = None


@dataclass(frozen=True, slots=True)
class EventQuery:
    limit: int = 100
    cursor: str | None = None
    filters: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.limit < 1:
            raise ValueError("limit must be positive")


@dataclass(frozen=True, slots=True)
class MarketQuery:
    limit: int = 100
    cursor: str | None = None
    filters: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.limit < 1:
            raise ValueError("limit must be positive")


@dataclass(frozen=True, slots=True)
class OrderBookQuery:
    """Request an order book using the provider's documented lookup reference.

    ``market_ref`` is a ticker for Kalshi and a slug for Polymarket US.
    Polymarket International order books are outcome-scoped, so ``outcome_id``
    must contain the CLOB asset ID (token ID for v1 or position ID for v2).
    """

    market_ref: str
    outcome_id: str | None = None
    depth: int | None = None

    def __post_init__(self) -> None:
        if not self.market_ref:
            raise ValueError("market_ref is required")
        if self.depth is not None and self.depth < 1:
            raise ValueError("depth must be positive")


@dataclass(frozen=True, slots=True)
class SettlementMetadata:
    primary_rules: str | None = None
    secondary_rules: str | None = None
    resolution_source: str | None = None
    early_close_condition: str | None = None
    can_close_early: bool | None = None
    expiration_value: str | None = None
    void_treatment: str | None = None
    push_treatment: str | None = None


@dataclass(frozen=True, slots=True)
class NormalizedEvent:
    provider: ProviderCode
    provider_event_id: str
    title: str
    subtitle: str | None
    sport: str | None
    league: str | None
    home_team: str | None
    away_team: str | None
    scheduled_start: datetime | None
    status: str | None
    resolution_source: str | None
    source_url: str
    provider_timestamp: datetime | None
    retrieved_at: datetime
    data_origin: DataOrigin
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class NormalizedOutcome:
    provider_outcome_id: str | None
    name: str | None
    description: str | None
    side: str | None
    line: Decimal | None
    offered_price: Decimal | None
    best_bid: Decimal | None
    best_ask: Decimal | None
    bid_size: Decimal | None
    ask_size: Decimal | None
    last_price: Decimal | None
    price_type: PriceType
    currency: str | None
    maximum_stake: Decimal | None = None
    stake_currency: str | None = None


@dataclass(frozen=True, slots=True)
class NormalizedMarket:
    provider: ProviderCode
    provider_market_id: str
    provider_market_slug: str | None
    provider_event_id: str | None
    title: str
    market_type: str | None
    period: str | None
    status: str | None
    event_start_time: datetime | None
    close_time: datetime | None
    outcomes: tuple[NormalizedOutcome, ...]
    settlement: SettlementMetadata | None
    liquidity: Decimal | None
    quote_timestamp: datetime | None
    retrieved_at: datetime
    data_origin: DataOrigin
    source_url: str
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class OrderBookLevel:
    price: Decimal
    size: Decimal


@dataclass(frozen=True, slots=True)
class OutcomeOrderBook:
    provider_outcome_id: str | None
    outcome_name: str | None
    bids: tuple[OrderBookLevel, ...]
    asks: tuple[OrderBookLevel, ...]
    last_trade_price: Decimal | None = None

    @property
    def best_bid(self) -> OrderBookLevel | None:
        return self.bids[0] if self.bids else None

    @property
    def best_ask(self) -> OrderBookLevel | None:
        return self.asks[0] if self.asks else None


@dataclass(frozen=True, slots=True)
class NormalizedOrderBook:
    provider: ProviderCode
    provider_market_id: str
    outcomes: tuple[OutcomeOrderBook, ...]
    provider_timestamp: datetime | None
    retrieved_at: datetime
    data_origin: DataOrigin
    source_url: str
    state_hash: str | None = None
    minimum_order_size: Decimal | None = None
    tick_size: Decimal | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)


T = TypeVar("T")


@dataclass(frozen=True, slots=True)
class ProviderPage(Generic[T]):
    items: tuple[T, ...]
    next_cursor: str | None
    retrieved_at: datetime
    data_origin: DataOrigin

