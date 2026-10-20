from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from decimal import Decimal

import httpx
import pytest
from edgeos.providers.base import ProviderHTTPError, ProviderUnavailableError

from edgeos.providers import (
    DataOrigin,
    DemoAdapter,
    KalshiAdapter,
    MarketQuery,
    MissingCredentialError,
    OrderBookQuery,
    PolymarketInternationalAdapter,
    PolymarketUSAdapter,
    ProviderCode,
    ProviderState,
    RetryPolicy,
    TheOddsAPIAdapter,
    UnsupportedCapabilityError,
)


FIXED_NOW = datetime(2026, 10, 16, 12, 0, tzinfo=UTC)


def run(coro):
    return asyncio.run(coro)


def test_polymarket_products_have_separate_id_namespaces_and_sources() -> None:
    international = PolymarketInternationalAdapter.descriptor
    us = PolymarketUSAdapter.descriptor

    assert international.code is ProviderCode.POLYMARKET_INTERNATIONAL
    assert us.code is ProviderCode.POLYMARKET_US
    assert international.api_base_urls != us.api_base_urls
    assert "International" in international.display_name
    assert "US" in us.display_name
    assert any("not interchangeable" in note for note in international.notes)
    assert any("not interchangeable" in note for note in us.notes)


def test_kalshi_normalizes_rules_quotes_and_binary_complement_books() -> None:
    calls: dict[str, int] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        calls[request.url.path] = calls.get(request.url.path, 0) + 1
        if request.url.path.endswith("/markets"):
            return httpx.Response(
                200,
                json={
                    "markets": [
                        {
                            "ticker": "KX-DEMO",
                            "event_ticker": "KX-EVENT",
                            "title": "Will the documented condition occur?",
                            "status": "open",
                            "updated_time": "2026-10-16T11:59:59Z",
                            "close_time": "2026-10-17T00:00:00Z",
                            "yes_bid_dollars": "0.4500",
                            "yes_ask_dollars": "0.4800",
                            "yes_bid_size_fp": "20.00",
                            "yes_ask_size_fp": "15.00",
                            "no_bid_dollars": "0.5200",
                            "no_ask_dollars": "0.5500",
                            "last_price_dollars": "0.4700",
                            "liquidity_dollars": "123.45",
                            "rules_primary": "Settles Yes if the condition occurs.",
                            "rules_secondary": "Official source controls.",
                            "can_close_early": False,
                        }
                    ],
                    "cursor": "next-page",
                },
            )
        if request.url.path.endswith("/markets/KX-DEMO/orderbook"):
            return httpx.Response(
                200,
                json={
                    "orderbook_fp": {
                        "yes_dollars": [["0.40", "50"], ["0.45", "20"]],
                        "no_dollars": [["0.50", "10"], ["0.52", "15"]],
                    }
                },
            )
        raise AssertionError(f"unexpected request: {request.url}")

    async def scenario() -> None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            adapter = KalshiAdapter(client=client, now=lambda: FIXED_NOW)
            first = await adapter.list_markets(MarketQuery(limit=20))
            second = await adapter.list_markets(MarketQuery(limit=20))
            market = first.items[0]

            assert market.provider is ProviderCode.KALSHI
            assert market.outcomes[0].best_ask == Decimal("0.4800")
            assert market.outcomes[0].ask_size == Decimal("15.00")
            assert market.settlement is not None
            assert market.settlement.primary_rules.startswith("Settles Yes")
            assert market.quote_timestamp == datetime(2026, 10, 16, 11, 59, 59, tzinfo=UTC)
            assert market.liquidity == Decimal("123.45")
            assert first.next_cursor == "next-page"
            assert first.data_origin is DataOrigin.LIVE_POLL
            assert second.data_origin is DataOrigin.CACHE
            assert calls["/trade-api/v2/markets"] == 1

            book = await adapter.get_order_book(OrderBookQuery("KX-DEMO"))
            yes, no = book.outcomes
            assert yes.best_bid.price == Decimal("0.45")
            assert yes.best_ask.price == Decimal("0.48")
            assert yes.best_ask.size == Decimal("15")
            assert no.best_bid.price == Decimal("0.52")
            assert no.best_ask.price == Decimal("0.55")
            # The endpoint does not provide a snapshot timestamp. Retrieval time is
            # deliberately not substituted for provider time.
            assert book.provider_timestamp is None

    run(scenario())


def test_base_coalesces_concurrent_identical_requests() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, json={"events": [], "cursor": ""})

    async def scenario() -> None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            adapter = KalshiAdapter(client=client, now=lambda: FIXED_NOW)
            await asyncio.gather(adapter.list_events(), adapter.list_events())

    run(scenario())
    assert calls == 1


def test_polymarket_international_uses_v2_position_id_and_executable_book() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "gamma-api.polymarket.com":
            assert request.url.path == "/markets/keyset"
            return httpx.Response(
                200,
                json={
                    "markets": [
                        {
                            "id": "gamma-703257",
                            "slug": "documented-market",
                            "question": "Will it occur?",
                            "version": "v2",
                            "conditionId": "0xcondition",
                            "closed": False,
                            "updatedAt": "2026-10-16T11:59:00Z",
                            "positionIds": ["v2-yes", "v2-no"],
                            "outcomes": {
                                "yes": {"positionId": "v2-yes"},
                                "no": {"positionId": "v2-no"},
                            },
                            "description": "Explicit resolution rules.",
                            "resolutionSource": "https://official.example/result",
                        }
                    ],
                    "next_cursor": "opaque-cursor",
                },
            )
        if request.url.host == "clob.polymarket.com":
            assert request.url.path == "/book"
            assert request.url.params["token_id"] == "v2-yes"
            return httpx.Response(
                200,
                json={
                    "market": "0xcondition",
                    "asset_id": "v2-yes",
                    "timestamp": "1782753357257",
                    "hash": "book-hash",
                    "bids": [
                        {"price": "0.42", "size": "8"},
                        {"price": "0.45", "size": "3"},
                    ],
                    "asks": [
                        {"price": "0.50", "size": "4"},
                        {"price": "0.48", "size": "5"},
                    ],
                    "min_order_size": "5",
                    "tick_size": "0.01",
                    "last_trade_price": "0.46",
                    "neg_risk": False,
                },
            )
        raise AssertionError(f"unexpected request: {request.url}")

    async def scenario() -> None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            adapter = PolymarketInternationalAdapter(client=client, now=lambda: FIXED_NOW)
            page = await adapter.list_markets(MarketQuery(limit=10))
            market = page.items[0]
            assert market.provider is ProviderCode.POLYMARKET_INTERNATIONAL
            assert market.outcomes[0].provider_outcome_id == "v2-yes"
            assert market.outcomes[0].currency == "pUSD"
            assert market.provider_market_id == "gamma-703257"
            assert page.next_cursor == "opaque-cursor"

            with pytest.raises(ValueError, match="outcome_id"):
                await adapter.get_order_book(OrderBookQuery("gamma-703257"))

            book = await adapter.get_order_book(
                OrderBookQuery("gamma-703257", outcome_id="v2-yes")
            )
            assert book.outcomes[0].best_bid.price == Decimal("0.45")
            assert book.outcomes[0].best_ask.price == Decimal("0.48")
            assert book.outcomes[0].last_trade_price == Decimal("0.46")
            assert book.minimum_order_size == Decimal("5")
            assert book.tick_size == Decimal("0.01")
            assert book.provider_timestamp is not None
            assert book.metadata["international_product"] is True

    run(scenario())


def test_polymarket_us_uses_gateway_slug_book_and_never_international_ids() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/markets":
            return httpx.Response(
                200,
                json={
                    "markets": [
                        {
                            "id": "us-market-9",
                            "slug": "us-documented-market",
                            "question": "Will the US contract settle long?",
                            "active": True,
                            "closed": False,
                            "updatedAt": "2026-10-16T11:58:00Z",
                            "bestBidQuote": {"value": "0.51", "currency": "USD"},
                            "bestAskQuote": {"value": "0.53", "currency": "USD"},
                            "marketSides": [
                                {
                                    "id": "us-side-long",
                                    "identifier": "US-LONG",
                                    "description": "Long",
                                    "long": True,
                                    "quote": {"value": "0.52", "currency": "USD"},
                                },
                                {
                                    "id": "us-side-short",
                                    "identifier": "US-SHORT",
                                    "description": "Short",
                                    "long": False,
                                    "quote": {"value": "0.48", "currency": "USD"},
                                },
                            ],
                        }
                    ]
                },
            )
        if request.url.path == "/v1/markets/us-documented-market/book":
            return httpx.Response(
                200,
                json={
                    "marketData": {
                        "marketSlug": "us-documented-market",
                        "bids": [
                            {"px": {"value": "0.49", "currency": "USD"}, "qty": "10"},
                            {"px": {"value": "0.51", "currency": "USD"}, "qty": "5"},
                        ],
                        "offers": [
                            {"px": {"value": "0.55", "currency": "USD"}, "qty": "4"},
                            {"px": {"value": "0.53", "currency": "USD"}, "qty": "6"},
                        ],
                        "state": "MARKET_STATE_OPEN",
                        "transactTime": "2026-10-16T11:59:31Z",
                    }
                },
            )
        raise AssertionError(f"unexpected request: {request.url}")

    async def scenario() -> None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            adapter = PolymarketUSAdapter(client=client, now=lambda: FIXED_NOW)
            market = (await adapter.list_markets(MarketQuery(limit=5))).items[0]
            assert market.provider is ProviderCode.POLYMARKET_US
            assert market.provider_market_id == "us-market-9"
            assert market.provider_market_slug == "us-documented-market"
            assert market.outcomes[0].provider_outcome_id == "US-LONG"
            assert market.outcomes[0].best_ask == Decimal("0.53")
            assert market.metadata["us_product"] is True

            book = await adapter.get_order_book(
                OrderBookQuery("us-documented-market", outcome_id="US-LONG")
            )
            assert book.provider is ProviderCode.POLYMARKET_US
            assert book.outcomes[0].best_bid.price == Decimal("0.51")
            assert book.outcomes[0].best_ask.price == Decimal("0.53")
            assert book.provider_timestamp == datetime(
                2026, 10, 16, 11, 59, 31, tzinfo=UTC
            )
            assert "gamma-api.polymarket.com" not in book.source_url

    run(scenario())


def test_the_odds_api_requires_key_retries_429_and_tracks_quota() -> None:
    async def missing_key_scenario() -> None:
        async with TheOddsAPIAdapter(now=lambda: FIXED_NOW) as without_key:
            health = await without_key.check_health()
            assert health.state is ProviderState.REQUIRES_API_KEY
            with pytest.raises(MissingCredentialError):
                await without_key.list_markets(
                    MarketQuery(filters={"sport": "basketball_nba"})
                )

    run(missing_key_scenario())

    attempts = 0
    sleeps: list[float] = []

    async def no_sleep(delay: float) -> None:
        sleeps.append(delay)

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        assert request.url.params["oddsFormat"] == "decimal"
        if attempts == 1:
            return httpx.Response(
                429,
                json={"message": "rate limited"},
                headers={"Retry-After": "0"},
            )
        return httpx.Response(
            200,
            headers={
                "x-requests-remaining": "99",
                "x-requests-used": "1",
                "x-requests-last": "1",
            },
            json=[
                {
                    "id": "event-1",
                    "sport_key": "basketball_nba",
                    "sport_title": "NBA",
                    "commence_time": "2026-10-17T00:00:00Z",
                    "home_team": "Home Team",
                    "away_team": "Away Team",
                    "bookmakers": [
                        {
                            "key": "fanduel",
                            "title": "FanDuel",
                            "markets": [
                                {
                                    "key": "h2h",
                                    "last_update": "2026-10-16T11:59:30Z",
                                    "outcomes": [
                                        {
                                            "name": "Home Team",
                                            "price": 2.20,
                                            "bet_limit": 250,
                                            "sid": "home-selection",
                                        },
                                        {"name": "Away Team", "price": 1.80},
                                    ],
                                }
                            ],
                        }
                    ],
                }
            ],
        )

    async def scenario() -> None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            adapter = TheOddsAPIAdapter(
                api_key="test-key",
                client=client,
                now=lambda: FIXED_NOW,
                sleep=no_sleep,
                retry_policy=RetryPolicy(
                    max_attempts=2,
                    base_delay_seconds=0,
                    max_delay_seconds=0,
                    jitter_ratio=0,
                ),
            )
            page = await adapter.list_markets(
                MarketQuery(
                    limit=10,
                    filters={
                        "sport": "basketball_nba",
                        "regions": "us",
                        "markets": "h2h",
                    },
                )
            )
            market = page.items[0]
            assert market.provider_market_id == "event-1:fanduel:h2h"
            assert market.outcomes[0].offered_price == Decimal("2.2")
            assert market.outcomes[0].maximum_stake == Decimal("250")
            assert market.outcomes[0].provider_outcome_id == "home-selection"
            assert market.outcomes[0].ask_size is None
            assert market.liquidity is None
            assert market.metadata["periodically_refreshed_not_immediately_executable"] is True
            assert market.quote_timestamp == datetime(2026, 10, 16, 11, 59, 30, tzinfo=UTC)
            snapshot = adapter.health_snapshot()
            assert snapshot.state is ProviderState.CONNECTED
            assert snapshot.rate_limit_events == 1
            assert snapshot.rate_limit is not None
            assert snapshot.rate_limit.remaining == 99

            with pytest.raises(UnsupportedCapabilityError):
                await adapter.get_order_book(OrderBookQuery(market.provider_market_id))

    run(scenario())
    assert attempts == 2
    assert sleeps == [0]


def test_demo_adapter_is_impossible_to_mistake_for_live_data() -> None:
    async def scenario() -> None:
        async with DemoAdapter(now=lambda: FIXED_NOW) as adapter:
            event = (await adapter.list_events()).items[0]
            market = (await adapter.list_markets()).items[0]
            health = await adapter.check_health()

            assert event.provider is ProviderCode.DEMO
            assert event.data_origin is DataOrigin.DEMO
            assert event.title.startswith("[DEMO]")
            assert market.data_origin is DataOrigin.DEMO
            assert market.metadata["not_executable"] is True
            assert health.state is ProviderState.DEMO_ONLY

    run(scenario())


def test_health_does_not_hide_latest_failure_after_a_success() -> None:
    attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        return httpx.Response(
            200 if attempts == 1 else 503, json={"events": [], "markets": []}
        )

    async def scenario() -> None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            adapter = KalshiAdapter(
                client=client, retry_policy=RetryPolicy(max_attempts=1)
            )
            await adapter.list_events()
            assert adapter.health_snapshot().state is ProviderState.CONNECTED
            with pytest.raises(ProviderHTTPError):
                await adapter.list_markets()
            health = adapter.health_snapshot()
            assert health.last_success_at is not None
            assert health.state is ProviderState.DISCONNECTED
            assert health.consecutive_failures == 1

    run(scenario())


def test_long_retry_after_blocks_other_routes_and_later_cycles(monkeypatch) -> None:
    ticks = [100.0]
    monkeypatch.setattr("edgeos.providers.base.time.monotonic", lambda: ticks[0])
    attempts = 0
    sleeps: list[float] = []

    async def no_sleep(delay: float) -> None:
        sleeps.append(delay)

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            return httpx.Response(429, headers={"Retry-After": "60"}, json={})
        return httpx.Response(200, json={"events": [], "markets": []})

    async def scenario() -> None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            adapter = KalshiAdapter(client=client, sleep=no_sleep)
            with pytest.raises(ProviderHTTPError):
                await adapter.list_markets()
            assert attempts == 1
            assert sleeps == []
            assert adapter.health_snapshot().state is ProviderState.DELAYED
            assert adapter.health_snapshot(state=ProviderState.DISCONNECTED).state is ProviderState.DELAYED
            with pytest.raises(ProviderUnavailableError, match="Retry-After"):
                await adapter.list_events()
            ticks[0] = 159.0
            with pytest.raises(ProviderUnavailableError, match="Retry-After"):
                await adapter.list_markets()
            assert adapter.health_snapshot().requests_attempted == 1
            ticks[0] = 160.0
            assert adapter.health_snapshot().state is ProviderState.DISCONNECTED
            await adapter.list_events()
            assert attempts == 2
            assert adapter.health_snapshot().state is ProviderState.CONNECTED

    run(scenario())


def test_retry_after_http_date_and_invalid_numbers() -> None:
    from datetime import timedelta
    from email.utils import format_datetime

    future = datetime.now(UTC) + timedelta(seconds=90)
    delay = KalshiAdapter._retry_after_seconds(format_datetime(future, usegmt=True))
    assert delay is not None and 88 <= delay <= 90
    assert KalshiAdapter._retry_after_seconds("nan") is None
    assert KalshiAdapter._retry_after_seconds("inf") is None
