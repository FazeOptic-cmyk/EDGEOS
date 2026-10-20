from __future__ import annotations

from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import ROUND_CEILING, ROUND_DOWN, ROUND_FLOOR, ROUND_HALF_EVEN, ROUND_HALF_UP, Decimal
from itertools import product

from .domain import (
    AllocationLeg,
    ArbitrageResult,
    BreakEvenPoint,
    BundleEvaluation,
    FeeSchedule,
    MatchStatus,
    Offer,
    OutcomeResult,
    PriceKind,
    ScenarioLeg,
)


ZERO = Decimal("0")
ONE = Decimal("1")
BPS = Decimal("10000")
FINANCIAL_CONTRACT_VERSION = "edgeos.cashflow.v2"
MAX_EXHAUSTIVE_STATES = 200_000


def _down(value: Decimal, increment: Decimal) -> Decimal:
    # Decimal division of repeating odds can produce 99.99999 for an exact 100.
    epsilon = increment * Decimal("1e-18")
    return ((value + epsilon) / increment).to_integral_value(rounding=ROUND_DOWN) * increment


def _up(value: Decimal, increment: Decimal) -> Decimal:
    return (value / increment).to_integral_value(rounding=ROUND_CEILING) * increment


def fee_for(schedule: FeeSchedule, cash_cost: Decimal, quantity: Decimal, *, on: datetime) -> Decimal:
    if not schedule.known:
        raise ValueError("fee_schedule_unknown")
    day = on.astimezone(UTC).date()
    if day < schedule.effective_from or (schedule.effective_to and day > schedule.effective_to):
        raise ValueError("fee_schedule_not_effective")
    raw = cash_cost * schedule.proportional_rate + quantity * schedule.per_contract + schedule.fixed_per_order
    rounding = {
        "ceiling": ROUND_CEILING,
        "floor": ROUND_FLOOR,
        "half_up": ROUND_HALF_UP,
        "half_even": ROUND_HALF_EVEN,
    }[schedule.rounding_mode]
    return (
        (raw / schedule.rounding_increment).to_integral_value(rounding=rounding)
        * schedule.rounding_increment
        if raw else ZERO
    )


def _economics_at_price(offer: Offer, price: Decimal) -> tuple[Decimal, Decimal]:
    slip = ONE + (offer.slippage_bps / BPS)
    if offer.price_kind is PriceKind.DECIMAL_ODDS:
        # One quantity unit is one currency unit staked. Adverse slippage reduces odds.
        return ONE, ONE + ((price - ONE) / slip)
    # One quantity unit is one contract. Adverse slippage increases its ask.
    return price * slip, ONE


def _economics(offer: Offer) -> tuple[Decimal, Decimal]:
    return _economics_at_price(offer, offer.price)


@dataclass(frozen=True)
class _Plan:
    quantities: tuple[Decimal, ...]
    cash_costs: tuple[Decimal, ...]
    fees: tuple[Decimal, ...]
    payouts: tuple[Decimal, ...]
    total_cost: Decimal
    total_fees: Decimal
    provider_spend: dict[str, Decimal]

    @property
    def deployed(self) -> Decimal:
        return self.total_cost + self.total_fees

    @property
    def profits(self) -> tuple[Decimal, ...]:
        return tuple(payout - self.deployed for payout in self.payouts)

    @property
    def objective(self) -> tuple[Decimal, Decimal, Decimal]:
        profits = self.profits
        spread = max(profits) - min(profits)
        # Maximize the minimum outcome. At an exact tie prefer less mismatch and
        # then less locked capital, rather than spending unused cash needlessly.
        return min(profits), -spread, -self.deployed


def _minimum_covered_quantity(offer: Offer) -> Decimal:
    return _up(max(offer.minimum_quantity, offer.quantity_increment), offer.quantity_increment)


def _known_fee_or_zero(offer: Offer, cash_cost: Decimal, quantity: Decimal, *, now: datetime) -> Decimal:
    schedule = offer.fee_schedule
    if quantity <= 0 or schedule is None or not schedule.known:
        return ZERO
    try:
        return fee_for(schedule, cash_cost, quantity, on=now)
    except ValueError:
        # The caller records the specific hard rejection after sizing. Unknown
        # costs are never execution-eligible; zero here is only a deterministic
        # way to produce an inspectable rejected scenario.
        return ZERO


def _build_plan(
    offers: Sequence[Offer],
    economics: Sequence[tuple[Decimal, Decimal]],
    quantities: Sequence[Decimal],
    *,
    now: datetime,
) -> _Plan:
    cash_costs: list[Decimal] = []
    fees: list[Decimal] = []
    payouts: list[Decimal] = []
    provider_spend: dict[str, Decimal] = {}
    for offer, quantity, (unit_cost, payout_per_unit) in zip(offers, quantities, economics, strict=True):
        cash_cost = quantity * unit_cost
        fee = _known_fee_or_zero(offer, cash_cost, quantity, now=now)
        spend = cash_cost + fee
        cash_costs.append(cash_cost)
        fees.append(fee)
        payouts.append(quantity * payout_per_unit)
        provider_spend[offer.provider] = provider_spend.get(offer.provider, ZERO) + spend
    return _Plan(
        quantities=tuple(quantities),
        cash_costs=tuple(cash_costs),
        fees=tuple(fees),
        payouts=tuple(payouts),
        total_cost=sum(cash_costs, ZERO),
        total_fees=sum(fees, ZERO),
        provider_spend=provider_spend,
    )


def _plan_is_feasible(
    plan: _Plan,
    *,
    total_allocation: Decimal,
    provider_capital_limits: Mapping[str, Decimal],
) -> bool:
    if plan.deployed > total_allocation:
        return False
    return all(plan.provider_spend.get(provider, ZERO) <= limit for provider, limit in provider_capital_limits.items())


def _max_quantities(
    offers: Sequence[Offer],
    economics: Sequence[tuple[Decimal, Decimal]],
    total_allocation: Decimal,
    provider_capital_limits: Mapping[str, Decimal],
) -> tuple[Decimal, ...]:
    maximums: list[Decimal] = []
    for offer, (unit_cost, _) in zip(offers, economics, strict=True):
        cash_cap = total_allocation
        if offer.provider in provider_capital_limits:
            cash_cap = min(cash_cap, provider_capital_limits[offer.provider])
        by_cash = _down(cash_cap / unit_cost, offer.quantity_increment) if unit_cost else ZERO
        if offer.maximum_quantity is not None:
            by_cash = min(by_cash, _down(offer.maximum_quantity, offer.quantity_increment))
        maximums.append(max(ZERO, by_cash))
    return tuple(maximums)


def _optimize_discrete_allocation(
    offers: Sequence[Offer],
    economics: Sequence[tuple[Decimal, Decimal]],
    total_allocation: Decimal,
    *,
    now: datetime,
    provider_capital_limits: Mapping[str, Decimal],
) -> tuple[_Plan, str, tuple[Decimal, ...]]:
    """Maximize minimum outcome P&L under cash, depth, and increment constraints.

    Small integer spaces are exhaustively searched, which is also used by tests
    as a reference proof. Larger spaces use a deterministic payout-target search
    followed by an increment-neighborhood search. The fallback preserves the
    same max-min objective without an unbounded combinatorial runtime.
    """
    minimums = tuple(_minimum_covered_quantity(offer) for offer in offers)
    maximums = _max_quantities(offers, economics, total_allocation, provider_capital_limits)
    if any(minimum > maximum for minimum, maximum in zip(minimums, maximums, strict=True)):
        zero_plan = _build_plan(offers, economics, [ZERO] * len(offers), now=now)
        return zero_plan, "minimum_coverage_unfunded", maximums

    step_counts = [int(((maximum - minimum) / offer.quantity_increment)) + 1
                   for offer, minimum, maximum in zip(offers, minimums, maximums, strict=True)]
    state_count = 1
    for count in step_counts:
        state_count *= count
        if state_count > MAX_EXHAUSTIVE_STATES:
            break

    best: _Plan | None = None

    def consider(quantities: Sequence[Decimal]) -> None:
        nonlocal best
        plan = _build_plan(offers, economics, quantities, now=now)
        if not _plan_is_feasible(
            plan, total_allocation=total_allocation, provider_capital_limits=provider_capital_limits
        ):
            return
        if best is None or plan.objective > best.objective:
            best = plan

    if state_count <= MAX_EXHAUSTIVE_STATES:
        ranges = [
            tuple(minimum + offer.quantity_increment * index for index in range(count))
            for offer, minimum, count in zip(offers, minimums, step_counts, strict=True)
        ]
        for quantities in product(*ranges):
            consider(quantities)
        if best is None:
            best = _build_plan(offers, economics, [ZERO] * len(offers), now=now)
        return best, "exhaustive_discrete_maximin", maximums

    minimum_target = max(
        minimum * payout for minimum, (_, payout) in zip(minimums, economics, strict=True)
    )
    maximum_target = min(
        maximum * payout for maximum, (_, payout) in zip(maximums, economics, strict=True)
    )

    def quantities_for_target(target: Decimal) -> tuple[Decimal, ...]:
        return tuple(
            min(maximum, max(minimum, _up(target / payout, offer.quantity_increment)))
            for offer, minimum, maximum, (_, payout) in zip(
                offers, minimums, maximums, economics, strict=True
            )
        )

    consider(minimums)
    low = minimum_target
    high = max(minimum_target, maximum_target)
    for _ in range(96):
        midpoint = (low + high) / 2
        candidate = _build_plan(offers, economics, quantities_for_target(midpoint), now=now)
        if _plan_is_feasible(
            candidate, total_allocation=total_allocation, provider_capital_limits=provider_capital_limits
        ):
            low = midpoint
            if best is None or candidate.objective > best.objective:
                best = candidate
        else:
            high = midpoint

    base = quantities_for_target(low)
    # Rounding and fee ceilings can make a nearby discrete vector better than
    # the continuous target. Search a deterministic bounded neighborhood.
    neighborhoods: list[tuple[Decimal, ...]] = []
    for offer, minimum, maximum, quantity in zip(offers, minimums, maximums, base, strict=True):
        nearby_values = {
            min(maximum, max(minimum, quantity + offer.quantity_increment * offset))
            for offset in range(-4, 5)
        }
        neighborhoods.append(tuple(sorted(nearby_values)))
    neighborhood_states = 1
    for neighborhood in neighborhoods:
        neighborhood_states *= len(neighborhood)
    if neighborhood_states <= MAX_EXHAUSTIVE_STATES:
        for quantities in product(*neighborhoods):
            consider(quantities)
    else:
        consider(base)
        for index, neighborhood in enumerate(neighborhoods):
            for value in neighborhood:
                candidate_quantities = list(base)
                candidate_quantities[index] = value
                consider(candidate_quantities)

    if best is None:
        best = _build_plan(offers, economics, [ZERO] * len(offers), now=now)
    return best, "bounded_target_maximin", maximums


def _fixed_allocation_plan_at_price(
    offers: Sequence[Offer],
    quantities: Sequence[Decimal],
    changed_index: int,
    changed_price: Decimal,
    *,
    now: datetime,
) -> _Plan:
    economics = [
        _economics_at_price(offer, changed_price) if index == changed_index else _economics(offer)
        for index, offer in enumerate(offers)
    ]
    return _build_plan(offers, economics, quantities, now=now)


def _break_even_points(
    offers: Sequence[Offer],
    quantities: Sequence[Decimal],
    *,
    now: datetime,
) -> tuple[BreakEvenPoint, ...]:
    points: list[BreakEvenPoint] = []
    current_plan = _build_plan(offers, [_economics(offer) for offer in offers], quantities, now=now)
    current_worst = min(current_plan.profits) if current_plan.profits else ZERO
    for index, offer in enumerate(offers):
        if quantities[index] <= 0:
            points.append(
                BreakEvenPoint(
                    offer_id=offer.offer_id,
                    price_kind=offer.price_kind,
                    current_price=offer.price,
                    fixed_allocation_break_even_price=None,
                    adverse_movement=None,
                    adverse_movement_bps=None,
                    limiting_outcome_id=None,
                )
            )
            continue

        if current_worst <= 0:
            break_even = offer.price
        elif offer.price_kind is PriceKind.DECIMAL_ODDS:
            bad = ONE
            good = offer.price
            bad_plan = _fixed_allocation_plan_at_price(offers, quantities, index, bad, now=now)
            if min(bad_plan.profits) > 0:
                break_even = None
            else:
                for _ in range(96):
                    midpoint = (bad + good) / 2
                    plan = _fixed_allocation_plan_at_price(offers, quantities, index, midpoint, now=now)
                    if min(plan.profits) > 0:
                        good = midpoint
                    else:
                        bad = midpoint
                break_even = good
        else:
            good = offer.price
            bad = ONE
            bad_plan = _fixed_allocation_plan_at_price(offers, quantities, index, bad, now=now)
            if min(bad_plan.profits) > 0:
                break_even = None
            else:
                for _ in range(96):
                    midpoint = (good + bad) / 2
                    plan = _fixed_allocation_plan_at_price(offers, quantities, index, midpoint, now=now)
                    if min(plan.profits) > 0:
                        good = midpoint
                    else:
                        bad = midpoint
                break_even = good

        movement: Decimal | None = None
        movement_bps: Decimal | None = None
        limiting_outcome: str | None = None
        if break_even is not None:
            movement = (
                offer.price - break_even
                if offer.price_kind is PriceKind.DECIMAL_ODDS
                else break_even - offer.price
            )
            movement = max(ZERO, movement)
            movement_bps = movement / offer.price * BPS if offer.price else None
            plan = _fixed_allocation_plan_at_price(offers, quantities, index, break_even, now=now)
            limiting_index = min(range(len(plan.profits)), key=lambda outcome_index: plan.profits[outcome_index])
            limiting_outcome = offers[limiting_index].outcome_id
        points.append(
            BreakEvenPoint(
                offer_id=offer.offer_id,
                price_kind=offer.price_kind,
                current_price=offer.price,
                fixed_allocation_break_even_price=break_even,
                adverse_movement=movement,
                adverse_movement_bps=movement_bps,
                limiting_outcome_id=limiting_outcome,
            )
        )
    return tuple(points)


def evaluate_bundle(legs: list[ScenarioLeg], outcome_ids: list[str]) -> BundleEvaluation:
    if not outcome_ids or len(set(outcome_ids)) != len(outcome_ids):
        raise ValueError("outcome_ids must be a unique, non-empty complete scenario set")
    total_cost = sum((leg.filled_quantity * leg.unit_cost for leg in legs), ZERO)
    total_fees = sum((leg.fees for leg in legs), ZERO)
    results: list[OutcomeResult] = []
    for outcome in outcome_ids:
        payout = sum(
            (leg.filled_quantity * leg.payout_per_unit.get(outcome, ZERO) for leg in legs),
            ZERO,
        )
        results.append(OutcomeResult(outcome_id=outcome, gross_payout=payout, net_profit=payout - total_cost - total_fees))
    values = [result.net_profit for result in results]
    requested_cost = sum((leg.requested_quantity * leg.unit_cost for leg in legs), ZERO)
    missing_cost = max(ZERO, requested_cost - total_cost)
    spread = max(values) - min(values)
    return BundleEvaluation(
        total_cost=total_cost,
        total_fees=total_fees,
        outcome_results=tuple(results),
        worst_case=min(values),
        best_case=max(values),
        unmatched_exposure=max(missing_cost, spread),
    )


def calculate_arbitrage(
    offers: list[Offer],
    total_allocation: Decimal,
    *,
    settlement_status: MatchStatus = MatchStatus.VERIFIED,
    now: datetime | None = None,
    maximum_quote_age_seconds: int = 30,
    maximum_quote_timestamp_skew_seconds: int = 5,
    allow_fee_assumptions: bool = False,
    require_observed_depth: bool = True,
    expected_outcome_ids: Collection[str] | None = None,
    provider_capital_limits: Mapping[str, Decimal] | None = None,
    expected_lockup_seconds: Decimal | None = None,
) -> ArbitrageResult:
    """Optimize and evaluate a complete mutually-exclusive outcome set.

    ``total_allocation`` is a hard cap on cash cost *including modeled fees*.
    Prices are executable asks/odds supplied by the caller. Last trades and
    midpoints must never be promoted to ``Offer`` by ingestion code. The solver
    maximizes the minimum outcome P&L subject to quantity increments, observed
    depth, total capital, and optional per-provider capital limits.
    """
    if total_allocation <= 0:
        raise ValueError("total_allocation must be positive")
    if maximum_quote_timestamp_skew_seconds <= 0:
        raise ValueError("maximum_quote_timestamp_skew_seconds must be positive")
    if expected_lockup_seconds is not None and expected_lockup_seconds < 0:
        raise ValueError("expected_lockup_seconds cannot be negative")
    if len(offers) < 2 or len({offer.outcome_id for offer in offers}) != len(offers):
        raise ValueError("offers must cover unique outcomes in a complete set")
    currencies = {offer.currency for offer in offers}
    if len(currencies) != 1:
        raise ValueError("offers must share one explicit currency")
    currency = next(iter(currencies))
    capital_limits = dict(provider_capital_limits or {})
    if any(limit < 0 for limit in capital_limits.values()):
        raise ValueError("provider capital limits cannot be negative")
    now = (now or datetime.now(UTC)).astimezone(UTC)
    rejection: list[str] = []
    assumptions: list[str] = []
    supplied_outcomes = {offer.outcome_id for offer in offers}
    expected_outcomes: tuple[str, ...] | None = None
    coverage_verified = False
    if expected_outcome_ids is None:
        rejection.append("outcome_completeness_unverified")
    else:
        expected_outcomes = tuple(expected_outcome_ids)
        if len(expected_outcomes) < 2 or any(not outcome for outcome in expected_outcomes):
            raise ValueError("expected_outcome_ids must contain at least two non-empty IDs")
        if len(set(expected_outcomes)) != len(expected_outcomes):
            raise ValueError("expected_outcome_ids must be unique")
        expected_set = set(expected_outcomes)
        if expected_set - supplied_outcomes:
            rejection.append("incomplete_outcome_coverage")
        if supplied_outcomes - expected_set:
            rejection.append("unexpected_outcome_coverage")
        coverage_verified = supplied_outcomes == expected_set
    if settlement_status is not MatchStatus.VERIFIED:
        rejection.append("settlement_not_verified")
    if any(offer.data_origin == "demo" for offer in offers):
        rejection.append("demo_data_not_executable")
    if require_observed_depth and any(offer.maximum_quantity is None for offer in offers):
        rejection.append("orderbook_depth_unknown")

    known_quote_times = [offer.quote_timestamp.astimezone(UTC) for offer in offers if offer.quote_timestamp is not None]
    quote_skew: Decimal | None = None
    if len(known_quote_times) >= 2:
        quote_skew = Decimal(str((max(known_quote_times) - min(known_quote_times)).total_seconds()))
        if quote_skew > maximum_quote_timestamp_skew_seconds:
            rejection.append("quote_timestamp_skew_exceeded")

    economics = [_economics(offer) for offer in offers]
    inverse_sum = sum((cost / payout for cost, payout in economics), ZERO)
    if inverse_sum <= 0:
        raise ValueError("invalid payout economics")

    plan, optimization_method, _maximum_quantities = _optimize_discrete_allocation(
        offers,
        economics,
        total_allocation,
        now=now,
        provider_capital_limits=capital_limits,
    )
    if optimization_method == "minimum_coverage_unfunded":
        rejection.append("minimum_quantity_unfunded")
    if plan.deployed > total_allocation:
        raise ArithmeticError("optimizer exceeded total_allocation")

    legs: list[AllocationLeg] = []
    total_cost = ZERO
    total_fees = ZERO
    allocation_limit_reasons: list[str] = []
    for index, (offer, filled, (unit_cost, payout_per_unit)) in enumerate(zip(
        offers, plan.quantities, economics, strict=True
    )):
        requested = filled
        cash_cost = plan.cash_costs[index]
        fee = plan.fees[index]
        leg_limit_reasons: list[str] = []
        if offer.maximum_quantity is not None and filled >= _down(
            offer.maximum_quantity, offer.quantity_increment
        ):
            leg_limit_reasons.append("observed_depth")
            allocation_limit_reasons.append(f"observed_depth:{offer.offer_id}")
        provider_limit = capital_limits.get(offer.provider)
        if provider_limit is not None:
            remaining = provider_limit - plan.provider_spend.get(offer.provider, ZERO)
            if remaining < offer.quantity_increment * unit_cost:
                leg_limit_reasons.append("provider_capital")
                allocation_limit_reasons.append(f"provider_capital:{offer.provider}")
        quote_age: Decimal | None = None
        stale = True
        if offer.quote_timestamp is not None:
            quote_age = Decimal(str((now - offer.quote_timestamp.astimezone(UTC)).total_seconds()))
            if quote_age < 0:
                stale = True
                rejection.append("quote_timestamp_in_future")
            else:
                stale = quote_age > maximum_quote_age_seconds
        if stale:
            rejection.append("stale_quote" if quote_age is not None else "quote_age_unknown")

        schedule = offer.fee_schedule
        if schedule is None or not schedule.known:
            if allow_fee_assumptions:
                assumptions.append(f"{offer.provider} fee assumed zero by explicit request")
            else:
                rejection.append("fee_schedule_unknown")
        else:
            try:
                # Re-evaluate for an explicit effective-date rejection. The
                # optimizer already used this same fee when it was effective.
                fee = fee_for(schedule, cash_cost, filled, on=now) if filled > 0 else ZERO
            except ValueError as exc:
                fee = ZERO
                rejection.append(str(exc))

        total_cost += cash_cost
        total_fees += fee
        legs.append(
            AllocationLeg(
                offer_id=offer.offer_id,
                provider=offer.provider,
                outcome_id=offer.outcome_id,
                price_kind=offer.price_kind,
                requested_quantity=requested,
                filled_quantity=filled,
                unit_cost=unit_cost,
                cash_cost=cash_cost,
                gross_payout_if_wins=filled * payout_per_unit,
                fees=fee,
                quote_age_seconds=quote_age,
                stale=stale,
                limit_reasons=tuple(leg_limit_reasons),
            )
        )

    unallocated = max(ZERO, total_allocation - (total_cost + total_fees))
    if unallocated > 0 and not allocation_limit_reasons:
        allocation_limit_reasons.append("discrete_increment_or_maximin_optimum")

    scenario_results: list[OutcomeResult] = []
    total_deployed = total_cost + total_fees
    scenario_ids = expected_outcomes or tuple(leg.outcome_id for leg in legs)
    for outcome_id in scenario_ids:
        payout = sum(
            (leg.gross_payout_if_wins for leg in legs if leg.outcome_id == outcome_id),
            ZERO,
        )
        scenario_results.append(
            OutcomeResult(
                outcome_id=outcome_id,
                gross_payout=payout,
                net_profit=payout - total_deployed,
            )
        )
    profits = [item.net_profit for item in scenario_results]
    payouts = [item.gross_payout for item in scenario_results]
    worst = min(profits)
    best = max(profits)
    equalized = min(payouts)
    gross_profit = equalized - total_cost
    unmatched = max(ZERO, best - worst)
    if unmatched > Decimal("0.01"):
        rejection.append("unmatched_exposure")
    gross_roi = gross_profit / total_cost if total_cost else ZERO
    net_roi = worst / total_deployed if total_deployed else ZERO
    unique_rejections = tuple(dict.fromkeys(rejection))
    if total_deployed > total_allocation:
        raise ArithmeticError("modeled deployment exceeds requested allocation")

    maximum_available_allocation: Decimal | None = None
    if all(offer.maximum_quantity is not None for offer in offers):
        depth_maximums = tuple(
            _down(offer.maximum_quantity, offer.quantity_increment)  # type: ignore[arg-type]
            for offer in offers
        )
        capacity_target = min(
            maximum * payout
            for maximum, (_, payout) in zip(depth_maximums, economics, strict=True)
        )

        def depth_plan(target: Decimal) -> _Plan:
            quantities = tuple(
                min(maximum, _up(target / payout, offer.quantity_increment))
                for offer, maximum, (_, payout) in zip(offers, depth_maximums, economics, strict=True)
            )
            return _build_plan(offers, economics, quantities, now=now)

        capacity_plan = depth_plan(capacity_target)
        if any(
            capacity_plan.provider_spend.get(provider, ZERO) > limit
            for provider, limit in capital_limits.items()
        ):
            low = ZERO
            high = capacity_target
            for _ in range(96):
                midpoint = (low + high) / 2
                trial = depth_plan(midpoint)
                if all(
                    trial.provider_spend.get(provider, ZERO) <= limit
                    for provider, limit in capital_limits.items()
                ):
                    low = midpoint
                    capacity_plan = trial
                else:
                    high = midpoint
        maximum_available_allocation = capacity_plan.deployed

    break_even_points = _break_even_points(offers, plan.quantities, now=now)
    return ArbitrageResult(
        requested_allocation=total_allocation,
        deployed_capital=total_deployed,
        unallocated_capital=unallocated,
        total_fees=total_fees,
        legs=tuple(legs),
        outcomes=tuple(scenario_results),
        equalized_gross_payout=equalized,
        gross_profit=gross_profit,
        net_profit_worst_case=worst,
        net_profit_best_case=best,
        gross_roi=gross_roi,
        net_roi=net_roi,
        maximum_available_allocation=maximum_available_allocation,
        unmatched_exposure=unmatched,
        expected_outcome_ids=expected_outcomes,
        outcome_coverage_verified=coverage_verified,
        is_theoretical_arbitrage=gross_profit > 0 and coverage_verified,
        execution_eligible=worst > 0 and coverage_verified and not unique_rejections,
        rejection_codes=unique_rejections,
        assumptions=tuple(assumptions),
        calculation_version=FINANCIAL_CONTRACT_VERSION,
        currency=currency,
        capital_lockup=total_deployed,
        expected_lockup_seconds=expected_lockup_seconds,
        quote_timestamp_skew_seconds=quote_skew,
        maximum_quote_timestamp_skew_seconds=Decimal(maximum_quote_timestamp_skew_seconds),
        allocation_limit_reasons=tuple(dict.fromkeys(allocation_limit_reasons)),
        optimization_method=optimization_method,
        break_even_points=break_even_points,
    )

