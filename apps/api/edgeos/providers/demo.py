"""Explicitly synthetic provider used only when EDGEOS is in demo mode."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from .base import MarketDataAdapter
from .models import (
    AccessMode,
    DataOrigin,
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


class DemoAdapter(MarketDataAdapter):
    descriptor = ProviderDescriptor(
        code=ProviderCode.DEMO,
        display_name="EDGEOS Demo Data",
        product_scope="Synthetic training data; never live or executable",
        access_mode=AccessMode.DEMO_ONLY,
        capabilities=frozenset(
            {
                ProviderCapability.EVENT_DISCOVERY,
                ProviderCapability.MARKET_DISCOVERY,
                ProviderCapability.CURRENT_QUOTES,
                ProviderCapability.BEST_BID_ASK,
                ProviderCapability.ORDER_BOOK_DEPTH,
                ProviderCapability.QUOTE_SIZE,
                ProviderCapability.RESOLUTION_RULES,
                ProviderCapability.HEALTH_DIAGNOSTICS,
            }
        ),
        feed_types=frozenset({FeedType.DEMO}),
        api_base_urls=(),
        official_docs_urls=(),
        notes=("All records are synthetic and carry provider=demo and data_origin=demo.",),
    )
    FIXTURE_TIME = datetime(2026, 1, 1, 12, tzinfo=UTC)

    async def list_events(self, query: EventQuery | None = None):
        event = NormalizedEvent(
            provider=ProviderCode.DEMO,
            provider_event_id="demo-event-001",
            title="[DEMO] Harbor Hawks vs Metro Comets",
            subtitle="Synthetic training event",
            sport="demo_basketball",
            league="EDGEOS Demo League",
            home_team="Harbor Hawks",
            away_team="Metro Comets",
            scheduled_start=self.FIXTURE_TIME,
            status="demo_only",
            resolution_source="Synthetic EDGEOS fixture",
            source_url="demo://edgeos/events/demo-event-001",
            provider_timestamp=self.FIXTURE_TIME,
            retrieved_at=self._now(),
            data_origin=DataOrigin.DEMO,
            metadata={"synthetic": True},
        )
        return ProviderPage((event,), None, self._now(), DataOrigin.DEMO)

    async def list_markets(self, query: MarketQuery | None = None):
        now = self._now()
        market = NormalizedMarket(
            provider=ProviderCode.DEMO,
            provider_market_id="demo-market-001",
            provider_market_slug="demo-hawks-comets",
            provider_event_id="demo-event-001",
            title="[DEMO] Game winner",
            market_type="demo_moneyline",
            period="full_game_including_overtime",
            status="demo_only",
            event_start_time=self.FIXTURE_TIME,
            close_time=self.FIXTURE_TIME,
            outcomes=(
                NormalizedOutcome(
                    "demo-hawks",
                    "Harbor Hawks",
                    "Synthetic outcome",
                    "yes",
                    None,
                    None,
                    Decimal("0.48"),
                    Decimal("0.49"),
                    Decimal("100"),
                    Decimal("100"),
                    Decimal("0.48"),
                    PriceType.CONTRACT_DOLLARS,
                    "DEMO_USD",
                ),
                NormalizedOutcome(
                    "demo-comets",
                    "Metro Comets",
                    "Synthetic outcome",
                    "no",
                    None,
                    None,
                    Decimal("0.52"),
                    Decimal("0.53"),
                    Decimal("100"),
                    Decimal("100"),
                    Decimal("0.52"),
                    PriceType.CONTRACT_DOLLARS,
                    "DEMO_USD",
                ),
            ),
            settlement=SettlementMetadata(
                primary_rules="Synthetic fixture settles from the recorded demo result only.",
                resolution_source="Synthetic EDGEOS fixture",
            ),
            liquidity=Decimal("100"),
            quote_timestamp=self.FIXTURE_TIME,
            retrieved_at=now,
            data_origin=DataOrigin.DEMO,
            source_url="demo://edgeos/markets/demo-market-001",
            metadata={"synthetic": True, "not_executable": True},
        )
        return ProviderPage((market,), None, now, DataOrigin.DEMO)

    async def get_order_book(self, query: OrderBookQuery):
        now = self._now()
        return NormalizedOrderBook(
            provider=ProviderCode.DEMO,
            provider_market_id=query.market_ref,
            outcomes=(
                OutcomeOrderBook(
                    "demo-hawks",
                    "Harbor Hawks",
                    (OrderBookLevel(Decimal("0.48"), Decimal("100")),),
                    (OrderBookLevel(Decimal("0.49"), Decimal("100")),),
                    Decimal("0.48"),
                ),
            ),
            provider_timestamp=self.FIXTURE_TIME,
            retrieved_at=now,
            data_origin=DataOrigin.DEMO,
            source_url="demo://edgeos/books/demo-market-001",
            metadata={"synthetic": True, "not_executable": True},
        )

    async def check_health(self):
        return self.health_snapshot(
            state=ProviderState.DEMO_ONLY,
            message="Synthetic demo fixture loaded; no external provider is connected.",
        )

