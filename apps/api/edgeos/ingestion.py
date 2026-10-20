"""Durable, idempotent persistence for normalized provider snapshots.

Adapters currently expose their validated provider-neutral contract rather than
the complete upstream response body.  The raw-ingestion table therefore stores
an immutable *adapter capture* and labels that stage explicitly; it must not be
described as a byte-for-byte upstream archive.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass, is_dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from enum import Enum
from typing import Any, Callable, Iterable
from uuid import UUID, uuid4

from .normalization import identity_key, normalize_name
from .providers.models import (
    DataOrigin,
    NormalizedEvent,
    NormalizedMarket,
    NormalizedOutcome,
    PriceType,
    ProviderCode,
    ProviderHealth,
)


ADAPTER_SCHEMA_VERSION = "1"
_SLUG_UNSAFE = re.compile(r"[^a-z0-9]+")


def provider_reference_slug(provider: ProviderCode) -> str:
    """Map adapter identities to the stable provider reference row.

    The public/demo adapter route is named ``demo`` while the database row is
    intentionally namespaced as ``edgeos_demo`` so it cannot be mistaken for
    an external provider.  Keep that distinction at this persistence boundary.
    """
    return "edgeos_demo" if provider is ProviderCode.DEMO else provider.value


def _wire(value: Any) -> Any:
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, datetime):
        return value.astimezone(UTC).isoformat().replace("+00:00", "Z")
    if isinstance(value, Enum):
        return value.value
    if is_dataclass(value) and not isinstance(value, type):
        return _wire(asdict(value))
    if isinstance(value, dict):
        return {str(key): _wire(item) for key, item in value.items()}
    if isinstance(value, (tuple, list, set, frozenset)):
        return [_wire(item) for item in value]
    return value


def canonical_capture(value: NormalizedEvent | NormalizedMarket) -> tuple[str, str]:
    payload = json.dumps(
        {"capture_stage": "normalized_adapter_contract", "record": _wire(value)},
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    )
    return payload, hashlib.sha256(payload.encode()).hexdigest()


def _stable_key(*parts: object) -> str:
    return hashlib.sha256("\x00".join(str(part) for part in parts).encode()).hexdigest()


def _slug(value: str) -> str:
    normalized = normalize_name(value)
    slug = _SLUG_UNSAFE.sub("-", normalized).strip("-")
    return slug or f"unknown-{_stable_key(value)[:12]}"


def _canonical_event_key(event: NormalizedEvent) -> str | None:
    """Return an exact-evidence identity, never a fuzzy event merge."""

    values = (event.sport, event.league, event.home_team, event.away_team)
    if event.scheduled_start is None or any(not value or not value.strip() for value in values):
        return None
    if event.scheduled_start.tzinfo is None or event.scheduled_start.utcoffset() is None:
        return None
    return "event:" + _stable_key(
        *(identity_key(str(value)) for value in values),
        event.scheduled_start.astimezone(UTC).isoformat(),
    )


def _explicit_bool(metadata: dict[str, Any], *keys: str) -> bool | None:
    for key in keys:
        value = metadata.get(key)
        if isinstance(value, bool):
            return value
    return None


def _known_treatment(value: object, allowed: frozenset[str]) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    normalized = value.strip().casefold().replace("-", "_").replace(" ", "_")
    aliases = {
        "money_back": "refund",
        "stake_returned": "refund",
        "returned": "refund",
        "no_action": "refund",
        "not_applicable": "not_applicable",
        "n/a": "not_applicable",
    }
    normalized = aliases.get(normalized, normalized)
    return normalized if normalized in allowed else "unknown"


def _binary_contract_structure(market: NormalizedMarket) -> bool:
    if len(market.outcomes) != 2:
        return False
    if any(outcome.price_type is not PriceType.CONTRACT_DOLLARS for outcome in market.outcomes):
        return False
    sides = {normalize_name(outcome.side or "") for outcome in market.outcomes}
    return sides in ({"yes", "no"}, {"long", "short"})


def _outcome_key(market: NormalizedMarket, outcome: NormalizedOutcome, index: int) -> str:
    # Provider subtitles can describe the proposition identically on both
    # sides. The explicit binary side, not that display text, identifies the
    # complementary payoff within this native market. Cross-market proposition
    # equivalence remains a separate strict matching requirement.
    if _binary_contract_structure(market):
        return identity_key(outcome.side or "")
    return identity_key(outcome.name or outcome.side or f"outcome-{index}")


def _market_structure(market: NormalizedMarket) -> tuple[bool | None, bool | None]:
    metadata = dict(market.metadata)
    mutually_exclusive = _explicit_bool(metadata, "mutually_exclusive")
    collectively_exhaustive = _explicit_bool(metadata, "collectively_exhaustive")
    if _binary_contract_structure(market):
        return True, True
    return mutually_exclusive, collectively_exhaustive


def _market_line(market: NormalizedMarket) -> Decimal | None:
    values = {outcome.line for outcome in market.outcomes if outcome.line is not None}
    return next(iter(values)) if len(values) == 1 else None


def _feed_class(origin: DataOrigin) -> str:
    return {
        DataOrigin.LIVE_POLL: "polled",
        DataOrigin.CACHE: "cached",
        DataOrigin.HISTORICAL: "historical",
        DataOrigin.DEMO: "demo",
    }[origin]


def _event_status(value: str | None) -> str:
    normalized = (value or "").casefold()
    if normalized in {"scheduled", "open", "active", "initialized"}:
        return "scheduled"
    if normalized in {"live", "in_play", "in-play"}:
        return "live"
    if normalized in {"suspended", "paused"}:
        return "suspended"
    if normalized in {"final", "settled", "closed", "completed"}:
        return "final"
    if normalized in {"cancelled", "canceled"}:
        return "cancelled"
    if normalized == "postponed":
        return "postponed"
    return "unknown"


def _market_status(value: str | None) -> str:
    normalized = (value or "").casefold()
    if normalized in {"open", "active", "initialized", "scheduled"}:
        return "open"
    if normalized in {"suspended", "paused"}:
        return "suspended"
    if normalized in {"closed", "final"}:
        return "closed"
    if normalized in {"settled", "resolved"}:
        return "settled"
    if normalized in {"cancelled", "canceled", "void"}:
        return "cancelled"
    return "unknown"


@dataclass(frozen=True, slots=True)
class WriteCounts:
    received: int = 0
    created: int = 0
    updated: int = 0
    rejected: int = 0

    def __add__(self, other: "WriteCounts") -> "WriteCounts":
        return WriteCounts(
            self.received + other.received,
            self.created + other.created,
            self.updated + other.updated,
            self.rejected + other.rejected,
        )


class PostgresIngestionRepository:
    """Synchronous transaction boundary used from the single worker process."""

    def __init__(self, connect: Callable[[], Any]) -> None:
        self._connect = connect

    def acquire_lease(self, lease_key: str, holder_id: str, ttl_seconds: int) -> UUID | None:
        token = uuid4()
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                insert into public.ingestion_worker_leases
                  (lease_key, holder_id, lease_token, acquired_at, heartbeat_at, expires_at)
                values (%s, %s, %s, now(), now(), now() + (%s * interval '1 second'))
                on conflict (lease_key) do update set
                  holder_id = excluded.holder_id, lease_token = excluded.lease_token,
                  acquired_at = excluded.acquired_at, heartbeat_at = excluded.heartbeat_at,
                  expires_at = excluded.expires_at, released_at = null
                where ingestion_worker_leases.released_at is not null
                   or ingestion_worker_leases.expires_at <= now()
                returning lease_token
                """,
                (lease_key, holder_id, token, ttl_seconds),
            )
            row = cursor.fetchone()
            return UUID(str(row[0])) if row else None

    def heartbeat(self, lease_key: str, holder_id: str, token: UUID, ttl_seconds: int) -> bool:
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                update public.ingestion_worker_leases
                   set heartbeat_at = now(), expires_at = now() + (%s * interval '1 second')
                 where lease_key = %s and holder_id = %s and lease_token = %s
                   and released_at is null and expires_at > now()
                """,
                (ttl_seconds, lease_key, holder_id, token),
            )
            return bool(cursor.rowcount)

    def release(self, lease_key: str, holder_id: str, token: UUID) -> None:
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                """update public.ingestion_worker_leases set released_at = now()
                     where lease_key = %s and holder_id = %s and lease_token = %s
                       and released_at is null""",
                (lease_key, holder_id, token),
            )

    def start_run(
        self, provider: ProviderCode, *, run_key: str, adapter_version: str, worker_id: str,
        reserved_requests: int = 0,
    ) -> str:
        if reserved_requests < 0:
            raise ValueError("reserved_requests cannot be negative")
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                insert into public.ingestion_runs
                  (provider_id, run_key, endpoint_class, adapter_version, schema_version,
                   worker_id, heartbeat_at, request_count)
                select id, %s, 'events_and_markets', %s, %s, %s, now(), %s
                  from public.providers where slug = %s
                returning id
                """,
                (
                    run_key,
                    adapter_version,
                    ADAPTER_SCHEMA_VERSION,
                    worker_id,
                    reserved_requests,
                    provider_reference_slug(provider),
                ),
            )
            row = cursor.fetchone()
            if row is None:
                raise KeyError(f"provider reference is not configured: {provider.value}")
            return str(row[0])

    def remaining_request_budget(self, provider: ProviderCode, hourly_limit: int) -> int:
        """Count actual attempts and unfinished reservations across restarts."""
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                select coalesce(sum(run.request_count),0)
                  from public.ingestion_runs run
                  join public.providers provider on provider.id=run.provider_id
                 where provider.slug=%s
                   and run.started_at > now() - interval '1 hour'
                """,
                (provider_reference_slug(provider),),
            )
            return max(0, hourly_limit - int(cursor.fetchone()[0]))

    def persist_events(
        self,
        provider: ProviderCode,
        run_id: str,
        events: Iterable[NormalizedEvent],
        *,
        adapter_version: str,
    ) -> WriteCounts:
        count = WriteCounts()
        with self._connect() as connection, connection.cursor() as cursor:
            provider_id = self._provider_id(cursor, provider)
            for event in events:
                count += WriteCounts(received=1)
                payload, fingerprint = canonical_capture(event)
                metadata = dict(_wire(event.metadata))
                # Preserve provider-neutral identity evidence alongside the
                # searchable provider event row.  These fields remain evidence
                # for a later canonicalization/review step; their presence does
                # not itself assert that two provider events are equivalent.
                metadata["normalized_event"] = {
                    "subtitle": event.subtitle,
                    "sport": event.sport,
                    "league": event.league,
                    "home_team": event.home_team,
                    "away_team": event.away_team,
                    "resolution_source": event.resolution_source,
                    "provider_timestamp": _wire(event.provider_timestamp),
                }
                observed = event.provider_timestamp or event.retrieved_at
                raw_key = _stable_key("event", event.provider_event_id, observed, fingerprint)
                cursor.execute(
                    """
                    insert into public.raw_ingestion_records
                      (provider_id, ingestion_run_id, record_type, external_id,
                       idempotency_key, payload, payload_hash, observed_at, received_at,
                       validation_status, adapter_version, schema_version,
                       provider_observed_at, fetched_at, ingested_at)
                    values (%s,%s,'event',%s,%s,%s::jsonb,%s,%s,%s,'valid',%s,%s,%s,%s,now())
                    on conflict (provider_id, idempotency_key) do nothing
                    returning id
                    """,
                    (provider_id, run_id, event.provider_event_id, raw_key, payload, fingerprint,
                     observed, event.retrieved_at, adapter_version, ADAPTER_SCHEMA_VERSION,
                     event.provider_timestamp, event.retrieved_at),
                )
                created = cursor.fetchone() is not None
                cursor.execute(
                    """
                    insert into public.provider_events
                      (provider_id, external_event_id, title, scheduled_start, status,
                       source_url, raw_fingerprint, first_seen_at, last_seen_at, is_demo, metadata)
                    values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb)
                    on conflict (provider_id, external_event_id) do update set
                      title=excluded.title, scheduled_start=excluded.scheduled_start,
                      status=excluded.status, source_url=excluded.source_url,
                      raw_fingerprint=excluded.raw_fingerprint,
                      first_seen_at=least(provider_events.first_seen_at, excluded.first_seen_at),
                      last_seen_at=greatest(provider_events.last_seen_at, excluded.last_seen_at),
                      metadata=excluded.metadata
                    returning id
                    """,
                    (provider_id, event.provider_event_id, event.title, event.scheduled_start,
                     _event_status(event.status), event.source_url, fingerprint,
                     event.retrieved_at, event.retrieved_at,
                     event.data_origin is DataOrigin.DEMO,
                     json.dumps(metadata, sort_keys=True, separators=(",", ":"))),
                )
                provider_event_id = cursor.fetchone()[0]
                self._materialize_canonical_event(
                    cursor,
                    provider_id=provider_id,
                    provider_event_id=provider_event_id,
                    event=event,
                )
                count += WriteCounts(created=1 if created else 0, updated=0 if created else 1)
        return count

    @staticmethod
    def _materialize_canonical_event(
        cursor: Any,
        *,
        provider_id: object,
        provider_event_id: object,
        event: NormalizedEvent,
    ) -> object | None:
        """Materialize only exact, complete identity evidence.

        Fuzzy identity remains confined to candidate matching. Incomplete event
        metadata keeps ``canonical_event_id`` null and therefore cannot enter a
        verified scanner row.
        """

        canonical_key = _canonical_event_key(event)
        if canonical_key is None:
            return None
        assert event.sport is not None
        assert event.league is not None
        assert event.home_team is not None
        assert event.away_team is not None
        assert event.scheduled_start is not None
        cursor.execute(
            """
            insert into public.sports (slug,name)
            values (%s,%s)
            on conflict (slug) do update set active=true
            returning id
            """,
            (_slug(event.sport), event.sport.strip()),
        )
        sport_id = cursor.fetchone()[0]
        cursor.execute(
            """
            insert into public.leagues (sport_id,slug,name)
            values (%s,%s,%s)
            on conflict (sport_id,slug) do update set active=true
            returning id
            """,
            (sport_id, _slug(event.league), event.league.strip()),
        )
        league_id = cursor.fetchone()[0]

        participant_ids: list[object] = []
        for participant in (event.home_team, event.away_team):
            normalized = identity_key(participant)
            if not normalized:
                return None
            cursor.execute(
                """
                insert into public.participants
                  (sport_id,canonical_name,normalized_name,participant_type)
                values (%s,%s,%s,'team')
                on conflict (sport_id,participant_type,normalized_name) do update set
                  canonical_name=excluded.canonical_name
                returning id
                """,
                (sport_id, participant.strip(), normalized),
            )
            participant_id = cursor.fetchone()[0]
            participant_ids.append(participant_id)
            cursor.execute(
                """
                insert into public.participant_aliases
                  (participant_id,provider_id,alias,normalized_alias)
                values (%s,%s,%s,%s)
                on conflict do nothing
                """,
                (participant_id, provider_id, participant.strip(), normalized),
            )

        cursor.execute(
            """
            insert into public.events
              (canonical_key,sport_id,league_id,home_participant_id,
               away_participant_id,name,scheduled_start,status)
            values (%s,%s,%s,%s,%s,%s,%s,%s)
            on conflict (canonical_key) do update set
              name=excluded.name,status=excluded.status,
              scheduled_start=excluded.scheduled_start
            returning id
            """,
            (
                canonical_key,
                sport_id,
                league_id,
                participant_ids[0],
                participant_ids[1],
                event.title.strip(),
                event.scheduled_start.astimezone(UTC),
                _event_status(event.status),
            ),
        )
        canonical_event_id = cursor.fetchone()[0]
        cursor.execute(
            """
            update public.provider_events
               set canonical_event_id=%s
             where id=%s and provider_id=%s
            """,
            (canonical_event_id, provider_event_id, provider_id),
        )
        cursor.execute(
            """
            update public.markets
               set event_id=%s
             where provider_event_id=%s and provider_id=%s
            """,
            (canonical_event_id, provider_event_id, provider_id),
        )
        return canonical_event_id

    def persist_markets(
        self,
        provider: ProviderCode,
        run_id: str,
        markets: Iterable[NormalizedMarket],
        *,
        adapter_version: str,
        stale_after_seconds: int,
    ) -> WriteCounts:
        count = WriteCounts()
        with self._connect() as connection, connection.cursor() as cursor:
            provider_id = self._provider_id(cursor, provider)
            for market in markets:
                count += WriteCounts(received=1)
                try:
                    created = self._persist_market(
                        cursor, provider_id, run_id, market,
                        adapter_version=adapter_version,
                        stale_after_seconds=stale_after_seconds,
                    )
                    count += WriteCounts(created=1 if created else 0, updated=0 if created else 1)
                except (TypeError, ValueError):
                    count += WriteCounts(rejected=1)
        return count

    def _persist_market(
        self,
        cursor: Any,
        provider_id: object,
        run_id: str,
        market: NormalizedMarket,
        *,
        adapter_version: str,
        stale_after_seconds: int,
    ) -> bool:
        payload, fingerprint = canonical_capture(market)
        observed = market.quote_timestamp or market.retrieved_at
        raw_key = _stable_key("market", market.provider_market_id, observed, fingerprint)
        cursor.execute(
            """
            insert into public.raw_ingestion_records
              (provider_id, ingestion_run_id, record_type, external_id, idempotency_key,
               payload, payload_hash, observed_at, received_at, validation_status,
               adapter_version, schema_version, provider_observed_at, fetched_at, ingested_at)
            values (%s,%s,'market',%s,%s,%s::jsonb,%s,%s,%s,'valid',%s,%s,%s,%s,now())
            on conflict (provider_id, idempotency_key) do nothing returning id
            """,
            (provider_id, run_id, market.provider_market_id, raw_key, payload, fingerprint,
             observed, market.retrieved_at, adapter_version, ADAPTER_SCHEMA_VERSION,
             market.quote_timestamp, market.retrieved_at),
        )
        raw_created = cursor.fetchone() is not None
        metadata = dict(_wire(market.metadata))
        metadata["provider_quote_timestamp_known"] = market.quote_timestamp is not None
        metadata["provider_market_slug"] = market.provider_market_slug
        metadata["event_start_time"] = _wire(market.event_start_time)
        metadata["settlement"] = _wire(market.settlement)
        metadata["reported_liquidity"] = _wire(market.liquidity)
        mutually_exclusive, collectively_exhaustive = _market_structure(market)
        market_line = _market_line(market)
        minimum_increment = None
        # Quantity steps, minimum order quantities, and price ticks have
        # different units. A price tick or minimum size cannot prove the
        # contract increment and must never be used to size a simulated fill.
        for key in ("quantity_increment", "minimum_increment"):
            value = market.metadata.get(key)
            if value is not None:
                try:
                    candidate = Decimal(str(value))
                except (ValueError, ArithmeticError):
                    continue
                if candidate.is_finite() and candidate > 0:
                    minimum_increment = candidate
                    break
        if "minimum_trade_quantity" not in metadata and "minimum_order_size" in metadata:
            metadata["minimum_trade_quantity"] = metadata["minimum_order_size"]
        cursor.execute(
            """
            insert into public.markets
              (provider_id, provider_event_id, event_id, external_market_id, title,
               market_type, period, line, currency, status, live,
               mutually_exclusive, collectively_exhaustive, minimum_increment,
               closes_at, source_url, last_seen_at, is_demo, metadata)
            values (
              %s,
              (select id from public.provider_events where provider_id=%s and external_event_id=%s),
              (select canonical_event_id from public.provider_events
                where provider_id=%s and external_event_id=%s),
              %s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb)
            on conflict (provider_id, external_market_id) do update set
              provider_event_id=coalesce(excluded.provider_event_id, markets.provider_event_id),
              event_id=coalesce(excluded.event_id, markets.event_id),
              title=excluded.title, market_type=excluded.market_type, period=excluded.period,
              line=excluded.line,
              status=excluded.status, live=excluded.live, closes_at=excluded.closes_at,
              mutually_exclusive=excluded.mutually_exclusive,
              collectively_exhaustive=excluded.collectively_exhaustive,
              minimum_increment=excluded.minimum_increment,
              source_url=excluded.source_url,
              last_seen_at=greatest(markets.last_seen_at, excluded.last_seen_at),
              metadata=excluded.metadata
            returning id
            """,
            (
             provider_id, provider_id, market.provider_event_id,
             provider_id, market.provider_event_id, market.provider_market_id,
             market.title, market.market_type or "unknown", market.period or "unknown", market_line,
             next((outcome.currency for outcome in market.outcomes if outcome.currency), "USD"),
             _market_status(market.status), bool(market.metadata.get("live", False)),
             mutually_exclusive, collectively_exhaustive, minimum_increment,
             market.close_time, market.source_url, market.retrieved_at,
             market.data_origin is DataOrigin.DEMO,
             json.dumps(metadata, sort_keys=True, separators=(",", ":")),
            ),
        )
        market_id = cursor.fetchone()[0]
        self._persist_market_rule(cursor, market_id, market)
        for index, outcome in enumerate(market.outcomes):
            external_id = outcome.provider_outcome_id or f"{market.provider_market_id}:{index}"
            canonical_key = _outcome_key(market, outcome, index)
            payout = outcome.offered_price if outcome.price_type is PriceType.DECIMAL_ODDS else Decimal("1")
            if payout is None:
                payout = Decimal("0")
            cursor.execute(
                """
                insert into public.outcomes
                  (market_id, external_outcome_id, canonical_key, name, outcome_type, line, payout_per_unit)
                values (%s,%s,%s,%s,%s,%s,%s)
                on conflict (market_id, external_outcome_id) do update set
                  canonical_key=excluded.canonical_key, name=excluded.name,
                  outcome_type=excluded.outcome_type, line=excluded.line,
                  payout_per_unit=excluded.payout_per_unit
                returning id
                """,
                (market_id, external_id, canonical_key, outcome.name or external_id,
                 outcome.side or "selection", outcome.line, payout),
            )
            outcome_id = cursor.fetchone()[0]
            decimal_odds = outcome.offered_price if outcome.price_type is PriceType.DECIMAL_ODDS else None
            probability = outcome.offered_price if outcome.price_type is PriceType.CONTRACT_DOLLARS else None
            if all(value is None for value in (decimal_odds, probability, outcome.best_bid, outcome.best_ask)):
                continue
            quote_key = _stable_key(external_id, observed, decimal_odds, probability,
                                    outcome.best_bid, outcome.best_ask, outcome.bid_size, outcome.ask_size)
            stale_at = (
                market.quote_timestamp + timedelta(seconds=stale_after_seconds)
                if market.quote_timestamp is not None else market.retrieved_at
            )
            cursor.execute(
                """
                insert into public.quotes
                  (provider_id, market_id, outcome_id, ingestion_key, decimal_odds,
                   probability_price, best_bid, best_ask, bid_size, ask_size, available_size,
                   currency, feed_class, observed_at, received_at, stale_at, source_sequence,
                   is_in_play, is_demo, raw_fingerprint)
                values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                on conflict (provider_id, ingestion_key) do nothing
                """,
                (provider_id, market_id, outcome_id, quote_key, decimal_odds, probability,
                 outcome.best_bid, outcome.best_ask, outcome.bid_size, outcome.ask_size,
                 outcome.maximum_stake if outcome.price_type is PriceType.DECIMAL_ODDS else outcome.ask_size,
                 outcome.currency or "USD", _feed_class(market.data_origin), observed,
                 market.retrieved_at, stale_at,
                 None if market.quote_timestamp is not None else "fetch_time_no_provider_timestamp",
                 bool(market.metadata.get("live", False)), market.data_origin is DataOrigin.DEMO,
                 fingerprint),
            )
        return raw_created

    @staticmethod
    def _persist_market_rule(cursor: Any, market_id: object, market: NormalizedMarket) -> None:
        settlement = market.settlement
        metadata = dict(market.metadata)
        includes_overtime = _explicit_bool(
            metadata, "includes_overtime", "overtime_included"
        )
        tie_treatment = _known_treatment(
            metadata.get("tie_treatment"),
            frozenset({"separate_outcome", "push", "loss", "not_applicable", "unknown"}),
        )
        push_value = settlement.push_treatment if settlement is not None else None
        push_treatment = _known_treatment(
            push_value if push_value is not None else metadata.get("push_treatment"),
            frozenset({"refund", "half_win", "half_loss", "loss", "not_applicable", "unknown"}),
        )
        void_value = settlement.void_treatment if settlement is not None else None
        void_treatment = _known_treatment(
            void_value if void_value is not None else metadata.get("void_treatment"),
            frozenset({"refund", "loss", "regrade", "not_applicable", "unknown"}),
        )
        cancellation_treatment = _known_treatment(
            metadata.get("cancellation_treatment"),
            frozenset({"refund", "action", "reschedule", "not_applicable", "unknown"}),
        )
        settlement_source = settlement.resolution_source if settlement is not None else None
        rules_parts = (
            settlement.primary_rules if settlement is not None else None,
            settlement.secondary_rules if settlement is not None else None,
        )
        rules_text = "\n".join(value.strip() for value in rules_parts if value and value.strip()) or None
        mutually_exclusive, collectively_exhaustive = _market_structure(market)
        rule_metadata = {
            "outcome_structure": (
                "mutually_exclusive_collectively_exhaustive"
                if mutually_exclusive is True and collectively_exhaustive is True
                else None
            ),
            "draw_treatment": metadata.get("draw_treatment"),
            "shootout_included": metadata.get("shootout_included"),
            "postponement_treatment": metadata.get("postponement_treatment"),
            "retirement_treatment": metadata.get("retirement_treatment"),
            "abandonment_treatment": metadata.get("abandonment_treatment"),
            "line_unit": metadata.get("line_unit"),
            "source_is_provider_normalized_contract": True,
        }
        material = {
            "includes_overtime": includes_overtime,
            "tie_treatment": tie_treatment,
            "push_treatment": push_treatment,
            "void_treatment": void_treatment,
            "cancellation_treatment": cancellation_treatment,
            "settlement_source": settlement_source,
            "rules_url": market.source_url,
            "rules_text": rules_text,
            "metadata": rule_metadata,
        }
        rules_hash = hashlib.sha256(
            json.dumps(material, sort_keys=True, separators=(",", ":"), default=str).encode()
        ).hexdigest()
        cursor.execute(
            """
            select rule_version,effective_from,rules_hash
              from public.market_rules
             where market_id=%s
             order by rule_version desc
             limit 1
             for update
            """,
            (market_id,),
        )
        latest = cursor.fetchone()
        if latest is not None and str(latest[2]) == rules_hash:
            return
        effective_from = market.retrieved_at.astimezone(UTC)
        version = 1
        if latest is not None:
            version = int(latest[0]) + 1
            latest_from = latest[1].astimezone(UTC)
            if effective_from <= latest_from:
                effective_from = latest_from + timedelta(microseconds=1)
            cursor.execute(
                """
                update public.market_rules
                   set effective_to=%s
                 where market_id=%s and rule_version=%s and effective_to is null
                """,
                (effective_from, market_id, latest[0]),
            )
        cursor.execute(
            """
            insert into public.market_rules
              (market_id,rule_version,includes_overtime,tie_treatment,push_treatment,
               void_treatment,cancellation_treatment,settlement_source,rules_url,
               rules_text,rules_hash,effective_from,metadata)
            values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb)
            """,
            (
                market_id,
                version,
                includes_overtime,
                tie_treatment,
                push_treatment,
                void_treatment,
                cancellation_treatment,
                settlement_source,
                market.source_url,
                rules_text,
                rules_hash,
                effective_from,
                json.dumps(rule_metadata, sort_keys=True, separators=(",", ":")),
            ),
        )

    def finish_run(self, run_id: str, status: str, counts: WriteCounts, diagnostics: dict[str, object]) -> None:
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                update public.ingestion_runs set status=%s, request_count=%s,
                  records_received=%s, records_created=%s, records_updated=%s,
                  records_rejected=%s, completed_at=now(), heartbeat_at=now(),
                  diagnostics=%s::jsonb where id=%s
                """,
                (status, diagnostics.get("request_count", 0), counts.received, counts.created,
                 counts.updated, counts.rejected,
                 json.dumps(_wire(diagnostics), sort_keys=True, separators=(",", ":")), run_id),
            )

    def record_health(self, health: ProviderHealth, active_instruments: int) -> None:
        state = health.state.value
        allowed = state if state in {"connected", "delayed", "disconnected", "requires_api_key", "not_supported", "demo_only"} else "disconnected"
        rate = health.rate_limit
        rate_state = (
            "exhausted" if rate is not None and rate.remaining == 0
            else "limited" if health.last_http_status == 429
            else "ok" if health.state.value == "connected"
            else "unknown"
        )
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                insert into public.provider_connections
                  (provider_id,status,last_attempt_at,last_success_at,last_error_at,last_error_code,
                   last_error_summary,rate_limit_state,
                   quota_limit,quota_remaining,quota_resets_at,active_instruments,
                   consecutive_failures,diagnostics)
                select id,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb
                  from public.providers where slug=%s
                on conflict (provider_id) do update set
                  status=excluded.status,last_attempt_at=excluded.last_attempt_at,
                  last_success_at=coalesce(excluded.last_success_at,provider_connections.last_success_at),
                  last_error_at=coalesce(excluded.last_error_at,provider_connections.last_error_at),
                  last_error_code=excluded.last_error_code,quota_limit=excluded.quota_limit,
                  last_error_summary=excluded.last_error_summary,
                  rate_limit_state=excluded.rate_limit_state,
                  quota_remaining=excluded.quota_remaining,quota_resets_at=excluded.quota_resets_at,
                  active_instruments=excluded.active_instruments,
                  consecutive_failures=excluded.consecutive_failures,diagnostics=excluded.diagnostics
                """,
                (allowed, health.checked_at, health.last_success_at, health.last_error_at,
                 f"HTTP_{health.last_http_status}" if health.last_error and health.last_http_status else (
                     "provider_unavailable" if health.last_error else None
                 ),
                 health.last_error or health.message, rate_state,
                 rate.limit if rate else None, rate.remaining if rate else None,
                 rate.reset_at if rate else None, active_instruments, health.consecutive_failures,
                 json.dumps({"latency_ms": health.last_latency_ms,
                             "measured_data_delay_seconds": health.measured_data_delay_seconds,
                             "requests_attempted": health.requests_attempted,
                             "rate_limit_events": health.rate_limit_events,
                             "message": health.message}),
                 provider_reference_slug(health.provider)),
            )

    @staticmethod
    def _provider_id(cursor: Any, provider: ProviderCode) -> object:
        cursor.execute(
            "select id from public.providers where slug=%s",
            (provider_reference_slug(provider),),
        )
        row = cursor.fetchone()
        if row is None:
            raise KeyError(f"provider reference is not configured: {provider.value}")
        return row[0]

