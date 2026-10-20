"""Read-only Kalshi adapter using the public Trade API v2 endpoints.

Official REST documentation rechecked 2026-10-20. Public REST market data does not
require credentials; Kalshi WebSocket market data does require authentication and
is therefore not claimed as an implemented feed here.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any, Mapping
from urllib.parse import quote

from .base import MarketDataAdapter, ProviderError
from .models import (
    AccessMode,
    EventQuery,
    FeedType,
    MarketQuery,
    NormalizedEvent,
    NormalizedMarket,
    NormalizedOrderBook,
    NormalizedOutcome,
    OrderBookLevel,
    OrderBookQuery,
    OutcomeOrderBook,
    PriceType,
    ProviderCapability,
    ProviderCode,
    ProviderDescriptor,
    ProviderPage,
    ProviderState,
    SettlementMetadata,
)
from .parsing import (
    allowed_filters,
    decimal_or_none,
    first_text,
    nested_mapping,
    parse_datetime,
)


class KalshiAdapter(MarketDataAdapter):
    BASE_URL = "https://external-api.kalshi.com/trade-api/v2"
    descriptor = ProviderDescriptor(
        code=ProviderCode.KALSHI,
        display_name="Kalshi",
        product_scope="Kalshi US event contracts",
        access_mode=AccessMode.PUBLIC,
        capabilities=frozenset(
            {
                ProviderCapability.EVENT_DISCOVERY,
                ProviderCapability.MARKET_DISCOVERY,
                ProviderCapability.CURRENT_QUOTES,
                ProviderCapability.BEST_BID_ASK,
                ProviderCapability.ORDER_BOOK_DEPTH,
                ProviderCapability.QUOTE_SIZE,
                ProviderCapability.EVENT_START_TIME,
                ProviderCapability.MARKET_STATUS,
                ProviderCapability.PROVIDER_TIMESTAMPS,
                ProviderCapability.RESOLUTION_RULES,
                ProviderCapability.SOURCE_REFERENCE,
                ProviderCapability.HEALTH_DIAGNOSTICS,
            }
        ),
        feed_types=frozenset({FeedType.REST_POLLING}),
        api_base_urls=(BASE_URL,),
        official_docs_urls=(
            "https://docs.kalshi.com/getting_started/quick_start_market_data",
            "https://docs.kalshi.com/api-reference/market/get-markets",
            "https://docs.kalshi.com/api-reference/market/get-market-orderbook",
            "https://docs.kalshi.com/getting_started/rate_limits",
        ),
        notes=(
            "Order books publish YES and NO bids; asks are exact binary "
            "complements of opposing bids.",
            "REST responses do not guarantee an order-book timestamp; "
            "retrieval time is kept separate.",
        ),
    )

    EVENT_FILTERS = frozenset(
        {
            "status",
            "series_ticker",
            "with_nested_markets",
            "min_close_ts",
            "max_close_ts",
        }
    )
    MARKET_FILTERS = frozenset(
        {
            "status",
            "event_ticker",
            "series_ticker",
            "min_created_ts",
            "max_created_ts",
            "min_close_ts",
            "max_close_ts",
            "min_settled_ts",
            "max_settled_ts",
            "min_updated_ts",
            "max_updated_ts",
            "mve_filter",
        }
    )

    async def list_events(
        self, query: EventQuery | None = None
    ) -> ProviderPage[NormalizedEvent]:
        query = query or EventQuery()
        params = allowed_filters(query.filters, self.EVENT_FILTERS)
        params["limit"] = min(query.limit, 200)
        if query.cursor:
            params["cursor"] = query.cursor
        payload = await self._request_json(
            "GET", f"{self.BASE_URL}/events", params=params, cache_ttl_seconds=10
        )
        data = payload.data if isinstance(payload.data, Mapping) else {}
        raw_events_value = data.get("events")
        raw_events: list[Any] = (
            raw_events_value if isinstance(raw_events_value, list) else []
        )
        events = tuple(
            self._event(item, payload)
            for item in raw_events
            if isinstance(item, Mapping)
        )
        return ProviderPage(
            items=events,
            next_cursor=first_text(data, "cursor"),
            retrieved_at=payload.received_at,
            data_origin=payload.data_origin,
        )

    def _event(self, item: Mapping[str, Any], payload: Any) -> NormalizedEvent:
        event_id = first_text(item, "event_ticker", "ticker", "id") or ""
        metadata = nested_mapping(item.get("product_metadata"))
        return NormalizedEvent(
            provider=ProviderCode.KALSHI,
            provider_event_id=event_id,
            title=first_text(item, "title") or event_id,
            subtitle=first_text(item, "sub_title", "subtitle"),
            sport=first_text(metadata, "sport", "sport_name"),
            league=first_text(metadata, "league", "league_name"),
            home_team=first_text(metadata, "home_team", "homeTeam"),
            away_team=first_text(metadata, "away_team", "awayTeam"),
            scheduled_start=parse_datetime(
                metadata.get("scheduled_start")
                or item.get("event_date")
            ),
            status=first_text(item, "status"),
            resolution_source=first_text(item, "settlement_source", "resolution_source"),
            source_url=f"{self.BASE_URL}/events/{quote(event_id, safe='')}",
            provider_timestamp=parse_datetime(item.get("last_updated_ts")),
            retrieved_at=payload.received_at,
            data_origin=payload.data_origin,
            metadata={
                "series_ticker": item.get("series_ticker"),
                "category": item.get("category"),
                "mutually_exclusive": item.get("mutually_exclusive"),
            },
        )

    async def list_markets(
        self, query: MarketQuery | None = None
    ) -> ProviderPage[NormalizedMarket]:
        query = query or MarketQuery()
        params = allowed_filters(query.filters, self.MARKET_FILTERS)
        params["limit"] = min(query.limit, 200)
        if query.cursor:
            params["cursor"] = query.cursor
        payload = await self._request_json(
            "GET", f"{self.BASE_URL}/markets", params=params, cache_ttl_seconds=2
        )
        data = payload.data if isinstance(payload.data, Mapping) else {}
        raw_markets_value = data.get("markets")
        raw_markets: list[Any] = (
            raw_markets_value if isinstance(raw_markets_value, list) else []
        )
        markets = tuple(
            self._market(item, payload) for item in raw_markets if isinstance(item, Mapping)
        )
        return ProviderPage(
            items=markets,
            next_cursor=first_text(data, "cursor"),
            retrieved_at=payload.received_at,
            data_origin=payload.data_origin,
        )

    def _market(self, item: Mapping[str, Any], payload: Any) -> NormalizedMarket:
        ticker = first_text(item, "ticker") or ""
        outcomes = (
            NormalizedOutcome(
                provider_outcome_id=f"{ticker}:yes",
                name=first_text(item, "yes_sub_title") or "Yes",
                description=None,
                side="yes",
                line=None,
                offered_price=None,
                best_bid=decimal_or_none(item.get("yes_bid_dollars")),
                best_ask=decimal_or_none(item.get("yes_ask_dollars")),
                bid_size=decimal_or_none(item.get("yes_bid_size_fp")),
                ask_size=decimal_or_none(item.get("yes_ask_size_fp")),
                last_price=decimal_or_none(item.get("last_price_dollars")),
                price_type=PriceType.CONTRACT_DOLLARS,
                currency="USD",
            ),
            NormalizedOutcome(
                provider_outcome_id=f"{ticker}:no",
                name=first_text(item, "no_sub_title") or "No",
                description=None,
                side="no",
                line=None,
                offered_price=None,
                best_bid=decimal_or_none(item.get("no_bid_dollars")),
                best_ask=decimal_or_none(item.get("no_ask_dollars")),
                bid_size=decimal_or_none(item.get("no_bid_size_fp")),
                ask_size=decimal_or_none(item.get("no_ask_size_fp")),
                last_price=None,
                price_type=PriceType.CONTRACT_DOLLARS,
                currency="USD",
            ),
        )
        return NormalizedMarket(
            provider=ProviderCode.KALSHI,
            provider_market_id=ticker,
            provider_market_slug=None,
            provider_event_id=first_text(item, "event_ticker"),
            title=first_text(item, "title", "subtitle") or ticker,
            market_type=first_text(item, "market_type", "strike_type"),
            period=None,
            status=first_text(item, "status"),
            event_start_time=parse_datetime(item.get("occurrence_datetime")),
            close_time=parse_datetime(item.get("close_time")),
            outcomes=outcomes,
            settlement=SettlementMetadata(
                primary_rules=first_text(item, "rules_primary"),
                secondary_rules=first_text(item, "rules_secondary"),
                early_close_condition=first_text(item, "early_close_condition"),
                can_close_early=item.get("can_close_early")
                if isinstance(item.get("can_close_early"), bool)
                else None,
                expiration_value=first_text(item, "expiration_value"),
            ),
            liquidity=decimal_or_none(item.get("liquidity_dollars")),
            quote_timestamp=parse_datetime(item.get("updated_time")),
            retrieved_at=payload.received_at,
            data_origin=payload.data_origin,
            source_url=f"{self.BASE_URL}/markets/{quote(ticker, safe='')}",
            metadata={
                "notional_value_dollars": item.get("notional_value_dollars"),
                "fractional_trading_enabled": item.get("fractional_trading_enabled"),
                "price_level_structure": item.get("price_level_structure"),
            },
        )

    async def get_order_book(self, query: OrderBookQuery) -> NormalizedOrderBook:
        params: dict[str, int] = {}
        if query.depth is not None:
            if query.depth > 100:
                raise ValueError("Kalshi order-book depth cannot exceed 100")
            params["depth"] = query.depth
        ticker = quote(query.market_ref, safe="")
        payload = await self._request_json(
            "GET",
            f"{self.BASE_URL}/markets/{ticker}/orderbook",
            params=params,
            cache_ttl_seconds=1,
        )
        data = payload.data if isinstance(payload.data, Mapping) else {}
        fp = nested_mapping(data.get("orderbook_fp"))
        legacy = nested_mapping(data.get("orderbook"))
        yes_bids = self._levels(fp.get("yes_dollars"), legacy.get("yes"))
        no_bids = self._levels(fp.get("no_dollars"), legacy.get("no"))
        one = Decimal("1")
        yes_asks = tuple(
            sorted(
                (OrderBookLevel(one - level.price, level.size) for level in no_bids),
                key=lambda level: level.price,
            )
        )
        no_asks = tuple(
            sorted(
                (OrderBookLevel(one - level.price, level.size) for level in yes_bids),
                key=lambda level: level.price,
            )
        )
        return NormalizedOrderBook(
            provider=ProviderCode.KALSHI,
            provider_market_id=query.market_ref,
            outcomes=(
                OutcomeOrderBook(
                    provider_outcome_id=f"{query.market_ref}:yes",
                    outcome_name="Yes",
                    bids=yes_bids,
                    asks=yes_asks,
                ),
                OutcomeOrderBook(
                    provider_outcome_id=f"{query.market_ref}:no",
                    outcome_name="No",
                    bids=no_bids,
                    asks=no_asks,
                ),
            ),
            provider_timestamp=None,
            retrieved_at=payload.received_at,
            data_origin=payload.data_origin,
            source_url=f"{self.BASE_URL}/markets/{ticker}/orderbook",
            metadata={
                "ask_derivation": (
                    "A YES ask is 1 minus a NO bid, and vice versa, per "
                    "Kalshi binary-orderbook documentation."
                )
            },
        )

    @staticmethod
    def _levels(dollar_levels: Any, cent_levels: Any) -> tuple[OrderBookLevel, ...]:
        source = dollar_levels if isinstance(dollar_levels, list) else cent_levels
        is_cents = not isinstance(dollar_levels, list)
        levels: list[OrderBookLevel] = []
        if not isinstance(source, list):
            return ()
        for raw in source:
            if not isinstance(raw, (list, tuple)) or len(raw) < 2:
                continue
            price = decimal_or_none(raw[0])
            size = decimal_or_none(raw[1])
            if price is None or size is None:
                continue
            if is_cents:
                price /= Decimal("100")
            levels.append(OrderBookLevel(price=price, size=size))
        return tuple(sorted(levels, key=lambda level: level.price, reverse=True))

    async def check_health(self):
        try:
            await self._request_json(
                "GET",
                f"{self.BASE_URL}/exchange/status",
                cache_ttl_seconds=0,
                deduplicate=False,
            )
        except ProviderError as exc:
            return self.health_snapshot(
                state=ProviderState.DISCONNECTED,
                message=str(exc),
            )
        return self.health_snapshot(
            state=ProviderState.CONNECTED,
            message="Public Trade API reachable; this adapter uses read-only REST polling.",
        )

