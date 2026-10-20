"""Pure canonical-match to opportunity evaluation pipeline.

The ingestion worker persists provider-neutral snapshots.  This module is the
deterministic calculation boundary that turns *explicit* canonical market,
settlement, quote, fee, depth, and payout evidence into research candidates.
It performs no I/O and never substitutes demo or inferred values for missing
evidence, which keeps database integration and fixture tests straightforward.
"""

from __future__ import annotations

import hashlib
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from decimal import Decimal
from itertools import product
from typing import Literal

from .arbitrage import calculate_arbitrage, evaluate_bundle, fee_for
from .domain import (
    ArbitrageResult,
    CanonicalMarket,
    MatchDecision,
    MatchStatus,
    Offer,
    OutcomeResult,
    PriceKind,
    RankInputs,
    RankResult,
    ScenarioLeg,
)
from .matching import material_fingerprints_still_match, verify_settlement_equivalence
from .normalization import identity_key, plausible_event_candidate
from .ranking import rank_opportunity


SupportedAction = Literal["back", "buy_yes", "buy_no"]
ExecutionBasis = Literal["orderbook_ask", "direct_sportsbook_offer", "indicative"]
OpportunityClassification = Literal["theoretical", "executable_quote_supported"]
DecisionSource = Literal["automatic", "trusted_persisted_review"]
ZERO = Decimal("0")
ONE = Decimal("1")
BPS = Decimal("10000")
CURRENT_DATA_ORIGINS = frozenset({"live_poll", "live_stream", "realtime_stream"})


@dataclass(frozen=True, slots=True)
class ExecutableOffer:
    """Quote evidence plus its complete settlement cash-flow vector.

    ``payout_per_unit`` is the gross cash returned for one filled quantity in
    every modeled event scenario.  It is how push, void, and refund behavior is
    represented. Missing scenarios are rejected instead of being treated as
    zero. Modeled fees are conservatively retained in every scenario. An
    ``indicative`` execution basis remains usable for transparent theoretical
    analysis but can never support an executable classification.
    """

    offer: Offer
    source_market_id: str
    action: SupportedAction
    execution_basis: ExecutionBasis
    provider_timestamp_known: bool
    payout_per_unit: Mapping[str, Decimal]

    def __post_init__(self) -> None:
        if self.action not in {"back", "buy_yes", "buy_no"}:
            raise ValueError("unsupported offer action")
        if self.execution_basis not in {
            "orderbook_ask",
            "direct_sportsbook_offer",
            "indicative",
        }:
            raise ValueError("unsupported execution basis")
        if not isinstance(self.provider_timestamp_known, bool):
            raise ValueError("provider_timestamp_known must be boolean")
        if not self.source_market_id.strip():
            raise ValueError("source_market_id is required")
        if not self.payout_per_unit:
            raise ValueError("payout_per_unit must include explicit scenario cash flows")
        if any(not scenario.strip() for scenario in self.payout_per_unit):
            raise ValueError("payout scenarios cannot be blank")
        if any(
            not isinstance(value, Decimal) or not value.is_finite() or value < 0
            for value in self.payout_per_unit.values()
        ):
            raise ValueError("payout cash flows must be finite non-negative Decimals")
        if self.provider_timestamp_known and self.offer.quote_timestamp is None:
            raise ValueError("known provider timestamp requires quote_timestamp")
        if self.action == "back" and self.offer.price_kind is not PriceKind.DECIMAL_ODDS:
            raise ValueError("back actions require decimal-odds prices")
        if self.action != "back" and self.offer.price_kind is not PriceKind.CONTRACT_ASK:
            raise ValueError("contract purchase actions require contract-ask prices")
        if (
            self.execution_basis == "orderbook_ask"
            and self.offer.price_kind is not PriceKind.CONTRACT_ASK
        ):
            raise ValueError("orderbook_ask evidence requires a contract-ask price")
        if (
            self.execution_basis == "direct_sportsbook_offer"
            and self.offer.price_kind is not PriceKind.DECIMAL_ODDS
        ):
            raise ValueError("direct sportsbook evidence requires decimal odds")


@dataclass(frozen=True, slots=True)
class MarketSnapshot:
    """Latest quote evidence for one canonicalized provider market."""

    market: CanonicalMarket
    offers: tuple[ExecutableOffer, ...]
    scenario_ids: tuple[str, ...]
    scenario_completeness_verified: bool
    execution_blockers: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if len(self.market.outcome_ids) < 2:
            raise ValueError("market must contain a complete multi-outcome set")
        if not isinstance(self.scenario_completeness_verified, bool):
            raise ValueError("scenario_completeness_verified must be boolean")
        normalized_outcomes = [identity_key(value) for value in self.market.outcome_ids]
        if any(not value for value in normalized_outcomes) or len(set(normalized_outcomes)) != len(
            normalized_outcomes
        ):
            raise ValueError("market outcome IDs must be non-empty and distinct after normalization")
        normalized_scenarios = [identity_key(value) for value in self.scenario_ids]
        if len(normalized_scenarios) < 2 or any(not value for value in normalized_scenarios):
            raise ValueError("scenario_ids must contain at least two non-empty scenarios")
        if len(set(normalized_scenarios)) != len(normalized_scenarios):
            raise ValueError("scenario_ids must be distinct after normalization")
        if any(not blocker.strip() for blocker in self.execution_blockers):
            raise ValueError("execution blockers cannot be blank")
        if len(set(self.execution_blockers)) != len(self.execution_blockers):
            raise ValueError("execution blockers must be unique")
        known_outcomes = set(normalized_outcomes)
        known_scenarios = set(normalized_scenarios)
        if not known_outcomes <= known_scenarios:
            raise ValueError("every canonical outcome must be represented in scenario_ids")
        for executable in self.offers:
            if executable.source_market_id != self.market.market_id:
                raise ValueError("offer source_market_id does not match the snapshot market")
            if executable.offer.provider != self.market.provider:
                raise ValueError("offer provider does not match the snapshot market provider")
            if identity_key(executable.offer.outcome_id) not in known_outcomes:
                raise ValueError("offer outcome is absent from the canonical outcome set")
            payout_scenarios = {
                identity_key(value) for value in executable.payout_per_unit
            }
            if len(payout_scenarios) != len(executable.payout_per_unit):
                raise ValueError("offer payout scenarios must be distinct after normalization")
            if payout_scenarios != known_scenarios:
                raise ValueError("offer payout vector must explicitly cover every scenario")
            payouts = {
                identity_key(scenario): value
                for scenario, value in executable.payout_per_unit.items()
            }
            winner = identity_key(executable.offer.outcome_id)
            winning_payout = (
                executable.offer.price
                if executable.offer.price_kind is PriceKind.DECIMAL_ODDS else ONE
            )
            if any(
                payouts[outcome] != (winning_payout if outcome == winner else ZERO)
                for outcome in known_outcomes
            ):
                raise ValueError("canonical outcome cash flows disagree with offer economics")
            for scenario in ("push", "void", "cancellation", "postponement", "retirement", "abandonment"):
                treatment = getattr(self.market.rules, f"{scenario}_treatment")
                if treatment is None or scenario not in payouts:
                    continue
                treatment = identity_key(treatment)
                refund = (
                    ONE if executable.offer.price_kind is PriceKind.DECIMAL_ODDS
                    else executable.offer.price
                )
                if (
                    (treatment == "refund" and payouts[scenario] != refund)
                    or (treatment == "loss" and payouts[scenario] != ZERO)
                ):
                    raise ValueError(f"{scenario} cash flow disagrees with settlement treatment")


@dataclass(frozen=True, slots=True)
class PipelineConfig:
    total_allocation: Decimal
    maximum_quote_age_seconds: int = 30
    maximum_quote_timestamp_skew_seconds: int = 5
    minimum_liquidity: Decimal = Decimal("1")
    maximum_offers_per_outcome: int = 4
    maximum_offer_combinations: int = 256
    # This is intentionally optional.  Ranking cannot claim measured fill
    # reliability until an operator supplies an observed/validated estimate.
    partial_fill_probability: Decimal | None = None
    quote_update_hz: Decimal | None = None
    historical_stability: Decimal | None = None
    expected_lockup_seconds: Decimal | None = None
    # An explicit adverse-price assumption, distinct from a measured estimate.
    slippage_bps: Decimal = ZERO

    def __post_init__(self) -> None:
        decimal_values = (
            ("total_allocation", self.total_allocation),
            ("minimum_liquidity", self.minimum_liquidity),
            ("partial_fill_probability", self.partial_fill_probability),
            ("quote_update_hz", self.quote_update_hz),
            ("historical_stability", self.historical_stability),
            ("expected_lockup_seconds", self.expected_lockup_seconds),
            ("slippage_bps", self.slippage_bps),
        )
        if any(
            value is not None
            and (not isinstance(value, Decimal) or not value.is_finite())
            for _, value in decimal_values
        ):
            raise ValueError("pipeline Decimal inputs must be finite Decimal values")
        integer_values = (
            self.maximum_quote_age_seconds,
            self.maximum_quote_timestamp_skew_seconds,
            self.maximum_offers_per_outcome,
            self.maximum_offer_combinations,
        )
        if any(not isinstance(value, int) or isinstance(value, bool) for value in integer_values):
            raise ValueError("pipeline count and duration inputs must be integers")
        if self.total_allocation <= 0:
            raise ValueError("total_allocation must be positive")
        if self.maximum_quote_age_seconds <= 0:
            raise ValueError("maximum_quote_age_seconds must be positive")
        if self.maximum_quote_timestamp_skew_seconds <= 0:
            raise ValueError("maximum_quote_timestamp_skew_seconds must be positive")
        if self.minimum_liquidity < 0:
            raise ValueError("minimum_liquidity cannot be negative")
        if self.slippage_bps < 0:
            raise ValueError("slippage_bps cannot be negative")
        if self.maximum_offers_per_outcome < 1:
            raise ValueError("maximum_offers_per_outcome must be positive")
        if self.maximum_offer_combinations < 1:
            raise ValueError("maximum_offer_combinations must be positive")
        for name, value in (
            ("partial_fill_probability", self.partial_fill_probability),
            ("historical_stability", self.historical_stability),
        ):
            if value is not None and not ZERO <= value <= ONE:
                raise ValueError(f"{name} must be between zero and one")
        if self.quote_update_hz is not None and self.quote_update_hz < 0:
            raise ValueError("quote_update_hz cannot be negative")
        if self.expected_lockup_seconds is not None and self.expected_lockup_seconds < 0:
            raise ValueError("expected_lockup_seconds cannot be negative")


@dataclass(frozen=True, slots=True)
class CandidateAudit:
    left_market_id: str
    right_market_id: str
    decision: MatchDecision
    decision_source: DecisionSource = "automatic"
    override_rejection: str | None = None
    gate_failures: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class GeneratedOpportunity:
    opportunity_key: str
    left_market_id: str
    right_market_id: str
    decision: MatchDecision
    selected_offers: tuple[ExecutableOffer, ...]
    calculation: ArbitrageResult
    full_scenario_outcomes: tuple[OutcomeResult, ...]
    full_worst_case: Decimal
    full_best_case: Decimal
    rank: RankResult | None
    classification: OpportunityClassification
    execution_supported: bool
    rejection_codes: tuple[str, ...]
    ranking_rejection_codes: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class PipelineRunResult:
    candidate_audits: tuple[CandidateAudit, ...]
    opportunities: tuple[GeneratedOpportunity, ...]
    evaluated_combinations: int
    rejection_counts: Mapping[str, int] = field(default_factory=dict)

    @property
    def strict_opportunities(self) -> tuple[GeneratedOpportunity, ...]:
        return tuple(item for item in self.opportunities if item.execution_supported)

    @property
    def ranked_opportunities(self) -> tuple[GeneratedOpportunity, ...]:
        return tuple(
            item
            for item in self.strict_opportunities
            if item.rank is not None and item.rank.eligible
        )


def _normalized_lookup(values: Sequence[str]) -> dict[str, str]:
    return {identity_key(value): value for value in values}


def _price_quality(
    executable: ExecutableOffer,
    *,
    config: PipelineConfig,
    now: datetime,
) -> tuple[int, Decimal, str, str]:
    offer = executable.offer
    evidence_failures = 0
    if executable.execution_basis == "indicative":
        evidence_failures += 1
    if offer.data_origin.casefold() not in CURRENT_DATA_ORIGINS:
        evidence_failures += 1
    if not executable.provider_timestamp_known:
        evidence_failures += 1
    if not offer.source_url:
        evidence_failures += 1
    if offer.maximum_quantity is None:
        evidence_failures += 1
    if offer.quote_timestamp is None:
        evidence_failures += 1
    else:
        quote_age = Decimal(str((now - offer.quote_timestamp.astimezone(UTC)).total_seconds()))
        if quote_age < 0 or quote_age > config.maximum_quote_age_seconds:
            evidence_failures += 1

    schedule = offer.fee_schedule
    schedule_valid = (
        schedule is not None
        and schedule.known
        and bool(schedule.source_url)
        and schedule.effective_from <= now.date()
        and (schedule.effective_to is None or now.date() <= schedule.effective_to)
    )
    if not schedule_valid:
        evidence_failures += 1

    slip = ONE + offer.slippage_bps / BPS
    if offer.price_kind is PriceKind.DECIMAL_ODDS:
        unit_cost = ONE
        payout = ONE + ((offer.price - ONE) / slip)
    else:
        unit_cost = offer.price * slip
        payout = ONE
    reference_quantity = max(offer.minimum_quantity, offer.quantity_increment)
    if unit_cost > 0:
        reference_quantity = max(
            reference_quantity,
            config.total_allocation / unit_cost,
        )
    if offer.maximum_quantity is not None:
        reference_quantity = min(reference_quantity, offer.maximum_quantity)
    approximate_fee_per_unit = ZERO
    if schedule is not None and schedule_valid and reference_quantity > 0:
        approximate_fee_per_unit = (
            fee_for(
                schedule,
                reference_quantity * unit_cost,
                reference_quantity,
                on=now,
            )
            / reference_quantity
        )
    implied_cost = (unit_cost + approximate_fee_per_unit) / payout
    return evidence_failures, implied_cost, executable.source_market_id, offer.offer_id


def _canonicalize_executable(
    executable: ExecutableOffer,
    *,
    outcomes: Mapping[str, str],
    scenarios: Mapping[str, str],
) -> ExecutableOffer:
    canonical_outcome = outcomes[identity_key(executable.offer.outcome_id)]
    canonical_payouts = {
        scenarios[identity_key(scenario)]: value
        for scenario, value in executable.payout_per_unit.items()
    }
    return ExecutableOffer(
        offer=executable.offer.model_copy(update={"outcome_id": canonical_outcome}),
        source_market_id=executable.source_market_id,
        action=executable.action,
        execution_basis=executable.execution_basis,
        provider_timestamp_known=executable.provider_timestamp_known,
        payout_per_unit=canonical_payouts,
    )


def _pair_gate_failures(left: MarketSnapshot, right: MarketSnapshot) -> tuple[str, ...]:
    failures: list[str] = []
    left_scenarios = {identity_key(value) for value in left.scenario_ids}
    right_scenarios = {identity_key(value) for value in right.scenario_ids}
    if left_scenarios != right_scenarios:
        failures.append("settlement_scenario_sets_differ")
    for snapshot in (left,) if left is right else (left, right):
        scenarios = {identity_key(value) for value in snapshot.scenario_ids}
        for scenario in ("push", "void", "cancellation", "postponement", "retirement", "abandonment"):
            treatment = getattr(snapshot.market.rules, f"{scenario}_treatment")
            if (
                treatment is not None
                and identity_key(treatment) in {"refund", "loss", "half_win", "half_loss"}
                and scenario not in scenarios
            ):
                failures.append(f"required_settlement_scenario_missing:{scenario}")
    if left is right:
        if not left.offers:
            failures.append("executable_quotes_missing")
    else:
        if not left.offers:
            failures.append("left_executable_quotes_missing")
        if not right.offers:
            failures.append("right_executable_quotes_missing")
    return tuple(dict.fromkeys(failures))


def _opportunity_key(left: MarketSnapshot, right: MarketSnapshot) -> str:
    identity = "\x00".join(sorted((left.market.market_id, right.market.market_id)))
    return hashlib.sha256(identity.encode()).hexdigest()


def _scenario_evaluation(
    selected: Sequence[ExecutableOffer],
    calculation: ArbitrageResult,
    scenario_ids: Sequence[str],
) -> tuple[OutcomeResult, ...]:
    by_offer = {leg.offer_id: leg for leg in calculation.legs}
    scenario_lookup = _normalized_lookup(scenario_ids)
    scenario_legs: list[ScenarioLeg] = []
    for executable in selected:
        allocation = by_offer[executable.offer.offer_id]
        payouts = dict(executable.payout_per_unit)
        # The core engine applies adverse slippage to the winning economics.
        # Preserve that same effective payout in the complete scenario matrix.
        if allocation.filled_quantity > 0:
            winning_scenario = scenario_lookup[identity_key(allocation.outcome_id)]
            payouts[winning_scenario] = (
                allocation.gross_payout_if_wins / allocation.filled_quantity
            )
        scenario_legs.append(
            ScenarioLeg(
                leg_id=allocation.offer_id,
                provider=allocation.provider,
                requested_quantity=allocation.requested_quantity,
                filled_quantity=allocation.filled_quantity,
                unit_cost=allocation.unit_cost,
                fees=allocation.fees,
                payout_per_unit=payouts,
            )
        )
    return evaluate_bundle(scenario_legs, list(scenario_ids)).outcome_results


def _evaluate_combination(
    left: MarketSnapshot,
    right: MarketSnapshot,
    decision: MatchDecision,
    selected: tuple[ExecutableOffer, ...],
    *,
    config: PipelineConfig,
    now: datetime,
) -> GeneratedOpportunity:
    canonical_outcomes = tuple(left.market.outcome_ids)
    scenario_lookup = _normalized_lookup(left.scenario_ids)
    scenario_ids = tuple(scenario_lookup.values())
    calculation = calculate_arbitrage(
        [item.offer for item in selected],
        config.total_allocation,
        settlement_status=decision.status,
        now=now,
        maximum_quote_age_seconds=config.maximum_quote_age_seconds,
        maximum_quote_timestamp_skew_seconds=config.maximum_quote_timestamp_skew_seconds,
        allow_fee_assumptions=False,
        require_observed_depth=True,
        expected_outcome_ids=canonical_outcomes,
        expected_lockup_seconds=config.expected_lockup_seconds,
    )
    if config.slippage_bps:
        calculation = calculation.model_copy(
            update={
                "assumptions": calculation.assumptions + (
                    f"Configured adverse slippage floor: {config.slippage_bps} bps; "
                    "modeled assumption, not a measured execution estimate.",
                )
            }
        )
    full_outcomes = _scenario_evaluation(selected, calculation, scenario_ids)
    full_profits = tuple(item.net_profit for item in full_outcomes)
    full_worst = min(full_profits)
    full_best = max(full_profits)
    rejections = list(calculation.rejection_codes)
    if full_worst <= 0:
        rejections.append("non_positive_full_scenario_return")
    if any(item.execution_basis == "indicative" for item in selected):
        rejections.append("indicative_price_not_executable")
    if any(item.offer.data_origin.casefold() not in CURRENT_DATA_ORIGINS for item in selected):
        rejections.append("non_current_quote_origin")
    if any(not item.provider_timestamp_known for item in selected):
        rejections.append("provider_quote_timestamp_unknown")
    if any(not item.offer.source_url for item in selected):
        rejections.append("quote_source_unknown")
    if any(
        item.offer.fee_schedule is not None
        and item.offer.fee_schedule.known
        and not item.offer.fee_schedule.source_url
        for item in selected
    ):
        rejections.append("fee_schedule_source_unknown")
    selected_market_ids = {item.source_market_id for item in selected}
    for snapshot in (left,) if left is right else (left, right):
        if snapshot.market.market_id in selected_market_ids:
            if not snapshot.scenario_completeness_verified:
                rejections.append("settlement_scenario_completeness_unknown")
            rejections.extend(snapshot.execution_blockers)
    maximum_liquidity = calculation.maximum_available_allocation
    if maximum_liquidity is None or maximum_liquidity < config.minimum_liquidity:
        rejections.append("insufficient_depth")

    rank: RankResult | None = None
    ranking_rejections: list[str] = []
    if config.partial_fill_probability is None:
        ranking_rejections.append("partial_fill_probability_unknown")
    else:
        known_ages = [leg.quote_age_seconds for leg in calculation.legs if leg.quote_age_seconds is not None]
        quote_age = max(known_ages) if len(known_ages) == len(calculation.legs) else None
        liquidity = calculation.maximum_available_allocation or ZERO
        rank = rank_opportunity(
            RankInputs(
                conservative_profit=full_worst,
                net_roi=(full_worst / calculation.deployed_capital if calculation.deployed_capital else ZERO),
                executable_liquidity=liquidity,
                quote_age_seconds=quote_age,
                quote_update_hz=config.quote_update_hz,
                settlement_status=decision.status,
                expected_slippage_bps=max(
                    (item.offer.slippage_bps for item in selected), default=ZERO
                ),
                partial_fill_probability=config.partial_fill_probability,
                stability=config.historical_stability,
                fee_schedule_known=all(
                    item.offer.fee_schedule is not None and item.offer.fee_schedule.known
                    for item in selected
                ),
                minimum_liquidity=config.minimum_liquidity,
                maximum_quote_age_seconds=Decimal(config.maximum_quote_age_seconds),
                quote_timestamp_skew_seconds=calculation.quote_timestamp_skew_seconds,
                maximum_quote_timestamp_skew_seconds=Decimal(
                    config.maximum_quote_timestamp_skew_seconds
                ),
            )
        )
        ranking_rejections.extend(rank.hard_rejections)

    unique_rejections = tuple(dict.fromkeys(rejections))
    unique_ranking_rejections = tuple(dict.fromkeys(ranking_rejections))
    execution_supported = (
        calculation.execution_eligible
        and full_worst > 0
        and not unique_rejections
    )
    return GeneratedOpportunity(
        opportunity_key=_opportunity_key(left, right),
        left_market_id=left.market.market_id,
        right_market_id=right.market.market_id,
        decision=decision,
        selected_offers=selected,
        calculation=calculation,
        full_scenario_outcomes=full_outcomes,
        full_worst_case=full_worst,
        full_best_case=full_best,
        rank=rank,
        classification=("executable_quote_supported" if execution_supported else "theoretical"),
        execution_supported=execution_supported,
        rejection_codes=unique_rejections,
        ranking_rejection_codes=unique_ranking_rejections,
    )


def _evaluate_pair(
    left: MarketSnapshot,
    right: MarketSnapshot,
    decision: MatchDecision,
    *,
    config: PipelineConfig,
    now: datetime,
) -> tuple[GeneratedOpportunity | None, int, Counter[str]]:
    outcome_lookup = _normalized_lookup(left.market.outcome_ids)
    scenario_lookup = _normalized_lookup(left.scenario_ids)
    grouped: dict[str, list[ExecutableOffer]] = {value: [] for value in outcome_lookup.values()}
    for snapshot in (left,) if left is right else (left, right):
        for executable in snapshot.offers:
            normalized = identity_key(executable.offer.outcome_id)
            if normalized not in outcome_lookup:
                continue
            canonical = _canonicalize_executable(
                executable,
                outcomes=outcome_lookup,
                scenarios=scenario_lookup,
            )
            if canonical.offer.slippage_bps < config.slippage_bps:
                canonical = replace(
                    canonical,
                    offer=canonical.offer.model_copy(update={"slippage_bps": config.slippage_bps}),
                )
            grouped[canonical.offer.outcome_id].append(canonical)

    if any(not values for values in grouped.values()):
        return None, 0, Counter({"complete_executable_outcome_coverage_missing": 1})
    bounded = [
        tuple(
            sorted(
                values,
                key=lambda item: _price_quality(item, config=config, now=now),
            )[: config.maximum_offers_per_outcome]
        )
        for values in grouped.values()
    ]
    best: GeneratedOpportunity | None = None
    evaluated = 0
    considered = 0
    rejects: Counter[str] = Counter()
    for selected_values in product(*bounded):
        if considered >= config.maximum_offer_combinations:
            rejects["offer_combination_limit_reached"] += 1
            break
        considered += 1
        selected = tuple(selected_values)
        if left is not right and len({item.source_market_id for item in selected}) < 2:
            continue
        if len({item.offer.offer_id for item in selected}) != len(selected):
            rejects["duplicate_offer_identity"] += 1
            continue
        evaluated += 1
        try:
            candidate = _evaluate_combination(
                left,
                right,
                decision,
                selected,
                config=config,
                now=now,
            )
        except ValueError as exc:
            rejects[str(exc)] += 1
            continue
        rejects.update(candidate.rejection_codes)
        rejects.update(candidate.ranking_rejection_codes)
        if not candidate.calculation.is_theoretical_arbitrage:
            continue
        objective = (
            int(candidate.execution_supported),
            candidate.full_worst_case,
            candidate.calculation.net_roi,
            -candidate.calculation.deployed_capital,
        )
        if best is None:
            best = candidate
        else:
            best_objective = (
                int(best.execution_supported),
                best.full_worst_case,
                best.calculation.net_roi,
                -best.calculation.deployed_capital,
            )
            if objective > best_objective:
                best = candidate
    return best, evaluated, rejects


def _trusted_decision(
    left: MarketSnapshot,
    right: MarketSnapshot,
    overrides: Mapping[tuple[str, str], MatchDecision],
) -> tuple[MatchDecision, DecisionSource, str | None]:
    automatic = verify_settlement_equivalence(left.market, right.market)
    if left is right:
        return automatic, "automatic", None
    override = overrides.get((left.market.market_id, right.market.market_id))
    if override is None:
        reversed_override = overrides.get(
            (right.market.market_id, left.market.market_id)
        )
        if reversed_override is not None:
            override = reversed_override.model_copy(
                update={
                    "left_material_fingerprint": reversed_override.right_material_fingerprint,
                    "right_material_fingerprint": reversed_override.left_material_fingerprint,
                }
            )
    if override is None:
        return automatic, "automatic", None
    if (
        override.status is MatchStatus.VERIFIED
        and (override.mismatches or override.unknown_fields)
    ):
        return automatic, "automatic", "trusted_decision_contains_unresolved_evidence"
    if override.engine_version != automatic.engine_version:
        return automatic, "automatic", "trusted_decision_engine_version_mismatch"
    if not override.left_material_fingerprint or not override.right_material_fingerprint:
        return automatic, "automatic", "trusted_decision_fingerprint_missing"
    if not material_fingerprints_still_match(
        left.market,
        right.market,
        reviewed_left_fingerprint=override.left_material_fingerprint,
        reviewed_right_fingerprint=override.right_material_fingerprint,
    ):
        return automatic, "automatic", "trusted_decision_fingerprint_mismatch"
    return override, "trusted_persisted_review", None


def generate_opportunities(
    snapshots: Sequence[MarketSnapshot],
    config: PipelineConfig,
    *,
    now: datetime | None = None,
    trusted_match_decisions: Mapping[tuple[str, str], MatchDecision] | None = None,
) -> PipelineRunResult:
    """Match snapshots and calculate transparent theoretical/strict results.

    Fuzzy identity is used only to nominate cross-market candidates.  A pair
    reaches calculation only after the existing explicit settlement verifier
    returns ``VERIFIED`` and the scenario sets match.  Each individual market
    is also evaluated for same-platform complementary-contract arbitrage.
    """

    if now is not None and (now.tzinfo is None or now.utcoffset() is None):
        raise ValueError("now must include a timezone")
    current = (now or datetime.now(UTC)).astimezone(UTC)
    ordered = sorted(snapshots, key=lambda item: item.market.market_id)
    market_ids = [snapshot.market.market_id for snapshot in ordered]
    if len(set(market_ids)) != len(market_ids):
        raise ValueError("snapshot market IDs must be unique")
    overrides = trusted_match_decisions or {}
    unordered_override_keys: set[frozenset[str]] = set()
    for key in overrides:
        if len(key) != 2 or not key[0].strip() or not key[1].strip():
            raise ValueError("trusted match decision keys require two market IDs")
        if key[0] == key[1]:
            raise ValueError("trusted match decisions cannot target a self-pair")
        unordered_key = frozenset(key)
        if unordered_key in unordered_override_keys:
            raise ValueError("trusted match decisions contain duplicate unordered pairs")
        unordered_override_keys.add(unordered_key)
    audits: list[CandidateAudit] = []
    opportunities_by_key: dict[str, GeneratedOpportunity] = {}
    rejection_counts: Counter[str] = Counter()
    evaluated_combinations = 0

    pairs: list[tuple[MarketSnapshot, MarketSnapshot]] = [
        (snapshot, snapshot) for snapshot in ordered
    ]
    pairs.extend(
        (left, right)
        for index, left in enumerate(ordered)
        for right in ordered[index + 1 :]
        if (
            plausible_event_candidate(left.market.event, right.market.event)
            or (left.market.market_id, right.market.market_id) in overrides
            or (right.market.market_id, left.market.market_id) in overrides
        )
    )

    for left, right in pairs:
        decision, decision_source, override_rejection = _trusted_decision(
            left, right, overrides
        )
        failures = _pair_gate_failures(left, right)
        audit = CandidateAudit(
            left_market_id=left.market.market_id,
            right_market_id=right.market.market_id,
            decision=decision,
            decision_source=decision_source,
            override_rejection=override_rejection,
            gate_failures=failures,
        )
        audits.append(audit)
        if override_rejection is not None:
            rejection_counts[override_rejection] += 1
        if decision.status is not MatchStatus.VERIFIED:
            rejection_counts[f"settlement_{decision.status.value}"] += 1
            continue
        if failures:
            rejection_counts.update(failures)
            continue
        candidate, evaluated, rejects = _evaluate_pair(
            left,
            right,
            decision,
            config=config,
            now=current,
        )
        evaluated_combinations += evaluated
        rejection_counts.update(rejects)
        if candidate is None or not candidate.calculation.is_theoretical_arbitrage:
            continue
        existing = opportunities_by_key.get(candidate.opportunity_key)
        if existing is None or (
            int(candidate.execution_supported), candidate.full_worst_case
        ) > (
            int(existing.execution_supported), existing.full_worst_case
        ):
            opportunities_by_key[candidate.opportunity_key] = candidate

    return PipelineRunResult(
        candidate_audits=tuple(audits),
        opportunities=tuple(
            sorted(opportunities_by_key.values(), key=lambda item: item.opportunity_key)
        ),
        evaluated_combinations=evaluated_combinations,
        rejection_counts=dict(sorted(rejection_counts.items())),
    )
