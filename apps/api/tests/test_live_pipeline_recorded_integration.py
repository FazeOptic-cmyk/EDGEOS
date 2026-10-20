from __future__ import annotations

from collections.abc import Iterable
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from edgeos.opportunity_pipeline import PipelineConfig, generate_opportunities
from edgeos.pipeline_persistence import (
    AlertEvaluationResult,
    FeeScheduleRecord,
    LifecycleChange,
    MarketLoadIssue,
    MarketLoadResult,
    MarketRuleRecord,
    MatchWrite,
    NormalizedMarketRecord,
    OpportunityWrite,
    OutcomeQuoteRecord,
    PersistedEvaluation,
    StoredMatchDecision,
    to_engine_snapshots,
)
from edgeos.post_ingestion import run_post_ingestion_cycle


NOW = datetime(2026, 10, 20, 14, 0, tzinfo=UTC)
EVENT_ID = "00000000-0000-0000-0000-000000000101"


def _rule(*, overtime: bool = True) -> MarketRuleRecord:
    return MarketRuleRecord(
        rule_id="rule-v1",
        rule_version=1,
        includes_overtime=overtime,
        tie_treatment="not_applicable",
        push_treatment="not_applicable",
        void_treatment="not_applicable",
        cancellation_treatment="not_applicable",
        settlement_source="official NBA result",
        rules_url="https://rules.example/nba",
        rules_text="Full game including overtime; no draw outcome.",
        rules_hash="recorded-rule-v1",
        verified_at=NOW - timedelta(days=1),
        metadata={
            "shootout_included": False,
            "draw_treatment": "not_applicable",
            "postponement_treatment": "not_applicable",
            "retirement_treatment": "not_applicable",
            "abandonment_treatment": "official result",
            "line_unit": "points",
            "outcome_structure": "mutually_exclusive_collectively_exhaustive",
        },
    )


def _fee(provider: str, *, rate: str = "0", rounding_mode: str = "half_up") -> FeeScheduleRecord:
    method = "none" if rate == "0" else "percentage_notional"
    parameters: dict[str, str] = (
        {} if method == "none" else {"rate": rate, "rounding_increment": "0.01"}
    )
    return FeeScheduleRecord(
        schedule_id=f"{provider}-fee-v1",
        provider=provider,
        effective_from=NOW - timedelta(days=1),
        effective_to=None,
        calculation_method=method,
        parameters=parameters,
        rounding_mode=rounding_mode,
        currency="USD",
        source_url=f"https://{provider}.example/fees",
        verified_at=NOW - timedelta(days=1),
    )


def _quote(
    market_id: str,
    outcome: str,
    ask: str,
    *,
    observed_at: datetime = NOW,
) -> OutcomeQuoteRecord:
    return OutcomeQuoteRecord(
        outcome_id=f"{market_id}-{outcome}-row",
        external_outcome_id=f"{market_id}-{outcome}",
        canonical_key=outcome,
        name=outcome.title(),
        outcome_type="selection",
        line=None,
        payout_per_unit=Decimal("1"),
        quote_id=f"{market_id}-{outcome}-quote-{observed_at.timestamp()}",
        decimal_odds=None,
        probability_price=Decimal(ask),
        best_bid=max(Decimal("0"), Decimal(ask) - Decimal("0.01")),
        best_ask=Decimal(ask),
        bid_size=Decimal("500"),
        ask_size=Decimal("500"),
        available_size=Decimal("500"),
        currency="USD",
        feed_class="polled",
        observed_at=observed_at,
        received_at=observed_at + timedelta(milliseconds=100),
        stale_at=observed_at + timedelta(seconds=30),
        is_in_play=False,
    )


def _market(
    market_id: str,
    provider: str,
    home_ask: str,
    away_ask: str,
    *,
    provider_status: str = "connected",
    overtime: bool = True,
    fee_rate: str = "0",
    fee_rounding_mode: str = "half_up",
    canonical_event_id: str | None = EVENT_ID,
    observed_at: datetime = NOW,
) -> NormalizedMarketRecord:
    return NormalizedMarketRecord(
        market_id=market_id,
        provider_id=f"{provider}-database-id",
        provider_slug=provider,
        provider_name=provider.title(),
        provider_kind="prediction_market",
        provider_status=provider_status,
        external_market_id=f"external-{market_id}",
        title="Knicks at Celtics — game winner",
        market_type="moneyline",
        period="full_game",
        line=Decimal("0"),
        currency="USD",
        live=False,
        minimum_increment=Decimal("0.01"),
        mutually_exclusive=True,
        collectively_exhaustive=True,
        closes_at=NOW + timedelta(hours=4),
        source_url=f"https://{provider}.example/markets/{market_id}",
        metadata={
            "minimum_trade_quantity": "0.01",
            "settlement": {"resolution_source": "official NBA result"},
        },
        provider_event_id=f"provider-event-{provider}",
        external_event_id=f"external-event-{provider}",
        event_title="New York Knicks at Boston Celtics",
        scheduled_start=NOW + timedelta(hours=4),
        event_status="scheduled",
        event_source_url=f"https://{provider}.example/events/game",
        event_metadata={
            "normalized_event": {
                "sport": "basketball",
                "league": "NBA",
                "home_team": "Boston Celtics",
                "away_team": "New York Knicks",
                "resolution_source": "official NBA result",
            }
        },
        semantic_event_id="semantic-knicks-celtics",
        canonical_event_id=canonical_event_id,
        rule=_rule(overtime=overtime),
        fee_schedule=_fee(provider, rate=fee_rate, rounding_mode=fee_rounding_mode),
        outcomes=(
            _quote(market_id, "home", home_ask, observed_at=observed_at),
            _quote(market_id, "away", away_ask, observed_at=observed_at),
        ),
    )


def _config() -> PipelineConfig:
    return PipelineConfig(
        total_allocation=Decimal("100"),
        maximum_quote_age_seconds=30,
        minimum_liquidity=Decimal("10"),
        partial_fill_probability=Decimal("0.02"),
        quote_update_hz=Decimal("0.2"),
        historical_stability=Decimal("0.9"),
    )


class _LifecycleRepository:
    """Stateful persistence double for lifecycle/alert contract verification."""

    def __init__(self, markets: tuple[NormalizedMarketRecord, ...]) -> None:
        self.markets = markets
        self.persisted: list[tuple[MatchWrite | None, OpportunityWrite | None]] = []
        self.active_keys: set[str] = set()
        self.withdrawals = 0
        self.alert_active = False

    def load_market_snapshots(
        self,
        *,
        now: datetime | None = None,
        maximum_quote_age_seconds: int = 120,
        limit: int = 5000,
    ) -> MarketLoadResult:
        del now, maximum_quote_age_seconds, limit
        return MarketLoadResult(self.markets)

    def load_match_decision_overrides(
        self, market_ids: Iterable[str]
    ) -> tuple[StoredMatchDecision, ...]:
        del market_ids
        return ()

    def persist_evaluation(
        self,
        match: MatchWrite | None,
        opportunity: OpportunityWrite | None,
        *,
        observed_at: datetime | None = None,
    ) -> PersistedEvaluation:
        del observed_at
        self.persisted.append((match, opportunity))
        if opportunity is not None:
            self.active_keys.add(opportunity.opportunity_key)
        return PersistedEvaluation(
            match_id="match-id" if match is not None else None,
            opportunity_id="opportunity-id" if opportunity is not None else None,
            effective_match_status=match.classification if match is not None else "verified",
        )

    def withdraw_absent_opportunities(
        self,
        *,
        evaluated_market_ids: Iterable[str],
        active_opportunity_keys: Iterable[str],
        observed_at: datetime | None = None,
    ) -> tuple[LifecycleChange, ...]:
        del evaluated_market_ids, observed_at
        still_active = set(active_opportunity_keys)
        removed = self.active_keys - still_active
        self.active_keys = still_active
        if not removed:
            return ()
        self.withdrawals += len(removed)
        self.alert_active = False
        return tuple(
            LifecycleChange("opportunity-id", key, "invalidated") for key in removed
        )

    def expire_stale_opportunities(
        self, *, now: datetime | None = None
    ) -> tuple[LifecycleChange, ...]:
        del now
        return ()

    def evaluate_alerts(
        self,
        opportunity_ids: Iterable[str],
        *,
        now: datetime | None = None,
    ) -> AlertEvaluationResult:
        del now
        if not tuple(opportunity_ids):
            return AlertEvaluationResult(0, 0, 0)
        if self.alert_active:
            return AlertEvaluationResult(0, 1, 0)
        self.alert_active = True
        return AlertEvaluationResult(1, 0, 0)


def test_recorded_normalized_records_reach_strict_cashflow_persistence_and_alerts() -> None:
    repository = _LifecycleRepository(
        (
            _market("market-a", "venue-a", "0.45", "0.62"),
            _market("market-b", "venue-b", "0.61", "0.45"),
        )
    )

    report = run_post_ingestion_cycle(repository, _config(), now=NOW)

    assert report.status == "completed"
    assert report.strict_opportunities == 1
    assert report.persisted_opportunities == 1
    assert report.alert_events_created == 1
    writes = [item for _, item in repository.persisted if item is not None]
    assert len(writes) == 1
    assert writes[0].event_id == EVENT_ID
    assert writes[0].worst_case_net_profit > 0
    assert {leg.market_id for leg in writes[0].legs} == {"market-a", "market-b"}


def test_incompatible_settlement_and_fee_erasure_never_reach_strict_feed() -> None:
    incompatible = (
        _market("market-a", "venue-a", "0.45", "0.62", overtime=True),
        _market("market-b", "venue-b", "0.61", "0.45", overtime=False),
    )
    snapshots, issues = to_engine_snapshots(incompatible)
    assert not issues
    incompatible_result = generate_opportunities(snapshots, _config(), now=NOW)
    assert not incompatible_result.strict_opportunities
    assert any(audit.decision.status.value == "rejected" for audit in incompatible_result.candidate_audits)

    costly = (
        # Ceiling charges at least one cent per nonzero order. Half-up would
        # legitimately charge zero for this fixture's smallest fractional fills.
        _market(
            "market-a", "venue-a", "0.45", "0.62",
            fee_rate="0.50", fee_rounding_mode="ceiling",
        ),
        _market(
            "market-b", "venue-b", "0.61", "0.45",
            fee_rate="0.50", fee_rounding_mode="ceiling",
        ),
    )
    costly_snapshots, costly_issues = to_engine_snapshots(costly)
    assert not costly_issues
    costly_result = generate_opportunities(costly_snapshots, _config(), now=NOW)
    assert not costly_result.strict_opportunities
    assert all(item.full_worst_case <= 0 for item in costly_result.opportunities)


def test_explicit_half_up_rounding_preserves_small_order_fee_economics() -> None:
    records = (
        _market("market-a", "venue-a", "0.45", "0.62", fee_rate="0.50"),
        _market("market-b", "venue-b", "0.61", "0.45", fee_rate="0.50"),
    )
    snapshots, issues = to_engine_snapshots(records)
    assert not issues
    result = generate_opportunities(snapshots, _config(), now=NOW)
    opportunity, = result.strict_opportunities
    assert opportunity.calculation.total_fees == 0
    assert opportunity.full_worst_case > 0
    for leg in opportunity.calculation.legs:
        # Under the explicitly supplied half-up schedule, raw fees below a
        # half-cent round to zero. A percentage alone does not prove fee erasure.
        assert leg.cash_cost * Decimal("0.50") < Decimal("0.005")


def test_stale_unknown_timestamp_and_disconnect_are_theoretical_only() -> None:
    stale_records = (
        _market(
            "market-a",
            "venue-a",
            "0.45",
            "0.62",
            observed_at=NOW - timedelta(seconds=31),
        ),
        _market(
            "market-b",
            "venue-b",
            "0.61",
            "0.45",
            observed_at=NOW - timedelta(seconds=31),
        ),
    )
    stale_snapshots, _ = to_engine_snapshots(stale_records)
    stale_result = generate_opportunities(stale_snapshots, _config(), now=NOW)
    assert not stale_result.strict_opportunities
    assert "stale_quote" in stale_result.rejection_counts

    disconnected = (
        _market("market-a", "venue-a", "0.45", "0.62"),
        _market(
            "market-b",
            "venue-b",
            "0.61",
            "0.45",
            provider_status="disconnected",
        ),
    )
    disconnected_snapshots, _ = to_engine_snapshots(disconnected)
    assert "provider_health_disconnected" in disconnected_snapshots[1].execution_blockers
    disconnected_result = generate_opportunities(disconnected_snapshots, _config(), now=NOW)
    assert not disconnected_result.strict_opportunities
    assert "provider_health_disconnected" in disconnected_result.rejection_counts

    unknown_timestamp = replace(
        disconnected_snapshots[0].offers[0], provider_timestamp_known=False
    )
    altered = replace(
        disconnected_snapshots[0], offers=(unknown_timestamp, *disconnected_snapshots[0].offers[1:])
    )
    timestamp_result = generate_opportunities(
        (altered, to_engine_snapshots((_market("market-b", "venue-b", "0.61", "0.45"),))[0][0]),
        _config(),
        now=NOW,
    )
    assert not timestamp_result.strict_opportunities
    assert "provider_quote_timestamp_unknown" in timestamp_result.rejection_counts


def test_canonical_event_guard_and_reconnect_withdraw_then_reactivate_alert() -> None:
    missing_repository = _LifecycleRepository(
        (
            _market("market-a", "venue-a", "0.45", "0.62", canonical_event_id=None),
            _market("market-b", "venue-b", "0.61", "0.45"),
        )
    )
    missing = run_post_ingestion_cycle(missing_repository, _config(), now=NOW)
    assert missing.strict_opportunities == 0
    assert missing.persisted_opportunities == 0
    assert "canonical_event_not_materialized" in missing.rejection_counts

    mismatch_repository = _LifecycleRepository(
        (
            _market("market-a", "venue-a", "0.45", "0.62"),
            _market(
                "market-b",
                "venue-b",
                "0.61",
                "0.45",
                canonical_event_id="00000000-0000-0000-0000-000000000202",
            ),
        )
    )
    mismatch = run_post_ingestion_cycle(mismatch_repository, _config(), now=NOW)
    assert mismatch.persisted_opportunities == 0
    assert any(issue.code == "canonical_event_id_mismatch" for issue in mismatch.issues)

    repository = _LifecycleRepository(
        (
            _market("market-a", "venue-a", "0.45", "0.62"),
            _market("market-b", "venue-b", "0.61", "0.45"),
        )
    )
    first = run_post_ingestion_cycle(repository, _config(), now=NOW)
    assert first.alert_events_created == 1
    repository.markets = (
        repository.markets[0],
        replace(repository.markets[1], provider_status="disconnected"),
    )
    lost = run_post_ingestion_cycle(repository, _config(), now=NOW + timedelta(seconds=1))
    assert lost.persisted_opportunities == 0
    assert lost.withdrawn_opportunities == 1
    repository.markets = (
        repository.markets[0],
        replace(repository.markets[1], provider_status="connected"),
    )
    recovered = run_post_ingestion_cycle(repository, _config(), now=NOW + timedelta(seconds=2))
    assert recovered.persisted_opportunities == 1
    assert recovered.alert_events_created == 1
    assert repository.withdrawals == 1


def test_loader_can_surface_unknown_provider_timestamp_without_inventing_a_quote() -> None:
    repository = _LifecycleRepository(())
    repository.load_market_snapshots = lambda **_: MarketLoadResult(  # type: ignore[method-assign]
        (), (MarketLoadIssue("market-a", "provider_timestamp_unknown"),)
    )

    report = run_post_ingestion_cycle(repository, _config(), now=NOW)

    assert report.status == "no_evaluable_markets"
    assert report.persisted_opportunities == 0
    assert report.issues[0].code == "provider_timestamp_unknown"
