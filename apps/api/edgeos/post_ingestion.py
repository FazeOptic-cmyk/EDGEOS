"""Connect persisted market evidence to matching, calculations, and alerts.

The authoritative cycle creates active opportunities only when the supplied
canonical, settlement, fee, timestamp, and liquidity evidence passes every gate.
The legacy evidence-only helper remains available for normalized captures that
have not been passed to the authoritative cycle.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Literal, Protocol

from .domain import MatchDecision, MatchStatus, PriceKind
from .normalization import normalize_name
from .opportunity_pipeline import (
    CandidateAudit,
    GeneratedOpportunity,
    MarketSnapshot,
    PipelineConfig,
    PipelineRunResult,
    generate_opportunities,
)
from .pipeline_persistence import (
    AlertEvaluationResult,
    LifecycleChange,
    MarketLoadIssue,
    MarketLoadResult,
    MatchWrite,
    NormalizedMarketRecord,
    OpportunityLegWrite,
    OpportunityOutcomeWrite,
    OpportunityWrite,
    PersistedEvaluation,
    StoredMatchDecision,
    match_write_from_decision,
    to_engine_snapshots,
)


GateState = Literal["blocked", "ready_for_authoritative_calculation"]


@dataclass(frozen=True, slots=True)
class CandidateEvidence:
    """Material facts required before a candidate may reach calculation."""

    canonical_event_equivalent: bool | None = None
    settlement_rules_verified: bool | None = None
    outcome_coverage_complete: bool | None = None
    provider_timestamps_known: bool | None = None
    quotes_fresh: bool | None = None
    executable_prices_known: bool | None = None
    fees_verified: bool | None = None
    depth_known: bool | None = None
    currencies_compatible: bool | None = None


@dataclass(frozen=True, slots=True)
class CandidateGateResult:
    state: GateState
    eligible_for_calculation: bool
    failures: tuple[str, ...]


_REQUIREMENTS: tuple[tuple[str, str], ...] = (
    ("canonical_event_equivalent", "canonical_event_not_verified"),
    ("settlement_rules_verified", "settlement_rules_not_verified"),
    ("outcome_coverage_complete", "outcome_coverage_not_verified"),
    ("provider_timestamps_known", "provider_timestamp_unknown"),
    ("quotes_fresh", "quote_stale_or_freshness_unknown"),
    ("executable_prices_known", "executable_price_unknown"),
    ("fees_verified", "fee_schedule_not_verified"),
    ("depth_known", "executable_depth_unknown"),
    ("currencies_compatible", "currency_conversion_not_modeled"),
)


def assess_candidate(evidence: CandidateEvidence) -> CandidateGateResult:
    """Require explicit ``True`` for every material input.

    ``None`` and ``False`` are intentionally equivalent at this gate. Unknown
    evidence never becomes a zero fee, fresh timestamp, unlimited size, or
    compatible-settlement assumption.
    """

    failures = tuple(code for field, code in _REQUIREMENTS if getattr(evidence, field) is not True)
    eligible = not failures
    return CandidateGateResult(
        state="ready_for_authoritative_calculation" if eligible else "blocked",
        eligible_for_calculation=eligible,
        failures=failures,
    )


@dataclass(frozen=True, slots=True)
class PostIngestionReport:
    """Legacy diagnostics for a normalized-only capture, without a pipeline run."""

    status: Literal["blocked"] = "blocked"
    evaluated_candidates: int = 0
    opportunities_created: int = 0
    alert_events_created: int = 0
    blockers: tuple[str, ...] = (
        "canonical_matching_stage_not_connected",
        "settlement_evidence_stage_not_connected",
        "fee_schedule_stage_not_connected",
        "opportunity_generation_stage_not_connected",
        "alert_evaluation_stage_not_connected",
    )

    def diagnostics(self) -> dict[str, object]:
        return asdict(self)


def normalized_ingestion_report() -> PostIngestionReport:
    """Describe a normalized-only capture; the worker uses the full cycle below."""

    return PostIngestionReport()


@dataclass(frozen=True, slots=True)
class PipelineCycleIssue:
    stage: Literal["load", "normalize", "override", "opportunity"]
    code: str
    market_id: str | None = None
    market_pair: tuple[str, str] | None = None


@dataclass(frozen=True, slots=True)
class PipelineCycleReport:
    """Compact operational result for one atomic-ish post-ingestion pass."""

    status: Literal["completed", "partial", "no_evaluable_markets"]
    observed_at: datetime
    loaded_market_records: int
    engine_snapshots: int
    candidate_audits: int
    evaluated_combinations: int
    theoretical_opportunities: int
    strict_opportunities: int
    persisted_matches: int
    persisted_opportunities: int
    withdrawn_opportunities: int
    expired_or_stale_opportunities: int
    alert_events_created: int
    alert_events_deduplicated: int
    alert_events_filtered: int
    rejection_counts: Mapping[str, int]
    issues: tuple[PipelineCycleIssue, ...] = ()

    def diagnostics(self) -> dict[str, object]:
        payload = asdict(self)
        payload["observed_at"] = self.observed_at.isoformat()
        return payload


class PipelineCycleRepository(Protocol):
    """Persistence port used by the worker's single post-ingestion call."""

    def load_market_snapshots(
        self,
        *,
        now: datetime | None = None,
        maximum_quote_age_seconds: int = 120,
        limit: int = 5000,
    ) -> MarketLoadResult: ...

    def load_match_decision_overrides(self, market_ids: Iterable[str]) -> tuple[StoredMatchDecision, ...]: ...

    def persist_evaluation(
        self,
        match: MatchWrite | None,
        opportunity: OpportunityWrite | None,
        *,
        observed_at: datetime | None = None,
    ) -> PersistedEvaluation: ...

    def withdraw_absent_opportunities(
        self,
        *,
        evaluated_market_ids: Iterable[str],
        active_opportunity_keys: Iterable[str],
        observed_at: datetime | None = None,
    ) -> tuple[LifecycleChange, ...]: ...

    def expire_stale_opportunities(self, *, now: datetime | None = None) -> tuple[LifecycleChange, ...]: ...

    def withdraw_provider_opportunities(
        self,
        provider_codes: Iterable[str],
        *,
        now: datetime | None = None,
        reason: str = "provider_disconnected",
    ) -> tuple[LifecycleChange, ...]: ...

    def evaluate_alerts(
        self,
        opportunity_ids: Iterable[str],
        *,
        now: datetime | None = None,
    ) -> AlertEvaluationResult: ...


@dataclass(frozen=True, slots=True)
class _OverrideEvidence:
    decision: MatchDecision
    source_evidence: Mapping[str, object]


_DATABASE_CURRENCY = re.compile(r"^[A-Za-z][A-Za-z0-9]{2,7}$")


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("post-ingestion timestamps must include a timezone")
    return value.astimezone(UTC)


def _stored_overrides(
    stored: Sequence[StoredMatchDecision],
) -> tuple[
    dict[tuple[str, str], MatchDecision], dict[frozenset[str], Mapping[str, object]], tuple[PipelineCycleIssue, ...]
]:
    decisions: dict[tuple[str, str], MatchDecision] = {}
    evidence: dict[frozenset[str], Mapping[str, object]] = {}
    issues: list[PipelineCycleIssue] = []
    seen: set[frozenset[str]] = set()
    for item in stored:
        pair = (item.left_market_id, item.right_market_id)
        unordered = frozenset(pair)
        if len(unordered) != 2 or unordered in seen:
            issues.append(PipelineCycleIssue("override", "duplicate_or_invalid_pair", market_pair=pair))
            continue
        seen.add(unordered)
        if not item.operator_reviewed:
            # System decisions are recomputed from current evidence every run.
            continue
        engine_version = getattr(item, "engine_version", None)
        if not isinstance(engine_version, str) or not engine_version.strip():
            issues.append(PipelineCycleIssue("override", "engine_version_missing", market_pair=pair))
            continue
        if not item.source_evidence:
            issues.append(PipelineCycleIssue("override", "source_evidence_missing", market_pair=pair))
            continue
        try:
            status = MatchStatus(item.classification)
        except ValueError:
            issues.append(PipelineCycleIssue("override", "classification_invalid", market_pair=pair))
            continue
        decision = MatchDecision(
            status=status,
            candidate_score=Decimal("0"),
            reasons=(f"persisted operator review decision version {item.decision_version}",),
            engine_version=engine_version,
            left_material_fingerprint=item.left_rule_fingerprint,
            right_material_fingerprint=item.right_rule_fingerprint,
        )
        decisions[pair] = decision
        evidence[unordered] = item.source_evidence
    return decisions, evidence, tuple(issues)


def _source_evidence(
    audit: CandidateAudit,
    snapshots: Mapping[str, MarketSnapshot],
    persisted: Mapping[frozenset[str], Mapping[str, object]],
) -> Mapping[str, object]:
    pair = frozenset((audit.left_market_id, audit.right_market_id))
    if audit.decision_source == "trusted_persisted_review" and pair in persisted:
        return persisted[pair]
    left = snapshots[audit.left_market_id].market
    right = snapshots[audit.right_market_id].market
    return {
        "decision_source": "automatic_settlement_engine",
        "engine_version": audit.decision.engine_version,
        "left_source_url": left.source_url,
        "right_source_url": right.source_url,
    }


def _strategy_type(
    opportunity: GeneratedOpportunity,
) -> Literal[
    "sportsbook_sportsbook",
    "sportsbook_prediction",
    "prediction_prediction",
    "multi_outcome",
    "complementary_contracts",
]:
    if opportunity.left_market_id == opportunity.right_market_id:
        return "complementary_contracts"
    if len(opportunity.calculation.expected_outcome_ids or ()) > 2:
        return "multi_outcome"
    price_kinds = {item.offer.price_kind for item in opportunity.selected_offers}
    if price_kinds == {PriceKind.DECIMAL_ODDS}:
        return "sportsbook_sportsbook"
    if price_kinds == {PriceKind.CONTRACT_ASK}:
        return "prediction_prediction"
    return "sportsbook_prediction"


def _stable_snapshot_key(opportunity: GeneratedOpportunity) -> str:
    allocation_by_offer = {item.offer_id: item for item in opportunity.calculation.legs}
    payload = {
        "opportunity_key": opportunity.opportunity_key,
        "calculation_version": opportunity.calculation.calculation_version,
        "offers": [
            {
                "offer_id": item.offer.offer_id,
                "quantity": str(allocation_by_offer[item.offer.offer_id].filled_quantity),
                "unit_cost": str(allocation_by_offer[item.offer.offer_id].unit_cost),
                "fee": str(allocation_by_offer[item.offer.offer_id].fees),
            }
            for item in opportunity.selected_offers
        ],
        "outcomes": [
            (item.outcome_id, str(item.gross_payout), str(item.net_profit))
            for item in opportunity.full_scenario_outcomes
        ],
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def _opportunity_write(
    opportunity: GeneratedOpportunity,
    records: Mapping[str, NormalizedMarketRecord],
    *,
    observed_at: datetime,
    maximum_quote_age_seconds: int,
) -> OpportunityWrite:
    if not opportunity.execution_supported:
        raise ValueError("opportunity_not_execution_supported")
    currency = opportunity.calculation.currency
    if not _DATABASE_CURRENCY.fullmatch(currency):
        raise ValueError(f"unsupported_database_currency:{currency}")

    allocation_by_offer = {item.offer_id: item for item in opportunity.calculation.legs}
    legs: list[OpportunityLegWrite] = []
    expiry_candidates: list[datetime] = []
    canonical_event_ids: set[str] = set()
    for executable in opportunity.selected_offers:
        record = records.get(executable.source_market_id)
        if record is None:
            raise ValueError(f"source_market_record_missing:{executable.source_market_id}")
        canonical_event_id = getattr(record, "canonical_event_id", None)
        if not isinstance(canonical_event_id, str) or not canonical_event_id.strip():
            raise ValueError(f"canonical_event_id_missing:{record.market_id}")
        canonical_event_ids.add(canonical_event_id)
        outcome = next(
            (item for item in record.outcomes if item.quote_id == executable.offer.offer_id),
            None,
        )
        if outcome is None:
            raise ValueError(f"source_quote_record_missing:{executable.offer.offer_id}")
        allocation = allocation_by_offer[executable.offer.offer_id]
        quote_timestamp = executable.offer.quote_timestamp
        if quote_timestamp is None:
            raise ValueError(f"source_quote_timestamp_missing:{executable.offer.offer_id}")
        quote_timestamp = _utc(quote_timestamp)
        expiry_candidates.append(quote_timestamp + timedelta(seconds=maximum_quote_age_seconds))
        if record.closes_at is not None:
            expiry_candidates.append(_utc(record.closes_at))
        legs.append(
            OpportunityLegWrite(
                provider_id=record.provider_id,
                market_id=record.market_id,
                outcome_id=outcome.outcome_id,
                quote_id=outcome.quote_id,
                action=executable.action,
                price=executable.offer.price,
                quantity=allocation.filled_quantity,
                allocation=allocation.cash_cost,
                fee=allocation.fees,
                available_size=executable.offer.maximum_quantity,
                expected_slippage=executable.offer.slippage_bps,
                quote_observed_at=quote_timestamp,
            )
        )
    if len(canonical_event_ids) != 1:
        raise ValueError("canonical_event_id_mismatch")
    total_fees = opportunity.calculation.total_fees
    outcomes: list[OpportunityOutcomeWrite] = []
    for result in opportunity.full_scenario_outcomes:
        refunds = Decimal("0")
        leg_cashflows: list[dict[str, object]] = []
        for executable in opportunity.selected_offers:
            allocation = allocation_by_offer[executable.offer.offer_id]
            payout_per_unit = executable.payout_per_unit[result.outcome_id]
            payout = allocation.filled_quantity * payout_per_unit
            if normalize_name(result.outcome_id) != normalize_name(allocation.outcome_id):
                refunds += payout
            leg_cashflows.append(
                {
                    "offer_id": executable.offer.offer_id,
                    "payout_per_unit": str(payout_per_unit),
                    "gross_payout": str(payout),
                }
            )
        outcomes.append(
            OpportunityOutcomeWrite(
                scenario_key=result.outcome_id,
                label=result.outcome_id,
                gross_payout=result.gross_payout,
                refunds=refunds,
                fees=total_fees,
                net_profit=result.net_profit,
                scenario_data={"leg_cashflows": leg_cashflows},
            )
        )

    deployed = opportunity.calculation.deployed_capital
    net_roi = opportunity.full_worst_case / deployed if deployed else None
    rank = opportunity.rank if opportunity.rank is not None and opportunity.rank.eligible else None
    score_components = rank.components if rank is not None else {}
    execution_components = (
        "liquidity",
        "freshness",
        "timestamp_alignment",
        "slippage",
        "fill_reliability",
        "settlement_confidence",
    )
    confidence_values = [score_components[name] for name in execution_components if name in score_components]
    execution_confidence = min(confidence_values) if confidence_values else Decimal("0")
    expires_at = min(expiry_candidates)
    return OpportunityWrite(
        opportunity_key=opportunity.opportunity_key,
        market_match_id=None,
        event_id=next(iter(canonical_event_ids)),
        strategy_type=_strategy_type(opportunity),
        status="active",
        match_status="verified",
        total_allocation=deployed,
        currency=currency,
        gross_profit=opportunity.calculation.gross_profit,
        total_fees=total_fees,
        worst_case_net_profit=opportunity.full_worst_case,
        best_case_net_profit=opportunity.full_best_case,
        gross_roi=opportunity.calculation.gross_roi,
        net_roi=net_roi,
        maximum_depth=opportunity.calculation.maximum_available_allocation,
        conservative_net_profit=opportunity.full_worst_case,
        execution_confidence=execution_confidence,
        research_score=rank.score if rank is not None and rank.score is not None else Decimal("0"),
        score_breakdown={
            "available": rank is not None,
            "components": score_components,
            "ranking_rejection_codes": opportunity.ranking_rejection_codes,
        },
        hard_gate_failures=(),
        detected_at=observed_at,
        last_valid_at=observed_at,
        expires_at=expires_at,
        assumptions={
            "calculation_assumptions": opportunity.calculation.assumptions,
            "classification": opportunity.classification,
            "execution_is_not_guaranteed": True,
            "fees_conservatively_retained_in_all_scenarios": True,
        },
        legs=tuple(legs),
        outcomes=tuple(outcomes),
        snapshot_ingestion_key=_stable_snapshot_key(opportunity),
        snapshot_data={
            "calculation_version": opportunity.calculation.calculation_version,
            "match_engine_version": opportunity.decision.engine_version,
            "quote_ids": tuple(item.quote_id for item in legs),
            "full_scenario_cashflows": True,
        },
    )


def _load_issues(
    stage: Literal["load", "normalize"], issues: Sequence[MarketLoadIssue]
) -> tuple[PipelineCycleIssue, ...]:
    return tuple(PipelineCycleIssue(stage, item.code, market_id=item.market_id) for item in issues)


def _empty_alert_result() -> AlertEvaluationResult:
    return AlertEvaluationResult(created=0, deduplicated=0, filtered=0)


def run_post_ingestion_cycle(
    repository: PipelineCycleRepository,
    config: PipelineConfig,
    *,
    now: datetime | None = None,
    load_limit: int = 5000,
) -> PipelineCycleReport:
    """Run one fail-closed load-to-alert cycle.

    Database failures intentionally propagate before lifecycle withdrawal. A
    retry is safe because match, opportunity, and snapshot writes are
    idempotent. If no market is evaluable, the cycle expires already-stale
    records but never performs a broad absence withdrawal.
    """

    current = _utc(now or datetime.now(UTC))
    if not 1 <= load_limit <= 50_000:
        raise ValueError("load_limit must be between 1 and 50000")
    loaded = repository.load_market_snapshots(
        now=current,
        maximum_quote_age_seconds=config.maximum_quote_age_seconds,
        limit=load_limit,
    )
    snapshots, normalization_issues = to_engine_snapshots(loaded.markets)
    issues: list[PipelineCycleIssue] = [
        *_load_issues("load", loaded.issues),
        *_load_issues("normalize", normalization_issues),
    ]

    if not snapshots:
        expired = repository.expire_stale_opportunities(now=current)
        return PipelineCycleReport(
            status="no_evaluable_markets",
            observed_at=current,
            loaded_market_records=len(loaded.markets),
            engine_snapshots=0,
            candidate_audits=0,
            evaluated_combinations=0,
            theoretical_opportunities=0,
            strict_opportunities=0,
            persisted_matches=0,
            persisted_opportunities=0,
            withdrawn_opportunities=0,
            expired_or_stale_opportunities=len(expired),
            alert_events_created=0,
            alert_events_deduplicated=0,
            alert_events_filtered=0,
            rejection_counts={},
            issues=tuple(issues),
        )

    market_ids = tuple(item.market.market_id for item in snapshots)
    stored = repository.load_match_decision_overrides(market_ids)
    trusted, trusted_evidence, override_issues = _stored_overrides(stored)
    issues.extend(override_issues)
    run: PipelineRunResult = generate_opportunities(
        snapshots,
        config,
        now=current,
        trusted_match_decisions=trusted,
    )
    snapshots_by_id = {item.market.market_id: item for item in snapshots}
    records_by_id = {item.market_id: item for item in loaded.markets}
    strict_by_pair = {(item.left_market_id, item.right_market_id): item for item in run.strict_opportunities}
    persisted_matches = 0
    persisted_opportunity_ids: list[str] = []
    active_keys: list[str] = []

    for audit in run.candidate_audits:
        pair = (audit.left_market_id, audit.right_market_id)
        opportunity = strict_by_pair.get(pair)
        opportunity_write: OpportunityWrite | None = None
        if opportunity is not None:
            try:
                opportunity_write = _opportunity_write(
                    opportunity,
                    records_by_id,
                    observed_at=current,
                    maximum_quote_age_seconds=config.maximum_quote_age_seconds,
                )
            except ValueError as exc:
                issues.append(
                    PipelineCycleIssue(
                        "opportunity",
                        str(exc),
                        market_pair=pair,
                    )
                )

        match_write: MatchWrite | None = None
        if audit.left_market_id != audit.right_market_id:
            match_write = match_write_from_decision(
                left_market_id=audit.left_market_id,
                right_market_id=audit.right_market_id,
                decision=audit.decision,
                source_evidence=_source_evidence(
                    audit,
                    snapshots_by_id,
                    trusted_evidence,
                ),
            )
        if match_write is None and opportunity_write is None:
            continue
        persisted = repository.persist_evaluation(
            match_write,
            opportunity_write,
            observed_at=current,
        )
        if persisted.match_id is not None:
            persisted_matches += 1
        if opportunity_write is not None and persisted.opportunity_id is not None:
            persisted_opportunity_ids.append(persisted.opportunity_id)
            active_keys.append(opportunity_write.opportunity_key)
        elif opportunity_write is not None:
            issues.append(
                PipelineCycleIssue(
                    "opportunity",
                    "persistence_did_not_return_opportunity_id",
                    market_pair=pair,
                )
            )

    withdrawn = repository.withdraw_absent_opportunities(
        evaluated_market_ids=market_ids,
        active_opportunity_keys=active_keys,
        observed_at=current,
    )
    expired = repository.expire_stale_opportunities(now=current)
    alerts = (
        repository.evaluate_alerts(persisted_opportunity_ids, now=current)
        if persisted_opportunity_ids
        else _empty_alert_result()
    )
    return PipelineCycleReport(
        status="partial" if issues else "completed",
        observed_at=current,
        loaded_market_records=len(loaded.markets),
        engine_snapshots=len(snapshots),
        candidate_audits=len(run.candidate_audits),
        evaluated_combinations=run.evaluated_combinations,
        theoretical_opportunities=len(run.opportunities),
        strict_opportunities=len(run.strict_opportunities),
        persisted_matches=persisted_matches,
        persisted_opportunities=len(persisted_opportunity_ids),
        withdrawn_opportunities=len(withdrawn),
        expired_or_stale_opportunities=len(expired),
        alert_events_created=alerts.created,
        alert_events_deduplicated=alerts.deduplicated,
        alert_events_filtered=alerts.filtered,
        rejection_counts=run.rejection_counts,
        issues=tuple(issues),
    )
