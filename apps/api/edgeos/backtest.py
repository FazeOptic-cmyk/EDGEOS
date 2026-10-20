from __future__ import annotations

import random
from datetime import datetime, timedelta
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class HistoricalSnapshot(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    snapshot_id: str
    opportunity_id: str
    observed_at: datetime
    available_at: datetime | None = None
    valid_until: datetime | None = None
    conservative_profit: Decimal
    allocation: Decimal = Field(gt=0)
    available_liquidity: Decimal = Field(ge=0)
    depth_observed: bool = True

    @field_validator("observed_at", "available_at", "valid_until")
    @classmethod
    def timezone_required(cls, value: datetime | None) -> datetime | None:
        if value is not None and value.tzinfo is None:
            raise ValueError("historical timestamps must include a timezone")
        return value

    @model_validator(mode="after")
    def availability_cannot_precede_observation(self) -> "HistoricalSnapshot":
        if self.available_at is not None and self.available_at < self.observed_at:
            raise ValueError("available_at cannot precede observed_at")
        return self


class BacktestConfig(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    fixed_delay_ms: int = Field(default=0, ge=0)
    randomized_delay_ms: int = Field(default=0, ge=0)
    slippage_bps: Decimal = Field(default=Decimal("0"), ge=0)
    partial_fill_ratio: Decimal = Field(default=Decimal("1"), ge=0, le=1)
    require_observed_depth: bool = True
    seed: int = 7


class BacktestResult(BaseModel):
    model_config = ConfigDict(frozen=True)
    total_signals: int
    valid_opportunities: int
    captured_opportunities: int
    failed_captures: int
    gross_return: Decimal
    net_return: Decimal
    worst_case_exposure: Decimal
    maximum_drawdown: Decimal
    assumptions: tuple[str, ...]


def run_backtest(snapshots: list[HistoricalSnapshot], config: BacktestConfig) -> BacktestResult:
    """Replay immutable snapshots using only data observable at decision time."""
    ordered = sorted(snapshots, key=lambda item: item.observed_at)
    rng = random.Random(config.seed)
    valid = captured = failed = 0
    gross = net = Decimal("0")
    worst_exposure = Decimal("0")
    equity = peak = max_drawdown = Decimal("0")
    assumptions: list[str] = []
    for snapshot in ordered:
        if config.require_observed_depth and not snapshot.depth_observed:
            failed += 1
            continue
        if not snapshot.depth_observed:
            assumptions.append("liquidity constrained by configured allocation; historical depth unavailable")
        valid += 1
        jitter = rng.randint(0, config.randomized_delay_ms) if config.randomized_delay_ms else 0
        # A backfilled quote cannot be acted upon before EDGEOS actually had it.
        decision_time = snapshot.available_at or snapshot.observed_at
        execution_time = decision_time + timedelta(milliseconds=config.fixed_delay_ms + jitter)
        if snapshot.valid_until is not None and execution_time > snapshot.valid_until:
            failed += 1
            continue
        ratio = min(config.partial_fill_ratio, snapshot.available_liquidity / snapshot.allocation)
        if ratio <= 0:
            failed += 1
            continue
        captured += 1
        gross_leg = snapshot.conservative_profit * ratio
        slipped = snapshot.allocation * ratio * config.slippage_bps / Decimal("10000")
        net_leg = gross_leg - slipped
        gross += gross_leg
        net += net_leg
        if ratio < 1:
            worst_exposure = max(worst_exposure, snapshot.allocation * (Decimal("1") - ratio))
        equity += net_leg
        peak = max(peak, equity)
        max_drawdown = max(max_drawdown, peak - equity)
    return BacktestResult(
        total_signals=len(ordered), valid_opportunities=valid,
        captured_opportunities=captured, failed_captures=failed,
        gross_return=gross, net_return=net, worst_case_exposure=worst_exposure,
        maximum_drawdown=max_drawdown, assumptions=tuple(dict.fromkeys(assumptions)),
    )

