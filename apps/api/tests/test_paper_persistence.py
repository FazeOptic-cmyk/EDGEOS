from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest

from edgeos.domain import (
    CancellationCreate,
    FillCreate,
    PaperPositionCreate,
    SettlementCreate,
)
from edgeos.paper_persistence import (
    PostgresPaperRepository,
    fill_notional,
    money,
    winning_payout,
)


def test_exact_fill_cashflows_use_actual_quantity_price_and_fee_basis() -> None:
    assert fill_notional("back", Decimal("95.45454545"), Decimal("2.20")) == Decimal(
        "95.45454545"
    )
    assert fill_notional("buy_yes", Decimal("100"), Decimal("0.47")) == Decimal(
        "47.00000000"
    )
    assert winning_payout(
        "back", Decimal("95.45454545"), Decimal("2.20"), Decimal("1")
    ) == Decimal("209.99999999")
    assert winning_payout(
        "buy_yes", Decimal("100"), Decimal("0.47"), Decimal("1")
    ) == Decimal("100.00000000")
    assert money(Decimal("1.000000005")) == Decimal("1.00000001")


@pytest.mark.parametrize(
    ("action", "price", "code"),
    [
        ("back", "1", "decimal_odds_fill_price_must_exceed_one"),
        ("buy_yes", "1.01", "contract_fill_price_must_not_exceed_one"),
        ("lay", "2", "unsupported_paper_fill_action"),
        ("sell_yes", "0.5", "unsupported_paper_fill_action"),
    ],
)
def test_unsupported_or_invalid_fill_economics_are_rejected(
    action: str, price: str, code: str
) -> None:
    with pytest.raises(ValueError, match=code):
        fill_notional(action, Decimal("10"), Decimal(price))


class _Cursor:
    def __init__(self, rows: list[Any]) -> None:
        self.rows = rows
        self.statements: list[tuple[str, tuple[Any, ...]]] = []

    def __enter__(self) -> _Cursor:
        return self

    def __exit__(self, *_: object) -> None:
        return None

    def execute(self, statement: str, parameters: tuple[Any, ...]) -> None:
        self.statements.append((statement, parameters))

    def fetchone(self) -> tuple[Any, ...] | None:
        return self.rows.pop(0)

    def fetchall(self) -> list[tuple[Any, ...]]:
        rows = self.rows.pop(0)
        assert isinstance(rows, list)
        return rows


class _Connection:
    def __init__(self, cursor: _Cursor) -> None:
        self._cursor = cursor

    def __enter__(self) -> _Connection:
        return self

    def __exit__(self, *_: object) -> None:
        return None

    def cursor(self) -> _Cursor:
        return self._cursor


def test_cancellation_rejects_existing_fills_and_keeps_every_query_owner_scoped() -> None:
    cursor = _Cursor(
        [
            ("portfolio-id",),
            ("portfolio-id", "USD", Decimal("10000")),
            None,
            ("partially_filled",),
            (True,),
        ]
    )
    repository = PostgresPaperRepository(lambda: _Connection(cursor))

    with pytest.raises(ValueError, match="position_has_fills"):
        repository.cancel(
            "11111111-1111-1111-1111-111111111111",
            "22222222-2222-2222-2222-222222222222",
            CancellationCreate(reason="Stop remaining simulation"),
            idempotency_key="cancel-key-123",
        )

    assert not any("update public.paper_" in sql.lower() for sql, _ in cursor.statements)
    assert all("user_id" in sql.lower() for sql, _ in cursor.statements)
    assert all(
        "11111111-1111-1111-1111-111111111111" in parameters
        for _, parameters in cursor.statements
    )


def test_position_creation_rejects_cross_currency_opportunity_before_reserving_cash() -> None:
    cursor = _Cursor(
        [
            ("portfolio-id", "USD", Decimal("10000")),
            None,
            (
                "opportunity-id",
                "prediction_prediction",
                Decimal("210"),
                "pUSD",
                Decimal("210"),
                {},
                "Example bundle",
                "basketball",
            ),
        ]
    )
    repository = PostgresPaperRepository(lambda: _Connection(cursor))

    with pytest.raises(ValueError, match="opportunity_currency_mismatch"):
        repository.create_position(
            "11111111-1111-1111-1111-111111111111",
            PaperPositionCreate(
                opportunity_id="opportunity-id",
                total_capital=Decimal("210"),
                notes="Must not mix settlement units",
            ),
            idempotency_key="create-key-123",
        )

    assert not any(
        "insert into public.paper_positions" in sql.lower()
        or "insert into public.paper_ledger_entries" in sql.lower()
        for sql, _ in cursor.statements
    )


def test_fill_idempotency_replay_must_match_the_original_payload() -> None:
    when = datetime.now(UTC) - timedelta(minutes=1)
    cursor = _Cursor([
        ("portfolio-id",),
        ("portfolio-id", "USD", Decimal("10000")),
        (
            "position-id", "leg-id", Decimal("10"), Decimal("2.2"),
            Decimal("0.25"), when,
        ),
    ])
    repository = PostgresPaperRepository(lambda: _Connection(cursor))

    with pytest.raises(ValueError, match="idempotency_key_reused"):
        repository.add_fill(
            "user-id",
            "position-id",
            FillCreate(
                leg_id="leg-id",
                quantity=Decimal("11"),
                price=Decimal("2.2"),
                fee=Decimal("0.25"),
                filled_at=when,
            ),
            idempotency_key="fill-key-123",
        )


def test_cancellation_idempotency_replay_must_match_the_original_reason() -> None:
    cursor = _Cursor([
        ("portfolio-id",),
        ("portfolio-id", "USD", Decimal("10000")),
        ("Cancelled unfilled paper bundle: Original reason",),
    ])
    repository = PostgresPaperRepository(lambda: _Connection(cursor))

    with pytest.raises(ValueError, match="idempotency_key_reused"):
        repository.cancel(
            "user-id",
            "position-id",
            CancellationCreate(reason="Changed reason"),
            idempotency_key="cancel-key-123",
        )


def test_settlement_idempotency_replay_must_match_the_original_payload() -> None:
    when = datetime.now(UTC) - timedelta(minutes=1)
    cursor = _Cursor([
        ("portfolio-id",),
        ("portfolio-id", "USD", Decimal("10000")),
        ("position-id", "home", "https://example.test/result", when),
    ])
    repository = PostgresPaperRepository(lambda: _Connection(cursor))

    with pytest.raises(ValueError, match="idempotency_key_reused"):
        repository.settle(
            "user-id",
            "position-id",
            SettlementCreate(
                outcome_id="away",
                result_source_url="https://example.test/result",
                settled_at=when,
            ),
            idempotency_key="settle-key-123",
        )


def test_unverified_void_word_cannot_force_a_full_refund() -> None:
    cursor = _Cursor([
        ("portfolio-id",),
        ("portfolio-id", "USD", Decimal("10000")),
        None,
        ("filled",),
        [(
            "leg-id", "back", "outcome-id", "home", "Home",
            Decimal("1"), Decimal("10"), Decimal("2"), Decimal("10"), Decimal("0"),
        )],
        None,
    ])
    repository = PostgresPaperRepository(lambda: _Connection(cursor))

    with pytest.raises(ValueError, match="unknown_outcome"):
        repository.settle(
            "user-id",
            "position-id",
            SettlementCreate(outcome_id="void"),
            idempotency_key="settle-key-void",
        )


def test_fill_and_settlement_timestamps_require_explicit_timezones() -> None:
    naive = datetime(2026, 10, 20, 12, 0)
    with pytest.raises(ValueError, match="timezone"):
        FillCreate(
            leg_id="leg-id", quantity=Decimal("1"), price=Decimal("2"),
            filled_at=naive,
        )
    with pytest.raises(ValueError, match="timezone"):
        SettlementCreate(outcome_id="home", settled_at=naive)


def test_future_fill_and_settlement_timestamps_are_rejected_before_database_access() -> None:
    repository = PostgresPaperRepository(lambda: pytest.fail("database must not be touched"))
    future = datetime.now(UTC) + timedelta(hours=1)
    with pytest.raises(ValueError, match="filled_at_in_future"):
        repository.add_fill(
            "user-id", "position-id",
            FillCreate(
                leg_id="leg-id", quantity=Decimal("1"), price=Decimal("2"),
                filled_at=future,
            ),
            idempotency_key="fill-key-future",
        )
    with pytest.raises(ValueError, match="settled_at_in_future"):
        repository.settle(
            "user-id", "position-id",
            SettlementCreate(outcome_id="home", settled_at=future),
            idempotency_key="settle-key-future",
        )


def test_range_analytics_uses_prior_equity_and_range_capital_for_roi() -> None:
    from_ = datetime(2026, 10, 1, tzinfo=UTC)
    to = datetime(2026, 10, 31, tzinfo=UTC)
    settled_at = datetime(2026, 10, 20, tzinfo=UTC)
    cursor = _Cursor([
        ("portfolio-id", "USD", Decimal("10000")),
        (
            Decimal("9800"), Decimal("100"), Decimal("60"), Decimal("0"),
            Decimal("100"), 1, 2, Decimal("600"),
        ),
        [(settled_at, Decimal("10"), "two_way", "football", "position-id", Decimal("200"))],
        (Decimal("50"),),
        (Decimal("1"), Decimal("200")),
        (1, 1),
    ])
    repository = PostgresPaperRepository(lambda: _Connection(cursor))

    result = repository.analytics("user-id", from_, to)

    assert result["summary"]["realized_pnl"] == Decimal("10")
    assert result["summary"]["net_roi"] == Decimal("0.05")
    assert result["summary"]["roi_capital_basis"] == Decimal("200")
    assert result["equity"][0]["portfolio_value"] == Decimal("10050")
    assert result["equity"][1]["portfolio_value"] == Decimal("10060")
