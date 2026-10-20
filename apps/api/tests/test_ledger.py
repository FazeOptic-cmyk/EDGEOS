from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest

from edgeos.arbitrage import calculate_arbitrage
from edgeos.domain import FillCreate, PositionCreate, PositionStatus, SettlementCreate
from edgeos.ledger import PaperLedger
from edgeos.local_persistence import LocalPaperLedger, LocalStateDatabase

from conftest import decimal_offer


def test_append_only_ledger_uses_actual_fill_prices_and_isolates_users(fixed_now, zero_fee) -> None:
    simulation = calculate_arbitrage(
        [decimal_offer("a", "home", "2.20", fixed_now, zero_fee), decimal_offer("b", "away", "2.00", fixed_now, zero_fee)],
        Decimal("210"), now=fixed_now,
    )
    ledger = PaperLedger()
    position = ledger.create_position("alice", PositionCreate(simulation=simulation))
    ledger.add_fill("alice", position.position_id, FillCreate(
        leg_id="a", quantity=Decimal("100"), price=Decimal("2.10"), filled_at=fixed_now,
    ))
    ledger.add_fill("alice", position.position_id, FillCreate(
        leg_id="b", quantity=Decimal("110"), price=Decimal("2.00"), filled_at=fixed_now,
    ))
    assert ledger.summary("alice")["realized_pnl"] == Decimal("0")
    assert ledger.summary("alice")["simulated_cash_flow"] == Decimal("-210")
    ledger.settle("alice", position.position_id, SettlementCreate(outcome_id="home", settled_at=fixed_now))
    # Actual odds 2.10 are honored: 210 payout - 210 staked = 0, not theoretical +10.
    assert ledger.summary("alice")["realized_pnl"] == Decimal("0")
    assert [entry.entry_type for entry in ledger.entries("alice")] == [
        "position_opened", "fill", "fill", "settlement_payout"
    ]
    with pytest.raises(KeyError):
        ledger.get_position("bob", position.position_id)
    assert ledger.positions("bob") == ()


def test_fill_cannot_exceed_planned_leg_quantity(fixed_now, zero_fee) -> None:
    simulation = calculate_arbitrage(
        [decimal_offer("a", "home", "2.20", fixed_now, zero_fee), decimal_offer("b", "away", "2.00", fixed_now, zero_fee)],
        Decimal("210"), now=fixed_now,
    )
    ledger = PaperLedger()
    position = ledger.create_position("alice", PositionCreate(simulation=simulation))
    with pytest.raises(ValueError, match="fill_exceeds_planned_quantity"):
        ledger.add_fill("alice", position.position_id, FillCreate(
            leg_id="a", quantity=Decimal("100.01"), price=Decimal("2.20"), filled_at=fixed_now,
        ))


def test_contract_fill_cost_uses_actual_ask(fixed_now, zero_fee) -> None:
    from edgeos.domain import Offer, PriceKind

    offers = [Offer(
        offer_id=side, provider=side, outcome_id=side,
        price_kind=PriceKind.CONTRACT_ASK, price=Decimal("0.49"),
        quote_timestamp=fixed_now, fee_schedule=zero_fee,
        quantity_increment=Decimal("1"), maximum_quantity=Decimal("100"), data_origin="historical",
    ) for side in ("yes", "no")]
    simulation = calculate_arbitrage(offers, Decimal("9.8"), now=fixed_now)
    ledger = PaperLedger()
    position = ledger.create_position("alice", PositionCreate(simulation=simulation))
    ledger.add_fill("alice", position.position_id, FillCreate(
        leg_id="yes", quantity=Decimal("10"), price=Decimal("0.51"), filled_at=fixed_now,
    ))
    assert ledger.summary("alice")["deployed_capital"] == Decimal("5.10")


def test_cancel_cannot_erase_recorded_fill_exposure(fixed_now, zero_fee) -> None:
    simulation = calculate_arbitrage(
        [
            decimal_offer("a", "home", "2.20", fixed_now, zero_fee),
            decimal_offer("b", "away", "2.00", fixed_now, zero_fee),
        ],
        Decimal("210"),
        now=fixed_now,
        expected_outcome_ids=("home", "away"),
    )
    ledger = PaperLedger()
    position = ledger.create_position(
        "alice",
        PositionCreate(simulation=simulation),
        idempotency_key="position-cancel-filled",
    )
    first_leg = simulation.legs[0]
    ledger.add_fill(
        "alice",
        position.position_id,
        FillCreate(
            leg_id=first_leg.offer_id,
            quantity=first_leg.filled_quantity,
            price=Decimal("2.20"),
        ),
        idempotency_key="fill-before-cancel",
    )

    with pytest.raises(ValueError, match="filled_position_cannot_be_cancelled"):
        ledger.cancel(
            "alice",
            position.position_id,
            reason="second leg was unavailable",
            idempotency_key="cancel-filled-position",
        )

    assert ledger.get_position("alice", position.position_id).status is PositionStatus.OPEN
    assert ledger.summary("alice")["simulated_cash_flow"] < 0


def test_fill_cannot_spend_more_than_released_reserve_and_available_cash(
    fixed_now, zero_fee
) -> None:
    """A fee or adverse fill cannot silently create a negative paper balance."""

    simulation = calculate_arbitrage(
        [
            decimal_offer("a", "home", "2.20", fixed_now, zero_fee),
            decimal_offer("b", "away", "2.00", fixed_now, zero_fee),
        ],
        Decimal("210"),
        now=fixed_now,
    )
    ledger = PaperLedger(starting_balance=Decimal("210"))
    position = ledger.create_position("alice", PositionCreate(simulation=simulation))

    ledger.add_fill(
        "alice",
        position.position_id,
        FillCreate(
            leg_id="a", quantity=Decimal("50"), price=Decimal("2.20"), filled_at=fixed_now
        ),
    )
    summary = ledger.summary("alice")
    assert summary["cash_balance"] == Decimal("0")
    assert summary["reserved_capital"] == Decimal("160")
    assert summary["deployed_capital"] == Decimal("50")

    with pytest.raises(ValueError, match="insufficient_unreserved_paper_capital"):
        ledger.add_fill(
            "alice",
            position.position_id,
            FillCreate(
                leg_id="a",
                quantity=Decimal("50"),
                price=Decimal("2.20"),
                fee=Decimal("0.01"),
                filled_at=fixed_now,
            ),
        )

    # A rejected fill is atomic: neither the fill nor a cash entry survives.
    assert sum(fill.quantity for fill in ledger.get_position("alice", position.position_id).fills) == Decimal("50")
    assert ledger.summary("alice") == summary


def test_contract_fill_fee_and_adverse_ask_use_exact_cash(fixed_now, zero_fee) -> None:
    from edgeos.domain import Offer, PriceKind

    offers = [
        Offer(
            offer_id=side,
            provider=side,
            outcome_id=side,
            price_kind=PriceKind.CONTRACT_ASK,
            price=Decimal("0.49"),
            quote_timestamp=fixed_now,
            fee_schedule=zero_fee,
            quantity_increment=Decimal("1"),
            maximum_quantity=Decimal("100"),
            data_origin="historical",
        )
        for side in ("yes", "no")
    ]
    simulation = calculate_arbitrage(offers, Decimal("9.8"), now=fixed_now)
    ledger = PaperLedger(starting_balance=Decimal("10.8"))
    position = ledger.create_position("alice", PositionCreate(simulation=simulation))
    ledger.add_fill(
        "alice",
        position.position_id,
        FillCreate(
            leg_id="yes",
            quantity=Decimal("10"),
            price=Decimal("0.51"),
            fee=Decimal("0.10"),
            filled_at=fixed_now,
        ),
    )

    summary = ledger.summary("alice")
    assert summary["simulated_cash_flow"] == Decimal("-5.20")
    assert summary["deployed_capital"] == Decimal("5.20")
    assert summary["reserved_capital"] == Decimal("4.90")
    assert summary["cash_balance"] == Decimal("0.70")


def test_settlement_rejects_an_unfilled_position(fixed_now, zero_fee) -> None:
    simulation = calculate_arbitrage(
        [
            decimal_offer("a", "home", "2.20", fixed_now, zero_fee),
            decimal_offer("b", "away", "2.00", fixed_now, zero_fee),
        ],
        Decimal("210"),
        now=fixed_now,
    )
    ledger = PaperLedger(starting_balance=Decimal("210"))
    position = ledger.create_position("alice", PositionCreate(simulation=simulation))

    with pytest.raises(ValueError, match="position_has_no_fills"):
        ledger.settle(
            "alice",
            position.position_id,
            SettlementCreate(outcome_id="home", settled_at=fixed_now),
        )

    assert ledger.get_position("alice", position.position_id).status is PositionStatus.OPEN
    assert ledger.summary("alice")["reserved_capital"] == Decimal("210")


def test_cash_reserve_and_position_value_conserve_across_full_lifecycle(
    fixed_now, zero_fee
) -> None:
    simulation = calculate_arbitrage(
        [
            decimal_offer("a", "home", "2.20", fixed_now, zero_fee),
            decimal_offer("b", "away", "2.00", fixed_now, zero_fee),
        ],
        Decimal("210"),
        now=fixed_now,
    )
    ledger = PaperLedger(starting_balance=Decimal("211"))
    position = ledger.create_position("alice", PositionCreate(simulation=simulation))
    ledger.add_fill(
        "alice",
        position.position_id,
        FillCreate(
            leg_id="a",
            quantity=Decimal("100"),
            price=Decimal("2.10"),
            fee=Decimal("1"),
            filled_at=fixed_now,
        ),
    )
    ledger.add_fill(
        "alice",
        position.position_id,
        FillCreate(
            leg_id="b", quantity=Decimal("110"), price=Decimal("2.00"), filled_at=fixed_now
        ),
    )

    open_summary = ledger.summary("alice")
    assert open_summary["cash_balance"] == Decimal("0")
    assert open_summary["reserved_capital"] == Decimal("0")
    assert open_summary["deployed_capital"] == Decimal("211")
    assert open_summary["portfolio_value"] == Decimal("211")

    ledger.settle(
        "alice",
        position.position_id,
        SettlementCreate(outcome_id="home", settled_at=fixed_now),
    )
    settled = ledger.summary("alice")
    assert settled["simulated_cash_flow"] == Decimal("-1")
    assert settled["realized_pnl"] == Decimal("-1")
    assert settled["cash_balance"] == Decimal("210")
    assert settled["reserved_capital"] == Decimal("0")
    assert settled["portfolio_value"] == Decimal("210")
    assert settled["roi"] == Decimal("-1") / Decimal("211")


def test_local_restart_preserves_exact_cash_reserve_and_fill_accounting(
    tmp_path: Path, fixed_now, zero_fee
) -> None:
    simulation = calculate_arbitrage(
        [
            decimal_offer("a", "home", "2.20", fixed_now, zero_fee),
            decimal_offer("b", "away", "2.00", fixed_now, zero_fee),
        ],
        Decimal("210"),
        now=fixed_now,
    )
    database = LocalStateDatabase(
        tmp_path / "data" / "edgeos-local.sqlite3", allowed_root=tmp_path
    )
    ledger = LocalPaperLedger(database, starting_balance=Decimal("211"))
    position = ledger.create_position("alice", PositionCreate(simulation=simulation))
    ledger.add_fill(
        "alice",
        position.position_id,
        FillCreate(
            leg_id="a",
            quantity=Decimal("50"),
            price=Decimal("2.20"),
            fee=Decimal("0.50"),
            filled_at=fixed_now,
        ),
    )
    before_restart = ledger.summary("alice")

    reopened = LocalPaperLedger(
        LocalStateDatabase(
            tmp_path / "data" / "edgeos-local.sqlite3", allowed_root=tmp_path
        )
    )
    assert reopened.summary("alice") == before_restart
    assert reopened.starting_balance == Decimal("211")
    assert before_restart["cash_balance"] == Decimal("0.50")
    assert before_restart["reserved_capital"] == Decimal("160")
    assert before_restart["deployed_capital"] == Decimal("50.50")
    assert before_restart["portfolio_value"] == Decimal("211")

