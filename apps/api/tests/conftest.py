from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from edgeos.domain import FeeSchedule, Offer, PriceKind


@pytest.fixture
def fixed_now() -> datetime:
    return datetime(2026, 10, 16, 12, 0, tzinfo=UTC)


@pytest.fixture
def zero_fee() -> FeeSchedule:
    return FeeSchedule(
        schedule_id="test-zero", provider="test", effective_from=date(2020, 1, 1),
        source_url="https://example.test/fee-schedule",
    )


def decimal_offer(
    offer_id: str,
    outcome: str,
    odds: str,
    now: datetime,
    fee: FeeSchedule,
    *,
    maximum: str | None = "10000",
    increment: str = "0.01",
) -> Offer:
    return Offer(
        offer_id=offer_id, provider=f"provider-{offer_id}", outcome_id=outcome,
        price_kind=PriceKind.DECIMAL_ODDS, price=Decimal(odds), quote_timestamp=now,
        fee_schedule=fee, maximum_quantity=Decimal(maximum) if maximum else None,
        quantity_increment=Decimal(increment), data_origin="historical",
    )

