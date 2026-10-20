from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

import pytest
from pydantic import ValidationError

from edgeos.backtest import BacktestConfig, HistoricalSnapshot, run_backtest


def snapshot(fixed_now, **updates):
    values = {
        "snapshot_id": "s1", "opportunity_id": "o1", "observed_at": fixed_now,
        "valid_until": fixed_now + timedelta(seconds=1),
        "conservative_profit": Decimal("10"), "allocation": Decimal("100"),
        "available_liquidity": Decimal("100"), "depth_observed": True,
    }
    values.update(updates)
    return HistoricalSnapshot(**values)


def test_backtest_respects_expiry_after_execution_delay(fixed_now) -> None:
    result = run_backtest([snapshot(fixed_now)], BacktestConfig(fixed_delay_ms=1500))
    assert result.total_signals == 1
    assert result.valid_opportunities == 1
    assert result.captured_opportunities == 0
    assert result.failed_captures == 1


def test_backfilled_snapshot_cannot_be_used_before_available_at(fixed_now) -> None:
    row = snapshot(
        fixed_now,
        available_at=fixed_now + timedelta(seconds=2),
        valid_until=fixed_now + timedelta(seconds=1),
    )
    result = run_backtest([row], BacktestConfig())
    assert result.captured_opportunities == 0


def test_invalid_availability_timestamp_rejected(fixed_now) -> None:
    with pytest.raises(ValidationError):
        snapshot(fixed_now, available_at=fixed_now - timedelta(seconds=1))


def test_missing_depth_is_explicit_assumption_or_hard_failure(fixed_now) -> None:
    row = snapshot(fixed_now, depth_observed=False)
    strict = run_backtest([row], BacktestConfig(require_observed_depth=True))
    modeled = run_backtest([row], BacktestConfig(require_observed_depth=False))
    assert strict.captured_opportunities == 0
    assert modeled.captured_opportunities == 1
    assert modeled.assumptions

