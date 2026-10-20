from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from .arbitrage import calculate_arbitrage
from .domain import FeeSchedule, MatchStatus, Offer, PriceKind


def demo_fee(provider: str) -> FeeSchedule:
    return FeeSchedule(
        schedule_id=f"demo-zero-{provider}", provider=provider,
        effective_from=date(2020, 1, 1), proportional_rate=Decimal("0"),
        per_contract=Decimal("0"), fixed_per_order=Decimal("0"),
        known=True, source_url="demo://edgeos/fees",
    )


def demo_opportunity(
    now: datetime | None = None,
    *,
    total_capital: Decimal = Decimal("210"),
) -> dict[str, object]:
    observed = (now or datetime.now(UTC)).astimezone(UTC)
    offers = [
        Offer(
            offer_id="demo-leg-a", provider="Demo Sportsbook A", outcome_id="Harbor Hawks",
            price_kind=PriceKind.DECIMAL_ODDS, price=Decimal("2.20"), quote_timestamp=observed,
            fee_schedule=demo_fee("demo_a"), maximum_quantity=Decimal("1000"),
            source_url="demo://edgeos/quotes/a", data_origin="demo",
        ),
        Offer(
            offer_id="demo-leg-b", provider="Demo Sportsbook B", outcome_id="Metro Comets",
            price_kind=PriceKind.DECIMAL_ODDS, price=Decimal("2.00"), quote_timestamp=observed,
            fee_schedule=demo_fee("demo_b"), maximum_quantity=Decimal("1000"),
            source_url="demo://edgeos/quotes/b", data_origin="demo",
        ),
    ]
    simulation = calculate_arbitrage(
        offers, total_capital, settlement_status=MatchStatus.VERIFIED, now=observed,
        expected_outcome_ids=("Harbor Hawks", "Metro Comets"),
    )
    return {
        "id": "demo-opportunity-001",
        "event": "[DEMO] Harbor Hawks vs Metro Comets",
        "sport": "Basketball",
        "league": "EDGEOS Demo League",
        "market": "Full game winner including overtime",
        "market_type": "moneyline",
        "platform_a": "Demo Sportsbook A",
        "platform_b": "Demo Sportsbook B",
        "prices": ["2.20", "2.00"],
        "gross_roi": simulation.gross_roi,
        "net_roi": simulation.net_roi,
        "total_allocation": simulation.deployed_capital,
        "potential_net_profit": simulation.net_profit_worst_case,
        "currency": simulation.currency,
        "available_liquidity": simulation.maximum_available_allocation,
        "event_start_time": observed + timedelta(hours=2),
        "phase": "prematch",
        "live": False,
        "observed_at": observed,
        "provider_timestamp": observed,
        "quote_age_seconds": Decimal("0"),
        "data_origin": "demo",
        "is_demo": True,
        "settlement_status": "verified",
        "research_score": Decimal("91.4"),
        "execution_eligible": False,
        "not_executable_reason": "Synthetic demo data; never a live or executable price.",
        "source_url": "demo://edgeos/opportunities/demo-opportunity-001",
        "simulation": simulation,
        "settlement_audit": {
            "status": "verified",
            "reasons": [
                "Synthetic fixture defines the same full-game period and overtime treatment.",
                "Synthetic outcomes are complete and mutually exclusive.",
            ],
            "scope": "demo fixture only",
        },
        "failure_reasons": ["Demo quotes cannot be executed.", "Real prices may move before both legs fill."],
        "price_history": [
            {"observed_at": observed, "a": "2.20", "b": "2.00", "data_origin": "demo"}
        ],
    }

