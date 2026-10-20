"""Durable boundaries for the normalized-market opportunity pipeline.

The ingestion adapters write provider-scoped facts.  This module loads only
fresh, non-demo quote evidence, exposes prior operator decisions without
weakening their material-fingerprint guard, and atomically persists match,
opportunity, history, and alert lifecycle records.

It intentionally does not guess absent timestamps, fees, depth, settlement
rules, or canonical event identity.  Missing evidence remains missing so the
pure matching/calculation pipeline can fail closed.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
import hashlib
import json
from typing import Any, Literal

from .domain import (
    CanonicalEvent,
    CanonicalMarket,
    FeeSchedule,
    MatchDecision,
    MatchStatus,
    Offer,
    PriceKind,
    SettlementRules,
)
from .normalization import normalize_name
from .opportunity_pipeline import ExecutableOffer, MarketSnapshot


OpportunityStatus = Literal["active", "stale", "invalidated", "expired", "settled"]
MatchClassification = Literal["verified", "review_required", "rejected"]


def _json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise ValueError("timestamps must include a timezone")
    return value.astimezone(UTC)


def _mapping(value: object) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _text_or_none(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text if text and text.casefold() not in {"unknown", "unspecified"} else None


def _decimal_or_none(value: object) -> Decimal | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


def _bool_or_none(value: object) -> bool | None:
    return value if isinstance(value, bool) else None


def _stable_semantic_event_id(
    *,
    provider_event_id: str,
    sport: str | None,
    league: str | None,
    home_team: str | None,
    away_team: str | None,
    scheduled_start: datetime | None,
) -> str:
    """Build a candidate identity, never an equivalence assertion.

    A cross-provider key is emitted only when every explicit event-identity
    field is present.  Incomplete evidence remains provider-event scoped so a
    title similarity can never silently merge unrelated events.
    """

    explicit = (sport, league, home_team, away_team)
    if scheduled_start is not None and all(_text_or_none(value) for value in explicit):
        payload = "\x00".join(
            (
                normalize_name(str(sport)),
                normalize_name(str(league)),
                normalize_name(str(home_team)),
                normalize_name(str(away_team)),
                _utc(scheduled_start).isoformat(),
            )
        )
        return f"semantic:{hashlib.sha256(payload.encode()).hexdigest()}"
    return f"provider-event:{provider_event_id}"


@dataclass(frozen=True, slots=True)
class MarketRuleRecord:
    rule_id: str | None
    rule_version: int | None
    includes_overtime: bool | None
    tie_treatment: str | None
    push_treatment: str | None
    void_treatment: str | None
    cancellation_treatment: str | None
    settlement_source: str | None
    rules_url: str | None
    rules_text: str | None
    rules_hash: str | None
    verified_at: datetime | None
    metadata: Mapping[str, Any]


@dataclass(frozen=True, slots=True)
class FeeScheduleRecord:
    schedule_id: str
    provider: str
    effective_from: datetime
    effective_to: datetime | None
    calculation_method: str
    parameters: Mapping[str, Any]
    rounding_mode: str
    currency: str
    source_url: str | None
    verified_at: datetime | None


@dataclass(frozen=True, slots=True)
class OutcomeQuoteRecord:
    outcome_id: str
    external_outcome_id: str
    canonical_key: str
    name: str
    outcome_type: str
    line: Decimal | None
    payout_per_unit: Decimal
    quote_id: str
    decimal_odds: Decimal | None
    probability_price: Decimal | None
    best_bid: Decimal | None
    best_ask: Decimal | None
    bid_size: Decimal | None
    ask_size: Decimal | None
    available_size: Decimal | None
    currency: str
    feed_class: str
    observed_at: datetime
    received_at: datetime
    stale_at: datetime
    is_in_play: bool


@dataclass(frozen=True, slots=True)
class NormalizedMarketRecord:
    market_id: str
    provider_id: str
    provider_slug: str
    provider_name: str
    provider_kind: str
    provider_status: str | None
    external_market_id: str
    title: str
    market_type: str
    period: str
    line: Decimal | None
    currency: str
    live: bool
    minimum_increment: Decimal | None
    mutually_exclusive: bool | None
    collectively_exhaustive: bool | None
    closes_at: datetime | None
    source_url: str | None
    metadata: Mapping[str, Any]
    provider_event_id: str
    external_event_id: str
    event_title: str
    scheduled_start: datetime | None
    event_status: str
    event_source_url: str | None
    event_metadata: Mapping[str, Any]
    semantic_event_id: str
    canonical_event_id: str | None
    rule: MarketRuleRecord
    fee_schedule: FeeScheduleRecord | None
    outcomes: tuple[OutcomeQuoteRecord, ...]


@dataclass(frozen=True, slots=True)
class MarketLoadIssue:
    market_id: str
    code: str


@dataclass(frozen=True, slots=True)
class MarketLoadResult:
    markets: tuple[NormalizedMarketRecord, ...]
    issues: tuple[MarketLoadIssue, ...] = ()


@dataclass(frozen=True, slots=True)
class StoredMatchDecision:
    match_id: str
    left_market_id: str
    right_market_id: str
    classification: MatchClassification
    left_rule_fingerprint: str | None
    right_rule_fingerprint: str | None
    decision_version: int
    source_evidence: Mapping[str, Any]
    operator_reviewed: bool
    engine_version: str


@dataclass(frozen=True, slots=True)
class MatchWrite:
    left_market_id: str
    right_market_id: str
    classification: MatchClassification
    candidate_score: Decimal | None
    settlement_confidence: Decimal
    equivalence_checks: Mapping[str, object]
    explanation: str
    engine_version: str
    left_rule_fingerprint: str | None
    right_rule_fingerprint: str | None
    decision_fingerprint: str
    source_evidence: Mapping[str, object]

    def __post_init__(self) -> None:
        if self.left_market_id == self.right_market_id:
            raise ValueError("same-market opportunities must not create a market_match")
        if not self.explanation.strip() or not self.engine_version.strip():
            raise ValueError("match explanation and engine version are required")
        if not self.decision_fingerprint.strip():
            raise ValueError("decision fingerprint is required")
        if self.classification == "verified" and (
            self.settlement_confidence != Decimal("1")
            or not self.left_rule_fingerprint
            or not self.right_rule_fingerprint
            or not self.source_evidence
        ):
            raise ValueError("verified matches require complete fingerprinted source evidence")


@dataclass(frozen=True, slots=True)
class OpportunityLegWrite:
    provider_id: str
    market_id: str
    outcome_id: str
    quote_id: str
    action: Literal["back", "buy_yes", "buy_no"]
    price: Decimal
    quantity: Decimal
    allocation: Decimal
    fee: Decimal
    available_size: Decimal | None
    expected_slippage: Decimal
    quote_observed_at: datetime


@dataclass(frozen=True, slots=True)
class OpportunityOutcomeWrite:
    scenario_key: str
    label: str
    gross_payout: Decimal
    refunds: Decimal
    fees: Decimal
    net_profit: Decimal
    scenario_data: Mapping[str, object]


@dataclass(frozen=True, slots=True)
class OpportunityWrite:
    opportunity_key: str
    market_match_id: str | None
    event_id: str | None
    strategy_type: Literal[
        "sportsbook_sportsbook",
        "sportsbook_prediction",
        "prediction_prediction",
        "multi_outcome",
        "complementary_contracts",
    ]
    status: OpportunityStatus
    match_status: MatchClassification
    total_allocation: Decimal
    currency: str
    gross_profit: Decimal
    total_fees: Decimal
    worst_case_net_profit: Decimal
    best_case_net_profit: Decimal
    gross_roi: Decimal | None
    net_roi: Decimal | None
    maximum_depth: Decimal | None
    conservative_net_profit: Decimal
    execution_confidence: Decimal
    research_score: Decimal
    score_breakdown: Mapping[str, object]
    hard_gate_failures: tuple[str, ...]
    detected_at: datetime
    last_valid_at: datetime | None
    expires_at: datetime | None
    assumptions: Mapping[str, object]
    legs: tuple[OpportunityLegWrite, ...]
    outcomes: tuple[OpportunityOutcomeWrite, ...]
    snapshot_ingestion_key: str
    snapshot_data: Mapping[str, object]

    def __post_init__(self) -> None:
        if not self.opportunity_key.strip() or not self.snapshot_ingestion_key.strip():
            raise ValueError("opportunity and snapshot idempotency keys are required")
        if self.status == "active" and self.match_status != "verified":
            raise ValueError("only verified matches can create active opportunities")
        if len(self.legs) < 2 or len(self.outcomes) < 2:
            raise ValueError("an opportunity requires at least two legs and scenarios")
        if len({item.scenario_key for item in self.outcomes}) != len(self.outcomes):
            raise ValueError("opportunity scenario keys must be unique")
        if self.best_case_net_profit < self.worst_case_net_profit:
            raise ValueError("best-case profit cannot be below worst-case profit")
        _utc(self.detected_at)
        if self.last_valid_at is not None:
            _utc(self.last_valid_at)
        if self.expires_at is not None:
            _utc(self.expires_at)


@dataclass(frozen=True, slots=True)
class PersistedEvaluation:
    match_id: str | None
    opportunity_id: str | None
    effective_match_status: MatchClassification


@dataclass(frozen=True, slots=True)
class LifecycleChange:
    opportunity_id: str
    opportunity_key: str
    status: OpportunityStatus


@dataclass(frozen=True, slots=True)
class AlertEvaluationResult:
    created: int
    deduplicated: int
    filtered: int


class PostgresPipelineRepository:
    """Trusted-worker persistence for the live research pipeline."""

    def __init__(self, connection_factory: Callable[[], Any]) -> None:
        self._connect = connection_factory

    def list_provider_health(self) -> tuple[dict[str, object], ...]:
        """Return persisted worker telemetry without making provider requests."""

        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                select p.slug,p.display_name,p.provider_kind,p.feed_type,
                       coalesce(pc.status,p.availability_status),p.requires_api_key,
                       p.refresh_interval_seconds,p.data_delay_seconds,p.data_delay_known,
                       pc.last_attempt_at,pc.last_success_at,pc.last_error_at,
                       pc.last_error_code,pc.last_error_summary,pc.rate_limit_state,
                       pc.quota_limit,pc.quota_remaining,pc.quota_resets_at,
                       coalesce(pc.active_instruments,0),coalesce(pc.consecutive_failures,0),
                       coalesce(pc.diagnostics,'{}'::jsonb),pc.updated_at
                  from public.providers p
                  left join public.provider_connections pc on pc.provider_id=p.id
                 where p.is_enabled order by p.display_name,p.slug
                """,
                (),
            )
            rows = cursor.fetchall()
        return tuple(
            {
                "provider": row[0],
                "display_name": row[1],
                "provider_kind": row[2],
                "feed_type": row[3],
                "state": row[4],
                "requires_api_key": bool(row[5]),
                "known_refresh_interval_seconds": row[6],
                "measured_data_delay_seconds": row[7] if row[8] else None,
                "last_attempt_at": row[9],
                "last_success_at": row[10],
                "last_error_at": row[11],
                "last_error_code": row[12],
                "message": row[13],
                "rate_limit_state": row[14],
                "quota_limit": row[15],
                "quota_remaining": row[16],
                "quota_resets_at": row[17],
                "active_instruments": int(row[18]),
                "consecutive_failures": int(row[19]),
                "diagnostics": dict(row[20]) if isinstance(row[20], Mapping) else {},
                "updated_at": row[21],
            }
            for row in rows
        )

    def load_market_snapshots(
        self,
        *,
        now: datetime | None = None,
        maximum_quote_age_seconds: int = 120,
        limit: int = 5000,
    ) -> MarketLoadResult:
        """Load complete, fresh, provider-timestamped non-demo market records."""

        if maximum_quote_age_seconds <= 0:
            raise ValueError("maximum_quote_age_seconds must be positive")
        if not 1 <= limit <= 50_000:
            raise ValueError("market load limit must be between 1 and 50000")
        observed_at = _utc(now or datetime.now(UTC))
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                select
                  m.id::text, p.id::text, p.slug, p.display_name, p.provider_kind,
                  pc.status, m.external_market_id, m.title, m.market_type, m.period,
                  m.line, m.currency, m.live, m.minimum_increment,
                  m.mutually_exclusive, m.collectively_exhaustive, m.closes_at,
                  m.source_url, m.metadata,
                  pe.id::text, pe.external_event_id, pe.title, pe.scheduled_start,
                  pe.status, pe.source_url, pe.metadata,
                  mr.id::text, mr.rule_version, mr.includes_overtime,
                  mr.tie_treatment, mr.push_treatment, mr.void_treatment,
                  mr.cancellation_treatment, mr.settlement_source, mr.rules_url,
                  mr.rules_text, mr.rules_hash, mr.verified_at, mr.metadata,
                  fs.id::text, fs.name, fs.version, fs.effective_from,
                  fs.effective_to, fs.calculation_method, fs.parameters,
                  fs.rounding_mode, fs.currency, fs.official_source_url,
                  fs.verified_at,
                  (select count(*) from public.outcomes expected
                    where expected.market_id = m.id and expected.status = 'open'),
                  outcome.id::text, outcome.external_outcome_id,
                  outcome.canonical_key, outcome.name, outcome.outcome_type,
                  outcome.line, outcome.payout_per_unit,
                  quote.id::text, quote.decimal_odds, quote.probability_price,
                  quote.best_bid, quote.best_ask, quote.bid_size, quote.ask_size,
                  quote.available_size, quote.currency, quote.feed_class,
                  quote.observed_at, quote.received_at, quote.stale_at,
                  quote.is_in_play, quote.source_sequence,
                  pe.canonical_event_id::text
                from public.markets m
                join public.providers p on p.id = m.provider_id
                join public.provider_events pe on pe.id = m.provider_event_id
                left join public.provider_connections pc on pc.provider_id = p.id
                join public.outcomes outcome
                  on outcome.market_id = m.id and outcome.status = 'open'
                join lateral (
                  select q.* from public.quotes q
                   where q.outcome_id = outcome.id
                     and q.observed_at <= %s
                     and q.observed_at >= %s - (%s * interval '1 second')
                     and q.stale_at is not null and q.stale_at >= %s
                     and q.feed_class in ('streaming', 'polled')
                     and not q.is_demo
                     and q.source_sequence is distinct from 'fetch_time_no_provider_timestamp'
                     and m.metadata->>'provider_quote_timestamp_known' = 'true'
                   order by q.observed_at desc, q.received_at desc, q.id desc
                   limit 1
                ) quote on true
                left join lateral (
                  select rule.* from public.market_rules rule
                   where rule.market_id = m.id
                     and rule.effective_from <= %s
                     and (rule.effective_to is null or rule.effective_to > %s)
                   order by rule.rule_version desc limit 1
                ) mr on true
                left join lateral (
                  select fee.* from public.fee_schedules fee
                   where fee.provider_id = p.id and fee.currency = m.currency
                     and fee.effective_from <= %s
                     and (fee.effective_to is null or fee.effective_to > %s)
                   order by fee.effective_from desc, fee.version desc limit 1
                ) fs on true
                where m.status = 'open' and not m.is_demo and not pe.is_demo
                  and not p.is_demo
                  and (m.closes_at is null or m.closes_at > %s)
                order by m.id, outcome.canonical_key
                limit %s
                """,
                (
                    observed_at,
                    observed_at,
                    maximum_quote_age_seconds,
                    observed_at,
                    observed_at,
                    observed_at,
                    observed_at,
                    observed_at,
                    observed_at,
                    limit,
                ),
            )
            rows = cursor.fetchall()
        return self._market_rows(rows)

    @classmethod
    def _market_rows(cls, rows: Sequence[tuple[Any, ...]]) -> MarketLoadResult:
        grouped: dict[str, list[tuple[Any, ...]]] = {}
        for row in rows:
            grouped.setdefault(str(row[0]), []).append(row)
        markets: list[NormalizedMarketRecord] = []
        issues: list[MarketLoadIssue] = []
        for market_id, group in grouped.items():
            first = group[0]
            expected_outcomes = int(first[50])
            if expected_outcomes < 2 or len(group) != expected_outcomes:
                issues.append(MarketLoadIssue(market_id, "incomplete_fresh_quote_coverage"))
                continue
            event_metadata = _mapping(first[25])
            normalized_event = _mapping(event_metadata.get("normalized_event"))
            scheduled_start = first[22]
            if scheduled_start is None:
                scheduled_start = _parse_datetime(_mapping(first[18]).get("event_start_time"))
            sport = _text_or_none(normalized_event.get("sport"))
            league = _text_or_none(normalized_event.get("league"))
            home = _text_or_none(normalized_event.get("home_team"))
            away = _text_or_none(normalized_event.get("away_team"))
            semantic_id = _stable_semantic_event_id(
                provider_event_id=str(first[19]),
                sport=sport,
                league=league,
                home_team=home,
                away_team=away,
                scheduled_start=scheduled_start,
            )
            rule = MarketRuleRecord(
                rule_id=str(first[26]) if first[26] is not None else None,
                rule_version=int(first[27]) if first[27] is not None else None,
                includes_overtime=first[28],
                tie_treatment=first[29],
                push_treatment=first[30],
                void_treatment=first[31],
                cancellation_treatment=first[32],
                settlement_source=first[33],
                rules_url=first[34],
                rules_text=first[35],
                rules_hash=first[36],
                verified_at=first[37],
                metadata=_mapping(first[38]),
            )
            fee = None
            if first[39] is not None:
                fee = FeeScheduleRecord(
                    schedule_id=str(first[39]),
                    provider=str(first[2]),
                    effective_from=first[42],
                    effective_to=first[43],
                    calculation_method=str(first[44]),
                    parameters=_mapping(first[45]),
                    rounding_mode=str(first[46]),
                    currency=str(first[47]),
                    source_url=first[48],
                    verified_at=first[49],
                )
            outcomes = tuple(
                OutcomeQuoteRecord(
                    outcome_id=str(row[51]),
                    external_outcome_id=str(row[52]),
                    canonical_key=str(row[53]),
                    name=str(row[54]),
                    outcome_type=str(row[55]),
                    line=row[56],
                    payout_per_unit=row[57],
                    quote_id=str(row[58]),
                    decimal_odds=row[59],
                    probability_price=row[60],
                    best_bid=row[61],
                    best_ask=row[62],
                    bid_size=row[63],
                    ask_size=row[64],
                    available_size=row[65],
                    currency=str(row[66]),
                    feed_class=str(row[67]),
                    observed_at=row[68],
                    received_at=row[69],
                    stale_at=row[70],
                    is_in_play=bool(row[71]),
                )
                for row in group
            )
            markets.append(
                NormalizedMarketRecord(
                    market_id=market_id,
                    provider_id=str(first[1]),
                    provider_slug=str(first[2]),
                    provider_name=str(first[3]),
                    provider_kind=str(first[4]),
                    provider_status=str(first[5]) if first[5] is not None else None,
                    external_market_id=str(first[6]),
                    title=str(first[7]),
                    market_type=str(first[8]),
                    period=str(first[9]),
                    line=first[10],
                    currency=str(first[11]),
                    live=bool(first[12]),
                    minimum_increment=first[13],
                    mutually_exclusive=first[14],
                    collectively_exhaustive=first[15],
                    closes_at=first[16],
                    source_url=first[17],
                    metadata=_mapping(first[18]),
                    provider_event_id=str(first[19]),
                    external_event_id=str(first[20]),
                    event_title=str(first[21]),
                    scheduled_start=scheduled_start,
                    event_status=str(first[23]),
                    event_source_url=first[24],
                    event_metadata=event_metadata,
                    semantic_event_id=semantic_id,
                    canonical_event_id=str(first[73]) if first[73] is not None else None,
                    rule=rule,
                    fee_schedule=fee,
                    outcomes=outcomes,
                )
            )
        return MarketLoadResult(tuple(markets), tuple(issues))

    def load_match_decision_overrides(
        self, market_ids: Iterable[str]
    ) -> tuple[StoredMatchDecision, ...]:
        ids = tuple(dict.fromkeys(str(value) for value in market_ids))
        if not ids:
            return ()
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                select mm.id::text, mm.left_market_id::text, mm.right_market_id::text,
                       mm.classification, mm.left_rule_fingerprint,
                       mm.right_rule_fingerprint, mm.decision_version,
                       mm.decision_source_evidence,
                       coalesce(latest.reviewer_kind = 'operator', false),
                       mm.engine_version
                  from public.market_matches mm
                  left join lateral (
                    select reviewer_kind from public.match_reviews review
                     where review.market_match_id = mm.id
                     order by review.decision_version desc limit 1
                  ) latest on true
                 where mm.left_market_id = any(%s::uuid[])
                   and mm.right_market_id = any(%s::uuid[])
                   and mm.invalidated_at is null
                """,
                (list(ids), list(ids)),
            )
            rows = cursor.fetchall()
        return tuple(
            StoredMatchDecision(
                match_id=str(row[0]),
                left_market_id=str(row[1]),
                right_market_id=str(row[2]),
                classification=row[3],
                left_rule_fingerprint=row[4],
                right_rule_fingerprint=row[5],
                decision_version=int(row[6]),
                source_evidence=_mapping(row[7]),
                operator_reviewed=bool(row[8]),
                engine_version=str(row[9]),
            )
            for row in rows
        )

    def persist_evaluation(
        self,
        match: MatchWrite | None,
        opportunity: OpportunityWrite | None,
        *,
        observed_at: datetime | None = None,
    ) -> PersistedEvaluation:
        current = _utc(observed_at or datetime.now(UTC))
        if match is None and opportunity is None:
            raise ValueError("an evaluation must contain a match or an opportunity")
        if match is None and opportunity is not None and opportunity.market_match_id is not None:
            raise ValueError("opportunity references a match that was not supplied")
        with self._connect() as connection, connection.cursor() as cursor:
            match_id: str | None = None
            effective_status: MatchClassification = (
                opportunity.match_status if opportunity is not None else "review_required"
            )
            if match is not None:
                match_id, effective_status = self._upsert_match(cursor, match, current)
            opportunity_id = None
            if opportunity is not None:
                expected_match_id = match_id if match is not None else None
                if opportunity.market_match_id not in {None, expected_match_id}:
                    raise ValueError("opportunity market_match_id does not match persisted decision")
                if opportunity.status == "active" and effective_status != "verified":
                    raise ValueError("effective stored match decision is not verified")
                cursor.execute(
                    "select status from public.opportunities where opportunity_key=%s for update",
                    (opportunity.opportunity_key,),
                )
                prior_opportunity = cursor.fetchone()
                opportunity_id = self._upsert_opportunity(
                    cursor,
                    opportunity,
                    match_id=expected_match_id,
                    match_status=effective_status,
                )
                if (
                    prior_opportunity is not None
                    and prior_opportunity[0] != "active"
                    and opportunity.status == "active"
                ):
                    # The quote-based snapshot key may already exist from the
                    # earlier active period. Preserve this distinct restoration
                    # transition even when the executable quotes are unchanged.
                    self._record_lifecycle_snapshots(
                        cursor,
                        ((
                            opportunity_id, opportunity.opportunity_key, "active",
                            opportunity.total_allocation, opportunity.gross_profit,
                            opportunity.worst_case_net_profit, opportunity.net_roi,
                            opportunity.maximum_depth, opportunity.execution_confidence,
                        ),),
                        observed_at=current,
                        reason="strict_evidence_restored",
                    )
        return PersistedEvaluation(match_id, opportunity_id, effective_status)

    @staticmethod
    def _upsert_match(
        cursor: Any, match: MatchWrite, observed_at: datetime
    ) -> tuple[str, MatchClassification]:
        left_id, right_id = sorted((match.left_market_id, match.right_market_id))
        left_fp, right_fp = (
            (match.left_rule_fingerprint, match.right_rule_fingerprint)
            if left_id == match.left_market_id
            else (match.right_rule_fingerprint, match.left_rule_fingerprint)
        )
        cursor.execute(
            """
            select mm.id::text, mm.classification, mm.left_rule_fingerprint,
                   mm.right_rule_fingerprint, mm.decision_fingerprint,
                   mm.decision_version, mm.invalidated_at,
                   (select reviewer_kind from public.match_reviews review
                     where review.market_match_id = mm.id
                     order by review.decision_version desc limit 1)
              from public.market_matches mm
             where least(mm.left_market_id, mm.right_market_id) = %s
               and greatest(mm.left_market_id, mm.right_market_id) = %s
               and mm.engine_version = %s
             for update
            """,
            (left_id, right_id, match.engine_version),
        )
        existing = cursor.fetchone()
        verified_at = observed_at if match.classification == "verified" else None
        if existing is None:
            cursor.execute(
                """
                insert into public.market_matches
                  (left_market_id, right_market_id, classification, candidate_score,
                   settlement_confidence, equivalence_checks, explanation,
                   engine_version, verified_at, left_rule_fingerprint,
                   right_rule_fingerprint, decision_fingerprint,
                   decision_source_evidence)
                values (%s,%s,%s,%s,%s,%s::jsonb,%s,%s,%s,%s,%s,%s,%s::jsonb)
                returning id::text
                """,
                (
                    left_id,
                    right_id,
                    match.classification,
                    match.candidate_score,
                    match.settlement_confidence,
                    _json(match.equivalence_checks),
                    match.explanation.strip(),
                    match.engine_version,
                    verified_at,
                    left_fp,
                    right_fp,
                    match.decision_fingerprint,
                    _json(match.source_evidence),
                ),
            )
            match_id = str(cursor.fetchone()[0])
            PostgresPipelineRepository._insert_system_review(
                cursor,
                match_id=match_id,
                previous=None,
                decision=match.classification,
                match=match,
                version=1,
                left_fingerprint=left_fp,
                right_fingerprint=right_fp,
            )
            return match_id, match.classification

        match_id = str(existing[0])
        stored_status: MatchClassification = existing[1]
        fingerprints_match = existing[2] == left_fp and existing[3] == right_fp
        operator_reviewed = existing[7] == "operator"
        if operator_reviewed and fingerprints_match and existing[6] is None:
            # Manual decisions survive engine reruns only for the exact material
            # evidence that the operator reviewed.
            return match_id, stored_status

        effective_status: MatchClassification = match.classification
        invalidation_reason = None
        if operator_reviewed and not fingerprints_match:
            effective_status = "review_required"
            invalidation_reason = "operator_decision_fingerprint_changed"
        changed = (
            existing[4] != match.decision_fingerprint
            or stored_status != effective_status
            or not fingerprints_match
            or existing[6] is not None
        )
        next_version = int(existing[5]) + (1 if changed else 0)
        cursor.execute(
            """
            update public.market_matches
               set classification=%s, candidate_score=%s, settlement_confidence=%s,
                   equivalence_checks=%s::jsonb, explanation=%s,
                   verified_at=%s, left_rule_fingerprint=%s,
                   right_rule_fingerprint=%s, decision_fingerprint=%s,
                   decision_source_evidence=%s::jsonb, decision_version=%s,
                   invalidated_at=%s, invalidation_reason=%s, updated_at=%s
             where id=%s
            """,
            (
                effective_status,
                match.candidate_score,
                Decimal("1") if effective_status == "verified" else Decimal("0"),
                _json(match.equivalence_checks),
                match.explanation.strip(),
                observed_at if effective_status == "verified" else None,
                left_fp,
                right_fp,
                match.decision_fingerprint,
                _json(match.source_evidence),
                next_version,
                observed_at if invalidation_reason else None,
                invalidation_reason,
                observed_at,
                match_id,
            ),
        )
        if changed:
            PostgresPipelineRepository._insert_system_review(
                cursor,
                match_id=match_id,
                previous=stored_status,
                decision=effective_status,
                match=match,
                version=next_version,
                left_fingerprint=left_fp,
                right_fingerprint=right_fp,
                rationale=(
                    "Material evidence changed; operator verification requires renewed review."
                    if invalidation_reason
                    else None
                ),
            )
        if effective_status != "verified":
            PostgresPipelineRepository._invalidate_match_opportunities(
                cursor, match_id, effective_status, observed_at
            )
        return match_id, effective_status

    @staticmethod
    def _insert_system_review(
        cursor: Any,
        *,
        match_id: str,
        previous: str | None,
        decision: str,
        match: MatchWrite,
        version: int,
        left_fingerprint: str | None,
        right_fingerprint: str | None,
        rationale: str | None = None,
    ) -> None:
        cursor.execute(
            """
            insert into public.match_reviews
              (market_match_id, previous_classification, decision, reviewer_kind,
               checks, rationale, decision_version, left_rule_fingerprint,
               right_rule_fingerprint, decision_fingerprint, source_evidence)
            values (%s,%s,%s,'system',%s::jsonb,%s,%s,%s,%s,%s,%s::jsonb)
            on conflict (market_match_id, decision_version) do nothing
            """,
            (
                match_id,
                previous,
                decision,
                _json(match.equivalence_checks),
                rationale or match.explanation.strip(),
                version,
                left_fingerprint,
                right_fingerprint,
                match.decision_fingerprint,
                _json(match.source_evidence),
            ),
        )

    @staticmethod
    def _invalidate_match_opportunities(
        cursor: Any, match_id: str, decision: str, observed_at: datetime
    ) -> None:
        reason = f"settlement_decision_{decision}"
        cursor.execute(
            """
            update public.opportunities
               set match_status=%s, status='invalidated', last_valid_at=coalesce(last_valid_at,%s),
                   expires_at=coalesce(expires_at,%s),
                   hard_gate_failures=case when hard_gate_failures ? %s
                     then hard_gate_failures else hard_gate_failures || jsonb_build_array(%s::text) end,
                   updated_at=%s
             where market_match_id=%s and status in ('active','stale')
            returning id::text
            """,
            (decision, observed_at, observed_at, reason, reason, observed_at, match_id),
        )
        ids = tuple(str(row[0]) for row in cursor.fetchall())
        PostgresPipelineRepository._withdraw_alert_events(cursor, ids, observed_at, reason)

    @staticmethod
    def _upsert_opportunity(
        cursor: Any,
        opportunity: OpportunityWrite,
        *,
        match_id: str | None,
        match_status: MatchClassification,
    ) -> str:
        cursor.execute(
            """
            insert into public.opportunities
              (opportunity_key,market_match_id,event_id,strategy_type,status,match_status,
               total_allocation,currency,gross_profit,total_fees,worst_case_net_profit,
               best_case_net_profit,gross_roi,net_roi,maximum_depth,
               conservative_net_profit,execution_confidence,research_score,
               score_breakdown,hard_gate_failures,detected_at,last_valid_at,
               expires_at,is_demo,assumptions)
            values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,
                    %s::jsonb,%s::jsonb,%s,%s,%s,false,%s::jsonb)
            on conflict (opportunity_key) do update set
              market_match_id=excluded.market_match_id,event_id=excluded.event_id,
              strategy_type=excluded.strategy_type,status=excluded.status,
              match_status=excluded.match_status,total_allocation=excluded.total_allocation,
              currency=excluded.currency,gross_profit=excluded.gross_profit,
              total_fees=excluded.total_fees,
              worst_case_net_profit=excluded.worst_case_net_profit,
              best_case_net_profit=excluded.best_case_net_profit,
              gross_roi=excluded.gross_roi,net_roi=excluded.net_roi,
              maximum_depth=excluded.maximum_depth,
              conservative_net_profit=excluded.conservative_net_profit,
              execution_confidence=excluded.execution_confidence,
              research_score=excluded.research_score,
              score_breakdown=excluded.score_breakdown,
              hard_gate_failures=excluded.hard_gate_failures,
              detected_at=least(opportunities.detected_at,excluded.detected_at),
              last_valid_at=excluded.last_valid_at,expires_at=excluded.expires_at,
              assumptions=excluded.assumptions,updated_at=statement_timestamp()
            returning id::text
            """,
            (
                opportunity.opportunity_key,
                match_id,
                opportunity.event_id,
                opportunity.strategy_type,
                opportunity.status,
                match_status,
                opportunity.total_allocation,
                opportunity.currency,
                opportunity.gross_profit,
                opportunity.total_fees,
                opportunity.worst_case_net_profit,
                opportunity.best_case_net_profit,
                opportunity.gross_roi,
                opportunity.net_roi,
                opportunity.maximum_depth,
                opportunity.conservative_net_profit,
                opportunity.execution_confidence,
                opportunity.research_score,
                _json(opportunity.score_breakdown),
                _json(opportunity.hard_gate_failures),
                opportunity.detected_at,
                opportunity.last_valid_at,
                opportunity.expires_at,
                _json(opportunity.assumptions),
            ),
        )
        opportunity_id = str(cursor.fetchone()[0])
        for number, leg in enumerate(opportunity.legs, start=1):
            cursor.execute(
                """
                insert into public.opportunity_legs
                  (opportunity_id,leg_number,provider_id,market_id,outcome_id,quote_id,
                   action,price,quantity,allocation,fee,available_size,
                   expected_slippage,quote_observed_at)
                values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                on conflict (opportunity_id,leg_number) do update set
                  provider_id=excluded.provider_id,market_id=excluded.market_id,
                  outcome_id=excluded.outcome_id,quote_id=excluded.quote_id,
                  action=excluded.action,price=excluded.price,quantity=excluded.quantity,
                  allocation=excluded.allocation,fee=excluded.fee,
                  available_size=excluded.available_size,
                  expected_slippage=excluded.expected_slippage,
                  quote_observed_at=excluded.quote_observed_at
                """,
                (
                    opportunity_id,
                    number,
                    leg.provider_id,
                    leg.market_id,
                    leg.outcome_id,
                    leg.quote_id,
                    leg.action,
                    leg.price,
                    leg.quantity,
                    leg.allocation,
                    leg.fee,
                    leg.available_size,
                    leg.expected_slippage,
                    leg.quote_observed_at,
                ),
            )
        cursor.execute(
            "delete from public.opportunity_legs where opportunity_id=%s and leg_number > %s",
            (opportunity_id, len(opportunity.legs)),
        )
        scenario_keys = [outcome.scenario_key for outcome in opportunity.outcomes]
        for outcome in opportunity.outcomes:
            cursor.execute(
                """
                insert into public.opportunity_outcomes
                  (opportunity_id,scenario_key,label,gross_payout,refunds,fees,
                   net_profit,scenario_data)
                values (%s,%s,%s,%s,%s,%s,%s,%s::jsonb)
                on conflict (opportunity_id,scenario_key) do update set
                  label=excluded.label,gross_payout=excluded.gross_payout,
                  refunds=excluded.refunds,fees=excluded.fees,
                  net_profit=excluded.net_profit,scenario_data=excluded.scenario_data
                """,
                (
                    opportunity_id,
                    outcome.scenario_key,
                    outcome.label,
                    outcome.gross_payout,
                    outcome.refunds,
                    outcome.fees,
                    outcome.net_profit,
                    _json(outcome.scenario_data),
                ),
            )
        cursor.execute(
            "delete from public.opportunity_outcomes where opportunity_id=%s and not (scenario_key = any(%s))",
            (opportunity_id, scenario_keys),
        )
        quote_ids = [leg.quote_id for leg in opportunity.legs]
        cursor.execute(
            """
            insert into public.opportunity_snapshots
              (opportunity_id,ingestion_key,observed_at,status,total_allocation,
               gross_profit,net_profit,net_roi,available_liquidity,
               execution_confidence,quote_ids,snapshot_data,is_demo)
            values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::uuid[],%s::jsonb,false)
            on conflict (ingestion_key) do nothing returning id::text
            """,
            (
                opportunity_id,
                opportunity.snapshot_ingestion_key,
                opportunity.detected_at,
                opportunity.status,
                opportunity.total_allocation,
                opportunity.gross_profit,
                opportunity.worst_case_net_profit,
                opportunity.net_roi,
                opportunity.maximum_depth,
                opportunity.execution_confidence,
                quote_ids,
                _json(opportunity.snapshot_data),
            ),
        )
        if cursor.fetchone() is None:
            cursor.execute(
                "select opportunity_id::text from public.opportunity_snapshots where ingestion_key=%s",
                (opportunity.snapshot_ingestion_key,),
            )
            existing = cursor.fetchone()
            if existing is None or str(existing[0]) != opportunity_id:
                raise ValueError("snapshot_ingestion_key_conflict")
        return opportunity_id

    def withdraw_absent_opportunities(
        self,
        *,
        evaluated_market_ids: Iterable[str],
        active_opportunity_keys: Iterable[str],
        observed_at: datetime | None = None,
    ) -> tuple[LifecycleChange, ...]:
        market_ids = tuple(dict.fromkeys(str(value) for value in evaluated_market_ids))
        if not market_ids:
            return ()
        active_keys = tuple(dict.fromkeys(str(value) for value in active_opportunity_keys))
        current = _utc(observed_at or datetime.now(UTC))
        exclusion = "and not (o.opportunity_key = any(%s))" if active_keys else ""
        parameters: list[object] = [current, current, current, list(market_ids)]
        if active_keys:
            parameters.append(list(active_keys))
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                f"""
                update public.opportunities o set
                  status='invalidated',last_valid_at=coalesce(last_valid_at,%s),
                  expires_at=coalesce(expires_at,%s),
                  hard_gate_failures=case
                    when hard_gate_failures ? 'not_valid_in_latest_evaluation'
                    then hard_gate_failures
                    else hard_gate_failures || jsonb_build_array('not_valid_in_latest_evaluation')
                  end,updated_at=%s
                where o.status in ('active','stale')
                  and exists (
                    select 1 from public.opportunity_legs leg
                     where leg.opportunity_id=o.id and leg.market_id=any(%s::uuid[])
                  )
                  {exclusion}
                returning o.id::text,o.opportunity_key,o.status,o.total_allocation,
                          o.gross_profit,o.worst_case_net_profit,o.net_roi,
                          o.maximum_depth,o.execution_confidence
                """,
                tuple(parameters),
            )
            rows = cursor.fetchall()
            self._record_lifecycle_snapshots(
                cursor, rows, observed_at=current, reason="not_valid_in_latest_evaluation"
            )
            self._withdraw_alert_events(
                cursor,
                tuple(str(row[0]) for row in rows),
                current,
                "not_valid_in_latest_evaluation",
            )
        return tuple(LifecycleChange(str(row[0]), str(row[1]), row[2]) for row in rows)

    def withdraw_provider_opportunities(
        self,
        provider_codes: Iterable[str],
        *,
        now: datetime | None = None,
        reason: str = "provider_disconnected",
    ) -> tuple[LifecycleChange, ...]:
        """Immediately withdraw opportunities exposed to a failed provider."""

        codes = tuple(dict.fromkeys(str(value).strip() for value in provider_codes if str(value).strip()))
        if not codes:
            return ()
        if not reason.strip() or len(reason) > 160:
            raise ValueError("withdrawal reason must be between 1 and 160 characters")
        current = _utc(now or datetime.now(UTC))
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                update public.opportunities o set
                  status='stale',last_valid_at=coalesce(last_valid_at,%s),
                  expires_at=coalesce(expires_at,%s),
                  hard_gate_failures=case when hard_gate_failures ? %s
                    then hard_gate_failures else hard_gate_failures || jsonb_build_array(%s::text) end,
                  updated_at=%s
                 where o.status in ('active','stale') and exists (
                   select 1 from public.opportunity_legs leg
                   join public.providers provider on provider.id=leg.provider_id
                    where leg.opportunity_id=o.id and provider.slug=any(%s)
                 )
                returning o.id::text,o.opportunity_key,o.status,o.total_allocation,
                          o.gross_profit,o.worst_case_net_profit,o.net_roi,
                          o.maximum_depth,o.execution_confidence
                """,
                (current, current, reason, reason, current, list(codes)),
            )
            rows = cursor.fetchall()
            self._record_lifecycle_snapshots(
                cursor, rows, observed_at=current, reason=reason
            )
            self._withdraw_alert_events(
                cursor, tuple(str(row[0]) for row in rows), current, reason
            )
        return tuple(LifecycleChange(str(row[0]), str(row[1]), row[2]) for row in rows)

    def expire_stale_opportunities(
        self, *, now: datetime | None = None
    ) -> tuple[LifecycleChange, ...]:
        current = _utc(now or datetime.now(UTC))
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                update public.opportunities o set
                  status=case
                    when exists (
                      select 1 from public.opportunity_legs leg
                      join public.markets market on market.id=leg.market_id
                       where leg.opportunity_id=o.id
                         and (market.status <> 'open' or market.closes_at <= %s)
                    ) then 'expired'
                    else 'stale'
                  end,
                  last_valid_at=coalesce(last_valid_at,%s),
                  expires_at=coalesce(expires_at,%s),updated_at=%s
                where o.status='active' and (
                  o.expires_at < %s or exists (
                    select 1 from public.opportunity_legs leg
                    left join public.quotes quote on quote.id=leg.quote_id
                     where leg.opportunity_id=o.id
                       and (quote.id is null or quote.stale_at is null or quote.stale_at < %s)
                  ) or exists (
                    select 1 from public.opportunity_legs leg
                    join public.markets market on market.id=leg.market_id
                     where leg.opportunity_id=o.id
                       and (market.status <> 'open' or market.closes_at <= %s)
                  )
                )
                returning o.id::text,o.opportunity_key,o.status,o.total_allocation,
                          o.gross_profit,o.worst_case_net_profit,o.net_roi,
                          o.maximum_depth,o.execution_confidence
                """,
                (current, current, current, current, current, current, current),
            )
            rows = cursor.fetchall()
            self._record_lifecycle_snapshots(
                cursor, rows, observed_at=current, reason="quote_or_market_expired"
            )
            self._withdraw_alert_events(
                cursor,
                tuple(str(row[0]) for row in rows),
                current,
                "quote_or_market_expired",
            )
        return tuple(LifecycleChange(str(row[0]), str(row[1]), row[2]) for row in rows)

    @staticmethod
    def _record_lifecycle_snapshots(
        cursor: Any,
        rows: Sequence[tuple[Any, ...]],
        *,
        observed_at: datetime,
        reason: str,
    ) -> None:
        for row in rows:
            ingestion_key = f"lifecycle:{row[0]}:{row[2]}:{observed_at.isoformat()}"
            cursor.execute(
                """
                insert into public.opportunity_snapshots
                  (opportunity_id,ingestion_key,observed_at,status,total_allocation,
                   gross_profit,net_profit,net_roi,available_liquidity,
                   execution_confidence,quote_ids,snapshot_data,is_demo)
                select %s,%s,%s,%s,%s,%s,%s,%s,%s,%s,
                       coalesce(array_agg(leg.quote_id) filter (where leg.quote_id is not null),'{}'),
                       %s::jsonb,false
                  from public.opportunity_legs leg where leg.opportunity_id=%s
                on conflict (ingestion_key) do nothing
                """,
                (
                    row[0],
                    ingestion_key,
                    observed_at,
                    row[2],
                    row[3],
                    row[4],
                    row[5],
                    row[6],
                    row[7],
                    row[8],
                    _json({"lifecycle_reason": reason}),
                    row[0],
                ),
            )

    @staticmethod
    def _withdraw_alert_events(
        cursor: Any,
        opportunity_ids: Sequence[str],
        observed_at: datetime,
        reason: str,
    ) -> None:
        if not opportunity_ids:
            return
        cursor.execute(
            """
            update public.user_alert_events
               set lifecycle_status='withdrawn',withdrawn_at=%s,
                   payload=payload || jsonb_build_object(
                     'withdrawn_at',%s::text,'withdrawal_reason',%s::text
                   )
             where opportunity_id=any(%s::uuid[]) and lifecycle_status='active'
            """,
            (observed_at, observed_at.isoformat(), reason, list(opportunity_ids)),
        )

    def evaluate_alerts(
        self,
        opportunity_ids: Iterable[str],
        *,
        now: datetime | None = None,
    ) -> AlertEvaluationResult:
        ids = tuple(dict.fromkeys(str(value) for value in opportunity_ids))
        if not ids:
            return AlertEvaluationResult(0, 0, 0)
        current = _utc(now or datetime.now(UTC))
        created = deduplicated = filtered = 0
        triggered_alerts: set[str] = set()
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                select o.id::text,o.opportunity_key,o.status,o.match_status,
                       o.worst_case_net_profit,o.net_roi,o.maximum_depth,o.expires_at,
                       min(q.observed_at),
                       array_agg(distinct p.slug),array_agg(distinct m.market_type),
                       coalesce(s.slug,pe.metadata->'normalized_event'->>'sport'),
                       coalesce(l.slug,pe.metadata->'normalized_event'->>'league'),
                       coalesce(e.name,pe.title,'Unknown event'),o.currency
                  from public.opportunities o
                  join public.opportunity_legs leg on leg.opportunity_id=o.id
                  join public.providers p on p.id=leg.provider_id
                  join public.markets m on m.id=leg.market_id
                  left join public.quotes q on q.id=leg.quote_id
                  left join public.events e on e.id=o.event_id
                  left join public.sports s on s.id=e.sport_id
                  left join public.leagues l on l.id=e.league_id
                  left join public.provider_events pe on pe.id=m.provider_event_id
                 where o.id=any(%s::uuid[]) and not o.is_demo
                 group by o.id,s.slug,l.slug,pe.metadata,pe.title,e.name
                 order by o.id
                """,
                (list(ids),),
            )
            opportunities = cursor.fetchall()
            cursor.execute(
                """
                select id::text,user_id::text,name,alert_type,conditions,
                       cooldown_seconds,last_triggered_at
                  from public.user_alerts where enabled for update
                """,
                (),
            )
            alerts = cursor.fetchall()
            for opportunity in opportunities:
                for alert in alerts:
                    if str(alert[0]) in triggered_alerts and int(alert[5]) > 0:
                        filtered += 1
                        continue
                    if not self._alert_matches(alert, opportunity, current):
                        filtered += 1
                        continue
                    dedupe_key = f"opportunity:{opportunity[1]}"
                    payload = {
                        "opportunity_key": opportunity[1],
                        "net_profit": str(opportunity[4]),
                        "net_roi": str(opportunity[5]) if opportunity[5] is not None else None,
                        "liquidity": str(opportunity[6]) if opportunity[6] is not None else None,
                        "quote_observed_at": (
                            opportunity[8].isoformat() if opportunity[8] is not None else None
                        ),
                        "providers": list(opportunity[9] or ()),
                        "market_types": list(opportunity[10] or ()),
                        "sport": opportunity[11],
                        "league": opportunity[12],
                        "lifecycle_status": "active",
                    }
                    cursor.execute(
                        """
                        insert into public.user_alert_events
                          (user_id,alert_id,opportunity_id,dedupe_key,title,body,
                           severity,payload,lifecycle_status,triggered_at)
                        values (%s,%s,%s,%s,%s,%s,'info',%s::jsonb,'active',%s)
                        on conflict (alert_id,dedupe_key) do update set
                          title=excluded.title,body=excluded.body,payload=excluded.payload,
                          lifecycle_status='active',withdrawn_at=null,
                          triggered_at=excluded.triggered_at,read_at=null,dismissed_at=null
                        where user_alert_events.lifecycle_status='withdrawn'
                        returning id::text
                        """,
                        (
                            alert[1],
                            alert[0],
                            opportunity[0],
                            dedupe_key,
                            f"{alert[2]}: {opportunity[13]}",
                            (
                                f"Verified research opportunity: {opportunity[4]} "
                                f"{opportunity[14]} conservative modeled net profit."
                            ),
                            _json(payload),
                            current,
                        ),
                    )
                    event = cursor.fetchone()
                    if event is None:
                        deduplicated += 1
                        continue
                    created += 1
                    triggered_alerts.add(str(alert[0]))
                    cursor.execute(
                        "update public.user_alerts set last_triggered_at=%s,updated_at=%s where id=%s",
                        (current, current, alert[0]),
                    )
        return AlertEvaluationResult(created, deduplicated, filtered)

    @staticmethod
    def _alert_matches(
        alert: tuple[Any, ...], opportunity: tuple[Any, ...], current: datetime
    ) -> bool:
        alert_type = str(alert[3])
        conditions = _mapping(alert[4])
        if opportunity[2] != "active" or opportunity[3] != "verified":
            return False
        if opportunity[7] is not None and opportunity[7] <= current:
            return False
        if alert_type not in {"new_verified_opportunity", "roi_threshold"}:
            return False
        cooldown = int(alert[5])
        if alert[6] is not None and (current - alert[6]).total_seconds() < cooldown:
            return False

        def minimum(key: str, actual: object, *, legacy: bool = False) -> bool:
            configured = conditions.get(key)
            if configured is None and legacy:
                configured = conditions.get("threshold")
            if configured is None:
                return True
            expected = _decimal_or_none(configured)
            observed = _decimal_or_none(actual)
            return expected is not None and observed is not None and observed >= expected

        if not minimum("minimum_net_profit", opportunity[4]):
            return False
        if not minimum("minimum_net_roi", opportunity[5], legacy=alert_type == "roi_threshold"):
            return False
        if not minimum("minimum_liquidity", opportunity[6]):
            return False
        maximum_age = conditions.get("maximum_quote_age_seconds")
        if maximum_age is not None:
            seconds = _decimal_or_none(maximum_age)
            quote_at = opportunity[8]
            if seconds is None or quote_at is None:
                return False
            if Decimal(str(max(0.0, (current - quote_at).total_seconds()))) > seconds:
                return False

        def allowed(key: str, values: Sequence[object]) -> bool:
            configured = conditions.get(key)
            if configured is None:
                return True
            requested = (
                {normalize_name(str(value)) for value in configured}
                if isinstance(configured, list)
                else {normalize_name(str(configured))}
            )
            available = {normalize_name(str(value)) for value in values if value is not None}
            return bool(requested & available)

        return (
            allowed("providers", tuple(opportunity[9] or ()))
            and allowed("market_types", tuple(opportunity[10] or ()))
            and allowed("sports", (opportunity[11],))
            and allowed("leagues", (opportunity[12],))
        )


def to_engine_snapshots(
    records: Iterable[NormalizedMarketRecord],
) -> tuple[tuple[MarketSnapshot, ...], tuple[MarketLoadIssue, ...]]:
    """Convert DB evidence into the pure engine contract without substitutions."""

    snapshots: list[MarketSnapshot] = []
    issues: list[MarketLoadIssue] = []
    for record in records:
        try:
            snapshot = _to_engine_snapshot(record)
        except ValueError as exc:
            issues.append(MarketLoadIssue(record.market_id, str(exc)))
            continue
        snapshots.append(snapshot)
    return tuple(snapshots), tuple(issues)


def _to_engine_snapshot(record: NormalizedMarketRecord) -> MarketSnapshot:
    if record.scheduled_start is None:
        raise ValueError("event_start_time_missing")
    event_evidence = _mapping(record.event_metadata.get("normalized_event"))
    sport = _text_or_none(event_evidence.get("sport")) or ""
    league = _text_or_none(event_evidence.get("league")) or ""
    home = _text_or_none(event_evidence.get("home_team"))
    away = _text_or_none(event_evidence.get("away_team"))
    missing_identity = tuple(
        name
        for name, value in (
            ("sport", sport),
            ("league", league),
            ("home_team", home),
            ("away_team", away),
        )
        if not value
    )
    if missing_identity:
        raise ValueError(f"event_identity_missing:{','.join(missing_identity)}")
    settlement = _mapping(record.metadata.get("settlement"))
    rule_meta = record.rule.metadata
    rules = SettlementRules(
        period=_text_or_none(record.period),
        overtime_included=record.rule.includes_overtime,
        shootout_included=_bool_or_none(rule_meta.get("shootout_included")),
        tie_treatment=_text_or_none(record.rule.tie_treatment),
        draw_treatment=_text_or_none(rule_meta.get("draw_treatment")),
        push_treatment=_text_or_none(record.rule.push_treatment),
        void_treatment=_text_or_none(record.rule.void_treatment),
        cancellation_treatment=_text_or_none(record.rule.cancellation_treatment),
        postponement_treatment=_text_or_none(rule_meta.get("postponement_treatment")),
        retirement_treatment=_text_or_none(rule_meta.get("retirement_treatment")),
        abandonment_treatment=_text_or_none(rule_meta.get("abandonment_treatment")),
        outcome_structure=_outcome_structure(record),
        threshold=record.line,
        line_unit=_text_or_none(rule_meta.get("line_unit")),
        resolution_source=(
            _text_or_none(record.rule.settlement_source)
            or _text_or_none(settlement.get("resolution_source"))
            or _text_or_none(event_evidence.get("resolution_source"))
        ),
        rules_text=(
            _text_or_none(record.rule.rules_text)
            or _joined_rules_text(settlement)
        ),
    )
    outcome_keys = tuple(item.canonical_key for item in record.outcomes)
    canonical = CanonicalMarket(
        market_id=record.market_id,
        event=CanonicalEvent(
            event_id=record.semantic_event_id,
            sport=sport,
            league=league,
            home_team=home,
            away_team=away,
            scheduled_start=record.scheduled_start,
        ),
        provider=_provider_identity(record),
        provider_market_id=record.external_market_id,
        market_type=record.market_type,
        outcome_ids=outcome_keys,
        rules=rules,
        source_url=record.source_url,
        proposition_id=_text_or_none(record.metadata.get("canonical_proposition_id")),
    )
    scenario_ids = list(outcome_keys)
    scenario_treatments = (
        ("push", rules.push_treatment),
        ("void", rules.void_treatment),
        ("cancellation", rules.cancellation_treatment),
        ("postponement", rules.postponement_treatment),
        ("retirement", rules.retirement_treatment),
        ("abandonment", rules.abandonment_treatment),
    )
    for name, treatment in scenario_treatments:
        if treatment in {"refund", "loss"} and name not in scenario_ids:
            scenario_ids.append(name)
        elif treatment in {"half_win", "half_loss", "regrade"}:
            raise ValueError(f"unsupported_{name}_cashflow:{treatment}")
    supported_scenario_treatments = {
        "refund", "loss", "not_applicable", "action", "reschedule", "official_result", "official result",
    }
    scenario_completeness_verified = (
        record.mutually_exclusive is True
        and record.collectively_exhaustive is True
        and record.rule.rule_id is not None
        and all(
            treatment in supported_scenario_treatments
            for _, treatment in scenario_treatments
        )
    )
    execution_blockers: list[str] = []
    if record.provider_status != "connected":
        execution_blockers.append(
            f"provider_health_{record.provider_status or 'unknown'}"
        )
    if record.metadata.get("periodically_refreshed_not_immediately_executable") is True:
        execution_blockers.append("provider_marks_quotes_indicative")
    if record.canonical_event_id is None:
        execution_blockers.append("canonical_event_not_materialized")
    if record.minimum_increment is None:
        execution_blockers.append("quantity_increment_unknown")
    fee = _fee_schedule(record.fee_schedule)
    offers: list[ExecutableOffer] = []
    for outcome in record.outcomes:
        if outcome.decimal_odds is not None and outcome.decimal_odds > 1:
            price_kind = PriceKind.DECIMAL_ODDS
            price = outcome.decimal_odds
            action: Literal["back", "buy_yes", "buy_no"] = "back"
            maximum = outcome.available_size
            winning_payout = outcome.decimal_odds
            refund_payout = Decimal("1")
            execution_basis: Literal[
                "orderbook_ask", "direct_sportsbook_offer", "indicative"
            ] = "indicative"
        elif outcome.best_ask is not None and Decimal("0") < outcome.best_ask <= Decimal("1"):
            # A last trade/probability display is never promoted to an
            # executable prediction-market purchase.
            price_kind = PriceKind.CONTRACT_ASK
            price = outcome.best_ask
            action = "buy_no" if normalize_name(outcome.canonical_key) in {"no", "short"} else "buy_yes"
            maximum = outcome.ask_size
            winning_payout = Decimal("1")
            refund_payout = price
            execution_basis = "orderbook_ask"
        else:
            continue
        payouts = {scenario: Decimal("0") for scenario in scenario_ids}
        payouts[outcome.canonical_key] = winning_payout
        for scenario, treatment in scenario_treatments:
            if scenario in payouts and treatment == "refund":
                payouts[scenario] = refund_payout
        increment = record.minimum_increment or Decimal("0.01")
        minimum = _decimal_or_none(record.metadata.get("minimum_trade_quantity")) or Decimal("0")
        offers.append(
            ExecutableOffer(
                offer=Offer(
                    offer_id=outcome.quote_id,
                    provider=_provider_identity(record),
                    outcome_id=outcome.canonical_key,
                    price_kind=price_kind,
                    price=price,
                    quote_timestamp=outcome.observed_at,
                    fee_schedule=fee,
                    currency=outcome.currency,
                    minimum_quantity=minimum,
                    quantity_increment=increment,
                    maximum_quantity=maximum,
                    source_url=record.source_url,
                    data_origin=(
                        "live_stream" if outcome.feed_class == "streaming" else "live_poll"
                    ),
                ),
                source_market_id=record.market_id,
                action=action,
                execution_basis=execution_basis,
                provider_timestamp_known=True,
                payout_per_unit=payouts,
            )
        )
    return MarketSnapshot(
        market=canonical,
        offers=tuple(offers),
        scenario_ids=tuple(scenario_ids),
        scenario_completeness_verified=scenario_completeness_verified,
        execution_blockers=tuple(dict.fromkeys(execution_blockers)),
    )


def _provider_identity(record: NormalizedMarketRecord) -> str:
    bookmaker = _text_or_none(record.metadata.get("bookmaker_key"))
    return f"{record.provider_slug}:{bookmaker}" if bookmaker else record.provider_slug


def _outcome_structure(record: NormalizedMarketRecord) -> str | None:
    if record.mutually_exclusive is True and record.collectively_exhaustive is True:
        return "mutually_exclusive_collectively_exhaustive"
    return _text_or_none(record.rule.metadata.get("outcome_structure"))


def _joined_rules_text(settlement: Mapping[str, Any]) -> str | None:
    parts = tuple(
        text
        for text in (
            _text_or_none(settlement.get("primary_rules")),
            _text_or_none(settlement.get("secondary_rules")),
        )
        if text is not None
    )
    return "\n".join(parts) if parts else None


def _fee_schedule(record: FeeScheduleRecord | None) -> FeeSchedule | None:
    if record is None or record.verified_at is None or not record.source_url:
        return None
    parameters = record.parameters
    method = record.calculation_method
    proportional = Decimal("0")
    per_contract = Decimal("0")
    fixed = Decimal("0")
    if record.rounding_mode not in {"ceiling", "floor", "half_up", "half_even"}:
        return None
    # A missing optional coefficient is zero only when the selected fee method
    # permits it. A present malformed value is never evidence of a zero fee.
    parsed: dict[str, Decimal] = {}
    for key in ("rate", "fixed_per_order", "per_contract", "rounding_increment"):
        if key in parameters:
            value = _decimal_or_none(parameters[key])
            if value is None or not value.is_finite() or value < 0:
                return None
            parsed[key] = value
    if "rounding_increment" in parsed and parsed["rounding_increment"] <= 0:
        return None
    supported = method == "none" and not any(
        parsed.get(key, Decimal("0")) for key in ("rate", "fixed_per_order", "per_contract")
    )
    if method == "percentage_notional":
        proportional = parsed.get("rate", Decimal("0"))
        supported = (
            "rate" in parsed and "rounding_increment" in parsed
            and not parsed.get("fixed_per_order") and not parsed.get("per_contract")
        )
    elif method == "flat":
        fixed = parsed.get("fixed_per_order", Decimal("0"))
        per_contract = parsed.get("per_contract", Decimal("0"))
        supported = (
            ("fixed_per_order" in parsed or "per_contract" in parsed)
            and "rounding_increment" in parsed
            and not parsed.get("rate")
        )
    if not supported:
        return None
    rounding = parsed.get("rounding_increment", Decimal("0.01"))
    return FeeSchedule(
        schedule_id=record.schedule_id,
        provider=record.provider,
        effective_from=record.effective_from.date(),
        effective_to=record.effective_to.date() if record.effective_to else None,
        proportional_rate=proportional,
        per_contract=per_contract,
        fixed_per_order=fixed,
        rounding_increment=rounding,
        known=True,
        source_url=record.source_url,
        rounding_mode=record.rounding_mode,
    )


def _parse_datetime(value: object) -> datetime | None:
    if isinstance(value, datetime):
        return _utc(value)
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return _utc(parsed) if parsed.tzinfo is not None else None


def match_write_from_decision(
    *,
    left_market_id: str,
    right_market_id: str,
    decision: MatchDecision,
    source_evidence: Mapping[str, object],
) -> MatchWrite:
    checks = {
        "reasons": decision.reasons,
        "mismatches": decision.mismatches,
        "unknown_fields": decision.unknown_fields,
        "complete_outcome_coverage": "outcome" not in " ".join(decision.mismatches),
        "all_material_rules_explicit": not decision.unknown_fields,
    }
    fingerprint_payload = {
        "status": decision.status.value,
        "checks": checks,
        "left": decision.left_material_fingerprint,
        "right": decision.right_material_fingerprint,
        "engine": decision.engine_version,
    }
    explanation = "; ".join((*decision.reasons, *decision.mismatches)) or "No match evidence supplied."
    return MatchWrite(
        left_market_id=left_market_id,
        right_market_id=right_market_id,
        classification=decision.status.value,
        candidate_score=decision.candidate_score,
        settlement_confidence=(
            Decimal("1") if decision.status is MatchStatus.VERIFIED else Decimal("0")
        ),
        equivalence_checks=checks,
        explanation=explanation,
        engine_version=decision.engine_version,
        left_rule_fingerprint=decision.left_material_fingerprint,
        right_rule_fingerprint=decision.right_material_fingerprint,
        decision_fingerprint=hashlib.sha256(_json(fingerprint_payload).encode()).hexdigest(),
        source_evidence=source_evidence,
    )
