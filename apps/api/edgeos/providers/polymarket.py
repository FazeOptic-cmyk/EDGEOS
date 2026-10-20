"""Polymarket International public Gamma/CLOB read-only adapter.

This product is intentionally modeled separately from Polymarket US.  Gamma IDs,
CLOB token/position IDs, settlement metadata, and endpoints are not interchangeable
with ``gateway.polymarket.us`` identifiers.
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


class PolymarketInternationalAdapter(MarketDataAdapter):
    GAMMA_URL = "https://gamma-api.polymarket.com"
    CLOB_URL = "https://clob.polymarket.com"
    DATA_URL = "https://data-api.polymarket.com"
    descriptor = ProviderDescriptor(
        code=ProviderCode.POLYMARKET_INTERNATIONAL,
        display_name="Polymarket International",
        product_scope="Polymarket international prediction markets",
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
        api_base_urls=(GAMMA_URL, CLOB_URL, DATA_URL),
        official_docs_urls=(
            "https://docs.polymarket.com/getting-started/api",
            "https://docs.polymarket.com/market-data/discover-markets",
            "https://docs.polymarket.com/market-data/prices-order-books",
        ),
        notes=(
            "International Gamma/CLOB IDs are not interchangeable with "
            "Polymarket US IDs and must never be joined directly.",
            "Executable research uses CLOB asks; Gamma display prices are "
            "retained only as non-executable reference fields.",
            "Protocol v1 uses token IDs while protocol v2 uses position IDs as CLOB asset IDs.",
        ),
    )

    EVENT_FILTERS = frozenset(
        {"closed", "tag_id", "game_id", "series_id", "slug", "id"}
    )
    MARKET_FILTERS = frozenset(
        {
            "closed",
            "tag_id",
            "sports_market_types",
            "game_id",
            "event_id",
            "slug",
            "id",
        }
    )

    async def list_events(
        self, query: EventQuery | None = None
    ) -> ProviderPage[NormalizedEvent]:
        query = query or EventQuery()
        params = allowed_filters(query.filters, self.EVENT_FILTERS)
        params.setdefault("closed", "false")
        params["limit"] = min(query.limit, 100)
        if query.cursor:
            params["after_cursor"] = query.cursor
        payload = await self._request_json(
            "GET",
            f"{self.GAMMA_URL}/events/keyset",
            params=params,
            cache_ttl_seconds=10,
        )
        data = payload.data if isinstance(payload.data, Mapping) else {}
        raw_events = data.get("events") or data.get("items")
        raw_events = raw_events if isinstance(raw_events, list) else []
        items = tuple(
            self._event(item, payload) for item in raw_events if isinstance(item, Mapping)
        )
        return ProviderPage(
            items=items,
            next_cursor=first_text(data, "next_cursor", "nextCursor"),
            retrieved_at=payload.received_at,
            data_origin=payload.data_origin,
        )

    def _event(self, item: Mapping[str, Any], payload: Any) -> NormalizedEvent:
        event_id = first_text(item, "id") or ""
        sports = nested_mapping(item.get("sports"))
        closed = item.get("closed")
        status = first_text(item, "status")
        if status is None and isinstance(closed, bool):
            status = "closed" if closed else "open"
        return NormalizedEvent(
            provider=ProviderCode.POLYMARKET_INTERNATIONAL,
            provider_event_id=event_id,
            title=first_text(item, "title") or event_id,
            subtitle=first_text(item, "subtitle"),
            sport=first_text(sports, "sport", "sportName") or first_text(item, "sport"),
            league=first_text(sports, "league", "leagueName") or first_text(item, "league"),
            home_team=first_text(sports, "homeTeam", "home_team"),
            away_team=first_text(sports, "awayTeam", "away_team"),
            scheduled_start=parse_datetime(
                item.get("startTime") or item.get("eventDate") or item.get("startDate")
            ),
            status=status,
            resolution_source=first_text(item, "resolutionSource", "resolution_source"),
            source_url=f"{self.GAMMA_URL}/events/{quote(event_id, safe='')}",
            provider_timestamp=parse_datetime(item.get("updatedAt")),
            retrieved_at=payload.received_at,
            data_origin=payload.data_origin,
            metadata={
                "slug": item.get("slug"),
                "category": item.get("category"),
                "game_id": item.get("gameId"),
            },
        )

    async def list_markets(
        self, query: MarketQuery | None = None
    ) -> ProviderPage[NormalizedMarket]:
        query = query or MarketQuery()
        params = allowed_filters(query.filters, self.MARKET_FILTERS)
        params.setdefault("closed", "false")
        params["limit"] = min(query.limit, 100)
        if query.cursor:
            params["after_cursor"] = query.cursor
        payload = await self._request_json(
            "GET",
            f"{self.GAMMA_URL}/markets/keyset",
            params=params,
            cache_ttl_seconds=2,
        )
        data = payload.data if isinstance(payload.data, Mapping) else {}
        raw_markets = data.get("markets") or data.get("items")
        raw_markets = raw_markets if isinstance(raw_markets, list) else []
        items = tuple(
            self._market(item, payload) for item in raw_markets if isinstance(item, Mapping)
        )
        return ProviderPage(
            items=items,
            next_cursor=first_text(data, "next_cursor", "nextCursor"),
            retrieved_at=payload.received_at,
            data_origin=payload.data_origin,
        )

    def _market(self, item: Mapping[str, Any], payload: Any) -> NormalizedMarket:
        market_id = first_text(item, "id") or ""
        slug = first_text(item, "slug")
        nested_events = item.get("events") if isinstance(item.get("events"), list) else []
        event = nested_events[0] if nested_events and isinstance(nested_events[0], Mapping) else {}
        sports = nested_mapping(item.get("sports"))
        closed = item.get("closed")
        status = first_text(item, "status")
        if status is None and isinstance(closed, bool):
            status = "closed" if closed else "open"
        return NormalizedMarket(
            provider=ProviderCode.POLYMARKET_INTERNATIONAL,
            provider_market_id=market_id,
            provider_market_slug=slug,
            provider_event_id=first_text(item, "eventId") or first_text(event, "id"),
            title=first_text(item, "question", "title") or market_id,
            market_type=first_text(
                sports, "sportsMarketType", "sports_market_type"
            ) or first_text(item, "sportsMarketType", "marketType"),
            period=first_text(sports, "period") or first_text(item, "period"),
            status=status,
            event_start_time=parse_datetime(
                item.get("gameStartTime")
                or item.get("eventDate")
                or item.get("startDate")
            ),
            close_time=parse_datetime(item.get("endDate")),
            outcomes=self._outcomes(item),
            settlement=SettlementMetadata(
                primary_rules=first_text(item, "rules", "description"),
                secondary_rules=first_text(item, "resolution"),
                resolution_source=first_text(
                    item, "resolutionSource", "resolution_source"
                ),
            ),
            liquidity=decimal_or_none(item.get("liquidityNum") or item.get("liquidity")),
            quote_timestamp=parse_datetime(item.get("updatedAt")),
            retrieved_at=payload.received_at,
            data_origin=payload.data_origin,
            source_url=f"{self.GAMMA_URL}/markets/{quote(market_id, safe='')}",
            metadata={
                "protocol_version": item.get("version"),
                "condition_id": item.get("conditionId"),
                "neg_risk": item.get("negRisk"),
                "international_product": True,
            },
        )

    @staticmethod
    def _outcomes(item: Mapping[str, Any]) -> tuple[NormalizedOutcome, ...]:
        version = str(item.get("version") or "v1").lower()
        raw_outcomes = item.get("outcomes")
        if isinstance(raw_outcomes, Mapping):
            outcomes: list[NormalizedOutcome] = []
            for side in ("yes", "no"):
                details = nested_mapping(raw_outcomes.get(side))
                asset_id = (
                    first_text(details, "positionId", "position_id")
                    if version == "v2"
                    else first_text(details, "tokenId", "token_id")
                )
                asset_id = asset_id or first_text(
                    details, "positionId", "position_id", "tokenId", "token_id"
                )
                outcomes.append(
                    NormalizedOutcome(
                        provider_outcome_id=asset_id,
                        name=side.capitalize(),
                        description=first_text(details, "description"),
                        side=side,
                        line=None,
                        offered_price=None,
                        best_bid=decimal_or_none(
                            details.get("bestBid") or details.get("best_bid")
                        ),
                        best_ask=decimal_or_none(
                            details.get("bestAsk") or details.get("best_ask")
                        ),
                        bid_size=decimal_or_none(
                            details.get("bestBidSize") or details.get("best_bid_size")
                        ),
                        ask_size=decimal_or_none(
                            details.get("bestAskSize") or details.get("best_ask_size")
                        ),
                        last_price=decimal_or_none(
                            details.get("lastTradePrice") or details.get("price")
                        ),
                        price_type=PriceType.CONTRACT_DOLLARS,
                        currency="pUSD" if version == "v2" else "USDC",
                    )
                )
            return tuple(outcomes)

        names = json_list(raw_outcomes)
        ids = json_list(
            item.get("positionIds") if version == "v2" else item.get("clobTokenIds")
        )
        if not ids:
            ids = json_list(item.get("positionIds")) or json_list(item.get("clobTokenIds"))
        prices = json_list(item.get("outcomePrices"))
        if not names and ids:
            names = ["Yes", "No"][: len(ids)]
        outcomes = []
        for index, name in enumerate(names):
            outcome_side = (
                str(name).lower() if str(name).lower() in {"yes", "no"} else None
            )
            outcomes.append(
                NormalizedOutcome(
                    provider_outcome_id=str(ids[index]) if index < len(ids) else None,
                    name=str(name),
                    description=None,
                    side=outcome_side,
                    line=None,
                    offered_price=None,
                    best_bid=decimal_or_none(item.get("bestBid")) if index == 0 else None,
                    best_ask=decimal_or_none(item.get("bestAsk")) if index == 0 else None,
                    bid_size=None,
                    ask_size=None,
                    last_price=decimal_or_none(prices[index]) if index < len(prices) else None,
                    price_type=PriceType.CONTRACT_DOLLARS,
                    currency="pUSD" if version == "v2" else "USDC",
                )
            )
        return tuple(outcomes)

    async def get_order_book(self, query: OrderBookQuery) -> NormalizedOrderBook:
        if not query.outcome_id:
            raise ValueError(
                "Polymarket International order books require an outcome_id "
                "(v1 token ID or v2 position ID); a Gamma market ID is not interchangeable."
            )
        payload = await self._request_json(
            "GET",
            f"{self.CLOB_URL}/book",
            params={"token_id": query.outcome_id},
            cache_ttl_seconds=1,
        )
        data = payload.data if isinstance(payload.data, Mapping) else {}
        bids = self._book_levels(data.get("bids"), reverse=True, depth=query.depth)
        asks = self._book_levels(data.get("asks"), reverse=False, depth=query.depth)
        return NormalizedOrderBook(
            provider=ProviderCode.POLYMARKET_INTERNATIONAL,
            provider_market_id=query.market_ref,
            outcomes=(
                OutcomeOrderBook(
                    provider_outcome_id=first_text(data, "asset_id") or query.outcome_id,
                    outcome_name=None,
                    bids=bids,
                    asks=asks,
                    last_trade_price=decimal_or_none(data.get("last_trade_price")),
                ),
            ),
            provider_timestamp=parse_datetime(data.get("timestamp")),
            retrieved_at=payload.received_at,
            data_origin=payload.data_origin,
            source_url=f"{self.CLOB_URL}/book",
            state_hash=first_text(data, "hash"),
            minimum_order_size=decimal_or_none(data.get("min_order_size")),
            tick_size=decimal_or_none(data.get("tick_size")),
            metadata={
                "condition_id": data.get("market"),
                "neg_risk": data.get("neg_risk"),
                "international_product": True,
            },
        )

    @staticmethod
    def _book_levels(
        raw_levels: Any, *, reverse: bool, depth: int | None
    ) -> tuple[OrderBookLevel, ...]:
        levels: list[OrderBookLevel] = []
        if isinstance(raw_levels, list):
            for raw in raw_levels:
                if not isinstance(raw, Mapping):
                    continue
                price = decimal_or_none(raw.get("price"))
                size = decimal_or_none(raw.get("size"))
                if price is not None and size is not None:
                    levels.append(OrderBookLevel(price, size))
        levels.sort(key=lambda level: level.price, reverse=reverse)
        return tuple(levels[:depth] if depth is not None else levels)

    async def check_health(self):
        try:
            await self._request_json(
                "GET",
                f"{self.GAMMA_URL}/markets/keyset",
                params={"closed": "false", "limit": 1},
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
            message=(
                "International Gamma market-data API reachable; identifiers "
                "are isolated from Polymarket US."
            ),
        )

