"""Durable repositories for user-owned watchlists, alert rules, and settings.

The API connects with a trusted server role, so every statement includes the
authenticated ``user_id`` explicitly.  Canonical research targets must already
exist: saving a watch never creates a fake sport, event, or market merely to
make the UI appear successful.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from decimal import Decimal, InvalidOperation
import json
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from psycopg.errors import UniqueViolation

from .domain import AlertCreate, AlertUpdate, WatchlistCreate
from .normalization import normalize_name


_WATCH_TARGETS: dict[str, str] = {
    "team": "participant",
    "sport": "sport",
    "league": "league",
    "event": "event",
    "market": "market",
    "provider": "provider",
}

_ALERT_TO_DATABASE = {"net_roi_above": "roi_threshold"}
_ALERT_FROM_DATABASE = {value: key for key, value in _ALERT_TO_DATABASE.items()}

_WATCHLIST_SELECT = """
    select item.id::text, item.target_type,
           coalesce(
             participant.normalized_name, sport.slug, league.slug,
             event.id::text, market.id::text, provider.slug,
             item.strategy_key
           ) as target_value,
           coalesce(
             item.label, participant.canonical_name, sport.name, league.name,
             event.name, market.title, provider.display_name, item.strategy_key
           ) as target_label,
           item.detail, item.created_at
      from public.user_watchlist_items item
      join public.user_watchlists list
        on list.id = item.watchlist_id and list.user_id = item.user_id
      left join public.participants participant on participant.id = item.participant_id
      left join public.sports sport on sport.id = item.sport_id
      left join public.leagues league on league.id = item.league_id
      left join public.events event on event.id = item.event_id
      left join public.markets market on market.id = item.market_id
      left join public.providers provider on provider.id = item.provider_id
"""

_ALERT_SELECT = """
    select id::text, name, alert_type, conditions, enabled,
           cooldown_seconds, last_triggered_at, created_at
      from public.user_alerts
"""


class PostgresPrivateRepository:
    """Authenticated durable storage with owner predicates on every operation."""

    def __init__(self, connection_factory: Callable[[], Any]) -> None:
        self._connect = connection_factory

    # ---- Watchlist -----------------------------------------------------

    def watchlists(self, user_id: str) -> tuple[dict[str, object], ...]:
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                _WATCHLIST_SELECT
                + """
                    where item.user_id = %s and list.user_id = %s
                    order by item.created_at desc, item.id
                """,
                (user_id, user_id),
            )
            rows = cursor.fetchall()
        return tuple(self._watchlist_row(row) for row in rows)

    @staticmethod
    def _watchlist_row(row: tuple[Any, ...]) -> dict[str, object]:
        target_type = str(row[1])
        return {
            "id": str(row[0]),
            "kind": "team" if target_type == "participant" else target_type,
            "value": str(row[2]),
            "label": str(row[3]),
            "detail": row[4],
            "created_at": row[5],
            "is_demo": False,
        }

    def add_watchlist(self, user_id: str, request: WatchlistCreate) -> dict[str, object]:
        """Resolve a real canonical target and add it to the user's default list."""
        value = request.value.strip()
        if not value:
            raise ValueError("watchlist value must not be blank")
        label = request.label.strip() if request.label is not None else None
        if request.label is not None and not label:
            raise ValueError("watchlist label must not be blank")
        target_type = "strategy" if request.kind == "strategy" else _WATCH_TARGETS[request.kind]
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                insert into public.user_watchlists (user_id, name, is_default)
                values (%s, 'Research watchlist', true)
                on conflict do nothing
                """,
                (user_id,),
            )
            cursor.execute(
                """
                select id from public.user_watchlists
                 where user_id = %s and is_default
                 order by created_at limit 1
                """,
                (user_id,),
            )
            watchlist = cursor.fetchone()
            if watchlist is None:
                # A pre-existing non-default list can already own the fixed
                # display name. Promote only that same user's list.
                cursor.execute(
                    """
                    update public.user_watchlists
                       set is_default = true, updated_at = now()
                     where user_id = %s and name = 'Research watchlist'
                    returning id
                    """,
                    (user_id,),
                )
                watchlist = cursor.fetchone()
                if watchlist is None:
                    raise RuntimeError("default_watchlist_unavailable")

            identifiers: dict[str, object | None] = {
                "participant_id": None,
                "sport_id": None,
                "league_id": None,
                "event_id": None,
                "market_id": None,
                "provider_id": None,
                "strategy_key": None,
            }
            if request.kind == "strategy":
                identifiers["strategy_key"] = value
            else:
                identifiers[f"{target_type}_id"] = self._resolve_target(
                    cursor, request.kind, value
                )

            cursor.execute(
                """
                insert into public.user_watchlist_items
                    (user_id, watchlist_id, target_type, participant_id, sport_id,
                     league_id, event_id, market_id, provider_id, strategy_key,
                     label, detail, idempotency_key)
                values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, null, %s)
                on conflict do nothing
                returning id::text
                """,
                (
                    user_id,
                    watchlist[0],
                    target_type,
                    identifiers["participant_id"],
                    identifiers["sport_id"],
                    identifiers["league_id"],
                    identifiers["event_id"],
                    identifiers["market_id"],
                    identifiers["provider_id"],
                    identifiers["strategy_key"],
                    label,
                    request.idempotency_key,
                ),
            )
            inserted = cursor.fetchone()
            if inserted is None:
                inserted = self._find_watchlist_retry(
                    cursor,
                    user_id=user_id,
                    watchlist_id=watchlist[0],
                    target_type=target_type,
                    identifiers=identifiers,
                    label=label,
                    idempotency_key=request.idempotency_key,
                )
            if inserted is None:
                raise RuntimeError("watchlist_write_failed")
            item_id = inserted[0]

            cursor.execute(
                _WATCHLIST_SELECT
                + """
                    where item.id = %s and item.user_id = %s and list.user_id = %s
                """,
                (item_id, user_id, user_id),
            )
            row = cursor.fetchone()
            if row is None:
                raise RuntimeError("watchlist_write_failed")
        return self._watchlist_row(row)

    @staticmethod
    def _find_watchlist_retry(
        cursor: Any,
        *,
        user_id: str,
        watchlist_id: object,
        target_type: str,
        identifiers: Mapping[str, object | None],
        label: str | None,
        idempotency_key: str | None,
    ) -> tuple[Any, ...] | None:
        identity_parameters = (
            user_id,
            watchlist_id,
            target_type,
            identifiers["participant_id"],
            identifiers["sport_id"],
            identifiers["league_id"],
            identifiers["event_id"],
            identifiers["market_id"],
            identifiers["provider_id"],
            identifiers["strategy_key"],
        )
        if idempotency_key is not None:
            cursor.execute(
                """
                select id::text, target_type, participant_id, sport_id, league_id,
                       event_id, market_id, provider_id, strategy_key, label
                  from public.user_watchlist_items
                 where user_id = %s and idempotency_key = %s
                """,
                (user_id, idempotency_key),
            )
            retry = cursor.fetchone()
            if retry is not None:
                expected = (
                    target_type,
                    identifiers["participant_id"],
                    identifiers["sport_id"],
                    identifiers["league_id"],
                    identifiers["event_id"],
                    identifiers["market_id"],
                    identifiers["provider_id"],
                    identifiers["strategy_key"],
                    label,
                )
                if tuple(retry[1:]) != expected:
                    raise ValueError("idempotency_key_conflict")
                return (retry[0],)

        cursor.execute(
            """
            select id::text from public.user_watchlist_items
             where user_id = %s and watchlist_id = %s and target_type = %s
               and participant_id is not distinct from %s
               and sport_id is not distinct from %s
               and league_id is not distinct from %s
               and event_id is not distinct from %s
               and market_id is not distinct from %s
               and provider_id is not distinct from %s
               and strategy_key is not distinct from %s
            """,
            identity_parameters,
        )
        existing = cursor.fetchone()
        if existing is not None and label is not None:
            cursor.execute(
                """
                update public.user_watchlist_items set label = %s
                 where id = %s and user_id = %s and watchlist_id = %s
                """,
                (label, existing[0], user_id, watchlist_id),
            )
            if cursor.rowcount != 1:
                raise RuntimeError("watchlist_write_failed")
        return existing

    @staticmethod
    def _resolve_target(cursor: Any, kind: str, value: str) -> object:
        if kind == "team":
            normalized = normalize_name(value)
            cursor.execute(
                """
                with candidates as (
                  select distinct p.id
                    from public.participants p
                    left join public.participant_aliases a on a.participant_id = p.id
                   where p.participant_type = 'team'
                     and (
                       p.id::text = %s or lower(p.canonical_name) = lower(%s)
                       or p.normalized_name = %s or a.normalized_alias = %s
                     )
                )
                select max(id::text)::uuid from candidates having count(*) = 1
                """,
                (value, value, normalized, normalized),
            )
        elif kind in {"sport", "league", "provider"}:
            if kind == "sport":
                table, name_column = "sports", "name"
            elif kind == "league":
                table, name_column = "leagues", "name"
            else:
                table, name_column = "providers", "display_name"
            # Both identifiers come only from this closed branch, never input.
            cursor.execute(
                f"""
                with candidates as (
                  select distinct id from public.{table}
                   where id::text = %s or slug = lower(%s)
                      or lower({name_column}) = lower(%s)
                )
                select max(id::text)::uuid from candidates having count(*) = 1
                """,
                (value, value, value),
            )
        elif kind in {"event", "market"}:
            if kind == "event":
                cursor.execute(
                    """
                    with candidates as (
                      select e.id from public.events e
                       where e.id::text = %s or e.canonical_key = %s
                          or lower(e.name) = lower(%s)
                      union
                      select pe.canonical_event_id from public.provider_events pe
                       where pe.external_event_id = %s
                         and pe.canonical_event_id is not null
                    )
                    select max(id::text)::uuid from candidates having count(*) = 1
                    """,
                    (value, value, value, value),
                )
            else:
                # A provider market ID may not be globally unique, so ambiguous
                # values are rejected rather than guessed.
                cursor.execute(
                    """
                    with candidates as (
                      select distinct id from public.markets
                       where id::text = %s or external_market_id = %s
                    )
                    select max(id::text)::uuid from candidates having count(*) = 1
                    """,
                    (value, value),
                )
        else:  # pragma: no cover - protected by Pydantic and the constant map
            raise ValueError("unsupported watchlist target")
        row = cursor.fetchone()
        if row is None or row[0] is None:
            raise KeyError(f"unknown_{kind}_target")
        return row[0]

    def delete_watchlist(self, user_id: str, item_id: str) -> None:
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                "delete from public.user_watchlist_items where id = %s and user_id = %s",
                (item_id, user_id),
            )
            if cursor.rowcount != 1:
                raise KeyError(item_id)

    # ---- Alerts --------------------------------------------------------

    def alerts(self, user_id: str) -> tuple[dict[str, object], ...]:
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                _ALERT_SELECT + " where user_id = %s order by created_at desc, id",
                (user_id,),
            )
            rows = cursor.fetchall()
        return tuple(self._alert_row(row) for row in rows)

    @staticmethod
    def _alert_row(row: tuple[Any, ...]) -> dict[str, object]:
        conditions = row[3] if isinstance(row[3], Mapping) else {}
        return {
            "id": str(row[0]),
            "name": row[1],
            "condition": _ALERT_FROM_DATABASE.get(str(row[2]), str(row[2])),
            "threshold": conditions.get("threshold"),
            "minimum_net_profit": conditions.get("minimum_net_profit"),
            "minimum_net_roi": conditions.get("minimum_net_roi"),
            "minimum_liquidity": conditions.get("minimum_liquidity"),
            "maximum_quote_age_seconds": conditions.get("maximum_quote_age_seconds"),
            "enabled": bool(row[4]),
            "cooldown_seconds": int(row[5]),
            "last_triggered_at": row[6],
            "created_at": row[7],
            "is_demo": False,
        }

    def add_alert(self, user_id: str, request: AlertCreate) -> dict[str, object]:
        alert_type = _ALERT_TO_DATABASE.get(request.condition, request.condition)
        name = (request.name or request.condition.replace("_", " ").title()).strip()
        if not name:
            raise ValueError("alert name must not be blank")
        conditions: dict[str, object] = {}
        for key in (
            "threshold",
            "minimum_net_profit",
            "minimum_net_roi",
            "minimum_liquidity",
        ):
            value = getattr(request, key)
            if value is not None:
                conditions[key] = str(value)
        if request.maximum_quote_age_seconds is not None:
            conditions["maximum_quote_age_seconds"] = request.maximum_quote_age_seconds
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                insert into public.user_alerts
                    (user_id, name, alert_type, conditions, enabled,
                     cooldown_seconds, idempotency_key)
                values (%s, %s, %s, %s::jsonb, %s, %s, %s)
                on conflict do nothing
                returning id::text, name, alert_type, conditions, enabled,
                          cooldown_seconds, last_triggered_at, created_at
                """,
                (
                    user_id,
                    name,
                    alert_type,
                    json.dumps(conditions, separators=(",", ":")),
                    request.enabled,
                    request.cooldown_seconds,
                    request.idempotency_key,
                ),
            )
            row = cursor.fetchone()
            if row is None:
                row = self._find_alert_retry(
                    cursor,
                    user_id=user_id,
                    name=name,
                    alert_type=alert_type,
                    conditions=conditions,
                    enabled=request.enabled,
                    cooldown_seconds=request.cooldown_seconds,
                    idempotency_key=request.idempotency_key,
                )
            if row is None:
                raise RuntimeError("alert_write_failed")
        return self._alert_row(row)

    @classmethod
    def _find_alert_retry(
        cls,
        cursor: Any,
        *,
        user_id: str,
        name: str,
        alert_type: str,
        conditions: Mapping[str, object],
        enabled: bool,
        cooldown_seconds: int,
        idempotency_key: str | None,
    ) -> tuple[Any, ...] | None:
        if idempotency_key is not None:
            cursor.execute(
                _ALERT_SELECT + " where user_id = %s and idempotency_key = %s",
                (user_id, idempotency_key),
            )
            retry = cursor.fetchone()
            if retry is not None:
                if not cls._alert_payload_matches(
                    retry,
                    name=name,
                    alert_type=alert_type,
                    conditions=conditions,
                    enabled=enabled,
                    cooldown_seconds=cooldown_seconds,
                ):
                    raise ValueError("idempotency_key_conflict")
                return retry

        cursor.execute(
            _ALERT_SELECT + " where user_id = %s and name = %s",
            (user_id, name),
        )
        existing = cursor.fetchone()
        if existing is not None and not cls._alert_payload_matches(
            existing,
            name=name,
            alert_type=alert_type,
            conditions=conditions,
            enabled=enabled,
            cooldown_seconds=cooldown_seconds,
        ):
            raise ValueError("alert_name_conflict")
        return existing

    @staticmethod
    def _alert_payload_matches(
        row: tuple[Any, ...],
        *,
        name: str,
        alert_type: str,
        conditions: Mapping[str, object],
        enabled: bool,
        cooldown_seconds: int,
    ) -> bool:
        stored_conditions = row[3] if isinstance(row[3], Mapping) else {}
        return (
            row[1] == name
            and row[2] == alert_type
            and dict(stored_conditions) == dict(conditions)
            and bool(row[4]) is enabled
            and int(row[5]) == cooldown_seconds
        )

    def update_alert(
        self, user_id: str, alert_id: str, request: AlertUpdate
    ) -> dict[str, object]:
        changes = request.model_dump(exclude_unset=True)
        assignments: list[str] = []
        parameters: list[object] = []
        if "condition" in changes:
            condition = changes["condition"]
            if condition is not None:
                assignments.append("alert_type = %s")
                parameters.append(_ALERT_TO_DATABASE.get(str(condition), str(condition)))
        condition_fields = (
            "threshold",
            "minimum_net_profit",
            "minimum_net_roi",
            "minimum_liquidity",
            "maximum_quote_age_seconds",
        )
        condition_patch: dict[str, object] = {}
        condition_removals: list[str] = []
        for field in condition_fields:
            if field not in changes:
                continue
            value = changes[field]
            if value is None:
                condition_removals.append(field)
            elif field == "maximum_quote_age_seconds":
                condition_patch[field] = int(value)
            else:
                condition_patch[field] = str(value)
        if condition_patch or condition_removals:
            assignments.append("conditions = (conditions - %s::text[]) || %s::jsonb")
            parameters.extend(
                (condition_removals, json.dumps(condition_patch, separators=(",", ":")))
            )
        for field in ("enabled", "name", "cooldown_seconds"):
            if field in changes and changes[field] is not None:
                assignments.append(f"{field} = %s")
                value = changes[field]
                if field == "name":
                    value = str(value).strip()
                    if not value:
                        raise ValueError("alert name must not be blank")
                parameters.append(value)
        if not assignments:
            return self._get_alert(user_id, alert_id)
        parameters.extend((alert_id, user_id))
        try:
            with self._connect() as connection, connection.cursor() as cursor:
                cursor.execute(
                    f"""
                    update public.user_alerts set {', '.join(assignments)}, updated_at = now()
                     where id = %s and user_id = %s
                    returning id::text, name, alert_type, conditions, enabled,
                              cooldown_seconds, last_triggered_at, created_at
                    """,
                    tuple(parameters),
                )
                row = cursor.fetchone()
                if row is None:
                    raise KeyError(alert_id)
        except UniqueViolation as exc:
            raise ValueError("alert_name_conflict") from exc
        return self._alert_row(row)

    def _get_alert(self, user_id: str, alert_id: str) -> dict[str, object]:
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                _ALERT_SELECT + " where id = %s and user_id = %s",
                (alert_id, user_id),
            )
            row = cursor.fetchone()
        if row is None:
            raise KeyError(alert_id)
        return self._alert_row(row)

    def delete_alert(self, user_id: str, alert_id: str) -> None:
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                "delete from public.user_alerts where id = %s and user_id = %s",
                (alert_id, user_id),
            )
            if cursor.rowcount != 1:
                raise KeyError(alert_id)

    def alert_events(self, user_id: str) -> tuple[dict[str, object], ...]:
        """List persisted notifications; rule evaluation belongs to a worker."""
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                select id::text, alert_id::text, opportunity_id::text,
                       provider_id::text, title, body, severity, payload,
                       triggered_at, read_at
                  from public.user_alert_events
                 where user_id = %s and dismissed_at is null
                   and lifecycle_status = 'active'
                 order by triggered_at desc limit 250
                """,
                (user_id,),
            )
            rows = cursor.fetchall()
        return tuple(
            {
                "id": str(row[0]),
                "alert_id": str(row[1]),
                "opportunity_id": str(row[2]) if row[2] is not None else None,
                "provider_id": str(row[3]) if row[3] is not None else None,
                "title": row[4],
                "body": row[5],
                "severity": row[6],
                "payload": dict(row[7]) if isinstance(row[7], Mapping) else {},
                "triggered_at": row[8],
                "read_at": row[9],
                "is_demo": False,
            }
            for row in rows
        )

    # ---- Settings compatibility API ----------------------------------

    def settings(self, user_id: str) -> dict[str, object]:
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                select p.timezone, p.base_currency, s.default_capital,
                       s.max_quote_age_seconds, s.analytics_preferences
                  from public.profiles p
                  join public.user_settings s on s.user_id = p.id
                 where p.id = %s
                """,
                (user_id,),
            )
            row = cursor.fetchone()
        if row is None:
            raise KeyError(user_id)
        preferences = row[4] if isinstance(row[4], Mapping) else {}
        return {
            "timezone": row[0],
            "currency": row[1],
            "default_allocation": row[2],
            "maximum_quote_age_seconds": row[3],
            "demo_mode": False,
            "verified_matches_only": preferences.get("verified_matches_only", True),
        }

    def update_settings(self, user_id: str, values: Mapping[str, object]) -> dict[str, object]:
        allowed = {
            "timezone", "currency", "default_allocation",
            "maximum_quote_age_seconds", "verified_matches_only",
        }
        unknown = set(values) - allowed
        if unknown:
            raise ValueError(f"unsupported settings: {', '.join(sorted(unknown))}")
        normalized = self._validated_settings(values)
        with self._connect() as connection, connection.cursor() as cursor:
            if "timezone" in normalized or "currency" in normalized:
                cursor.execute(
                    """
                    update public.profiles set
                      timezone = coalesce(%s, timezone),
                      base_currency = coalesce(%s, base_currency), updated_at = now()
                     where id = %s
                    """,
                    (normalized.get("timezone"), normalized.get("currency"), user_id),
                )
                if cursor.rowcount != 1:
                    raise KeyError(user_id)
            if any(
                key in normalized
                for key in ("default_allocation", "maximum_quote_age_seconds", "verified_matches_only")
            ):
                cursor.execute(
                    """
                    update public.user_settings set
                      default_capital = coalesce(%s, default_capital),
                      max_quote_age_seconds = coalesce(%s, max_quote_age_seconds),
                      analytics_preferences = case when %s is null then analytics_preferences
                        else jsonb_set(analytics_preferences, '{verified_matches_only}', to_jsonb(%s::boolean), true) end,
                      updated_at = now()
                     where user_id = %s
                    """,
                    (
                        normalized.get("default_allocation"),
                        normalized.get("maximum_quote_age_seconds"),
                        normalized.get("verified_matches_only"),
                        normalized.get("verified_matches_only"),
                        user_id,
                    ),
                )
                if cursor.rowcount != 1:
                    raise KeyError(user_id)
        return self.settings(user_id)

    @staticmethod
    def _validated_settings(values: Mapping[str, object]) -> dict[str, object]:
        normalized = dict(values)
        if "timezone" in normalized:
            timezone = normalized["timezone"]
            if not isinstance(timezone, str) or not timezone.strip() or len(timezone.strip()) > 100:
                raise ValueError("timezone must be a valid IANA timezone")
            timezone = timezone.strip()
            try:
                ZoneInfo(timezone)
            except (ZoneInfoNotFoundError, ValueError) as exc:
                raise ValueError("timezone must be a valid IANA timezone") from exc
            normalized["timezone"] = timezone
        if "currency" in normalized:
            currency = normalized["currency"]
            if not isinstance(currency, str):
                raise ValueError("currency must be a three-letter code")
            currency = currency.strip().upper()
            if len(currency) != 3 or not currency.isascii() or not currency.isalpha():
                raise ValueError("currency must be a three-letter code")
            normalized["currency"] = currency
        if "default_allocation" in normalized:
            allocation = normalized["default_allocation"]
            if isinstance(allocation, bool):
                raise ValueError("default_allocation must be a non-negative number")
            try:
                amount = Decimal(str(allocation))
            except (InvalidOperation, ValueError) as exc:
                raise ValueError("default_allocation must be a non-negative number") from exc
            if not amount.is_finite() or amount < 0 or amount >= Decimal("10000000000000000"):
                raise ValueError("default_allocation must be a non-negative number")
            normalized["default_allocation"] = amount
        if "maximum_quote_age_seconds" in normalized:
            maximum_age = normalized["maximum_quote_age_seconds"]
            if (
                not isinstance(maximum_age, int)
                or isinstance(maximum_age, bool)
                or not 1 <= maximum_age <= 2_592_000
            ):
                raise ValueError("maximum_quote_age_seconds must be between 1 and 2592000")
        if "verified_matches_only" in normalized and not isinstance(
            normalized["verified_matches_only"], bool
        ):
            raise ValueError("verified_matches_only must be a boolean")
        return normalized

