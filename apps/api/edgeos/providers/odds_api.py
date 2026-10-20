"""The Odds API v4 adapter for periodically refreshed sportsbook offers."""

from __future__ import annotations

from typing import Any, Mapping
from urllib.parse import quote

from .base import (
    MarketDataAdapter,
    MissingCredentialError,
    ProviderError,
    ProviderHTTPError,
    UnsupportedCapabilityError,
)
from .models import (
    AccessMode,
    EventQuery,
    FeedType,
    MarketQuery,
    NormalizedEvent,
    NormalizedMarket,
    NormalizedOutcome,
    OrderBookQuery,
    PriceType,
    ProviderCapability,
    ProviderCode,
    ProviderDescriptor,
    ProviderPage,
    ProviderState,
)
from .parsing import allowed_filters, decimal_or_none, first_text, parse_datetime


class TheOddsAPIAdapter(MarketDataAdapter):
    BASE_URL = "https://api.the-odds-api.com/v4"
    descriptor = ProviderDescriptor(
        code=ProviderCode.THE_ODDS_API,
        display_name="The Odds API",
        product_scope="Aggregated sportsbook odds where covered by the selected plan",
        access_mode=AccessMode.API_KEY_REQUIRED,
        capabilities=frozenset(
            {
                ProviderCapability.EVENT_DISCOVERY,
                ProviderCapability.MARKET_DISCOVERY,
                ProviderCapability.CURRENT_QUOTES,
                ProviderCapability.QUOTE_SIZE,
                ProviderCapability.EVENT_START_TIME,
                ProviderCapability.MARKET_STATUS,
                ProviderCapability.PROVIDER_TIMESTAMPS,
                ProviderCapability.SOURCE_REFERENCE,
                ProviderCapability.API_QUOTA,
                ProviderCapability.HEALTH_DIAGNOSTICS,
            }
        ),
        feed_types=frozenset({FeedType.REST_POLLING}),
        api_base_urls=(BASE_URL,),
        official_docs_urls=(
            "https://the-odds-api.com/liveapi/guides/v4/",
            "https://the-odds-api.com/guide/rate-limit.html",
        ),
        credential_environment_variables=("THE_ODDS_API_KEY",),
        notes=(
            "Sportsbook coverage depends on sport, region, bookmaker, and subscription plan.",
            "The feed is periodically refreshed and is not labeled immediately executable.",
            "The API does not publish general sportsbook order-book depth; "
            "liquidity is unknown when an outcome bet limit is absent.",
        ),
    )

    EVENT_FILTERS = frozenset({"commenceTimeFrom", "commenceTimeTo", "dateFormat"})
    ODDS_FILTERS = frozenset(
        {
            "regions",
            "markets",
            "bookmakers",
            "commenceTimeFrom",
            "commenceTimeTo",
            "eventIds",
            "includeLinks",
            "includeSids",
            "includeBetLimits",
            "includeRotationNumbers",
        }
    )

    def __init__(self, *, api_key: str | None = None, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._api_key = api_key.strip() if api_key and api_key.strip() else None

    def _require_key(self) -> str:
        if self._api_key is None:
            raise MissingCredentialError(
                "The Odds API requires THE_ODDS_API_KEY; no live request was attempted."
            )
        return self._api_key

    @staticmethod
    def _sport(filters: Mapping[str, Any]) -> str:
        sport = filters.get("sport")
        if sport is None or not str(sport).strip():
            raise ValueError("The Odds API queries require the official sport key")
        return str(sport).strip()

    async def list_events(
        self, query: EventQuery | None = None
    ) -> ProviderPage[NormalizedEvent]:
        query = query or EventQuery()
        key = self._require_key()
        sport = self._sport(query.filters)
        params = allowed_filters(query.filters, self.EVENT_FILTERS)
        params.update({"apiKey": key, "dateFormat": "iso"})
        payload = await self._request_json(
            "GET",
            f"{self.BASE_URL}/sports/{quote(sport, safe='')}/events",
            params=params,
            cache_ttl_seconds=10,
        )
        raw_events = payload.data if isinstance(payload.data, list) else []
        raw_events = raw_events[: query.limit]
        items = tuple(
            self._event(item, payload, sport)
            for item in raw_events
            if isinstance(item, Mapping)
        )
        return ProviderPage(
            items=items,
            next_cursor=None,
            retrieved_at=payload.received_at,
            data_origin=payload.data_origin,
        )

    def _event(self, item: Mapping[str, Any], payload: Any, sport: str):
        event_id = first_text(item, "id") or ""
        home = first_text(item, "home_team")
        away = first_text(item, "away_team")
        return NormalizedEvent(
            provider=ProviderCode.THE_ODDS_API,
            provider_event_id=event_id,
            title=" vs ".join(value for value in (away, home) if value) or event_id,
            subtitle=None,
            sport=first_text(item, "sport_key") or sport,
            league=first_text(item, "sport_title"),
            home_team=home,
            away_team=away,
            scheduled_start=parse_datetime(item.get("commence_time")),
            status="open",
            resolution_source=None,
            source_url=f"{self.BASE_URL}/sports/{quote(sport, safe='')}/events",
            provider_timestamp=None,
            retrieved_at=payload.received_at,
            data_origin=payload.data_origin,
            metadata={"settlement_rules_available": False},
        )

    async def list_markets(
        self, query: MarketQuery | None = None
    ) -> ProviderPage[NormalizedMarket]:
        query = query or MarketQuery()
        key = self._require_key()
        sport = self._sport(query.filters)
        params = allowed_filters(query.filters, self.ODDS_FILTERS)
        params.setdefault("regions", "us")
        params.setdefault("markets", "h2h")
        params.update({"apiKey": key, "oddsFormat": "decimal", "dateFormat": "iso"})
        payload = await self._request_json(
            "GET",
            f"{self.BASE_URL}/sports/{quote(sport, safe='')}/odds",
            params=params,
            cache_ttl_seconds=5,
        )
        raw_events = payload.data if isinstance(payload.data, list) else []
        markets: list[NormalizedMarket] = []
        for event in raw_events:
            if not isinstance(event, Mapping):
                continue
            markets.extend(self._event_markets(event, payload, sport))
            if len(markets) >= query.limit:
                break
        return ProviderPage(
            items=tuple(markets[: query.limit]),
            next_cursor=None,
            retrieved_at=payload.received_at,
            data_origin=payload.data_origin,
        )

    def _event_markets(self, event: Mapping[str, Any], payload: Any, sport: str):
        event_id = first_text(event, "id") or ""
        result: list[NormalizedMarket] = []
        bookmakers = event.get("bookmakers")
        if not isinstance(bookmakers, list):
            return result
        for bookmaker in bookmakers:
            if not isinstance(bookmaker, Mapping):
                continue
            book_key = first_text(bookmaker, "key") or "unknown"
            raw_markets = bookmaker.get("markets")
            if not isinstance(raw_markets, list):
                continue
            for raw_market in raw_markets:
                if not isinstance(raw_market, Mapping):
                    continue
                market_key = first_text(raw_market, "key") or "unknown"
                composite_id = f"{event_id}:{book_key}:{market_key}"
                raw_outcomes = raw_market.get("outcomes")
                outcomes: list[NormalizedOutcome] = []
                if isinstance(raw_outcomes, list):
                    for raw_outcome in raw_outcomes:
                        if not isinstance(raw_outcome, Mapping):
                            continue
                        outcomes.append(
                            NormalizedOutcome(
                                provider_outcome_id=first_text(raw_outcome, "sid"),
                                name=first_text(raw_outcome, "name"),
                                description=first_text(raw_outcome, "description"),
                                side=None,
                                line=decimal_or_none(raw_outcome.get("point")),
                                offered_price=decimal_or_none(raw_outcome.get("price")),
                                best_bid=None,
                                best_ask=None,
                                bid_size=None,
                                ask_size=None,
                                last_price=None,
                                price_type=PriceType.DECIMAL_ODDS,
                                currency=None,
                                maximum_stake=decimal_or_none(
                                    raw_outcome.get("bet_limit")
                                ),
                                # The Odds API documents that currency depends on
                                # the exchange region and does not always return it.
                                stake_currency=first_text(raw_outcome, "currency"),
                            )
                        )
                result.append(
                    NormalizedMarket(
                        provider=ProviderCode.THE_ODDS_API,
                        provider_market_id=composite_id,
                        provider_market_slug=None,
                        provider_event_id=event_id,
                        title=f"{first_text(bookmaker, 'title') or book_key} {market_key}",
                        market_type=market_key,
                        period=None,
                        status="open",
                        event_start_time=parse_datetime(event.get("commence_time")),
                        close_time=None,
                        outcomes=tuple(outcomes),
                        settlement=None,
                        liquidity=None,
                        quote_timestamp=parse_datetime(
                            raw_market.get("last_update") or bookmaker.get("last_update")
                        ),
                        retrieved_at=payload.received_at,
                        data_origin=payload.data_origin,
                        source_url=f"{self.BASE_URL}/sports/{quote(sport, safe='')}/odds",
                        metadata={
                            "bookmaker_key": book_key,
                            "bookmaker_title": bookmaker.get("title"),
                            "identifier_is_composite": True,
                            "liquidity_available": False,
                            "settlement_rules_available": False,
                            "periodically_refreshed_not_immediately_executable": True,
                        },
                    )
                )
        return result

    async def get_order_book(self, query: OrderBookQuery):
        raise UnsupportedCapabilityError(
            "The Odds API does not publish sportsbook order-book depth or stake limits."
        )

    async def check_health(self):
        if self._api_key is None:
            return self.health_snapshot(
                state=ProviderState.REQUIRES_API_KEY,
                message="Set THE_ODDS_API_KEY to enable this provider; no request was attempted.",
            )
        try:
            await self._request_json(
                "GET",
                f"{self.BASE_URL}/sports",
                params={"apiKey": self._api_key},
                cache_ttl_seconds=0,
                deduplicate=False,
            )
        except ProviderHTTPError as exc:
            state = (
                ProviderState.REQUIRES_API_KEY
                if exc.status_code in {401, 403}
                else ProviderState.DISCONNECTED
            )
            return self.health_snapshot(state=state, message=str(exc))
        except ProviderError as exc:
            return self.health_snapshot(
                state=ProviderState.DISCONNECTED,
                message=str(exc),
            )
        return self.health_snapshot(
            state=ProviderState.CONNECTED,
            message=(
                "API reachable; bookmaker coverage and refresh cadence vary "
                "by plan and market."
            ),
        )

