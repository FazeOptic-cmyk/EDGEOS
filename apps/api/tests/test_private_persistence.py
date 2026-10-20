from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import pytest

from edgeos.domain import AlertCreate, AlertUpdate, WatchlistCreate
from edgeos.private_persistence import PostgresPrivateRepository


class Step:
    def __init__(
        self,
        *,
        row: tuple[Any, ...] | None = None,
        rows: list[tuple[Any, ...]] | None = None,
        rowcount: int = 1,
    ) -> None:
        self.row = row
        self.rows = rows or []
        self.rowcount = rowcount


class ScriptedCursor:
    def __init__(self, steps: list[Step]) -> None:
        self.steps = steps
        self.executions: list[tuple[str, tuple[object, ...]]] = []
        self.current = Step(rowcount=0)

    def __enter__(self) -> ScriptedCursor:
        return self

    def __exit__(self, *_: object) -> None:
        return None

    def execute(self, sql: str, parameters: tuple[object, ...]) -> None:
        if not self.steps:
            raise AssertionError(f"unexpected SQL: {sql}")
        self.current = self.steps.pop(0)
        self.executions.append((sql, parameters))

    def fetchone(self) -> tuple[Any, ...] | None:
        return self.current.row

    def fetchall(self) -> list[tuple[Any, ...]]:
        return self.current.rows

    @property
    def rowcount(self) -> int:
        return self.current.rowcount


class FakeConnection:
    def __init__(self, cursor: ScriptedCursor) -> None:
        self._cursor = cursor

    def __enter__(self) -> FakeConnection:
        return self

    def __exit__(self, *_: object) -> None:
        return None

    def cursor(self) -> ScriptedCursor:
        return self._cursor


def _repository(cursor: ScriptedCursor) -> PostgresPrivateRepository:
    return PostgresPrivateRepository(lambda: FakeConnection(cursor))


def test_watchlist_reads_persisted_label_and_detail_with_owner_predicates() -> None:
    created_at = datetime(2026, 10, 20, tzinfo=UTC)
    cursor = ScriptedCursor([
        Step(rows=[("item-1", "participant", "new york", "My Knicks", "NBA team", created_at)])
    ])

    items = _repository(cursor).watchlists("user-a")

    assert items == ({
        "id": "item-1",
        "kind": "team",
        "value": "new york",
        "label": "My Knicks",
        "detail": "NBA team",
        "created_at": created_at,
        "is_demo": False,
    },)
    sql, parameters = cursor.executions[0]
    assert "item.label" in sql and "item.detail" in sql
    assert "item.user_id = %s and list.user_id = %s" in sql
    assert parameters == ("user-a", "user-a")


def test_add_watchlist_persists_user_label_and_idempotency_key() -> None:
    created_at = datetime(2026, 10, 20, tzinfo=UTC)
    cursor = ScriptedCursor([
        Step(),
        Step(row=("list-1",)),
        Step(row=("item-1",)),
        Step(row=("item-1", "strategy", "low_execution_risk", "Low risk", None, created_at)),
    ])
    request = WatchlistCreate(
        kind="strategy",
        value=" low_execution_risk ",
        label=" Low risk ",
        idempotency_key="watch-key-0001",
    )

    item = _repository(cursor).add_watchlist("user-a", request)

    assert item["label"] == "Low risk"
    insert_sql, insert_parameters = cursor.executions[2]
    assert "label, detail, idempotency_key" in insert_sql
    assert insert_parameters[-2:] == ("Low risk", "watch-key-0001")
    read_sql, read_parameters = cursor.executions[3]
    assert "list.user_id = %s" in read_sql
    assert read_parameters == ("item-1", "user-a", "user-a")


def test_watchlist_idempotency_key_cannot_be_reused_for_another_target() -> None:
    cursor = ScriptedCursor([
        Step(row=("item-1", "sport", None, "sport-a", None, None, None, None, None, "Sport A"))
    ])
    identifiers = {
        "participant_id": None,
        "sport_id": "sport-b",
        "league_id": None,
        "event_id": None,
        "market_id": None,
        "provider_id": None,
        "strategy_key": None,
    }

    with pytest.raises(ValueError, match="idempotency_key_conflict"):
        PostgresPrivateRepository._find_watchlist_retry(
            cursor,
            user_id="user-a",
            watchlist_id="list-1",
            target_type="sport",
            identifiers=identifiers,
            label="Sport B",
            idempotency_key="watch-key-0001",
        )

    assert cursor.executions[0][1] == ("user-a", "watch-key-0001")


def test_canonical_resolution_rejects_ambiguous_team_instead_of_guessing() -> None:
    cursor = ScriptedCursor([Step(row=(None,))])

    with pytest.raises(KeyError, match="unknown_team_target"):
        PostgresPrivateRepository._resolve_target(cursor, "team", "LA FC")

    sql, parameters = cursor.executions[0]
    assert "having count(*) = 1" in sql
    assert "limit 1" not in sql.lower()
    assert "p.participant_type = 'team'" in sql
    assert parameters == ("LA FC", "LA FC", "los angeles", "los angeles")


def test_add_alert_persists_threshold_and_retry_key_with_public_condition_name() -> None:
    created_at = datetime(2026, 10, 20, tzinfo=UTC)
    cursor = ScriptedCursor([
        Step(row=(
            "alert-1",
            "ROI gate",
            "roi_threshold",
            {"threshold": "2.50"},
            True,
            600,
            None,
            created_at,
        ))
    ])
    request = AlertCreate(
        condition="net_roi_above",
        threshold=Decimal("2.50"),
        name=" ROI gate ",
        cooldown_seconds=600,
        idempotency_key="alert-key-0001",
    )

    alert = _repository(cursor).add_alert("user-a", request)

    assert alert["condition"] == "net_roi_above"
    assert alert["threshold"] == "2.50"
    sql, parameters = cursor.executions[0]
    assert "idempotency_key" in sql
    assert parameters[-1] == "alert-key-0001"
    assert parameters[1:4] == ("ROI gate", "roi_threshold", '{"threshold":"2.50"}')


def test_alert_retry_rejects_same_key_with_changed_threshold() -> None:
    created_at = datetime(2026, 10, 20, tzinfo=UTC)
    cursor = ScriptedCursor([
        Step(row=(
            "alert-1", "ROI gate", "roi_threshold", {"threshold": "1.00"},
            True, 300, None, created_at,
        ))
    ])

    with pytest.raises(ValueError, match="idempotency_key_conflict"):
        PostgresPrivateRepository._find_alert_retry(
            cursor,
            user_id="user-a",
            name="ROI gate",
            alert_type="roi_threshold",
            conditions={"threshold": "2.00"},
            enabled=True,
            cooldown_seconds=300,
            idempotency_key="alert-key-0001",
        )


def test_explicit_null_threshold_removes_key_and_update_is_owner_scoped() -> None:
    created_at = datetime(2026, 10, 20, tzinfo=UTC)
    cursor = ScriptedCursor([
        Step(row=(
            "alert-1", "ROI gate", "roi_threshold", {}, True, 300, None, created_at,
        ))
    ])

    alert = _repository(cursor).update_alert(
        "user-a", "alert-1", AlertUpdate(threshold=None)
    )

    assert alert["threshold"] is None
    sql, parameters = cursor.executions[0]
    assert "conditions = (conditions - %s::text[]) || %s::jsonb" in sql
    assert "where id = %s and user_id = %s" in sql
    assert parameters == (["threshold"], "{}", "alert-1", "user-a")


def test_alert_conjunction_filters_are_stored_with_explicit_units() -> None:
    created_at = datetime(2026, 10, 20, tzinfo=UTC)
    conditions = {
        "minimum_net_profit": "5.00",
        "minimum_net_roi": "0.02",
        "minimum_liquidity": "100",
        "maximum_quote_age_seconds": 30,
    }
    cursor = ScriptedCursor([
        Step(row=(
            "alert-1", "Strict live edge", "new_verified_opportunity",
            conditions, True, 300, None, created_at,
        ))
    ])

    alert = _repository(cursor).add_alert(
        "user-a",
        AlertCreate(
            name="Strict live edge",
            condition="new_verified_opportunity",
            minimum_net_profit=Decimal("5.00"),
            minimum_net_roi=Decimal("0.02"),
            minimum_liquidity=Decimal("100"),
            maximum_quote_age_seconds=30,
        ),
    )

    assert alert["minimum_net_profit"] == "5.00"
    assert alert["minimum_net_roi"] == "0.02"  # ratio: 0.02 means 2%
    assert alert["minimum_liquidity"] == "100"
    assert alert["maximum_quote_age_seconds"] == 30
    assert '"maximum_quote_age_seconds":30' in str(cursor.executions[0][1][3])


def test_delete_watchlist_fails_closed_for_non_owner() -> None:
    cursor = ScriptedCursor([Step(rowcount=0)])

    with pytest.raises(KeyError, match="item-1"):
        _repository(cursor).delete_watchlist("user-b", "item-1")

    assert cursor.executions[0][1] == ("item-1", "user-b")


def test_alert_events_return_persisted_evidence_and_are_owner_scoped() -> None:
    triggered_at = datetime(2026, 10, 20, tzinfo=UTC)
    cursor = ScriptedCursor([
        Step(rows=[(
            "event-1", "alert-1", "opportunity-1", None, "Quote stale",
            "The latest quote exceeded the configured age.", "warning",
            {"quote_age_seconds": 91}, triggered_at, None,
        )])
    ])

    events = _repository(cursor).alert_events("user-a")

    assert events[0]["alert_id"] == "alert-1"
    assert events[0]["severity"] == "warning"
    assert events[0]["payload"] == {"quote_age_seconds": 91}
    sql, parameters = cursor.executions[0]
    assert "where user_id = %s" in sql
    assert parameters == ("user-a",)


@pytest.mark.parametrize(
    "values",
    [
        {"timezone": "Mars/Olympus"},
        {"currency": "US"},
        {"default_allocation": "NaN"},
        {"maximum_quote_age_seconds": 0},
        {"verified_matches_only": "true"},
    ],
)
def test_invalid_settings_fail_before_database_access(values: dict[str, object]) -> None:
    repository = PostgresPrivateRepository(lambda: pytest.fail("database must not be touched"))

    with pytest.raises(ValueError):
        repository.update_settings("user-a", values)


def test_settings_updates_are_normalized_and_owner_scoped() -> None:
    cursor = ScriptedCursor([
        Step(rowcount=1),
        Step(rowcount=1),
        Step(row=("UTC", "USD", Decimal("250.00"), 45, {"verified_matches_only": False})),
    ])

    result = _repository(cursor).update_settings(
        "user-a",
        {
            "timezone": " UTC ",
            "currency": "usd",
            "default_allocation": "250.00",
            "maximum_quote_age_seconds": 45,
            "verified_matches_only": False,
        },
    )

    assert result["currency"] == "USD"
    assert result["verified_matches_only"] is False
    assert cursor.executions[0][1] == ("UTC", "USD", "user-a")
    assert cursor.executions[1][1] == (Decimal("250.00"), 45, False, False, "user-a")
    assert cursor.executions[2][1] == ("user-a",)
