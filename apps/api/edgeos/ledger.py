from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from threading import RLock
from uuid import uuid4

from pydantic import BaseModel, ConfigDict

from .domain import AllocationLeg, FillCreate, PositionCreate, PositionStatus, PriceKind, SettlementCreate


class LedgerEntry(BaseModel):
    model_config = ConfigDict(frozen=True)
    entry_id: str
    user_id: str
    position_id: str
    entry_type: str
    amount: Decimal
    occurred_at: datetime
    metadata: dict[str, str] = {}


class PaperFill(BaseModel):
    model_config = ConfigDict(frozen=True)
    fill_id: str
    leg_id: str
    quantity: Decimal
    price: Decimal
    fee: Decimal
    filled_at: datetime


class PaperPosition(BaseModel):
    model_config = ConfigDict(frozen=True)
    position_id: str
    user_id: str
    created_at: datetime
    status: PositionStatus
    request: PositionCreate
    fills: tuple[PaperFill, ...] = ()
    settled_outcome_id: str | None = None
    settled_at: datetime | None = None


class PaperLedger:
    """Append-only paper ledger with strict user scoping.

    This in-process implementation powers demo mode. Production persistence can
    implement the same methods against the RLS-protected Postgres tables.
    """

    def __init__(self, *, starting_balance: Decimal = Decimal("10000")) -> None:
        if starting_balance < 0:
            raise ValueError("starting_balance cannot be negative")
        self.starting_balance = starting_balance
        self._positions: dict[str, PaperPosition] = {}
        self._entries: list[LedgerEntry] = []
        self._idempotency: dict[tuple[str, str, str], str] = {}
        self._lock = RLock()

    def create_position(
        self,
        user_id: str,
        request: PositionCreate,
        *,
        idempotency_key: str | None = None,
    ) -> PaperPosition:
        operation_key = (user_id, "create_position", idempotency_key or "")
        if idempotency_key and operation_key in self._idempotency:
            return self.get_position(user_id, self._idempotency[operation_key])
        now = datetime.now(UTC)
        position = PaperPosition(
            position_id=str(uuid4()), user_id=user_id, created_at=now,
            status=PositionStatus.OPEN, request=request,
        )
        with self._lock:
            summary = self.summary(user_id)
            if request.simulation.requested_allocation > summary["cash_balance"]:
                raise ValueError("insufficient_unreserved_paper_capital")
            self._positions[position.position_id] = position
            self._entries.append(LedgerEntry(
                entry_id=str(uuid4()), user_id=user_id, position_id=position.position_id,
                entry_type="position_opened", amount=Decimal("0"), occurred_at=now,
                metadata={"simulation": "true"},
            ))
            if idempotency_key:
                self._idempotency[operation_key] = position.position_id
        return position

    def get_position(self, user_id: str, position_id: str) -> PaperPosition:
        position = self._positions.get(position_id)
        if position is None or position.user_id != user_id:
            # Identical result for missing and foreign-owned IDs prevents enumeration.
            raise KeyError(position_id)
        return position

    @staticmethod
    def _fill_cost(
        leg: AllocationLeg, *, quantity: Decimal, price: Decimal, fee: Decimal
    ) -> Decimal:
        """Return the cash consumed by an actual simulated fill.

        Decimal-odds quantities are stakes, while prediction-market quantities
        are contract counts.  Keep this definition in one place so admission,
        reporting, and settlement all use the same cash-flow convention.
        """

        notional = quantity if leg.price_kind is PriceKind.DECIMAL_ODDS else quantity * price
        return notional + fee

    @staticmethod
    def _leg_reserves(position: PaperPosition) -> dict[str, Decimal]:
        """Allocate the position reserve across its planned legs.

        The optimizer may leave a small amount undeployed because of contract
        increments.  Distributing the entire requested allocation in
        proportion to modeled leg spend mirrors the durable ledger's planned
        stakes and ensures a fully filled bundle releases its whole reserve.
        The last leg receives the Decimal remainder so the allocations add
        exactly to ``requested_allocation``.
        """

        legs = position.request.simulation.legs
        if not legs:
            return {}
        total = sum((leg.cash_cost + leg.fees for leg in legs), Decimal("0"))
        remaining = position.request.simulation.requested_allocation
        reserves: dict[str, Decimal] = {}
        for index, leg in enumerate(legs):
            if index == len(legs) - 1:
                reserve = remaining
            elif total > 0:
                reserve = position.request.simulation.requested_allocation * (
                    leg.cash_cost + leg.fees
                ) / total
            else:
                reserve = position.request.simulation.requested_allocation / Decimal(len(legs))
            reserve = max(Decimal("0"), min(remaining, reserve))
            reserves[leg.offer_id] = reserve
            remaining -= reserve
        return reserves

    @classmethod
    def _remaining_reserve(cls, position: PaperPosition) -> Decimal:
        if position.status is not PositionStatus.OPEN:
            return Decimal("0")
        reserves = cls._leg_reserves(position)
        remaining = Decimal("0")
        for leg in position.request.simulation.legs:
            planned_quantity = leg.filled_quantity
            if planned_quantity <= 0:
                remaining += reserves.get(leg.offer_id, Decimal("0"))
                continue
            filled_quantity = sum(
                (fill.quantity for fill in position.fills if fill.leg_id == leg.offer_id),
                Decimal("0"),
            )
            unfilled_fraction = max(
                Decimal("0"),
                Decimal("1") - min(Decimal("1"), filled_quantity / planned_quantity),
            )
            remaining += reserves.get(leg.offer_id, Decimal("0")) * unfilled_fraction
        return max(Decimal("0"), remaining)

    def add_fill(
        self,
        user_id: str,
        position_id: str,
        request: FillCreate,
        *,
        idempotency_key: str | None = None,
    ) -> PaperPosition:
        with self._lock:
            operation_key = (user_id, f"fill:{position_id}", idempotency_key or "")
            if idempotency_key and operation_key in self._idempotency:
                return self.get_position(user_id, position_id)
            position = self.get_position(user_id, position_id)
            if position.status is not PositionStatus.OPEN:
                raise ValueError("position_not_open")
            if request.leg_id not in {leg.offer_id for leg in position.request.simulation.legs}:
                raise ValueError("unknown_leg")
            when = (request.filled_at or datetime.now(UTC)).astimezone(UTC)
            fill = PaperFill(
                fill_id=str(uuid4()), leg_id=request.leg_id, quantity=request.quantity,
                price=request.price, fee=request.fee, filled_at=when,
            )
            leg = next(leg for leg in position.request.simulation.legs if leg.offer_id == request.leg_id)
            already_filled = sum((item.quantity for item in position.fills if item.leg_id == request.leg_id), Decimal("0"))
            if already_filled + request.quantity > leg.filled_quantity:
                raise ValueError("fill_exceeds_planned_quantity")
            if leg.price_kind is PriceKind.DECIMAL_ODDS and request.price <= 1:
                raise ValueError("decimal_odds_fill_price_must_exceed_one")
            if leg.price_kind is PriceKind.CONTRACT_ASK and request.price > 1:
                raise ValueError("contract_fill_price_must_not_exceed_one")
            updated = position.model_copy(update={"fills": position.fills + (fill,)})
            cash_cost = self._fill_cost(
                leg, quantity=request.quantity, price=request.price, fee=request.fee
            )
            # Filling releases only the proportional planned reserve.  A worse
            # executable price or fee may consume genuinely unreserved paper
            # cash, but it can never make available cash negative.
            released_reserve = self._remaining_reserve(position) - self._remaining_reserve(updated)
            available_after_release = Decimal(self.summary(user_id)["cash_balance"]) + released_reserve
            if cash_cost > available_after_release:
                raise ValueError("insufficient_unreserved_paper_capital")
            self._positions[position_id] = updated
            self._entries.append(LedgerEntry(
                entry_id=str(uuid4()), user_id=user_id, position_id=position_id,
                entry_type="fill", amount=-cash_cost, occurred_at=when,
                metadata={"fill_id": fill.fill_id, "leg_id": request.leg_id},
            ))
            if idempotency_key:
                self._idempotency[operation_key] = fill.fill_id
            return updated

    def settle(
        self,
        user_id: str,
        position_id: str,
        request: SettlementCreate,
        *,
        idempotency_key: str | None = None,
    ) -> PaperPosition:
        with self._lock:
            operation_key = (user_id, f"settle:{position_id}", idempotency_key or "")
            if idempotency_key and operation_key in self._idempotency:
                return self.get_position(user_id, position_id)
            position = self.get_position(user_id, position_id)
            if position.status is not PositionStatus.OPEN:
                raise ValueError("position_not_open")
            if not position.fills:
                raise ValueError("position_has_no_fills")
            if request.outcome_id not in {result.outcome_id for result in position.request.simulation.outcomes}:
                raise ValueError("unknown_outcome")
            when = (request.settled_at or datetime.now(UTC)).astimezone(UTC)
            payout = Decimal("0")
            for fill in position.fills:
                leg = next(leg for leg in position.request.simulation.legs if leg.offer_id == fill.leg_id)
                if leg.outcome_id == request.outcome_id:
                    payout += (
                        fill.quantity * fill.price
                        if leg.price_kind is PriceKind.DECIMAL_ODDS
                        else fill.quantity
                    )
            if payout:
                self._entries.append(LedgerEntry(
                    entry_id=str(uuid4()), user_id=user_id, position_id=position_id,
                    entry_type="settlement_payout", amount=payout, occurred_at=when,
                    metadata={"outcome_id": request.outcome_id},
                ))
            updated = position.model_copy(update={
                "status": PositionStatus.SETTLED,
                "settled_outcome_id": request.outcome_id,
                "settled_at": when,
            })
            self._positions[position_id] = updated
            if idempotency_key:
                self._idempotency[operation_key] = position_id
            return updated

    def cancel(
        self,
        user_id: str,
        position_id: str,
        *,
        reason: str,
        idempotency_key: str | None = None,
    ) -> PaperPosition:
        if not reason.strip():
            raise ValueError("cancellation_reason_required")
        with self._lock:
            operation_key = (user_id, f"cancel:{position_id}", idempotency_key or "")
            if idempotency_key and operation_key in self._idempotency:
                return self.get_position(user_id, position_id)
            position = self.get_position(user_id, position_id)
            if position.status is not PositionStatus.OPEN:
                raise ValueError("position_not_open")
            # Releasing an entirely unfilled plan is safe. Recorded fills are
            # economic exposure and may not disappear behind a status change;
            # they require settlement or an auditable correction/unwind.
            if position.fills:
                raise ValueError("filled_position_cannot_be_cancelled")
            when = datetime.now(UTC)
            updated = position.model_copy(update={"status": PositionStatus.CANCELED})
            self._positions[position_id] = updated
            self._entries.append(LedgerEntry(
                entry_id=str(uuid4()), user_id=user_id, position_id=position_id,
                entry_type="position_cancelled", amount=Decimal("0"), occurred_at=when,
                metadata={"reason": reason.strip()},
            ))
            if idempotency_key:
                self._idempotency[operation_key] = position_id
            return updated

    def adjustment(self, user_id: str, position_id: str, amount: Decimal, reason: str) -> LedgerEntry:
        if not reason.strip():
            raise ValueError("adjustment reason is required")
        self.get_position(user_id, position_id)
        entry = LedgerEntry(
            entry_id=str(uuid4()), user_id=user_id, position_id=position_id,
            entry_type="audited_adjustment", amount=amount, occurred_at=datetime.now(UTC),
            metadata={"reason": reason.strip()},
        )
        with self._lock:
            self._entries.append(entry)
        return entry

    def positions(self, user_id: str) -> tuple[PaperPosition, ...]:
        return tuple(position for position in self._positions.values() if position.user_id == user_id)

    def entries(self, user_id: str) -> tuple[LedgerEntry, ...]:
        return tuple(entry for entry in self._entries if entry.user_id == user_id)

    def summary(self, user_id: str) -> dict[str, Decimal | int]:
        entries = self.entries(user_id)
        positions = self.positions(user_id)
        settled_ids = {position.position_id for position in positions if position.status is PositionStatus.SETTLED}
        realized = sum((entry.amount for entry in entries if entry.position_id in settled_ids), Decimal("0"))
        cash_flow = sum((entry.amount for entry in entries), Decimal("0"))
        deployed = -sum((entry.amount for entry in entries if entry.entry_type == "fill"), Decimal("0"))
        settled_deployed = -sum(
            (entry.amount for entry in entries if entry.entry_type == "fill" and entry.position_id in settled_ids),
            Decimal("0"),
        )
        active_positions = tuple(position for position in positions if position.status is PositionStatus.OPEN)
        reserved = sum((self._remaining_reserve(position) for position in active_positions), Decimal("0"))
        active_deployed = -sum(
            (
                entry.amount
                for entry in entries
                if entry.entry_type == "fill" and entry.position_id not in settled_ids
            ),
            Decimal("0"),
        )
        cash_balance = self.starting_balance + cash_flow - reserved
        # Without trustworthy current marks, active fills remain at exact cost.
        # This keeps cash, reserve, and deployed capital reconcilable while
        # leaving unrealized P&L explicitly zero.
        portfolio_value = cash_balance + reserved + active_deployed
        return {
            "simulated_cash_flow": cash_flow,
            "realized_pnl": realized,
            "unrealized_pnl": Decimal("0"),
            "deployed_capital": deployed,
            "reserved_capital": reserved,
            "cash_balance": cash_balance,
            "portfolio_value": portfolio_value,
            "roi": realized / settled_deployed if settled_deployed else Decimal("0"),
            "active_positions": len(active_positions),
            "settled_positions": sum(p.status is PositionStatus.SETTLED for p in positions),
        }

