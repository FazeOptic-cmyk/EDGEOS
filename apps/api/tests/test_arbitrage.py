from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

import pytest

from edgeos.arbitrage import calculate_arbitrage, evaluate_bundle, fee_for
from edgeos.domain import FeeSchedule, Offer, PriceKind, ScenarioLeg

from conftest import decimal_offer


def test_documented_220_200_example_exact(fixed_now, zero_fee) -> None:
    result = calculate_arbitrage(
        [
            decimal_offer("a", "A", "2.20", fixed_now, zero_fee),
            decimal_offer("b", "B", "2.00", fixed_now, zero_fee),
        ],
        Decimal("210"), now=fixed_now, expected_outcome_ids=("A", "B"),
    )
    assert [leg.filled_quantity for leg in result.legs] == [Decimal("100.00"), Decimal("110.00")]
    assert result.equalized_gross_payout == Decimal("220")
    assert result.gross_profit == Decimal("10")
    assert result.gross_roi.quantize(Decimal("0.000001")) == Decimal("0.047619")
    assert result.net_profit_worst_case == Decimal("10")
    assert result.execution_eligible


def test_three_way_complete_market(fixed_now, zero_fee) -> None:
    result = calculate_arbitrage(
        [decimal_offer(str(i), outcome, "3.60", fixed_now, zero_fee) for i, outcome in enumerate(("home", "draw", "away"))],
        Decimal("300"), now=fixed_now, expected_outcome_ids=("home", "draw", "away"),
    )
    assert [leg.filled_quantity for leg in result.legs] == [Decimal("100.00")] * 3
    assert result.equalized_gross_payout == Decimal("360")
    assert result.net_profit_worst_case == Decimal("60")


def test_missing_canonical_outcome_is_modeled_as_total_loss_and_rejected(fixed_now, zero_fee) -> None:
    result = calculate_arbitrage(
        [
            decimal_offer("home", "home", "2.20", fixed_now, zero_fee),
            decimal_offer("away", "away", "2.20", fixed_now, zero_fee),
        ],
        Decimal("200"), now=fixed_now,
        expected_outcome_ids=("home", "draw", "away"),
    )
    by_outcome = {item.outcome_id: item for item in result.outcomes}
    assert by_outcome["draw"].gross_payout == Decimal("0")
    assert by_outcome["draw"].net_profit == -result.deployed_capital
    assert "incomplete_outcome_coverage" in result.rejection_codes
    assert not result.outcome_coverage_verified
    assert not result.is_theoretical_arbitrage
    assert not result.execution_eligible


def test_unspecified_canonical_outcomes_default_fail_closed(fixed_now, zero_fee) -> None:
    result = calculate_arbitrage(
        [decimal_offer("a", "A", "2.20", fixed_now, zero_fee), decimal_offer("b", "B", "2.00", fixed_now, zero_fee)],
        Decimal("210"), now=fixed_now,
    )
    assert "outcome_completeness_unverified" in result.rejection_codes
    assert not result.outcome_coverage_verified
    assert not result.execution_eligible


def test_fees_are_inside_cash_cap_and_turn_appearance_into_loss(fixed_now, zero_fee) -> None:
    fee = FeeSchedule(
        schedule_id="fixed-two", provider="test", effective_from=zero_fee.effective_from,
        fixed_per_order=Decimal("2"), rounding_increment=Decimal("0.01"),
    )
    result = calculate_arbitrage(
        [decimal_offer("a", "A", "2.02", fixed_now, fee), decimal_offer("b", "B", "2.02", fixed_now, fee)],
        Decimal("202"), now=fixed_now,
    )
    assert result.deployed_capital <= Decimal("202")
    assert result.gross_profit > 0
    assert result.net_profit_worst_case < 0
    assert not result.execution_eligible


def test_high_proportional_fee_never_exceeds_hard_cash_cap(fixed_now, zero_fee) -> None:
    fee = FeeSchedule(
        schedule_id="fifty-percent", provider="test", effective_from=zero_fee.effective_from,
        proportional_rate=Decimal("0.5"), rounding_increment=Decimal("0.01"),
    )
    result = calculate_arbitrage(
        [decimal_offer("a", "A", "2.20", fixed_now, fee), decimal_offer("b", "B", "2.00", fixed_now, fee)],
        Decimal("210"), now=fixed_now,
    )
    assert result.deployed_capital <= Decimal("210")
    assert result.total_fees > 0


def test_contract_rounding_uses_discrete_quantities(fixed_now, zero_fee) -> None:
    offers = [
        Offer(
            offer_id=f"c{i}", provider=f"p{i}", outcome_id=outcome,
            price_kind=PriceKind.CONTRACT_ASK, price=Decimal("0.49"),
            quote_timestamp=fixed_now, fee_schedule=zero_fee,
            quantity_increment=Decimal("1"), maximum_quantity=Decimal("100"),
            data_origin="historical",
        )
        for i, outcome in enumerate(("yes", "no"))
    ]
    result = calculate_arbitrage(offers, Decimal("10"), now=fixed_now)
    assert [leg.filled_quantity for leg in result.legs] == [Decimal("10"), Decimal("10")]
    assert result.deployed_capital == Decimal("9.80")
    assert result.net_profit_worst_case == Decimal("0.20")


@pytest.mark.parametrize(
    ("mode", "expected"),
    [("ceiling", "0.03"), ("floor", "0.02"), ("half_up", "0.03"), ("half_even", "0.02")],
)
def test_fee_rounding_uses_verified_schedule_mode(fixed_now, zero_fee, mode, expected) -> None:
    schedule = FeeSchedule(
        schedule_id="explicit-rounding",
        provider="test",
        effective_from=zero_fee.effective_from,
        fixed_per_order=Decimal("0.025"),
        rounding_increment=Decimal("0.01"),
        rounding_mode=mode,
    )
    assert fee_for(schedule, Decimal("100"), Decimal("10"), on=fixed_now) == Decimal(expected)


def test_unsupported_fee_rounding_is_rejected(zero_fee) -> None:
    with pytest.raises(ValueError, match="rounding_mode"):
        FeeSchedule(
            schedule_id="bad-rounding",
            provider="test",
            effective_from=zero_fee.effective_from,
            rounding_mode="guess",
        )


def test_stale_future_and_unknown_depth_are_hard_rejections(fixed_now, zero_fee) -> None:
    stale = decimal_offer("a", "A", "2.2", fixed_now - timedelta(minutes=5), zero_fee)
    future = decimal_offer("b", "B", "2", fixed_now + timedelta(seconds=1), zero_fee, maximum=None)
    result = calculate_arbitrage([stale, future], Decimal("210"), now=fixed_now)
    assert "stale_quote" in result.rejection_codes
    assert "quote_timestamp_in_future" in result.rejection_codes
    assert "orderbook_depth_unknown" in result.rejection_codes
    assert not result.execution_eligible


def test_depth_constrained_optimizer_scales_complete_bundle_instead_of_overfilling(fixed_now, zero_fee) -> None:
    result = calculate_arbitrage(
        [
            decimal_offer("a", "A", "2.20", fixed_now, zero_fee, maximum="20"),
            decimal_offer("b", "B", "2.00", fixed_now, zero_fee),
        ],
        Decimal("210"), now=fixed_now, expected_outcome_ids=("A", "B"),
    )
    assert [leg.filled_quantity for leg in result.legs] == [Decimal("20"), Decimal("22")]
    assert result.deployed_capital == Decimal("42")
    assert result.net_profit_worst_case == Decimal("2")
    assert "partial_fill_risk" not in result.rejection_codes
    assert "unmatched_exposure" not in result.rejection_codes
    assert "observed_depth:a" in result.allocation_limit_reasons


def test_unknown_fees_require_explicit_assumption(fixed_now, zero_fee) -> None:
    first = decimal_offer("a", "A", "2.2", fixed_now, zero_fee).model_copy(update={"fee_schedule": None})
    second = decimal_offer("b", "B", "2", fixed_now, zero_fee)
    strict = calculate_arbitrage([first, second], Decimal("210"), now=fixed_now)
    assumed = calculate_arbitrage([first, second], Decimal("210"), now=fixed_now, allow_fee_assumptions=True)
    assert "fee_schedule_unknown" in strict.rejection_codes
    assert not strict.execution_eligible
    assert "fee_schedule_unknown" not in assumed.rejection_codes
    assert assumed.assumptions


def test_general_cashflow_engine_handles_push_void_and_missing_outcome() -> None:
    legs = [
        ScenarioLeg(
            leg_id="a", provider="a", requested_quantity=Decimal("10"), filled_quantity=Decimal("10"),
            unit_cost=Decimal("1"), payout_per_unit={"home": Decimal("2.1"), "push": Decimal("1")},
        ),
        ScenarioLeg(
            leg_id="b", provider="b", requested_quantity=Decimal("10"), filled_quantity=Decimal("5"),
            unit_cost=Decimal("1"), payout_per_unit={"away": Decimal("2.1"), "push": Decimal("1")},
        ),
    ]
    result = evaluate_bundle(legs, ["home", "away", "push", "unmatched"])
    by_outcome = {row.outcome_id: row.net_profit for row in result.outcome_results}
    assert by_outcome["push"] == Decimal("0")
    assert by_outcome["unmatched"] == Decimal("-15")
    assert result.unmatched_exposure > 0


def test_timestamp_skew_is_reported_and_hard_rejected(fixed_now, zero_fee) -> None:
    offers = [
        decimal_offer("a", "A", "2.20", fixed_now, zero_fee),
        decimal_offer("b", "B", "2.00", fixed_now + timedelta(seconds=8), zero_fee),
    ]
    result = calculate_arbitrage(
        offers,
        Decimal("210"),
        now=fixed_now + timedelta(seconds=8),
        maximum_quote_timestamp_skew_seconds=5,
        expected_outcome_ids=("A", "B"),
    )
    assert result.quote_timestamp_skew_seconds == Decimal("8.0")
    assert "quote_timestamp_skew_exceeded" in result.rejection_codes
    assert not result.execution_eligible


def test_break_even_prices_measure_adverse_move_for_fixed_allocation(fixed_now, zero_fee) -> None:
    result = calculate_arbitrage(
        [
            decimal_offer("a", "A", "2.20", fixed_now, zero_fee),
            decimal_offer("b", "B", "2.00", fixed_now, zero_fee),
        ],
        Decimal("210"),
        now=fixed_now,
        expected_outcome_ids=("A", "B"),
    )
    points = {point.offer_id: point for point in result.break_even_points}
    assert points["a"].fixed_allocation_break_even_price.quantize(Decimal("0.000001")) == Decimal("2.100000")
    assert points["a"].adverse_movement.quantize(Decimal("0.000001")) == Decimal("0.100000")
    assert points["b"].fixed_allocation_break_even_price.quantize(Decimal("0.000001")) == Decimal("1.909091")
    assert result.calculation_version == "edgeos.cashflow.v2"
    assert result.currency == "USD"


def test_provider_cash_token_currency_is_preserved_and_never_silently_converted(
    fixed_now, zero_fee
) -> None:
    token_offers = [
        decimal_offer("a", "A", "2.20", fixed_now, zero_fee).model_copy(
            update={"currency": "pUSD"}
        ),
        decimal_offer("b", "B", "2.00", fixed_now, zero_fee).model_copy(
            update={"currency": "pUSD"}
        ),
    ]
    result = calculate_arbitrage(
        token_offers,
        Decimal("210"),
        now=fixed_now,
        expected_outcome_ids=("A", "B"),
    )
    assert result.currency == "pUSD"

    mixed = [token_offers[0], token_offers[1].model_copy(update={"currency": "USD"})]
    with pytest.raises(ValueError, match="share one explicit currency"):
        calculate_arbitrage(
            mixed,
            Decimal("210"),
            now=fixed_now,
            expected_outcome_ids=("A", "B"),
        )
    assert result.capital_lockup == result.deployed_capital


def test_discrete_optimizer_matches_small_exhaustive_reference(fixed_now, zero_fee) -> None:
    fee = FeeSchedule(
        schedule_id="discrete",
        provider="test",
        effective_from=zero_fee.effective_from,
        per_contract=Decimal("0.01"),
        fixed_per_order=Decimal("0.03"),
        rounding_increment=Decimal("0.01"),
    )
    offers = [
        Offer(
            offer_id="yes", provider="one", outcome_id="yes", price_kind=PriceKind.CONTRACT_ASK,
            price=Decimal("0.43"), quote_timestamp=fixed_now, fee_schedule=fee,
            quantity_increment=Decimal("1"), maximum_quantity=Decimal("6"), data_origin="historical",
        ),
        Offer(
            offer_id="no", provider="two", outcome_id="no", price_kind=PriceKind.CONTRACT_ASK,
            price=Decimal("0.51"), quote_timestamp=fixed_now, fee_schedule=fee,
            quantity_increment=Decimal("1"), maximum_quantity=Decimal("6"), data_origin="historical",
        ),
    ]
    result = calculate_arbitrage(
        offers, Decimal("5"), now=fixed_now, expected_outcome_ids=("yes", "no")
    )

    reference: tuple[tuple[Decimal, Decimal, Decimal], tuple[Decimal, Decimal]] | None = None
    for yes in range(1, 7):
        for no in range(1, 7):
            quantities = (Decimal(yes), Decimal(no))
            costs = (quantities[0] * Decimal("0.43"), quantities[1] * Decimal("0.51"))
            fees = (
                fee_for(fee, costs[0], quantities[0], on=fixed_now),
                fee_for(fee, costs[1], quantities[1], on=fixed_now),
            )
            deployed = sum(costs) + sum(fees)
            if deployed > Decimal("5"):
                continue
            profits = (quantities[0] - deployed, quantities[1] - deployed)
            objective = (min(profits), -(max(profits) - min(profits)), -deployed)
            candidate = objective, quantities
            if reference is None or objective > reference[0]:
                reference = candidate
    assert reference is not None
    assert tuple(leg.filled_quantity for leg in result.legs) == reference[1]
    assert result.net_profit_worst_case == reference[0][0]
    assert result.optimization_method == "exhaustive_discrete_maximin"


def test_provider_capital_limits_are_enforced(fixed_now, zero_fee) -> None:
    offers = [
        decimal_offer("a", "A", "2.20", fixed_now, zero_fee),
        decimal_offer("b", "B", "2.00", fixed_now, zero_fee),
    ]
    result = calculate_arbitrage(
        offers,
        Decimal("210"),
        now=fixed_now,
        expected_outcome_ids=("A", "B"),
        provider_capital_limits={"provider-a": Decimal("20"), "provider-b": Decimal("100")},
    )
    assert result.legs[0].cash_cost + result.legs[0].fees <= Decimal("20")
    assert "provider_capital:provider-a" in result.allocation_limit_reasons


def test_stress_scenarios_show_price_move_rejected_leg_and_one_sided_void(fixed_now, zero_fee) -> None:
    moved = [
        decimal_offer("a", "A", "2.02", fixed_now, zero_fee).model_copy(
            update={"slippage_bps": Decimal("250")}
        ),
        decimal_offer("b", "B", "2.02", fixed_now, zero_fee).model_copy(
            update={"slippage_bps": Decimal("250")}
        ),
    ]
    moved_result = calculate_arbitrage(
        moved, Decimal("202"), now=fixed_now, expected_outcome_ids=("A", "B")
    )
    assert moved_result.net_profit_worst_case < 0

    stressed = evaluate_bundle(
        [
            ScenarioLeg(
                leg_id="filled", provider="a", requested_quantity=Decimal("10"),
                filled_quantity=Decimal("10"), unit_cost=Decimal("1"),
                payout_per_unit={"home": Decimal("2.1"), "void_a": Decimal("1")},
            ),
            ScenarioLeg(
                leg_id="rejected", provider="b", requested_quantity=Decimal("10"),
                filled_quantity=Decimal("0"), unit_cost=Decimal("1"),
                payout_per_unit={"away": Decimal("2.1"), "void_b": Decimal("1")},
            ),
        ],
        ["home", "away", "void_a", "void_b"],
    )
    outcomes = {row.outcome_id: row.net_profit for row in stressed.outcome_results}
    assert outcomes["away"] == Decimal("-10")
    assert outcomes["void_a"] == Decimal("0")
    assert outcomes["void_b"] == Decimal("-10")
    assert stressed.unmatched_exposure >= Decimal("10")

