from __future__ import annotations

from collections.abc import Iterable
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
from typing import cast

import pytest

from edgeos.domain import (
    CanonicalEvent,
    CanonicalMarket,
    FeeSchedule,
    Offer,
    PriceKind,
    SettlementRules,
)
from edgeos.opportunity_pipeline import (
    ExecutableOffer,
    MarketSnapshot,
    PipelineConfig,
)
from edgeos.pipeline_persistence import (
    AlertEvaluationResult,
    LifecycleChange,
    MarketLoadIssue,
    MarketLoadResult,
    MatchWrite,
    NormalizedMarketRecord,
    OpportunityOutcomeWrite,
    OpportunityWrite,
    OutcomeQuoteRecord,
    PersistedEvaluation,
    StoredMatchDecision,
)
from edgeos.post_ingestion import run_post_ingestion_cycle


NOW = datetime(2026, 10, 20, 12, 0, tzinfo=UTC)
OUTCOMES = ("home", "away")


def rules() -> SettlementRules:
    return SettlementRules(
        period="full game",
        overtime_included=True,
        shootout_included=False,
        tie_treatment="no tie",
        draw_treatment="not_applicable",
        push_treatment="not_applicable",
        void_treatment="not_applicable",
        cancellation_treatment="not_applicable",
        postponement_treatment="reschedule",
        retirement_treatment="not_applicable",
        abandonment_treatment="official result",
        outcome_structure="two way winner",
        threshold=Decimal("0"),
        line_unit="points",
        resolution_source="official league result",
        rules_text="Full game including overtime.",
    )


def market(market_id: str, provider: str) -> CanonicalMarket:
    return CanonicalMarket(
        market_id=market_id,
        event=CanonicalEvent(
            event_id="event",
            sport="basketball",
            league="NBA",
            home_team="New York Knicks",
            away_team="Boston Celtics",
            scheduled_start=NOW + timedelta(hours=4),
        ),
        provider=provider,
        provider_market_id=f"external-{market_id}",
        market_type="moneyline",
        outcome_ids=OUTCOMES,
        rules=rules(),
        source_url=f"https://{provider}.example/market",
    )


def executable(
    market_id: str,
    provider: str,
    offer_id: str,
    outcome: str,
    odds: str,
    *,
    currency: str = "USD",
) -> ExecutableOffer:
    return ExecutableOffer(
        offer=Offer(
            offer_id=offer_id,
            provider=provider,
            outcome_id=outcome,
            price_kind=PriceKind.DECIMAL_ODDS,
            price=Decimal(odds),
            quote_timestamp=NOW,
            fee_schedule=FeeSchedule(
                schedule_id=f"{provider}-zero",
                provider=provider,
                effective_from=date(2026, 1, 1),
                source_url=f"https://{provider}.example/fees",
            ),
            currency=currency,
            maximum_quantity=Decimal("1000"),
            source_url=f"https://{provider}.example/quote/{offer_id}",
            data_origin="live_poll",
        ),
        source_market_id=market_id,
        action="back",
        execution_basis="direct_sportsbook_offer",
        provider_timestamp_known=True,
        payout_per_unit={
            "home": Decimal(odds) if outcome == "home" else Decimal("0"),
            "away": Decimal(odds) if outcome == "away" else Decimal("0"),
        },
    )


def snapshot(canonical: CanonicalMarket, offer: ExecutableOffer) -> MarketSnapshot:
    return MarketSnapshot(
        market=canonical,
        offers=(offer,),
        scenario_ids=OUTCOMES,
        scenario_completeness_verified=True,
    )


def record(
    canonical: CanonicalMarket,
    selected: ExecutableOffer,
    *,
    provider_id: str,
    outcome_id: str,
    canonical_event_id: str = "00000000-0000-0000-0000-000000000001",
) -> NormalizedMarketRecord:
    offer = selected.offer
    return cast(
        NormalizedMarketRecord,
        SimpleNamespace(
            market_id=canonical.market_id,
            provider_id=provider_id,
            canonical_event_id=canonical_event_id,
            closes_at=NOW + timedelta(hours=4),
            outcomes=(
                OutcomeQuoteRecord(
                    outcome_id=outcome_id,
                    external_outcome_id=f"external-{outcome_id}",
                    canonical_key=offer.outcome_id,
                    name=offer.outcome_id,
                    outcome_type="selection",
                    line=None,
                    payout_per_unit=offer.price,
                    quote_id=offer.offer_id,
                    decimal_odds=offer.price,
                    probability_price=None,
                    best_bid=None,
                    best_ask=None,
                    bid_size=None,
                    ask_size=None,
                    available_size=offer.maximum_quantity,
                    currency=offer.currency,
                    feed_class="polled",
                    observed_at=NOW,
                    received_at=NOW,
                    stale_at=NOW + timedelta(seconds=30),
                    is_in_play=False,
                ),
            ),
        ),
    )


class FakeRepository:
    def __init__(
        self,
        loaded: MarketLoadResult,
        *,
        overrides: tuple[StoredMatchDecision, ...] = (),
    ) -> None:
        self.loaded = loaded
        self.overrides = overrides
        self.calls: list[str] = []
        self.persisted: list[tuple[MatchWrite | None, OpportunityWrite | None]] = []
        self.withdrawn_active_keys: tuple[str, ...] | None = None

    def load_market_snapshots(
        self,
        *,
        now: datetime | None = None,
        maximum_quote_age_seconds: int = 120,
        limit: int = 5000,
    ) -> MarketLoadResult:
        self.calls.append("load")
        assert now == NOW
        assert maximum_quote_age_seconds == 30
        assert limit == 5000
        return self.loaded

    def load_match_decision_overrides(self, market_ids: Iterable[str]) -> tuple[StoredMatchDecision, ...]:
        self.calls.append("overrides")
        assert set(market_ids) == {item.market_id for item in self.loaded.markets}
        return self.overrides

    def persist_evaluation(
        self,
        match: MatchWrite | None,
        opportunity: OpportunityWrite | None,
        *,
        observed_at: datetime | None = None,
    ) -> PersistedEvaluation:
        self.calls.append("persist")
        assert observed_at == NOW
        self.persisted.append((match, opportunity))
        return PersistedEvaluation(
            match_id="persisted-match" if match is not None else None,
            opportunity_id="persisted-opportunity" if opportunity is not None else None,
            effective_match_status="verified",
        )

    def withdraw_absent_opportunities(
        self,
        *,
        evaluated_market_ids: Iterable[str],
        active_opportunity_keys: Iterable[str],
        observed_at: datetime | None = None,
    ) -> tuple[LifecycleChange, ...]:
        self.calls.append("withdraw")
        assert set(evaluated_market_ids) == {item.market_id for item in self.loaded.markets}
        assert observed_at == NOW
        self.withdrawn_active_keys = tuple(active_opportunity_keys)
        return ()

    def expire_stale_opportunities(self, *, now: datetime | None = None) -> tuple[LifecycleChange, ...]:
        self.calls.append("expire")
        assert now == NOW
        return ()

    def evaluate_alerts(
        self,
        opportunity_ids: Iterable[str],
        *,
        now: datetime | None = None,
    ) -> AlertEvaluationResult:
        self.calls.append("alerts")
        assert tuple(opportunity_ids) == ("persisted-opportunity",)
        assert now == NOW
        return AlertEvaluationResult(created=2, deduplicated=1, filtered=3)


def setup_pair(
    currency: str = "USD",
) -> tuple[
    tuple[MarketSnapshot, MarketSnapshot],
    tuple[NormalizedMarketRecord, NormalizedMarketRecord],
]:
    left_market = market("market-a", "book-a")
    right_market = market("market-b", "book-b")
    left_offer = executable("market-a", "book-a", "quote-home", "home", "2.20", currency=currency)
    right_offer = executable("market-b", "book-b", "quote-away", "away", "2.00", currency=currency)
    snapshots = (
        snapshot(left_market, left_offer),
        snapshot(right_market, right_offer),
    )
    records = (
        record(left_market, left_offer, provider_id="provider-a", outcome_id="outcome-home"),
        record(right_market, right_offer, provider_id="provider-b", outcome_id="outcome-away"),
    )
    return snapshots, records


def pipeline_config() -> PipelineConfig:
    return PipelineConfig(
        total_allocation=Decimal("210"),
        maximum_quote_age_seconds=30,
        partial_fill_probability=Decimal("0.02"),
    )


def test_cycle_persists_then_withdraws_expires_and_evaluates_alerts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snapshots, records = setup_pair()
    repository = FakeRepository(MarketLoadResult(records))
    monkeypatch.setattr(
        "edgeos.post_ingestion.to_engine_snapshots",
        lambda _records: (snapshots, ()),
    )

    report = run_post_ingestion_cycle(repository, pipeline_config(), now=NOW)

    assert report.status == "completed"
    assert report.candidate_audits == 3
    assert report.evaluated_combinations == 1
    assert report.theoretical_opportunities == 1
    assert report.strict_opportunities == 1
    assert report.persisted_matches == 1
    assert report.persisted_opportunities == 1
    assert report.alert_events_created == 2
    assert report.alert_events_deduplicated == 1
    assert report.alert_events_filtered == 3
    assert repository.calls == [
        "load",
        "overrides",
        "persist",
        "withdraw",
        "expire",
        "alerts",
    ]
    assert len(repository.persisted) == 1
    persisted_match, persisted_opportunity = repository.persisted[0]
    assert persisted_match is not None
    assert persisted_match.classification == "verified"
    assert persisted_opportunity is not None
    assert persisted_opportunity.status == "active"
    assert persisted_opportunity.total_allocation == Decimal("210")
    assert persisted_opportunity.worst_case_net_profit == Decimal("10")
    assert persisted_opportunity.expires_at == NOW + timedelta(seconds=30)
    assert {item.provider_id for item in persisted_opportunity.legs} == {
        "provider-a",
        "provider-b",
    }
    assert {item.outcome_id for item in persisted_opportunity.legs} == {
        "outcome-home",
        "outcome-away",
    }
    assert all(isinstance(item, OpportunityOutcomeWrite) for item in persisted_opportunity.outcomes)
    assert persisted_opportunity.assumptions["execution_is_not_guaranteed"] is True
    assert repository.withdrawn_active_keys == (persisted_opportunity.opportunity_key,)


def test_no_evaluable_markets_expires_but_never_performs_broad_withdrawal() -> None:
    repository = FakeRepository(MarketLoadResult((), (MarketLoadIssue("market-a", "quotes_missing"),)))

    report = run_post_ingestion_cycle(repository, pipeline_config(), now=NOW)

    assert report.status == "no_evaluable_markets"
    assert report.persisted_opportunities == 0
    assert report.withdrawn_opportunities == 0
    assert repository.calls == ["load", "expire"]
    assert report.issues == (report.issues[0].__class__("load", "quotes_missing", market_id="market-a"),)


@pytest.mark.parametrize("currency", ["USDC", "pUSD"])
def test_supported_cash_token_currency_is_preserved_without_conversion(
    currency: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    snapshots, records = setup_pair(currency)
    repository = FakeRepository(MarketLoadResult(records))
    monkeypatch.setattr(
        "edgeos.post_ingestion.to_engine_snapshots",
        lambda _records: (snapshots, ()),
    )

    report = run_post_ingestion_cycle(repository, pipeline_config(), now=NOW)

    assert report.persisted_opportunities == 1
    opportunity = repository.persisted[0][1]
    assert opportunity is not None
    assert opportunity.currency == currency


@pytest.mark.parametrize(
    ("right_event_id", "expected_issue"),
    [
        (None, "canonical_event_id_missing:market-b"),
        (
            "00000000-0000-0000-0000-000000000002",
            "canonical_event_id_mismatch",
        ),
    ],
)
def test_missing_or_mismatched_canonical_event_link_blocks_active_persistence(
    monkeypatch: pytest.MonkeyPatch,
    right_event_id: str | None,
    expected_issue: str,
) -> None:
    snapshots, records = setup_pair()
    setattr(records[1], "canonical_event_id", right_event_id)
    repository = FakeRepository(MarketLoadResult(records))
    monkeypatch.setattr(
        "edgeos.post_ingestion.to_engine_snapshots",
        lambda _records: (snapshots, ()),
    )

    report = run_post_ingestion_cycle(repository, pipeline_config(), now=NOW)

    assert report.status == "partial"
    assert report.persisted_matches == 1
    assert report.persisted_opportunities == 0
    assert any(issue.code == expected_issue for issue in report.issues)
    assert len(repository.persisted) == 1
    assert repository.persisted[0][0] is not None
    assert repository.persisted[0][1] is None
    assert repository.withdrawn_active_keys == ()
