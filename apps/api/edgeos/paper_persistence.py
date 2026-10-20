"""Transactional PostgreSQL paper-portfolio accounting.

The repository is server-only.  Every private query carries ``user_id`` even
though the connection is trusted, and every mutation locks the owning
portfolio so concurrent requests cannot reserve the same paper cash twice.
All values remain ``Decimal``/PostgreSQL ``numeric`` from request to ledger.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from decimal import Decimal, ROUND_HALF_UP
import json
from typing import Any
from uuid import uuid4

from .domain import CancellationCreate, FillCreate, PaperPositionCreate, SettlementCreate


MONEY = Decimal("0.00000001")
PRICE = Decimal("0.0000000001")
_ACTIVE = ("open", "partially_filled", "filled")
def money(value: Decimal) -> Decimal:
    """Round an accounting amount to the database's NUMERIC(24, 8) scale."""
    return value.quantize(MONEY, rounding=ROUND_HALF_UP)


def fill_notional(action: str, quantity: Decimal, price: Decimal) -> Decimal:
    """Return actual cash paid for a supported simulated fill."""
    if action == "back":
        if price <= 1:
            raise ValueError("decimal_odds_fill_price_must_exceed_one")
        return money(quantity)
    if action in {"buy_yes", "buy_no"}:
        if price > 1:
            raise ValueError("contract_fill_price_must_not_exceed_one")
        return money(quantity * price)
    # Lay/sell accounting needs collateral and short-sale rules that are not
    # represented by the current schema.  Reject rather than invent exposure.
    raise ValueError("unsupported_paper_fill_action")


def winning_payout(
    action: str,
    quantity: Decimal,
    fill_price: Decimal,
    payout_per_unit: Decimal,
) -> Decimal:
    if action == "back":
        return money(quantity * fill_price)
    if action in {"buy_yes", "buy_no"}:
        return money(quantity * payout_per_unit)
    raise ValueError("unsupported_paper_fill_action")


class PostgresPaperRepository:
    """Durable paper trading with append-only double-purpose cash entries."""

    def __init__(self, connection_factory: Callable[[], Any]) -> None:
        self._connect = connection_factory

    # ---- public reads -------------------------------------------------

    def portfolio(self, user_id: str) -> dict[str, object]:
        with self._connect() as connection, connection.cursor() as cursor:
            portfolio = self._select_portfolio(cursor, user_id, lock=False)
            summary = self._summary(cursor, user_id, portfolio)
            positions = self._positions(cursor, user_id)
        return {
            "mode": "live",
            "currency": portfolio[1],
            "summary": summary,
            "positions": positions,
            "simulation_only": True,
            "is_demo": False,
        }

    def ledger_entries(self, user_id: str) -> tuple[dict[str, object], ...]:
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                select l.id::text, l.position_id::text, l.entry_type,
                       l.signed_amount, l.currency, l.occurred_at, l.memo
                  from public.paper_ledger_entries l
                 where l.user_id = %s
                 order by l.occurred_at, l.id
                """,
                (user_id,),
            )
            rows = cursor.fetchall()
        return tuple(
            {
                "entry_id": row[0],
                "position_id": row[1],
                "entry_type": row[2],
                "amount": row[3],
                "currency": row[4],
                "occurred_at": row[5],
                "memo": row[6],
            }
            for row in rows
        )

    # ---- mutations ----------------------------------------------------

    def create_position(
        self,
        user_id: str,
        request: PaperPositionCreate,
        *,
        idempotency_key: str,
    ) -> dict[str, object]:
        reference = f"api:create:{idempotency_key}"
        with self._connect() as connection, connection.cursor() as cursor:
            portfolio = self._select_portfolio(cursor, user_id, lock=True)
            cursor.execute(
                """
                select id::text, opportunity_id::text, planned_allocation, notes
                  from public.paper_positions
                 where user_id = %s and external_reference = %s
                 order by created_at limit 1
                """,
                (user_id, reference),
            )
            replay = cursor.fetchone()
            if replay is not None:
                if (
                    replay[1] != request.opportunity_id
                    or replay[2] != request.total_capital
                    or replay[3] != request.notes
                ):
                    raise ValueError("idempotency_key_reused")
                return self._position(cursor, user_id, replay[0])

            cursor.execute(
                """
                select o.id::text, o.strategy_type, o.total_allocation, o.currency,
                       o.maximum_depth, o.assumptions,
                       coalesce((select e.name from public.events e where e.id = o.event_id),
                                'Paper research bundle'),
                       coalesce((select s.slug from public.events e
                                   join public.sports s on s.id = e.sport_id
                                  where e.id = o.event_id), 'unknown')
                  from public.opportunities o
                 where o.id::text = %s and o.status = 'active'
                   and o.match_status = 'verified' and not o.is_demo
                   and jsonb_array_length(o.hard_gate_failures) = 0
                   and (o.expires_at is null or o.expires_at > statement_timestamp())
                 for share of o
                """,
                (request.opportunity_id,),
            )
            opportunity = cursor.fetchone()
            if opportunity is None:
                raise KeyError(request.opportunity_id)
            if str(opportunity[3]) != str(portfolio[1]):
                # A single paper cash balance cannot safely aggregate USD,
                # USDC, pUSD, or any other settlement unit. Keep research
                # visible, but refuse to reserve it against the wrong ledger.
                raise ValueError("opportunity_currency_mismatch")
            original_allocation = Decimal(opportunity[2])
            if original_allocation <= 0:
                raise ValueError("opportunity_has_no_allocation")
            if opportunity[4] is not None and request.total_capital > opportunity[4]:
                raise ValueError("allocation_exceeds_available_depth")

            cash = self._cash_balance(cursor, user_id, portfolio[0])
            if request.total_capital > cash:
                raise ValueError("insufficient_unreserved_paper_capital")

            cursor.execute(
                """
                select ol.leg_number, ol.provider_id, ol.market_id, ol.outcome_id,
                       ol.action, ol.price, ol.quantity, ol.allocation, ol.fee
                  from public.opportunity_legs ol
                 where ol.opportunity_id = %s
                 order by ol.leg_number
                """,
                (opportunity[0],),
            )
            source_legs = cursor.fetchall()
            if not source_legs:
                raise ValueError("opportunity_has_no_legs")
            unsupported = [row[4] for row in source_legs if row[4] not in {"back", "buy_yes", "buy_no"}]
            if unsupported:
                raise ValueError("unsupported_paper_fill_action")

            scale = request.total_capital / original_allocation
            assumptions = dict(opportunity[5] or {})
            assumptions.update(
                {
                    "calculation_version": assumptions.get(
                        "calculation_version", "edgeos.cashflow.v2"
                    ),
                    "source_opportunity_id": opportunity[0],
                    "source_total_allocation": str(original_allocation),
                    "requested_total_capital": str(request.total_capital),
                    "scale_factor": str(scale),
                    "sport": opportunity[7],
                }
            )
            cursor.execute(
                """
                insert into public.paper_positions
                    (user_id, portfolio_id, opportunity_id, external_reference,
                     position_type, strategy_type, title, status, currency,
                     planned_allocation, notes, source_assumptions, opened_at)
                values (%s, %s, %s, %s, 'arbitrage_bundle', %s, %s, 'open',
                        %s, %s, %s, %s::jsonb, statement_timestamp())
                returning id::text
                """,
                (
                    user_id,
                    portfolio[0],
                    opportunity[0],
                    reference,
                    opportunity[1],
                    opportunity[6],
                    opportunity[3],
                    request.total_capital,
                    request.notes,
                    json.dumps(assumptions),
                ),
            )
            position_id = cursor.fetchone()[0]
            allocated = Decimal("0")
            for index, leg in enumerate(source_legs):
                stake = (
                    money(request.total_capital - allocated)
                    if index == len(source_legs) - 1
                    else money(Decimal(leg[7]) * scale)
                )
                allocated += stake
                quantity = money(Decimal(leg[6]) * scale)
                if quantity <= 0:
                    raise ValueError("scaled_quantity_below_database_precision")
                cursor.execute(
                    """
                    insert into public.paper_position_legs
                        (user_id, position_id, leg_number, provider_id, market_id,
                         outcome_id, action, planned_price, planned_quantity, planned_stake)
                    values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        user_id,
                        position_id,
                        leg[0],
                        leg[1],
                        leg[2],
                        leg[3],
                        leg[4],
                        leg[5],
                        quantity,
                        stake,
                    ),
                )
            self._entry(
                cursor,
                user_id=user_id,
                portfolio_id=portfolio[0],
                position_id=position_id,
                group_id=uuid4(),
                key=f"{reference}:reserve",
                kind="reserve",
                direction="debit",
                amount=request.total_capital,
                currency=opportunity[3],
                memo="Reserved for a simulated arbitrage bundle",
            )
            return self._position(cursor, user_id, position_id)

    def add_fill(
        self,
        user_id: str,
        position_id: str,
        request: FillCreate,
        *,
        idempotency_key: str,
    ) -> dict[str, object]:
        if (
            request.filled_at is not None
            and request.filled_at > datetime.now(UTC) + timedelta(minutes=5)
        ):
            raise ValueError("filled_at_in_future")
        fill_key = f"api:fill:{idempotency_key}"
        with self._connect() as connection, connection.cursor() as cursor:
            portfolio = self._lock_position_portfolio(cursor, user_id, position_id)
            cursor.execute(
                """
                select pl.position_id::text, f.leg_id::text, f.quantity,
                       f.price, f.fee, f.filled_at
                  from public.paper_fills f
                  join public.paper_position_legs pl
                    on pl.id = f.leg_id and pl.user_id = f.user_id
                 where f.user_id = %s and f.fill_key = %s
                """,
                (user_id, fill_key),
            )
            replay = cursor.fetchone()
            if replay is not None:
                requested_time = (
                    request.filled_at.astimezone(UTC)
                    if request.filled_at is not None
                    else None
                )
                if (
                    replay[0] != position_id
                    or replay[1] != request.leg_id
                    or Decimal(replay[2]) != request.quantity
                    or Decimal(replay[3]) != request.price
                    or Decimal(replay[4]) != money(request.fee)
                    or (requested_time is not None and replay[5] != requested_time)
                ):
                    raise ValueError("idempotency_key_reused")
                return self._position(cursor, user_id, position_id)

            cursor.execute(
                """
                select p.status, p.currency, l.action, l.planned_price,
                       l.planned_quantity, l.planned_stake, l.filled_quantity,
                       l.average_fill_price, l.simulated_fee
                  from public.paper_positions p
                  join public.paper_position_legs l
                    on l.position_id = p.id and l.user_id = p.user_id
                 where p.id::text = %s and p.user_id = %s and l.id::text = %s
                 for update of p, l
                """,
                (position_id, user_id, request.leg_id),
            )
            leg = cursor.fetchone()
            if leg is None:
                raise ValueError("unknown_leg")
            if leg[0] not in {"open", "partially_filled"}:
                raise ValueError("position_not_open")
            prior_quantity = Decimal(leg[6])
            planned_quantity = Decimal(leg[4])
            new_quantity = prior_quantity + request.quantity
            if new_quantity > planned_quantity:
                raise ValueError("fill_exceeds_planned_quantity")
            notional = fill_notional(leg[2], request.quantity, request.price)
            fee = money(request.fee)
            when = (request.filled_at or datetime.now(UTC)).astimezone(UTC)
            release = money(
                Decimal(leg[5]) * new_quantity / planned_quantity
                - Decimal(leg[5]) * prior_quantity / planned_quantity
            )
            group = uuid4()
            self._entry(
                cursor,
                user_id=user_id,
                portfolio_id=portfolio[0],
                position_id=position_id,
                group_id=group,
                key=f"{fill_key}:release",
                kind="release",
                direction="credit",
                amount=release,
                currency=leg[1],
                memo=f"Released proportional reserve for paper leg {request.leg_id}",
                occurred_at=when,
            )
            if notional + fee > self._cash_balance(cursor, user_id, portfolio[0]):
                raise ValueError("insufficient_unreserved_paper_capital")
            cursor.execute(
                """
                insert into public.paper_fills
                    (user_id, leg_id, fill_key, quantity, price, notional, fee,
                     slippage, filled_at)
                values (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    user_id,
                    request.leg_id,
                    fill_key,
                    request.quantity,
                    request.price,
                    notional,
                    fee,
                    request.price - Decimal(leg[3]),
                    when,
                ),
            )
            average = (
                (Decimal(leg[7]) * prior_quantity if leg[7] is not None else Decimal("0"))
                + request.price * request.quantity
            ) / new_quantity
            cursor.execute(
                """
                update public.paper_position_legs
                   set filled_quantity = %s, average_fill_price = %s,
                       simulated_fee = %s,
                       status = case when %s = planned_quantity
                                     then 'filled' else 'partially_filled' end
                 where id::text = %s and user_id = %s and position_id::text = %s
                """,
                (
                    new_quantity,
                    average.quantize(PRICE, rounding=ROUND_HALF_UP),
                    money(Decimal(leg[8]) + fee),
                    new_quantity,
                    request.leg_id,
                    user_id,
                    position_id,
                ),
            )
            self._entry(
                cursor,
                user_id=user_id,
                portfolio_id=portfolio[0],
                position_id=position_id,
                group_id=group,
                key=f"{fill_key}:cost",
                kind="fill_cost",
                direction="debit",
                amount=notional,
                currency=leg[1],
                memo=f"Actual simulated fill cost for leg {request.leg_id}",
                occurred_at=when,
            )
            if fee:
                self._entry(
                    cursor,
                    user_id=user_id,
                    portfolio_id=portfolio[0],
                    position_id=position_id,
                    group_id=group,
                    key=f"{fill_key}:fee",
                    kind="fee",
                    direction="debit",
                    amount=fee,
                    currency=leg[1],
                    memo=f"Actual simulated fill fee for leg {request.leg_id}",
                    occurred_at=when,
                )
            committed = self._committed(cursor, user_id, position_id)
            cursor.execute(
                """
                update public.paper_positions p
                   set committed_capital = %s, worst_case_exposure = %s,
                       status = case when exists (
                           select 1 from public.paper_position_legs l
                            where l.position_id = p.id and l.user_id = p.user_id
                              and l.filled_quantity < l.planned_quantity
                       ) then 'partially_filled' else 'filled' end
                 where p.id::text = %s and p.user_id = %s
                """,
                (committed, committed, position_id, user_id),
            )
            return self._position(cursor, user_id, position_id)

    def cancel(
        self,
        user_id: str,
        position_id: str,
        request: CancellationCreate,
        *,
        idempotency_key: str,
    ) -> dict[str, object]:
        operation_key = f"api:cancel:{idempotency_key}"
        with self._connect() as connection, connection.cursor() as cursor:
            portfolio = self._lock_position_portfolio(cursor, user_id, position_id)
            cursor.execute(
                """
                select memo from public.paper_ledger_entries
                 where user_id = %s and idempotency_key = %s
                """,
                (user_id, operation_key),
            )
            replay = cursor.fetchone()
            cancellation_memo = f"Cancelled unfilled paper bundle: {request.reason.strip()}"
            if replay is not None:
                if replay[0] != cancellation_memo:
                    raise ValueError("idempotency_key_reused")
                return self._position(cursor, user_id, position_id)
            status = self._position_status(cursor, user_id, position_id)
            if status not in _ACTIVE:
                raise ValueError("position_not_open")
            cursor.execute(
                """
                select exists (
                    select 1 from public.paper_fills f
                    join public.paper_position_legs l
                      on l.id = f.leg_id and l.user_id = f.user_id
                    where f.user_id = %s and l.position_id::text = %s
                )
                """,
                (user_id, position_id),
            )
            if cursor.fetchone()[0]:
                raise ValueError("position_has_fills")
            remaining = self._position_reserve(cursor, user_id, position_id)
            self._entry(
                cursor,
                user_id=user_id,
                portfolio_id=portfolio[0],
                position_id=position_id,
                group_id=uuid4(),
                key=operation_key,
                kind="release",
                direction="credit",
                amount=remaining,
                currency=portfolio[1],
                memo=cancellation_memo,
            )
            cursor.execute(
                """
                update public.paper_position_legs
                   set status = 'cancelled'
                 where user_id = %s and position_id::text = %s and status = 'pending'
                """,
                (user_id, position_id),
            )
            cursor.execute(
                """
                update public.paper_positions
                   set status = 'cancelled', closed_at = statement_timestamp()
                 where id::text = %s and user_id = %s
                """,
                (position_id, user_id),
            )
            return self._position(cursor, user_id, position_id)

    def settle(
        self,
        user_id: str,
        position_id: str,
        request: SettlementCreate,
        *,
        idempotency_key: str,
    ) -> dict[str, object]:
        if (
            request.settled_at is not None
            and request.settled_at > datetime.now(UTC) + timedelta(minutes=5)
        ):
            raise ValueError("settled_at_in_future")
        settlement_key = f"api:settle:{idempotency_key}"
        with self._connect() as connection, connection.cursor() as cursor:
            portfolio = self._lock_position_portfolio(cursor, user_id, position_id)
            cursor.execute(
                """
                select position_id::text, outcome_key, result_source_url, settled_at
                  from public.paper_settlements
                 where user_id = %s and settlement_key = %s
                """,
                (user_id, settlement_key),
            )
            replay = cursor.fetchone()
            if replay is not None:
                requested_time = (
                    request.settled_at.astimezone(UTC)
                    if request.settled_at is not None
                    else None
                )
                if (
                    replay[0] != position_id
                    or replay[1] != request.outcome_id
                    or replay[2] != request.result_source_url
                    or (requested_time is not None and replay[3] != requested_time)
                ):
                    raise ValueError("idempotency_key_reused")
                return self._position(cursor, user_id, position_id)
            status = self._position_status(cursor, user_id, position_id)
            if status not in _ACTIVE:
                raise ValueError("position_not_open")
            cursor.execute(
                """
                select l.id::text, l.action, o.id::text, o.canonical_key, o.name,
                       o.payout_per_unit, f.quantity, f.price, f.notional, f.fee
                  from public.paper_position_legs l
                  join public.outcomes o on o.id = l.outcome_id
                  join public.paper_fills f on f.leg_id = l.id and f.user_id = l.user_id
                 where l.user_id = %s and l.position_id::text = %s
                 order by l.leg_number, f.filled_at, f.id
                """,
                (user_id, position_id),
            )
            fills = cursor.fetchall()
            if not fills:
                raise ValueError("position_has_no_fills")
            cursor.execute(
                """
                select oo.scenario_data
                  from public.paper_positions p
                  join public.opportunity_outcomes oo on oo.opportunity_id = p.opportunity_id
                 where p.id::text = %s and p.user_id = %s
                   and lower(oo.scenario_key) = lower(%s)
                 limit 1
                """,
                (position_id, user_id, request.outcome_id),
            )
            scenario = cursor.fetchone()
            key = request.outcome_id.casefold()
            winners = [row for row in fills if key in {str(row[2]).casefold(), str(row[3]).casefold(), str(row[4]).casefold()}]
            scenario_data = dict(scenario[0] or {}) if scenario is not None else {}
            # A caller-provided word such as "void" is never proof of refund
            # treatment. Only the versioned opportunity scenario may authorize it.
            refund_all = scenario is not None and scenario_data.get("refund_all") is True
            if scenario is None and not winners and not refund_all:
                raise ValueError("unknown_outcome")
            if scenario is not None and not winners and not refund_all:
                raise ValueError("settlement_outcome_not_mappable")
            total_cost = money(sum((Decimal(row[8]) for row in fills), Decimal("0")))
            total_fees = money(sum((Decimal(row[9]) for row in fills), Decimal("0")))
            refunds = total_cost if refund_all else Decimal("0")
            payout = Decimal("0")
            if not refund_all:
                for row in winners:
                    payout += winning_payout(
                        row[1], Decimal(row[6]), Decimal(row[7]), Decimal(row[5])
                    )
            payout = money(payout)
            net_pnl = money(payout + refunds - total_cost - total_fees)
            when = (request.settled_at or datetime.now(UTC)).astimezone(UTC)
            cursor.execute(
                """
                insert into public.paper_settlements
                    (user_id, position_id, settlement_key, outcome_key, gross_payout,
                     refunds, fees, net_pnl, result_source_url, settlement_data, settled_at)
                values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s)
                """,
                (
                    user_id,
                    position_id,
                    settlement_key,
                    request.outcome_id,
                    payout,
                    refunds,
                    total_fees,
                    net_pnl,
                    request.result_source_url,
                    json.dumps(
                        {
                            "actual_fill_cost": str(total_cost),
                            "actual_fill_fees": str(total_fees),
                            "actual_fill_count": len(fills),
                            "refund_all": refund_all,
                            "calculation_version": "edgeos.paper-settlement.v1",
                        }
                    ),
                    when,
                ),
            )
            group = uuid4()
            remaining = self._position_reserve(cursor, user_id, position_id)
            if remaining:
                self._entry(
                    cursor,
                    user_id=user_id,
                    portfolio_id=portfolio[0],
                    position_id=position_id,
                    group_id=group,
                    key=f"{settlement_key}:release",
                    kind="release",
                    direction="credit",
                    amount=remaining,
                    currency=portfolio[1],
                    memo="Released unfilled reserve at settlement",
                    occurred_at=when,
                )
            if payout:
                self._entry(
                    cursor,
                    user_id=user_id,
                    portfolio_id=portfolio[0],
                    position_id=position_id,
                    group_id=group,
                    key=f"{settlement_key}:payout",
                    kind="payout",
                    direction="credit",
                    amount=payout,
                    currency=portfolio[1],
                    memo=f"Actual simulated payout for {request.outcome_id}",
                    occurred_at=when,
                )
            if refunds:
                self._entry(
                    cursor,
                    user_id=user_id,
                    portfolio_id=portfolio[0],
                    position_id=position_id,
                    group_id=group,
                    key=f"{settlement_key}:refund",
                    kind="refund",
                    direction="credit",
                    amount=refunds,
                    currency=portfolio[1],
                    memo=f"Actual simulated refund for {request.outcome_id}",
                    occurred_at=when,
                )
            cursor.execute(
                """
                update public.paper_position_legs
                   set status = case when %s then 'void' else 'settled' end
                 where user_id = %s and position_id::text = %s
                """,
                (refund_all, user_id, position_id),
            )
            cursor.execute(
                """
                update public.paper_positions
                   set status = 'settled', realized_pnl = %s, unrealized_pnl = 0,
                       settlement_outcome = %s, closed_at = %s, settled_at = %s
                 where id::text = %s and user_id = %s
                """,
                (net_pnl, request.outcome_id, when, when, position_id, user_id),
            )
            return self._position(cursor, user_id, position_id)

    # ---- analytics ----------------------------------------------------

    def analytics(self, user_id: str, from_: datetime, to: datetime) -> dict[str, object]:
        with self._connect() as connection, connection.cursor() as cursor:
            portfolio = self._select_portfolio(cursor, user_id, lock=False)
            summary = self._summary(cursor, user_id, portfolio)
            cursor.execute(
                """
                select p.settled_at, p.realized_pnl, p.strategy_type,
                       coalesce(s.slug, 'unknown'), p.id::text,
                       p.committed_capital
                  from public.paper_positions p
                  left join public.opportunities o on o.id = p.opportunity_id
                  left join public.events e on e.id = o.event_id
                  left join public.sports s on s.id = e.sport_id
                 where p.user_id = %s and p.status = 'settled'
                   and p.settled_at between %s and %s
                 order by p.settled_at, p.id
                """,
                (user_id, from_, to),
            )
            settled = cursor.fetchall()
            cursor.execute(
                """
                select coalesce(sum(p.realized_pnl), 0)
                  from public.paper_positions p
                 where p.user_id = %s and p.status = 'settled'
                   and p.settled_at < %s
                """,
                (user_id, from_),
            )
            prior_realized = Decimal(cursor.fetchone()[0])
            cursor.execute(
                """
                select coalesce(sum(f.fee), 0), coalesce(sum(f.notional), 0)
                  from public.paper_fills f
                  join public.paper_position_legs l
                    on l.id = f.leg_id and l.user_id = f.user_id
                 where f.user_id = %s and f.filled_at between %s and %s
                """,
                (user_id, from_, to),
            )
            fees, turnover = cursor.fetchone()
            cursor.execute(
                """
                select count(*), count(*) filter (where exists (
                         select 1 from public.paper_position_legs l
                         join public.paper_fills f
                           on f.leg_id = l.id and f.user_id = l.user_id
                        where l.position_id = p.id and l.user_id = p.user_id))
                  from public.paper_positions p
                 where p.user_id = %s and p.created_at between %s and %s
                """,
                (user_id, from_, to),
            )
            attempted, captured = cursor.fetchone()

        starting = Decimal(portfolio[2])
        # Historical realized equity intentionally ignores reservations and
        # unmarked open positions; it never presents theoretical P&L as earned.
        opening_value = starting + prior_realized
        series: list[dict[str, object]] = [
            {"timestamp": from_, "portfolio_value": opening_value, "cumulative_pnl": prior_realized}
        ]
        peak = opening_value
        maximum_drawdown = Decimal("0")
        running = prior_realized
        for row in settled:
            running += Decimal(row[1])
            value = starting + running
            peak = max(peak, value)
            maximum_drawdown = max(maximum_drawdown, peak - value)
            series.append(
                {"timestamp": row[0], "portfolio_value": value, "cumulative_pnl": running}
            )

        def grouped(index: int) -> list[dict[str, object]]:
            buckets: dict[str, tuple[Decimal, int]] = {}
            for row in settled:
                label = str(row[index])
                value, count = buckets.get(label, (Decimal("0"), 0))
                buckets[label] = (value + Decimal(row[1]), count + 1)
            return [
                {"key": key, "label": key.replace("_", " ").title(), "net_pnl": value, "count": count}
                for key, (value, count) in sorted(buckets.items())
            ]

        range_realized = sum((Decimal(row[1]) for row in settled), Decimal("0"))
        range_capital = sum((Decimal(row[5]) for row in settled), Decimal("0"))
        range_roi = range_realized / range_capital if range_capital else Decimal("0")

        return {
            "mode": "live",
            "currency": portfolio[1],
            "simulation_only": True,
            "insufficient_data": attempted == 0,
            "from": from_,
            "to": to,
            "summary": {
                "realized_pnl": range_realized,
                "unrealized_pnl": summary["unrealized_pnl"],
                "net_roi": range_roi,
                "roi_capital_basis": range_capital,
                "fees": fees,
                "turnover": turnover,
                "reserved_capital": summary["reserved_capital"],
                "maximum_drawdown": maximum_drawdown if settled else None,
                "captured_opportunities": captured,
                "attempted_opportunities": attempted,
            },
            "equity": series if settled else [],
            "strategy": grouped(2),
            "provider": [],
            "sport": grouped(3),
            "market": [],
            "quote_age": [{"bucket": "unknown", "count": attempted}],
            "methodology": {
                "equity": "realized settlement P&L only; open positions remain at cost and are not marked with invented prices",
                "net_roi": "realized P&L in the selected UTC interval divided by committed capital of positions settled in that interval",
                "equity_opening_balance": "portfolio starting balance plus realized P&L before the selected interval",
                "capture_rate_denominator": "paper positions created in the selected UTC interval",
                "provider_pnl": "not attributed without a defensible allocation model",
            },
        }

    # ---- internal SQL helpers ----------------------------------------

    @staticmethod
    def _select_portfolio(cursor: Any, user_id: str, *, lock: bool) -> tuple[Any, ...]:
        cursor.execute(
            f"""
            select id::text, currency, starting_balance
              from public.paper_portfolios
             where user_id = %s and status = 'active'
             order by is_default desc, created_at, id
             limit 1 {"for update" if lock else ""}
            """,
            (user_id,),
        )
        row = cursor.fetchone()
        if row is None:
            raise RuntimeError("default_paper_portfolio_unavailable")
        return row

    def _lock_position_portfolio(
        self, cursor: Any, user_id: str, position_id: str
    ) -> tuple[Any, ...]:
        cursor.execute(
            """
            select portfolio_id::text from public.paper_positions
             where id::text = %s and user_id = %s
            """,
            (position_id, user_id),
        )
        row = cursor.fetchone()
        if row is None:
            raise KeyError(position_id)
        cursor.execute(
            """
            select id::text, currency, starting_balance
              from public.paper_portfolios
             where id::text = %s and user_id = %s
             for update
            """,
            (row[0], user_id),
        )
        portfolio = cursor.fetchone()
        if portfolio is None:
            raise KeyError(position_id)
        return portfolio

    @staticmethod
    def _cash_balance(cursor: Any, user_id: str, portfolio_id: str) -> Decimal:
        cursor.execute(
            """
            select cash_balance from public.paper_portfolio_balances
             where portfolio_id::text = %s and user_id = %s
            """,
            (portfolio_id, user_id),
        )
        row = cursor.fetchone()
        if row is None:
            raise RuntimeError("paper_portfolio_balance_unavailable")
        return Decimal(row[0])

    @staticmethod
    def _position_status(cursor: Any, user_id: str, position_id: str) -> str:
        cursor.execute(
            """
            select status from public.paper_positions
             where id::text = %s and user_id = %s
             for update
            """,
            (position_id, user_id),
        )
        row = cursor.fetchone()
        if row is None:
            raise KeyError(position_id)
        return str(row[0])

    @staticmethod
    def _position_reserve(cursor: Any, user_id: str, position_id: str) -> Decimal:
        cursor.execute(
            """
            select coalesce(sum(amount) filter (
                       where entry_type = 'reserve' and direction = 'debit'), 0)
                   - coalesce(sum(amount) filter (
                       where entry_type = 'release' and direction = 'credit'), 0)
              from public.paper_ledger_entries
             where user_id = %s and position_id::text = %s
            """,
            (user_id, position_id),
        )
        return max(Decimal("0"), Decimal(cursor.fetchone()[0]))

    @staticmethod
    def _committed(cursor: Any, user_id: str, position_id: str) -> Decimal:
        cursor.execute(
            """
            select coalesce(sum(f.notional + f.fee), 0)
              from public.paper_fills f
              join public.paper_position_legs l
                on l.id = f.leg_id and l.user_id = f.user_id
             where f.user_id = %s and l.position_id::text = %s
            """,
            (user_id, position_id),
        )
        return money(Decimal(cursor.fetchone()[0]))

    @staticmethod
    def _entry(
        cursor: Any,
        *,
        user_id: str,
        portfolio_id: str,
        position_id: str,
        group_id: object,
        key: str,
        kind: str,
        direction: str,
        amount: Decimal,
        currency: str,
        memo: str,
        occurred_at: datetime | None = None,
    ) -> None:
        cursor.execute(
            """
            insert into public.paper_ledger_entries
                (user_id, portfolio_id, position_id, entry_group_id,
                 idempotency_key, entry_type, direction, amount, currency,
                 memo, occurred_at)
            values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                    coalesce(%s, statement_timestamp()))
            """,
            (
                user_id,
                portfolio_id,
                position_id,
                group_id,
                key,
                kind,
                direction,
                money(amount),
                currency,
                memo,
                occurred_at,
            ),
        )

    def _summary(
        self, cursor: Any, user_id: str, portfolio: tuple[Any, ...]
    ) -> dict[str, object]:
        cursor.execute(
            """
            select b.cash_balance, b.reserved_capital,
                   coalesce(sum(p.realized_pnl) filter (where p.status = 'settled'), 0),
                   coalesce(sum(p.unrealized_pnl) filter (
                       where p.status in ('open', 'partially_filled', 'filled')), 0),
                   coalesce(sum(p.committed_capital) filter (
                       where p.status in ('open', 'partially_filled', 'filled')), 0),
                   count(p.id) filter (
                       where p.status in ('open', 'partially_filled', 'filled')),
                   count(p.id) filter (where p.status = 'settled'),
                   coalesce(sum(p.committed_capital) filter (where p.status = 'settled'), 0)
              from public.paper_portfolio_balances b
              left join public.paper_positions p
                on p.portfolio_id = b.portfolio_id and p.user_id = b.user_id
             where b.portfolio_id::text = %s and b.user_id = %s
             group by b.cash_balance, b.reserved_capital
            """,
            (portfolio[0], user_id),
        )
        row = cursor.fetchone()
        if row is None:
            raise RuntimeError("paper_portfolio_balance_unavailable")
        cash, reserved, realized, unrealized, active_cost, active_count, settled_count, settled_cost = row
        roi = Decimal(realized) / Decimal(settled_cost) if settled_cost else Decimal("0")
        return {
            "portfolio_value": Decimal(cash) + Decimal(reserved) + Decimal(active_cost),
            "cash_balance": cash,
            "reserved_capital": reserved,
            "realized_pnl": realized,
            "unrealized_pnl": unrealized,
            "net_roi": roi,
            "active_positions": active_count,
            "settled_positions": settled_count,
        }

    def _positions(self, cursor: Any, user_id: str) -> list[dict[str, object]]:
        cursor.execute(
            """
            select p.id::text, p.opportunity_id::text, p.title, p.strategy_type,
                   coalesce(s.slug, p.source_assumptions->>'sport', 'unknown'),
                   p.status, p.planned_allocation, p.committed_capital,
                   p.realized_pnl, p.unrealized_pnl, p.worst_case_exposure,
                   p.notes, p.created_at, p.opened_at, p.settled_at,
                   p.source_assumptions, p.currency
              from public.paper_positions p
              left join public.opportunities o on o.id = p.opportunity_id
              left join public.events e on e.id = o.event_id
              left join public.sports s on s.id = e.sport_id
             where p.user_id = %s
             order by p.created_at desc, p.id
            """,
            (user_id,),
        )
        positions = cursor.fetchall()
        cursor.execute(
            """
            select l.position_id::text, l.id::text, provider.display_name,
                   outcome.name, l.planned_quantity, l.filled_quantity,
                   l.average_fill_price, l.status
              from public.paper_position_legs l
              join public.paper_positions p
                on p.id = l.position_id and p.user_id = l.user_id
              join public.providers provider on provider.id = l.provider_id
              join public.outcomes outcome on outcome.id = l.outcome_id
             where l.user_id = %s and p.user_id = %s
             order by p.created_at desc, l.leg_number
            """,
            (user_id, user_id),
        )
        legs: dict[str, list[dict[str, object]]] = {}
        for row in cursor.fetchall():
            legs.setdefault(row[0], []).append(
                {
                    "id": row[1],
                    "provider": row[2],
                    "outcome": row[3],
                    "planned_quantity": row[4],
                    "filled_quantity": row[5],
                    "average_fill_price": row[6],
                    "status": row[7],
                }
            )
        result: list[dict[str, object]] = []
        for row in positions:
            assumptions = dict(row[15] or {})
            result.append(
                {
                    "id": row[0],
                    "opportunity_id": row[1],
                    "title": row[2],
                    "strategy": row[3],
                    "sport": row[4],
                    "status": row[5],
                    "planned_allocation": row[6],
                    "committed_capital": row[7],
                    "realized_pnl": row[8],
                    "unrealized_pnl": row[9],
                    "worst_case_exposure": row[10],
                    "notes": row[11],
                    "created_at": row[12],
                    "opened_at": row[13],
                    "settled_at": row[14],
                    "currency": row[16],
                    "is_demo": False,
                    "calculation_version": assumptions.get(
                        "calculation_version", "edgeos.cashflow.v2"
                    ),
                    "legs": legs.get(row[0], []),
                }
            )
        return result

    def _position(self, cursor: Any, user_id: str, position_id: str) -> dict[str, object]:
        positions = self._positions(cursor, user_id)
        for position in positions:
            if position["id"] == position_id:
                return position
        raise KeyError(position_id)
