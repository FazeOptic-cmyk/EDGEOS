"""Polymarket US public gateway adapter.

Polymarket US is a distinct exchange product and never shares provider IDs or
order books with the Polymarket International Gamma/CLOB adapter.
"""

from __future__ import annotations

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
    json_list,
    nested_mapping,
    parse_datetime,
)


class PolymarketUSAdapter(MarketDataAdapter):
    BASE_URL = "https://gateway.polymarket.us/v1"
    descriptor = ProviderDescriptor(
        code=ProviderCode.POLYMARKET_US,
        display_name="Polymarket US",
        product_scope="Polymarket US regulated event contracts",
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
            "https://docs.polymarket.us/getting-started/quickstart",
            "https://docs.polymarket.us/api-reference/events/get-events",
            "https://docs.polymarket.us/api-reference/markets/get-markets",
            "https://docs.polymarket.us/api-reference/markets/get-market-bbo",
            "https://docs.polymarket.us/api-reference/markets/get-market-book",
        ),
        notes=(
            "Public REST market-data endpoints require no authentication.",
            "Institutional ISV and gRPC data access uses separate onboarding "
            "and credentials and is not claimed here.",
            "Polymarket US IDs and books are not interchangeable with Polymarket International.",
        ),
    )

    EVENT_FILTERS = frozenset(
        {
            "active",
            "closed",
            "archived",
            "tagSlug",
            "tagIds",
            "seriesId",
            "gameId",
            "categories",
            "marketTypes",
            "live",
            "sportsMarketTypes",
            "startTimeMin",
            "startTimeMax",
        }
    )
    MARKET_FILTERS = frozenset(
        {
            "active",
            "closed",
            "archived",
            "id",
            "slug",
            "volumeNumMin",
            "liquidityNumMin",
            "sportsMarketTypes",
            "orderBy",
            "orderDirection",
        }
    )

    async def list_events(
        self, query: EventQuery | None = None
    ) -> ProviderPage[NormalizedEvent]:
        query = query or EventQuery()
        offset = self._offset(query.cursor)
        params = allowed_filters(query.filters, self.EVENT_FILTERS)
        params.setdefault("active", "true")
        params.setdefault("closed", "false")
        params.update({"limit": min(query.limit, 100), "offset": offset})
        payload = await self._request_json(
            "GET", f"{self.BASE_URL}/events", params=params, cache_ttl_seconds=10
        )
        data = payload.data if isinstance(payload.data, Mapping) else {}
        raw_events_value = data.get("events")
        raw_events: list[Any] = (
            raw_events_value if isinstance(raw_events_value, list) else []
        )
        items = tuple(
            self._event(item, payload) for item in raw_events if isinstance(item, Mapping)
        )
        next_cursor = str(offset + len(raw_events)) if len(raw_events) == params["limit"] else None
        return ProviderPage(
            items=items,
            next_cursor=next_cursor,
            retrieved_at=payload.received_at,
            data_origin=payload.data_origin,
        )

    def _event(self, item: Mapping[str, Any], payload: Any) -> NormalizedEvent:
        event_id = first_text(item, "id") or ""
        primary_tag = nested_mapping(item.get("primaryTag"))
        sport = nested_mapping(primary_tag.get("sport"))
        league = nested_mapping(primary_tag.get("league"))
        status = first_text(item, "status")
        if status is None:
            if item.get("closed") is True:
                status = "closed"
            elif item.get("active") is True:
                status = "open"
        return NormalizedEvent(
            provider=ProviderCode.POLYMARKET_US,
            provider_event_id=event_id,
            title=first_text(item, "title") or event_id,
            subtitle=first_text(item, "subtitle"),
            sport=first_text(sport, "name", "slug"),
            league=first_text(league, "name", "slug"),
            home_team=first_text(item, "homeTeam", "home_team"),
            away_team=first_text(item, "awayTeam", "away_team"),
            scheduled_start=parse_datetime(
                item.get("startTime") or item.get("eventDate") or item.get("startDate")
            ),
            status=status,
            resolution_source=first_text(item, "resolutionSource"),
            source_url=f"{self.BASE_URL}/events/{quote(event_id, safe='')}",
            provider_timestamp=parse_datetime(item.get("updatedAt")),
            retrieved_at=payload.received_at,
            data_origin=payload.data_origin,
            metadata={
                "slug": item.get("slug"),
                "category": item.get("category"),
                "game_id": item.get("gameId"),
                "us_product": True,
            },
        )

    async def list_markets(
        self, query: MarketQuery | None = None
    ) -> ProviderPage[NormalizedMarket]:
        query = query or MarketQuery()
        offset = self._offset(query.cursor)
        params = allowed_filters(query.filters, self.MARKET_FILTERS)
        params.setdefault("active", "true")
        params.setdefault("closed", "false")
        params.update({"limit": min(query.limit, 100), "offset": offset})
        payload = await self._request_json(
            "GET", f"{self.BASE_URL}/markets", params=params, cache_ttl_seconds=2
        )
        data = payload.data if isinstance(payload.data, Mapping) else {}
        raw_markets_value = data.get("markets")
        raw_markets: list[Any] = (
            raw_markets_value if isinstance(raw_markets_value, list) else []
        )
        items = tuple(
            self._market(item, payload) for item in raw_markets if isinstance(item, Mapping)
        )
        next_cursor = (
            str(offset + len(raw_markets))
            if len(raw_markets) == params["limit"]
            else None
        )
        return ProviderPage(
            items=items,
            next_cursor=next_cursor,
            retrieved_at=payload.received_at,
            data_origin=payload.data_origin,
        )

    def _market(self, item: Mapping[str, Any], payload: Any) -> NormalizedMarket:
        market_id = first_text(item, "id") or ""
        slug = first_text(item, "slug")
        status = first_text(item, "status", "ep3Status")
        if status is None:
            if item.get("closed") is True:
                status = "closed"
            elif item.get("active") is True:
                status = "open"
        return NormalizedMarket(
            provider=ProviderCode.POLYMARKET_US,
            provider_market_id=market_id,
            provider_market_slug=slug,
            provider_event_id=first_text(item, "eventId"),
            title=first_text(item, "question", "title") or market_id,
            market_type=first_text(
                item, "sportsMarketTypeV2", "sportsMarketType", "marketType"
            ),
            period=first_text(item, "period"),
            status=status,
            event_start_time=parse_datetime(
                item.get("gameStartTime") or item.get("startDate")
            ),
            close_time=parse_datetime(item.get("endDate")),
            outcomes=self._outcomes(item),
            settlement=SettlementMetadata(
                primary_rules=first_text(item, "description"),
                secondary_rules=first_text(item, "rulesDisclaimer"),
                resolution_source=first_text(item, "resolutionSource"),
            ),
            liquidity=decimal_or_none(item.get("liquidity")),
            quote_timestamp=parse_datetime(item.get("ep3SyncedAt") or item.get("updatedAt")),
            retrieved_at=payload.received_at,
            data_origin=payload.data_origin,
            source_url=(
                f"{self.BASE_URL}/markets/{quote(slug, safe='')}"
                if slug
                else f"{self.BASE_URL}/markets"
            ),
            metadata={
                "minimum_trade_quantity": item.get("minimumTradeQty"),
                "fee_coefficient": item.get("feeCoefficient"),
                "us_product": True,
            },
        )

    @staticmethod
    def _outcomes(item: Mapping[str, Any]) -> tuple[NormalizedOutcome, ...]:
        sides = item.get("marketSides")
        if isinstance(sides, list) and sides:
            outcomes = []
            for side in sides:
                if not isinstance(side, Mapping):
                    continue
                is_long = side.get("long") if isinstance(side.get("long"), bool) else None
                outcomes.append(
                    NormalizedOutcome(
                        provider_outcome_id=first_text(side, "identifier", "id"),
                        name=first_text(side, "description", "identifier"),
                        description=first_text(side, "description"),
                        side="long" if is_long is True else "short" if is_long is False else None,
                        line=None,
                        offered_price=None,
                        best_bid=decimal_or_none(item.get("bestBidQuote")) if is_long else None,
                        best_ask=decimal_or_none(item.get("bestAskQuote")) if is_long else None,
                        bid_size=None,
                        ask_size=None,
                        last_price=decimal_or_none(side.get("quote")),
                        price_type=PriceType.CONTRACT_DOLLARS,
                        currency=first_text(nested_mapping(side.get("quote")), "currency") or "USD",
                    )
                )
            return tuple(outcomes)
        names = json_list(item.get("outcomes"))
        prices = json_list(item.get("outcomePrices"))
        return tuple(
            NormalizedOutcome(
                provider_outcome_id=None,
                name=str(name),
                description=None,
                side=str(name).lower() if str(name).lower() in {"yes", "no"} else None,
                line=None,
                offered_price=None,
                best_bid=decimal_or_none(item.get("bestBidQuote")) if index == 0 else None,
                best_ask=decimal_or_none(item.get("bestAskQuote")) if index == 0 else None,
                bid_size=None,
                ask_size=None,
                last_price=decimal_or_none(prices[index]) if index < len(prices) else None,
                price_type=PriceType.CONTRACT_DOLLARS,
                currency="USD",
            )
            for index, name in enumerate(names)
        )

    async def get_order_book(self, query: OrderBookQuery) -> NormalizedOrderBook:
        slug = quote(query.market_ref, safe="")
        payload = await self._request_json(
            "GET",
            f"{self.BASE_URL}/markets/{slug}/book",
            cache_ttl_seconds=1,
        )
        data = payload.data if isinstance(payload.data, Mapping) else {}
        market_data = nested_mapping(data.get("marketData"))
        bids = self._levels(market_data.get("bids"), reverse=True, depth=query.depth)
        asks = self._levels(market_data.get("offers"), reverse=False, depth=query.depth)
        stats = nested_mapping(market_data.get("stats"))
        return NormalizedOrderBook(
            provider=ProviderCode.POLYMARKET_US,
            provider_market_id=query.market_ref,
            outcomes=(
                OutcomeOrderBook(
                    provider_outcome_id=query.outcome_id,
                    outcome_name=None,
                    bids=bids,
                    asks=asks,
                    last_trade_price=decimal_or_none(
                        stats.get("lastTradePx") or market_data.get("lastTradePx")
                    ),
                ),
            ),
            # The public Gateway schema calls the book snapshot timestamp
            # ``transactTime``.  Keep it distinct from our HTTP retrieval time;
            # a successful re-fetch must never make an old book look fresh.
            provider_timestamp=parse_datetime(market_data.get("transactTime")),
            retrieved_at=payload.received_at,
            data_origin=payload.data_origin,
            source_url=f"{self.BASE_URL}/markets/{slug}/book",
            metadata={"state": market_data.get("state"), "us_product": True},
        )

    @staticmethod
    def _levels(raw_levels: Any, *, reverse: bool, depth: int | None):
        levels: list[OrderBookLevel] = []
        if isinstance(raw_levels, list):
            for raw in raw_levels:
                if not isinstance(raw, Mapping):
                    continue
                price = decimal_or_none(raw.get("px"))
                size = decimal_or_none(raw.get("qty"))
                if price is not None and size is not None:
                    levels.append(OrderBookLevel(price, size))
        levels.sort(key=lambda level: level.price, reverse=reverse)
        return tuple(levels[:depth] if depth is not None else levels)

    @staticmethod
    def _offset(cursor: str | None) -> int:
        if cursor is None:
            return 0
        try:
            offset = int(cursor)
        except ValueError as exc:
            raise ValueError("Polymarket US cursor must be a numeric offset") from exc
        if offset < 0:
            raise ValueError("Polymarket US cursor cannot be negative")
        return offset

    async def check_health(self):
        try:
            await self._request_json(
                "GET",
                f"{self.BASE_URL}/markets",
                params={"limit": 1, "offset": 0},
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
            message="Polymarket US public gateway reachable; this is not the international CLOB.",
        )

