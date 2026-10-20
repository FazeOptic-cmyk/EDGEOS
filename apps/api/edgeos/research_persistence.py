"""Read models for the durable scanner and settlement-review queue.

All SQL sort keys are selected from fixed allowlists and all user input is bound
as parameters.  The repository deliberately returns ``None`` for unavailable
quote age and depth instead of manufacturing freshness or executable size.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, Literal


@dataclass(frozen=True, slots=True)
class OpportunityFilters:
    search: str | None = None
    sport: str | None = None
    league: str | None = None
    provider: str | None = None
    market_type: str | None = None
    minimum_net_roi: Decimal | None = None
    minimum_theoretical_profit: Decimal | None = None
    minimum_liquidity: Decimal | None = None
    maximum_quote_age: int | None = None
    verified_only: bool = True
    phase: Literal["prematch", "live", "all"] = "all"
    sort: str = "research_score"
    descending: bool = True
    page: int = 1
    limit: int = 50


_SORT_SQL = {
    "theoretical_roi": "o.gross_roi",
    "theoretical_profit": "o.worst_case_net_profit",
    # Freshness risk is quote age, not the quote timestamp itself. The scanner
    # requests this sort ascending so the newest complete bundle appears first.
    "execution_risk": (
        "case when latest.latest_quote_at is null then null "
        "else extract(epoch from (statement_timestamp() - latest.latest_quote_at)) end"
    ),
    "match_confidence": "o.execution_confidence",
    "liquidity": "o.maximum_depth",
    "quote_time": "latest_quote_at",
    "research_score": "o.research_score",
    "net_roi": "o.net_roi",
}

# The current cash-flow and paper-ledger contract supports sportsbook backs and
# purchases of binary contracts.  Lay and short-sale records remain valid
# future schema values, but require collateral/margin accounting that is not
# implemented yet.  Keep them out of the strict scanner rather than
# misrepresenting an executable bid as an ask or an unbounded liability as a
# cash stake.
_SUPPORTED_SCANNER_ACTIONS = ("back", "buy_yes", "buy_no")


class PostgresResearchRepository:
    """Server-only durable public research repository."""

    def __init__(self, connection_factory: Callable[[], Any]) -> None:
        self._connect = connection_factory

    def list_provider_health(self) -> tuple[dict[str, Any], ...]:
        """Read worker telemetry without issuing an upstream provider request."""

        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                select p.slug,p.display_name,
                       coalesce(c.status,p.availability_status),p.feed_type,
                       c.last_success_at,c.last_attempt_at,
                       coalesce(c.last_error_summary,c.last_error_code),
                       p.refresh_interval_seconds,p.data_delay_seconds,
                       p.data_delay_known,c.quota_limit,c.quota_remaining,
                       c.quota_resets_at,c.rate_limit_state,c.active_instruments,
                       c.diagnostics,p.metadata,p.requires_api_key
                  from public.providers p
                  left join public.provider_connections c on c.provider_id=p.id
                 where p.is_enabled
                 order by p.display_name,p.slug
                """
            )
            rows = cursor.fetchall()
        providers: list[dict[str, Any]] = []
        for row in rows:
            diagnostics = row[15] if isinstance(row[15], Mapping) else {}
            metadata = row[16] if isinstance(row[16], Mapping) else {}
            status = str(row[2])
            if status == "requires_configuration":
                status = "requires_api_key" if row[17] else "unknown"
            coverage = metadata.get("product_scope") or metadata.get("coverage") or []
            measured_delay = diagnostics.get("measured_data_delay_seconds")
            providers.append(
                {
                    "id": str(row[0]),
                    "name": str(row[1]),
                    "status": status,
                    "feed_type": str(row[3]),
                    "last_successful_update": row[4],
                    "checked_at": row[5],
                    "last_error": row[6],
                    "known_refresh_interval_seconds": row[7],
                    "measured_data_delay_seconds": (
                        measured_delay
                        if isinstance(measured_delay, (int, float))
                        and not isinstance(measured_delay, bool)
                        else row[8] if row[9] else None
                    ),
                    "api_quota_usage": {
                        "limit": row[10],
                        "remaining": row[11],
                        "reset_at": row[12],
                    },
                    "rate_limit_status": row[13] or "unknown",
                    "active_instruments": row[14],
                    "market_coverage": coverage,
                    "message": (
                        "Persisted worker telemetry; this request did not contact the provider."
                        if row[5] is not None
                        else "No worker health observation has been persisted yet."
                    ),
                }
            )
        return tuple(providers)

    def list_opportunities(
        self, filters: OpportunityFilters, *, now: datetime | None = None
    ) -> tuple[tuple[dict[str, Any], ...], int]:
        if filters.sort not in _SORT_SQL:
            raise ValueError("unsupported opportunity sort")
        if filters.page < 1 or not 1 <= filters.limit <= 200:
            raise ValueError("invalid pagination")
        current = (now or datetime.now(UTC)).astimezone(UTC)
        clauses = [
            "o.status = 'active'" if filters.verified_only else "o.status in ('active', 'stale')",
            "not o.is_demo",
            (
                "not exists (select 1 from public.opportunity_legs unsupported "
                "where unsupported.opportunity_id = o.id "
                "and unsupported.action not in ('back', 'buy_yes', 'buy_no'))"
            ),
        ]
        parameters: list[object] = []

        def condition(sql: str, value: object | None) -> None:
            if value is not None:
                clauses.append(sql)
                parameters.append(value)

        if filters.search and filters.search.strip():
            clauses.append("(e.name ilike %s or markets.market_titles ilike %s)")
            term = f"%{filters.search.strip()}%"
            parameters.extend((term, term))
        if filters.sport is not None:
            clauses.append("(lower(s.slug) = lower(%s) or lower(s.name) = lower(%s))")
            parameters.extend((filters.sport, filters.sport))
        if filters.league is not None:
            clauses.append("(lower(l.slug) = lower(%s) or lower(l.name) = lower(%s))")
            parameters.extend((filters.league, filters.league))
        if filters.provider is not None:
            clauses.append(
                "exists (select 1 from unnest(markets.provider_slugs || markets.provider_names) "
                "as provider(value) where lower(provider.value) = lower(%s))"
            )
            parameters.append(filters.provider)
        condition("%s = any(markets.market_types)", filters.market_type)
        condition("o.net_roi >= %s", filters.minimum_net_roi)
        condition("o.worst_case_net_profit >= %s", filters.minimum_theoretical_profit)
        if filters.minimum_liquidity is not None:
            # Unknown depth cannot satisfy a minimum executable-size filter.
            clauses.append("o.maximum_depth is not null and o.maximum_depth >= %s")
            parameters.append(filters.minimum_liquidity)
        if filters.maximum_quote_age is not None:
            # Unknown provider timestamps cannot satisfy a freshness filter.
            clauses.append("latest.latest_quote_at is not null and latest.latest_quote_at >= %s")
            parameters.append(current.timestamp() - filters.maximum_quote_age)
            clauses[-1] = "latest.latest_quote_at is not null and latest.latest_quote_at >= to_timestamp(%s)"
        if filters.verified_only:
            clauses.append("o.match_status = 'verified'")
            clauses.append("e.scheduled_start is not null")
        if filters.phase == "live":
            clauses.append("markets.is_live")
        elif filters.phase == "prematch":
            clauses.append("not markets.is_live")

        order = "desc" if filters.descending else "asc"
        nulls = "nulls last"
        offset = (filters.page - 1) * filters.limit
        sql = f"""
            select o.id::text, e.name, s.slug, coalesce(l.name, 'Unknown league'),
                   coalesce(markets.market_titles, 'Unknown market'),
                   markets.provider_names, markets.prices,
                   o.gross_roi, o.net_roi, o.total_allocation,
                   o.worst_case_net_profit, o.maximum_depth, o.detected_at,
                   latest.latest_quote_at, latest.feed_class, e.scheduled_start,
                   markets.is_live, o.match_status, o.research_score,
                   o.assumptions, o.currency, o.status, o.expires_at,
                   count(*) over() as full_count
              from public.opportunities o
              left join public.events e on e.id = o.event_id
              left join public.sports s on s.id = e.sport_id
              left join public.leagues l on l.id = e.league_id
              cross join lateral (
                select string_agg(distinct m.title, ' / ') as market_titles,
                       array_agg(distinct m.market_type) as market_types,
                       array_agg(distinct p.slug) as provider_slugs,
                       array_agg(
                         coalesce(m.metadata->>'bookmaker_title', p.display_name)
                         order by ol.leg_number
                       ) as provider_names,
                       array_agg(
                         case when ol.action in ('buy_yes', 'buy_no')
                              then 1 / nullif(ol.price, 0) else ol.price end
                         order by ol.leg_number
                       ) as prices,
                       bool_or(m.live) as is_live
                  from public.opportunity_legs ol
                  join public.providers p on p.id = ol.provider_id
                  join public.markets m on m.id = ol.market_id
                 where ol.opportunity_id = o.id
              ) markets
              cross join lateral (
                select min(q.observed_at) as latest_quote_at,
                       case
                         when count(q.id) = 0 then 'unknown'
                         when bool_or(q.feed_class = 'cached') then 'cache'
                         when bool_or(q.feed_class = 'historical') then 'historical'
                         else 'live_poll'
                       end as feed_class
                  from public.opportunity_legs ol
                  left join public.quotes q on q.id = ol.quote_id
                 where ol.opportunity_id = o.id
              ) latest
             where {' and '.join(clauses)}
             order by {_SORT_SQL[filters.sort]} {order} {nulls}, o.detected_at desc, o.id
             limit %s offset %s
        """
        parameters.extend((filters.limit, offset))
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(sql, tuple(parameters))
            rows = cursor.fetchall()
        if not rows:
            return (), 0
        total = int(rows[0][23])
        return tuple(self._opportunity_list_row(row, current) for row in rows), total

    @staticmethod
    def _opportunity_list_row(row: tuple[Any, ...], current: datetime) -> dict[str, Any]:
        provider_names = tuple(row[5] or ())
        prices = tuple(row[6] or ())
        latest_quote_at = row[13]
        quote_age: Decimal | None = None
        if isinstance(latest_quote_at, datetime):
            quote_age = Decimal(str(max(0.0, (current - latest_quote_at.astimezone(UTC)).total_seconds())))
        assumptions = row[19] if isinstance(row[19], Mapping) else {}
        source_urls = assumptions.get("source_urls") if isinstance(assumptions, Mapping) else None
        source_url = source_urls[0] if isinstance(source_urls, list) and source_urls else None
        return {
            "id": row[0],
            "event": row[1] or "Unknown event",
            "sport": row[2] or "unknown",
            "league": row[3],
            "market": row[4],
            "market_type": assumptions.get("market_type", "unknown"),
            "platform_a": provider_names[0] if provider_names else "Unknown provider",
            "platform_b": provider_names[1] if len(provider_names) > 1 else "Unknown provider",
            "prices": prices,
            "gross_roi": row[7],
            "net_roi": row[8],
            "total_allocation": row[9],
            "potential_net_profit": row[10],
            "available_liquidity": row[11],
            "observed_at": row[12],
            "provider_timestamp": latest_quote_at,
            "quote_age_seconds": quote_age,
            "data_origin": row[14] or "live_poll",
            "event_start_time": row[15],
            "phase": "live" if row[16] else "prematch",
            "live": bool(row[16]),
            "settlement_status": row[17],
            "research_score": row[18],
            "currency": row[20],
            "source_url": source_url,
            "status": row[21],
            "expires_at": row[22],
            "is_demo": False,
        }

    def get_opportunity(self, opportunity_id: str) -> dict[str, Any] | None:
        """Return the exact persisted cash-flow snapshot used by the scanner."""
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                select o.id::text, e.name, s.slug, coalesce(l.name, 'Unknown league'),
                       o.strategy_type, o.total_allocation, o.gross_profit, o.total_fees,
                       o.worst_case_net_profit, o.best_case_net_profit, o.gross_roi,
                       o.net_roi, o.maximum_depth, o.detected_at, e.scheduled_start,
                       o.match_status, o.research_score, o.hard_gate_failures,
                       o.assumptions, mm.explanation, mm.equivalence_checks,
                       o.currency, o.status, o.expires_at
                  from public.opportunities o
                  left join public.events e on e.id = o.event_id
                  left join public.sports s on s.id = e.sport_id
                  left join public.leagues l on l.id = e.league_id
                  left join public.market_matches mm on mm.id = o.market_match_id
                 where o.id = %s and not o.is_demo
                   and not exists (
                       select 1 from public.opportunity_legs unsupported
                        where unsupported.opportunity_id = o.id
                          and unsupported.action not in ('back', 'buy_yes', 'buy_no')
                   )
                """,
                (opportunity_id,),
            )
            row = cursor.fetchone()
            if row is None:
                return None
            cursor.execute(
                """
                select coalesce(m.metadata->>'bookmaker_title', p.display_name),
                       outc.name, ol.price, ol.quantity, ol.allocation,
                       ol.fee, ol.quote_observed_at, ol.available_size, m.live,
                       m.market_type, m.title, m.source_url, ol.action
                  from public.opportunity_legs ol
                  join public.providers p on p.id = ol.provider_id
                  join public.markets m on m.id = ol.market_id
                  join public.outcomes outc on outc.id = ol.outcome_id
                 where ol.opportunity_id = %s order by ol.leg_number
                """,
                (opportunity_id,),
            )
            legs = cursor.fetchall()
            # Defense in depth for repositories backed by a non-snapshot
            # connection or legacy data: never serialize a short/lay leg using
            # the buy/back DTO contract.
            if any(leg[12] not in _SUPPORTED_SCANNER_ACTIONS for leg in legs):
                return None
            cursor.execute(
                """
                select scenario_key, label, gross_payout, refunds, fees, net_profit
                  from public.opportunity_outcomes
                 where opportunity_id = %s order by scenario_key
                """,
                (opportunity_id,),
            )
            outcomes = cursor.fetchall()
            cursor.execute(
                """
                select observed_at, net_roi, net_profit, available_liquidity, status
                  from public.opportunity_snapshots
                 where opportunity_id = %s order by observed_at desc limit 500
                """,
                (opportunity_id,),
            )
            history = cursor.fetchall()

        assumptions = row[18] if isinstance(row[18], Mapping) else {}
        provider_names = [leg[0] for leg in legs]
        prices = [Decimal("1") / leg[2] if leg[12] in {"buy_yes", "buy_no"} else leg[2] for leg in legs]
        quote_times = [leg[6] for leg in legs if isinstance(leg[6], datetime)]
        # Scanner freshness is bounded by the oldest leg. A newer leg must not
        # conceal stale exposure on another provider.
        latest_quote = min(quote_times) if quote_times else None
        now = datetime.now(UTC)
        return {
            "id": row[0], "event": row[1] or "Unknown event", "sport": row[2] or "unknown",
            "league": row[3], "market": " / ".join(dict.fromkeys(str(leg[10]) for leg in legs)),
            "market_type": assumptions.get("market_type", legs[0][9] if legs else "unknown"),
            "platform_a": provider_names[0] if provider_names else "Unknown provider",
            "platform_b": provider_names[1] if len(provider_names) > 1 else "Unknown provider",
            "prices": prices, "gross_roi": row[10], "net_roi": row[11],
            "total_allocation": row[5], "potential_net_profit": row[8],
            "available_liquidity": row[12], "observed_at": row[13],
            "provider_timestamp": latest_quote,
            "quote_age_seconds": (
                Decimal(str(max(0.0, (now - latest_quote.astimezone(UTC)).total_seconds())))
                if latest_quote else None
            ),
            "data_origin": assumptions.get("data_origin", "live_poll"),
            "event_start_time": row[14], "phase": "live" if any(leg[8] for leg in legs) else "prematch",
            "live": any(leg[8] for leg in legs), "settlement_status": row[15],
            "research_score": row[16], "is_demo": False,
            "currency": row[21],
            "status": row[22], "expires_at": row[23],
            "source_url": next((leg[11] for leg in legs if leg[11]), None),
            "simulation": {
                "requested_allocation": row[5], "deployed_capital": row[5],
                "total_fees": row[7], "net_profit_worst_case": row[8],
                "net_profit_best_case": row[9],
                "legs": [
                    {"provider": leg[0], "outcome_id": leg[1], "requested_quantity": leg[3],
                     "filled_quantity": leg[3], "cash_cost": leg[4], "fees": leg[5],
                     "price_kind": (
                         "contract_ask"
                         if leg[12] in {"buy_yes", "buy_no"}
                         else "decimal_odds"
                     ),
                     "unit_cost": (
                         leg[2]
                         if leg[12] in {"buy_yes", "buy_no"}
                         else Decimal("1")
                     )}
                    for leg in legs
                ],
                "outcomes": [
                    {"outcome_id": outcome[0], "label": outcome[1], "gross_payout": outcome[2],
                     "refunds": outcome[3], "fees": outcome[4], "net_profit": outcome[5]}
                    for outcome in outcomes
                ],
                "gross_profit": row[6], "gross_roi": row[10], "net_roi": row[11],
                "calculation_version": assumptions.get("calculation_version", "unknown"),
                "currency": row[21],
            },
            "settlement_audit": {
                "status": row[15], "reasons": [row[19]] if row[19] else [],
                "checks": row[20] or {}, "scope": "persisted versioned match decision",
            },
            "failure_reasons": row[17] or [],
            "price_history": [
                {"observed_at": item[0], "net_roi": item[1], "net_profit": item[2],
                 "available_liquidity": item[3], "status": item[4]}
                for item in history
            ],
        }

    def list_match_reviews(self, *, limit: int = 100) -> tuple[dict[str, Any], ...]:
        if not 1 <= limit <= 200:
            raise ValueError("invalid review limit")
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                select mm.id::text, mm.classification, mm.candidate_score,
                       mm.settlement_confidence, mm.equivalence_checks, mm.explanation,
                       mm.engine_version, mm.decision_version, mm.left_rule_fingerprint,
                       mm.right_rule_fingerprint, mm.decision_fingerprint,
                       mm.decision_source_evidence, mm.invalidated_at, mm.invalidation_reason,
                       lm.title, lp.display_name, rm.title, rp.display_name,
                       coalesce((
                         select jsonb_agg(jsonb_build_object(
                           'id', mr.id, 'decision', mr.decision, 'reviewer_kind', mr.reviewer_kind,
                           'rationale', mr.rationale, 'reviewed_at', mr.reviewed_at,
                           'decision_version', mr.decision_version, 'source_evidence', mr.source_evidence
                         ) order by mr.decision_version desc)
                         from public.match_reviews mr where mr.market_match_id = mm.id
                       ), '[]'::jsonb)
                  from public.market_matches mm
                  join public.markets lm on lm.id = mm.left_market_id
                  join public.providers lp on lp.id = lm.provider_id
                  join public.markets rm on rm.id = mm.right_market_id
                  join public.providers rp on rp.id = rm.provider_id
                 where mm.classification = 'review_required' or mm.invalidated_at is not null
                 order by mm.updated_at desc limit %s
                """,
                (limit,),
            )
            rows = cursor.fetchall()
        return tuple({
            "id": row[0], "classification": row[1], "candidate_score": row[2],
            "settlement_confidence": row[3], "checks": row[4], "explanation": row[5],
            "engine_version": row[6], "decision_version": row[7],
            "left_rule_fingerprint": row[8], "right_rule_fingerprint": row[9],
            "decision_fingerprint": row[10], "source_evidence": row[11],
            "invalidated_at": row[12], "invalidation_reason": row[13],
            "left": {"market": row[14], "provider": row[15]},
            "right": {"market": row[16], "provider": row[17]}, "history": row[18],
        } for row in rows)

    def decide_match(
        self,
        *,
        match_id: str,
        reviewer_id: str,
        decision: Literal["verified", "review_required", "rejected"],
        rationale: str,
        checks: Mapping[str, object],
        source_evidence: Mapping[str, object],
        expected_version: int,
    ) -> dict[str, Any]:
        if not rationale.strip():
            raise ValueError("review rationale is required")
        if decision == "verified" and (
            not checks.get("complete_outcome_coverage")
            or not checks.get("all_material_rules_explicit")
            or not source_evidence
        ):
            raise ValueError("verified decisions require complete rule and source evidence")
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                select classification, decision_version, left_rule_fingerprint,
                       right_rule_fingerprint, decision_fingerprint
                  from public.market_matches where id = %s for update
                """,
                (match_id,),
            )
            current = cursor.fetchone()
            if current is None:
                raise KeyError(match_id)
            if int(current[1]) != expected_version:
                raise RuntimeError("review_version_conflict")
            next_version = expected_version + 1
            verified_at = datetime.now(UTC) if decision == "verified" else None
            cursor.execute(
                """
                update public.market_matches set classification = %s,
                       settlement_confidence = %s, verified_at = %s,
                       decision_version = %s, decision_source_evidence = %s::jsonb,
                       invalidated_at = null, invalidation_reason = null, updated_at = now()
                 where id = %s and decision_version = %s
                """,
                (decision, Decimal("1") if decision == "verified" else Decimal("0"),
                 verified_at, next_version, _json(source_evidence), match_id, expected_version),
            )
            if cursor.rowcount != 1:
                raise RuntimeError("review_version_conflict")
            cursor.execute(
                """
                update public.opportunities
                   set match_status = %s,
                       status = case
                         when %s = 'verified' then status
                         when status in ('active', 'stale') then 'invalidated'
                         else status
                       end,
                       last_valid_at = case
                         when %s = 'verified' then last_valid_at
                         else coalesce(last_valid_at, statement_timestamp())
                       end,
                       hard_gate_failures = case
                         when %s = 'verified' then hard_gate_failures
                         when hard_gate_failures ? ('settlement_decision_' || %s)
                           then hard_gate_failures
                         else hard_gate_failures
                           || jsonb_build_array('settlement_decision_' || %s)
                       end,
                       updated_at = now()
                 where market_match_id = %s
                """,
                (
                    decision,
                    decision,
                    decision,
                    decision,
                    decision,
                    decision,
                    match_id,
                ),
            )
            cursor.execute(
                """
                insert into public.match_reviews
                    (market_match_id, previous_classification, decision, reviewer_kind,
                     reviewer_id, checks, rationale, decision_version,
                     left_rule_fingerprint, right_rule_fingerprint, decision_fingerprint,
                     source_evidence)
                values (%s, %s, %s, 'operator', %s, %s::jsonb, %s, %s, %s, %s, %s, %s::jsonb)
                returning id::text, reviewed_at
                """,
                (match_id, current[0], decision, reviewer_id, _json(checks), rationale.strip(),
                 next_version, current[2], current[3], current[4], _json(source_evidence)),
            )
            review = cursor.fetchone()
        return {"id": review[0], "match_id": match_id, "decision": decision,
                "decision_version": next_version, "reviewed_at": review[1]}

    def list_saved_opportunities(self, user_id: str) -> tuple[dict[str, Any], ...]:
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                select opportunity_id::text, favorited, saved_at
                  from public.user_saved_opportunities
                 where user_id = %s order by saved_at desc
                """,
                (user_id,),
            )
            rows = cursor.fetchall()
        return tuple({"opportunity_id": row[0], "favorited": row[1], "saved_at": row[2]} for row in rows)

    def save_opportunity(
        self, user_id: str, opportunity_id: str, *, favorited: bool
    ) -> dict[str, Any]:
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                insert into public.user_saved_opportunities (user_id, opportunity_id, favorited)
                select %s, o.id, %s from public.opportunities o
                 where o.id = %s and not o.is_demo
                on conflict (user_id, opportunity_id) do update
                    set favorited = excluded.favorited
                returning opportunity_id::text, favorited, saved_at
                """,
                (user_id, favorited, opportunity_id),
            )
            row = cursor.fetchone()
            if row is None:
                raise KeyError(opportunity_id)
        return {"opportunity_id": row[0], "favorited": row[1], "saved_at": row[2]}

    def delete_saved_opportunity(self, user_id: str, opportunity_id: str) -> bool:
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                delete from public.user_saved_opportunities
                 where user_id = %s and opportunity_id = %s
                """,
                (user_id, opportunity_id),
            )
            return bool(cursor.rowcount)


def _json(value: Mapping[str, object]) -> str:
    import json

    return json.dumps(dict(value), sort_keys=True, separators=(",", ":"), default=str)
