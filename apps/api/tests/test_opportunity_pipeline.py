from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest

from edgeos.domain import (
    CanonicalEvent,
    CanonicalMarket,
    FeeSchedule,
    MatchDecision,
    MatchStatus,
    Offer,
    PriceKind,
    SettlementRules,
)
from edgeos.matching import MATCHING_ENGINE_VERSION, material_market_fingerprint
from edgeos.opportunity_pipeline import (
    ExecutionBasis,
    ExecutableOffer,
    MarketSnapshot,
    PipelineConfig,
    SupportedAction,
    generate_opportunities,
)


NOW = datetime(2026, 10, 20, 12, 0, tzinfo=UTC)
OUTCOMES = ("home", "away")


def event(event_id: str = "event") -> CanonicalEvent:
    return CanonicalEvent(
        event_id=event_id,
        sport="basketball",
        league="NBA",
        home_team="New York Knicks",
        away_team="Boston Celtics",
        scheduled_start=NOW + timedelta(hours=4),
    )


def complete_rules(**updates: object) -> SettlementRules:
    values: dict[str, object] = {
        "period": "full game",
        "overtime_included": True,
        "shootout_included": False,
        "tie_treatment": "no tie",
        "draw_treatment": "not_applicable",
        "push_treatment": "not_applicable",
        "void_treatment": "not_applicable",
        "cancellation_treatment": "not_applicable",
        "postponement_treatment": "reschedule",
        "retirement_treatment": "not_applicable",
        "abandonment_treatment": "official result",
        "outcome_structure": "two way winner",
        "threshold": Decimal("0"),
        "line_unit": "points",
        "resolution_source": "official NBA result",
        "rules_text": "Full game including overtime.",
    }
    values.update(updates)
    return SettlementRules(**values)


def market(
    market_id: str,
    provider: str,
    *,
    rules: SettlementRules | None = None,
    outcomes: tuple[str, ...] = OUTCOMES,
    market_type: str = "moneyline",
) -> CanonicalMarket:
    return CanonicalMarket(
        market_id=market_id,
        event=event(),
        provider=provider,
        provider_market_id=f"external-{market_id}",
        market_type=market_type,
        outcome_ids=outcomes,
        rules=rules or complete_rules(),
        source_url=f"https://{provider}.example/market/{market_id}",
    )


def fee(provider: str, *, fixed: str = "0") -> FeeSchedule:
    return FeeSchedule(
        schedule_id=f"{provider}-2026",
        provider=provider,
        effective_from=date(2026, 1, 1),
        fixed_per_order=Decimal(fixed),
        source_url=f"https://{provider}.example/fees",
    )


def decimal_executable(
    market_id: str,
    provider: str,
    offer_id: str,
    outcome: str,
    odds: str,
    *,
    scenarios: tuple[str, ...] = OUTCOMES,
    payouts: dict[str, Decimal] | None = None,
    quoted_at: datetime = NOW,
    data_origin: str = "live_poll",
    execution_basis: ExecutionBasis = "direct_sportsbook_offer",
    fixed_fee: str = "0",
    provider_timestamp_known: bool = True,
) -> ExecutableOffer:
    payout_vector = {scenario: Decimal("0") for scenario in scenarios}
    payout_vector[outcome] = Decimal(odds)
    if payouts is not None:
        payout_vector.update(payouts)
    return ExecutableOffer(
        offer=Offer(
            offer_id=offer_id,
            provider=provider,
            outcome_id=outcome,
            price_kind=PriceKind.DECIMAL_ODDS,
            price=Decimal(odds),
            quote_timestamp=quoted_at,
            fee_schedule=fee(provider, fixed=fixed_fee),
            maximum_quantity=Decimal("1000"),
            quantity_increment=Decimal("0.01"),
            source_url=f"https://{provider}.example/quote/{offer_id}",
            data_origin=data_origin,
        ),
        source_market_id=market_id,
        action="back",
        execution_basis=execution_basis,
        provider_timestamp_known=provider_timestamp_known,
        payout_per_unit=payout_vector,
    )


def snapshot(
    canonical_market: CanonicalMarket,
    *offers: ExecutableOffer,
    scenarios: tuple[str, ...] = OUTCOMES,
) -> MarketSnapshot:
    return MarketSnapshot(
        market=canonical_market,
        offers=tuple(offers),
        scenario_ids=scenarios,
        scenario_completeness_verified=True,
    )


def config(
    *,
    total_allocation: Decimal = Decimal("210"),
    partial_fill_probability: Decimal | None = Decimal("0.02"),
    minimum_liquidity: Decimal = Decimal("1"),
) -> PipelineConfig:
    return PipelineConfig(
        total_allocation=total_allocation,
        partial_fill_probability=partial_fill_probability,
        minimum_liquidity=minimum_liquidity,
    )


def cross_audit(result, left_id: str, right_id: str):
    return next(
        audit
        for audit in result.candidate_audits
        if audit.left_market_id == left_id and audit.right_market_id == right_id
    )


def test_verified_cross_market_opportunity_uses_current_executable_evidence() -> None:
    left_market = market("market-a", "book-a")
    right_market = market("market-b", "book-b")
    left = snapshot(
        left_market,
        decimal_executable("market-a", "book-a", "a-home", "home", "2.20"),
    )
    right = snapshot(
        right_market,
        decimal_executable("market-b", "book-b", "b-away", "away", "2.00"),
    )

    result = generate_opportunities((right, left), config(), now=NOW)

    assert result.evaluated_combinations == 1
    assert len(result.opportunities) == 1
    opportunity = result.opportunities[0]
    assert opportunity.decision.status is MatchStatus.VERIFIED
    assert opportunity.classification == "executable_quote_supported"
    assert opportunity.execution_supported
    assert opportunity.rejection_codes == ()
    assert opportunity.ranking_rejection_codes == ()
    assert opportunity.calculation.deployed_capital == Decimal("210")
    assert opportunity.full_worst_case == Decimal("10")
    assert opportunity.full_best_case == Decimal("10")
    assert opportunity.calculation.net_roi.quantize(Decimal("0.000001")) == Decimal(
        "0.047619"
    )
    assert len(result.strict_opportunities) == 1
    assert len(result.ranked_opportunities) == 1


@pytest.mark.parametrize("data_origin", ["cache", "historical", "demo"])
def test_indicative_and_non_live_prices_never_claim_current_execution_support(
    data_origin: str,
) -> None:
    left_market = market("market-a", "aggregator")
    right_market = market("market-b", "book-b")
    left = snapshot(
        left_market,
        decimal_executable(
            "market-a",
            "aggregator",
            "a-home",
            "home",
            "2.20",
            data_origin=data_origin,
            execution_basis="indicative",
        ),
    )
    right = snapshot(
        right_market,
        decimal_executable("market-b", "book-b", "b-away", "away", "2.00"),
    )

    opportunity = generate_opportunities((left, right), config(), now=NOW).opportunities[0]

    assert opportunity.calculation.is_theoretical_arbitrage
    assert opportunity.classification == "theoretical"
    assert not opportunity.execution_supported
    assert "indicative_price_not_executable" in opportunity.rejection_codes
    assert "non_current_quote_origin" in opportunity.rejection_codes


def test_provider_timestamp_and_market_semantics_are_explicit_execution_gates() -> None:
    left_market = market("market-a", "book-a")
    left_offer = decimal_executable(
        "market-a",
        "book-a",
        "a-home",
        "home",
        "2.20",
        provider_timestamp_known=False,
    )
    left = MarketSnapshot(
        market=left_market,
        offers=(left_offer,),
        scenario_ids=OUTCOMES,
        scenario_completeness_verified=False,
        execution_blockers=("periodically_refreshed_not_immediately_executable",),
    )
    right = snapshot(
        market("market-b", "book-b"),
        decimal_executable("market-b", "book-b", "b-away", "away", "2.00"),
    )

    opportunity = generate_opportunities((left, right), config(), now=NOW).opportunities[0]

    assert opportunity.calculation.is_theoretical_arbitrage
    assert not opportunity.execution_supported
    assert "provider_quote_timestamp_unknown" in opportunity.rejection_codes
    assert "settlement_scenario_completeness_unknown" in opportunity.rejection_codes
    assert (
        "periodically_refreshed_not_immediately_executable"
        in opportunity.rejection_codes
    )


def test_offer_pruning_prefers_valid_evidence_before_a_better_indicative_price() -> None:
    left_market = market("market-a", "book-a")
    left = snapshot(
        left_market,
        decimal_executable(
            "market-a",
            "book-a",
            "indicative-home",
            "home",
            "3.00",
            execution_basis="indicative",
        ),
        decimal_executable(
            "market-a", "book-a", "executable-home", "home", "2.20"
        ),
    )
    right = snapshot(
        market("market-b", "book-b"),
        decimal_executable("market-b", "book-b", "b-away", "away", "2.00"),
    )
    strict_config = PipelineConfig(
        total_allocation=Decimal("210"),
        partial_fill_probability=Decimal("0.02"),
        maximum_offers_per_outcome=1,
    )

    opportunity = generate_opportunities((left, right), strict_config, now=NOW).opportunities[0]

    assert opportunity.execution_supported
    assert {item.offer.offer_id for item in opportunity.selected_offers} == {
        "executable-home",
        "b-away",
    }


def test_missing_fill_model_blocks_ranking_but_not_quote_evidence_classification() -> None:
    left = snapshot(
        market("market-a", "book-a"),
        decimal_executable("market-a", "book-a", "a-home", "home", "2.20"),
    )
    right = snapshot(
        market("market-b", "book-b"),
        decimal_executable("market-b", "book-b", "b-away", "away", "2.00"),
    )

    opportunity = generate_opportunities(
        (left, right),
        config(partial_fill_probability=None),
        now=NOW,
    ).opportunities[0]

    assert opportunity.execution_supported
    assert opportunity.classification == "executable_quote_supported"
    assert opportunity.rank is None
    assert opportunity.ranking_rejection_codes == ("partial_fill_probability_unknown",)


@pytest.mark.parametrize(
    ("right_rules", "expected_status"),
    [
        (
            complete_rules(period="regulation", overtime_included=False),
            MatchStatus.REJECTED,
        ),
        (complete_rules(cancellation_treatment=None), MatchStatus.REVIEW_REQUIRED),
    ],
)
def test_incompatible_or_unknown_settlement_never_reaches_calculation(
    right_rules: SettlementRules,
    expected_status: MatchStatus,
) -> None:
    left = snapshot(
        market("market-a", "book-a"),
        decimal_executable("market-a", "book-a", "a-home", "home", "2.20"),
    )
    right = snapshot(
        market("market-b", "book-b", rules=right_rules),
        decimal_executable("market-b", "book-b", "b-away", "away", "2.00"),
    )

    result = generate_opportunities((left, right), config(), now=NOW)
    audit = cross_audit(result, "market-a", "market-b")

    assert audit.decision.status is expected_status
    assert result.evaluated_combinations == 0
    assert not result.opportunities


def test_push_cash_flow_turns_normal_outcome_arbitrage_into_theoretical_only() -> None:
    scenarios = ("home", "away", "push")
    left = snapshot(
        market("market-a", "book-a", rules=complete_rules(push_treatment="refund")),
        decimal_executable(
            "market-a",
            "book-a",
            "a-home",
            "home",
            "2.20",
            scenarios=scenarios,
            payouts={"push": Decimal("1")},
        ),
        scenarios=scenarios,
    )
    right = snapshot(
        market("market-b", "book-b", rules=complete_rules(push_treatment="refund")),
        decimal_executable(
            "market-b",
            "book-b",
            "b-away",
            "away",
            "2.00",
            scenarios=scenarios,
            payouts={"push": Decimal("1")},
        ),
        scenarios=scenarios,
    )

    opportunity = generate_opportunities((left, right), config(), now=NOW).opportunities[0]
    results = {row.outcome_id: row.net_profit for row in opportunity.full_scenario_outcomes}

    assert opportunity.calculation.is_theoretical_arbitrage
    assert results == {
        "home": Decimal("10"),
        "away": Decimal("10"),
        "push": Decimal("0"),
    }
    assert opportunity.full_worst_case == Decimal("0")
    assert not opportunity.execution_supported
    assert "non_positive_full_scenario_return" in opportunity.rejection_codes


def test_fees_and_stale_quotes_cannot_survive_strict_gates() -> None:
    fee_left = snapshot(
        market("fee-a", "book-a"),
        decimal_executable(
            "fee-a", "book-a", "fee-home", "home", "2.02", fixed_fee="2"
        ),
    )
    fee_right = snapshot(
        market("fee-b", "book-b"),
        decimal_executable(
            "fee-b", "book-b", "fee-away", "away", "2.02", fixed_fee="2"
        ),
    )
    fee_result = generate_opportunities(
        (fee_left, fee_right), config(total_allocation=Decimal("202")), now=NOW
    )
    fee_opportunity = fee_result.opportunities[0]
    assert fee_opportunity.calculation.gross_profit > 0
    assert fee_opportunity.full_worst_case < 0
    assert not fee_opportunity.execution_supported
    assert "non_positive_full_scenario_return" in fee_opportunity.rejection_codes

    stale_left = snapshot(
        market("stale-a", "book-a"),
        decimal_executable(
            "stale-a",
            "book-a",
            "stale-home",
            "home",
            "2.20",
            quoted_at=NOW - timedelta(minutes=2),
        ),
    )
    stale_right = snapshot(
        market("stale-b", "book-b"),
        decimal_executable("stale-b", "book-b", "stale-away", "away", "2.00"),
    )
    stale_opportunity = generate_opportunities(
        (stale_left, stale_right), config(), now=NOW
    ).opportunities[0]
    assert "stale_quote" in stale_opportunity.rejection_codes
    assert not stale_opportunity.execution_supported


def test_same_market_complementary_contracts_need_no_persisted_pair() -> None:
    prediction_market = market(
        "binary-market",
        "exchange",
        outcomes=("yes", "no"),
        market_type="binary contract",
    )

    def contract(
        offer_id: str, outcome: str, action: SupportedAction
    ) -> ExecutableOffer:
        return ExecutableOffer(
            offer=Offer(
                offer_id=offer_id,
                provider="exchange",
                outcome_id=outcome,
                price_kind=PriceKind.CONTRACT_ASK,
                price=Decimal("0.45"),
                quote_timestamp=NOW,
                fee_schedule=fee("exchange"),
                maximum_quantity=Decimal("100"),
                quantity_increment=Decimal("1"),
                source_url=f"https://exchange.example/book/{offer_id}",
                data_origin="live_poll",
            ),
            source_market_id="binary-market",
            action=action,
            execution_basis="orderbook_ask",
            provider_timestamp_known=True,
            payout_per_unit={
                "yes": Decimal("1") if outcome == "yes" else Decimal("0"),
                "no": Decimal("1") if outcome == "no" else Decimal("0"),
            },
        )

    only_snapshot = snapshot(
        prediction_market,
        contract("yes-ask", "yes", "buy_yes"),
        contract("no-ask", "no", "buy_no"),
        scenarios=("yes", "no"),
    )
    result = generate_opportunities(
        (only_snapshot,), config(total_allocation=Decimal("90")), now=NOW
    )

    assert len(result.strict_opportunities) == 1
    opportunity = result.strict_opportunities[0]
    assert opportunity.left_market_id == opportunity.right_market_id == "binary-market"
    assert opportunity.full_worst_case == Decimal("10.00")
    assert cross_audit(result, "binary-market", "binary-market").decision_source == "automatic"


def test_trusted_operator_decision_requires_current_material_fingerprints() -> None:
    unknown = complete_rules(cancellation_treatment=None)
    left_market = market("market-a", "book-a", rules=unknown)
    right_market = market("market-b", "book-b", rules=unknown)
    left = snapshot(
        left_market,
        decimal_executable("market-a", "book-a", "a-home", "home", "2.20"),
    )
    right = snapshot(
        right_market,
        decimal_executable("market-b", "book-b", "b-away", "away", "2.00"),
    )
    trusted = MatchDecision(
        status=MatchStatus.VERIFIED,
        candidate_score=Decimal("1"),
        reasons=("operator reviewed cited source rules",),
        engine_version=MATCHING_ENGINE_VERSION,
        left_material_fingerprint=material_market_fingerprint(left_market),
        right_material_fingerprint=material_market_fingerprint(right_market),
    )

    accepted = generate_opportunities(
        (left, right),
        config(),
        now=NOW,
        trusted_match_decisions={
            ("market-b", "market-a"): trusted.model_copy(
                update={
                    "left_material_fingerprint": trusted.right_material_fingerprint,
                    "right_material_fingerprint": trusted.left_material_fingerprint,
                }
            )
        },
    )
    accepted_audit = cross_audit(accepted, "market-a", "market-b")
    assert accepted_audit.decision_source == "trusted_persisted_review"
    assert accepted_audit.override_rejection is None
    assert len(accepted.strict_opportunities) == 1

    outdated = generate_opportunities(
        (left, right),
        config(),
        now=NOW,
        trusted_match_decisions={
            ("market-a", "market-b"): trusted.model_copy(
                update={"engine_version": "edgeos.settlement.v1"}
            )
        },
    )
    outdated_audit = cross_audit(outdated, "market-a", "market-b")
    assert outdated_audit.override_rejection == "trusted_decision_engine_version_mismatch"
    assert outdated_audit.decision.status is MatchStatus.REVIEW_REQUIRED
    assert not outdated.opportunities

    changed_market = right_market.model_copy(
        update={"rules": unknown.model_copy(update={"rules_text": "Materially changed source text."})}
    )
    changed = snapshot(
        changed_market,
        decimal_executable("market-b", "book-b", "b-away", "away", "2.00"),
    )
    rejected = generate_opportunities(
        (left, changed),
        config(),
        now=NOW,
        trusted_match_decisions={("market-a", "market-b"): trusted},
    )
    rejected_audit = cross_audit(rejected, "market-a", "market-b")
    assert rejected_audit.decision_source == "automatic"
    assert rejected_audit.override_rejection == "trusted_decision_fingerprint_mismatch"
    assert rejected_audit.decision.status is MatchStatus.REVIEW_REQUIRED
    assert not rejected.opportunities


def test_trusted_override_nominates_pair_that_fuzzy_matching_did_not() -> None:
    unknown = complete_rules(cancellation_treatment=None)
    left_market = market("market-a", "book-a", rules=unknown)
    right_market = market("market-b", "book-b", rules=unknown).model_copy(
        update={
            "event": event().model_copy(
                update={"scheduled_start": NOW + timedelta(hours=12)}
            )
        }
    )
    left = snapshot(
        left_market,
        decimal_executable("market-a", "book-a", "a-home", "home", "2.20"),
    )
    right = snapshot(
        right_market,
        decimal_executable("market-b", "book-b", "b-away", "away", "2.00"),
    )
    trusted = MatchDecision(
        status=MatchStatus.VERIFIED,
        candidate_score=Decimal("0"),
        reasons=("operator verified provider event identity",),
        engine_version=MATCHING_ENGINE_VERSION,
        left_material_fingerprint=material_market_fingerprint(left_market),
        right_material_fingerprint=material_market_fingerprint(right_market),
    )

    result = generate_opportunities(
        (left, right),
        config(),
        now=NOW,
        trusted_match_decisions={("market-a", "market-b"): trusted},
    )

    assert cross_audit(result, "market-a", "market-b").decision_source == (
        "trusted_persisted_review"
    )
    assert len(result.strict_opportunities) == 1


def test_snapshot_requires_explicit_complete_scenario_vectors() -> None:
    canonical_market = market("market-a", "book-a")
    incomplete = decimal_executable(
        "market-a", "book-a", "a-home", "home", "2.20"
    )
    incomplete = ExecutableOffer(
        offer=incomplete.offer,
        source_market_id=incomplete.source_market_id,
        action=incomplete.action,
        execution_basis=incomplete.execution_basis,
        provider_timestamp_known=incomplete.provider_timestamp_known,
        payout_per_unit={"home": Decimal("2.20")},
    )

    with pytest.raises(ValueError, match="explicitly cover every scenario"):
        snapshot(canonical_market, incomplete)


@pytest.mark.parametrize(
    "scenario", ["push", "void", "cancellation", "postponement", "retirement", "abandonment"]
)
def test_declared_completeness_cannot_omit_a_refund_scenario(scenario: str) -> None:
    settlement = complete_rules(**{f"{scenario}_treatment": "refund"})
    left = snapshot(
        market("market-a", "book-a", rules=settlement),
        decimal_executable("market-a", "book-a", "a-home", "home", "2.20"),
    )
    right = snapshot(
        market("market-b", "book-b", rules=settlement),
        decimal_executable("market-b", "book-b", "b-away", "away", "2.00"),
    )
    result = generate_opportunities((left, right), config(), now=NOW)
    assert not result.opportunities
    assert result.evaluated_combinations == 0
    assert f"required_settlement_scenario_missing:{scenario}" in cross_audit(
        result, "market-a", "market-b"
    ).gate_failures


@pytest.mark.parametrize("scenario", ["postponement", "retirement", "abandonment"])
def test_nonstandard_refund_scenarios_prevent_profitable_execution_claims(scenario: str) -> None:
    settlement = complete_rules(**{f"{scenario}_treatment": "refund"})
    scenarios = (*OUTCOMES, scenario)
    left = snapshot(
        market("market-a", "book-a", rules=settlement),
        decimal_executable(
            "market-a", "book-a", "a-home", "home", "2.20",
            scenarios=scenarios, payouts={scenario: Decimal("1")},
        ),
        scenarios=scenarios,
    )
    right = snapshot(
        market("market-b", "book-b", rules=settlement),
        decimal_executable(
            "market-b", "book-b", "b-away", "away", "2.00",
            scenarios=scenarios, payouts={scenario: Decimal("1")},
        ),
        scenarios=scenarios,
    )
    opportunity = generate_opportunities((left, right), config(), now=NOW).opportunities[0]
    assert opportunity.full_worst_case == 0
    assert not opportunity.execution_supported
    assert "non_positive_full_scenario_return" in opportunity.rejection_codes


@pytest.mark.parametrize(
    "payouts",
    [{"home": Decimal("0")}, {"away": Decimal("1")}],
)
def test_snapshot_rejects_canonical_cash_flows_that_the_optimizer_cannot_model(payouts) -> None:
    executable = decimal_executable(
        "market-a", "book-a", "a-home", "home", "2.20", payouts=payouts
    )
    with pytest.raises(ValueError, match="cash flows disagree with offer economics"):
        snapshot(market("market-a", "book-a"), executable)


def test_snapshot_rejects_inflated_refund_cash_flows() -> None:
    scenarios = (*OUTCOMES, "postponement")
    executable = decimal_executable(
        "market-a", "book-a", "a-home", "home", "2.20",
        scenarios=scenarios, payouts={"postponement": Decimal("100")},
    )
    with pytest.raises(ValueError, match="postponement cash flow disagrees"):
        snapshot(
            market("market-a", "book-a", rules=complete_rules(postponement_treatment="refund")),
            executable,
            scenarios=scenarios,
        )


def test_offer_outcome_alias_cannot_be_promoted_to_canonical_identity() -> None:
    outcomes = ("New York FC", "Boston")
    executable = decimal_executable(
        "market-a", "book-a", "a-home", "New York CF", "2.20", scenarios=outcomes
    )
    with pytest.raises(ValueError, match="offer outcome is absent"):
        snapshot(
            market("market-a", "book-a", outcomes=outcomes), executable, scenarios=outcomes
        )


def test_configured_slippage_is_conservative_explicit_and_does_not_mutate_sources() -> None:
    left = snapshot(
        market("market-a", "book-a"),
        decimal_executable("market-a", "book-a", "a-home", "home", "2.20"),
    )
    right = snapshot(
        market("market-b", "book-b"),
        decimal_executable("market-b", "book-b", "b-away", "away", "2.00"),
    )
    baseline = generate_opportunities((left, right), config(), now=NOW).opportunities[0]
    conservative = generate_opportunities(
        (left, right),
        PipelineConfig(total_allocation=Decimal("210"), slippage_bps=Decimal("10")),
        now=NOW,
    ).opportunities[0]
    assert conservative.full_worst_case < baseline.full_worst_case
    assert all(item.offer.slippage_bps == Decimal("10") for item in conservative.selected_offers)
    assert left.offers[0].offer.slippage_bps == right.offers[0].offer.slippage_bps == 0
    assert "modeled assumption, not a measured execution estimate" in conservative.calculation.assumptions[0]


@pytest.mark.parametrize("slippage", [Decimal("-1"), Decimal("NaN"), Decimal("Infinity")])
def test_slippage_configuration_must_be_finite_and_nonnegative(slippage: Decimal) -> None:
    with pytest.raises(ValueError):
        PipelineConfig(total_allocation=Decimal("100"), slippage_bps=slippage)

