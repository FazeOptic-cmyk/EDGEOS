from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


Money = Decimal
Probability = Annotated[Decimal, Field(ge=0, le=1)]


class FrozenModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class MatchStatus(StrEnum):
    VERIFIED = "verified"
    REVIEW_REQUIRED = "review_required"
    REJECTED = "rejected"


class PriceKind(StrEnum):
    DECIMAL_ODDS = "decimal_odds"
    CONTRACT_ASK = "contract_ask"


class PositionStatus(StrEnum):
    OPEN = "open"
    CLOSED = "closed"
    SETTLED = "settled"
    CANCELED = "canceled"


class SettlementRules(FrozenModel):
    period: str | None = None
    overtime_included: bool | None = None
    shootout_included: bool | None = None
    tie_treatment: str | None = None
    draw_treatment: str | None = None
    push_treatment: str | None = None
    void_treatment: str | None = None
    cancellation_treatment: str | None = None
    postponement_treatment: str | None = None
    retirement_treatment: str | None = None
    abandonment_treatment: str | None = None
    outcome_structure: str | None = None
    threshold: Money | None = None
    line_unit: str | None = None
    resolution_source: str | None = None
    rules_text: str | None = None


class CanonicalEvent(FrozenModel):
    event_id: str
    sport: str
    league: str
    home_team: str | None
    away_team: str | None
    scheduled_start: datetime
    aliases: tuple[str, ...] = ()

    @field_validator("scheduled_start")
    @classmethod
    def timezone_required(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("scheduled_start must include a timezone")
        return value.astimezone(UTC)


class CanonicalMarket(FrozenModel):
    market_id: str
    event: CanonicalEvent
    provider: str
    provider_market_id: str
    market_type: str
    outcome_ids: tuple[str, ...]
    rules: SettlementRules
    source_url: str | None = None
    # A shared, explicitly resolved proposition identity, including the subject
    # and predicate. Provider IDs and display labels are not cross-market proof.
    proposition_id: str | None = None


class MatchDecision(FrozenModel):
    status: MatchStatus
    candidate_score: Decimal
    reasons: tuple[str, ...]
    mismatches: tuple[str, ...] = ()
    unknown_fields: tuple[str, ...] = ()
    engine_version: str = "edgeos.settlement.v3"
    left_material_fingerprint: str | None = None
    right_material_fingerprint: str | None = None


class FeeSchedule(FrozenModel):
    schedule_id: str
    provider: str
    effective_from: date
    effective_to: date | None = None
    proportional_rate: Money = Decimal("0")
    per_contract: Money = Decimal("0")
    fixed_per_order: Money = Decimal("0")
    rounding_increment: Money = Decimal("0.01")
    rounding_mode: Literal["ceiling", "floor", "half_up", "half_even"] = "ceiling"
    known: bool = True
    source_url: str | None = None

    @model_validator(mode="after")
    def validate_dates(self) -> "FeeSchedule":
        if self.effective_to and self.effective_to < self.effective_from:
            raise ValueError("effective_to precedes effective_from")
        if self.proportional_rate < 0 or self.per_contract < 0 or self.fixed_per_order < 0:
            raise ValueError("fees cannot be negative")
        if self.rounding_increment <= 0:
            raise ValueError("rounding_increment must be positive")
        return self


class Offer(FrozenModel):
    offer_id: str
    provider: str
    outcome_id: str
    price_kind: PriceKind
    price: Money
    quote_timestamp: datetime | None
    fee_schedule: FeeSchedule | None
    # Research feeds may settle in provider-denominated cash tokens such as
    # USDC or pUSD. They remain distinct currencies; mixed codes are rejected
    # by the engine unless an explicit conversion layer is added.
    currency: str = Field(default="USD", pattern=r"^[A-Za-z][A-Za-z0-9]{2,7}$")
    minimum_quantity: Money = Decimal("0")
    quantity_increment: Money = Decimal("0.01")
    maximum_quantity: Money | None = None
    slippage_bps: Money = Decimal("0")
    source_url: str | None = None
    data_origin: str = "demo"

    @model_validator(mode="after")
    def validate_price(self) -> "Offer":
        if self.price <= 0:
            raise ValueError("price must be positive")
        if self.price_kind is PriceKind.DECIMAL_ODDS and self.price <= 1:
            raise ValueError("decimal odds must exceed one")
        if self.price_kind is PriceKind.CONTRACT_ASK and self.price > 1:
            raise ValueError("contract ask must be at most one")
        if self.quantity_increment <= 0:
            raise ValueError("quantity_increment must be positive")
        if self.minimum_quantity < 0:
            raise ValueError("minimum_quantity cannot be negative")
        if self.maximum_quantity is not None and self.maximum_quantity < 0:
            raise ValueError("maximum_quantity cannot be negative")
        if self.maximum_quantity is not None and self.maximum_quantity < self.minimum_quantity:
            raise ValueError("maximum_quantity cannot be less than minimum_quantity")
        if self.slippage_bps < 0:
            raise ValueError("slippage_bps cannot be negative")
        if self.quote_timestamp and self.quote_timestamp.tzinfo is None:
            raise ValueError("quote_timestamp must include a timezone")
        return self


class AllocationLeg(FrozenModel):
    offer_id: str
    provider: str
    outcome_id: str
    price_kind: PriceKind
    requested_quantity: Money
    filled_quantity: Money
    unit_cost: Money
    cash_cost: Money
    gross_payout_if_wins: Money
    fees: Money
    quote_age_seconds: Decimal | None
    stale: bool
    limit_reasons: tuple[str, ...] = ()


class OutcomeResult(FrozenModel):
    outcome_id: str
    gross_payout: Money
    net_profit: Money


class BreakEvenPoint(FrozenModel):
    offer_id: str
    price_kind: PriceKind
    current_price: Money
    fixed_allocation_break_even_price: Money | None
    adverse_movement: Money | None
    adverse_movement_bps: Money | None
    limiting_outcome_id: str | None
    methodology: str = "fixed_allocation_all_outcome_cashflow"


class ArbitrageResult(FrozenModel):
    requested_allocation: Money
    deployed_capital: Money
    unallocated_capital: Money
    total_fees: Money
    legs: tuple[AllocationLeg, ...]
    outcomes: tuple[OutcomeResult, ...]
    equalized_gross_payout: Money
    gross_profit: Money
    net_profit_worst_case: Money
    net_profit_best_case: Money
    gross_roi: Money
    net_roi: Money
    maximum_available_allocation: Money | None
    unmatched_exposure: Money
    expected_outcome_ids: tuple[str, ...] | None
    outcome_coverage_verified: bool
    is_theoretical_arbitrage: bool
    execution_eligible: bool
    rejection_codes: tuple[str, ...]
    assumptions: tuple[str, ...]
    calculation_version: str = "edgeos.cashflow.v2"
    currency: str = "USD"
    capital_definition: str = "cash_cost_plus_fees"
    capital_lockup: Money = Decimal("0")
    expected_lockup_seconds: Decimal | None = None
    quote_timestamp_skew_seconds: Decimal | None = None
    maximum_quote_timestamp_skew_seconds: Decimal = Decimal("5")
    allocation_limit_reasons: tuple[str, ...] = ()
    optimization_method: str = "continuous_equal_payout"
    break_even_points: tuple[BreakEvenPoint, ...] = ()


class ScenarioLeg(FrozenModel):
    leg_id: str
    provider: str
    requested_quantity: Money
    filled_quantity: Money
    unit_cost: Money
    fees: Money = Decimal("0")
    payout_per_unit: dict[str, Money]


class BundleEvaluation(FrozenModel):
    total_cost: Money
    total_fees: Money
    outcome_results: tuple[OutcomeResult, ...]
    worst_case: Money
    best_case: Money
    unmatched_exposure: Money


class RankInputs(FrozenModel):
    conservative_profit: Money
    net_roi: Money
    executable_liquidity: Money
    quote_age_seconds: Decimal | None
    quote_update_hz: Decimal | None = None
    settlement_status: MatchStatus
    expected_slippage_bps: Decimal = Field(ge=0)
    partial_fill_probability: Probability
    duration_seconds: Decimal | None = None
    stability: Probability | None = None
    fee_schedule_known: bool = True
    minimum_liquidity: Money = Decimal("1")
    maximum_quote_age_seconds: Decimal = Field(default=Decimal("30"), gt=0)
    quote_timestamp_skew_seconds: Decimal | None = None
    maximum_quote_timestamp_skew_seconds: Decimal | None = Field(default=None, gt=0)


class RankResult(FrozenModel):
    eligible: bool
    score: Decimal | None
    hard_rejections: tuple[str, ...]
    components: dict[str, Decimal]


class SimulateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    total_capital: Money = Field(gt=0)
    offers: list[Offer] = Field(min_length=2)
    expected_outcome_ids: list[str] | None = None
    # Informational only at the public API boundary. The server fails closed
    # unless it resolves a persisted VERIFIED match itself.
    settlement_status: MatchStatus = MatchStatus.REVIEW_REQUIRED
    maximum_quote_age_seconds: int = Field(default=30, gt=0)
    maximum_quote_timestamp_skew_seconds: int = Field(default=5, gt=0)
    provider_capital_limits: dict[str, Money] = Field(default_factory=dict)
    expected_lockup_seconds: Decimal | None = Field(default=None, ge=0)
    allow_fee_assumptions: bool = False
    require_observed_depth: bool = True
    now: datetime | None = None

    @field_validator("provider_capital_limits")
    @classmethod
    def validate_provider_capital_limits(cls, value: dict[str, Money]) -> dict[str, Money]:
        if any(not provider.strip() for provider in value):
            raise ValueError("provider capital-limit keys cannot be empty")
        if any(limit < 0 for limit in value.values()):
            raise ValueError("provider capital limits cannot be negative")
        return value

    @field_validator("expected_outcome_ids")
    @classmethod
    def validate_expected_outcomes(cls, value: list[str] | None) -> list[str] | None:
        if value is None:
            return None
        if len(value) < 2:
            raise ValueError("expected_outcome_ids must contain at least two outcomes")
        if any(not outcome for outcome in value):
            raise ValueError("expected outcome IDs cannot be empty")
        if len(set(value)) != len(value):
            raise ValueError("expected_outcome_ids must be unique")
        return value


class ScenarioSimulationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    outcome_ids: list[str] = Field(min_length=2)
    legs: list[ScenarioLeg] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_scenario_matrix(self) -> "ScenarioSimulationRequest":
        if len(set(self.outcome_ids)) != len(self.outcome_ids):
            raise ValueError("outcome_ids must be unique")
        known = set(self.outcome_ids)
        unknown = {
            outcome
            for leg in self.legs
            for outcome in leg.payout_per_unit
            if outcome not in known
        }
        if unknown:
            raise ValueError(f"payout matrix references unknown outcomes: {', '.join(sorted(unknown))}")
        return self


class PositionCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    opportunity_id: str | None = None
    strategy: str = "arbitrage"
    sport: str | None = None
    notes: str | None = None
    simulation: ArbitrageResult


class PaperPositionCreate(BaseModel):
    """Browser contract; the server resolves and recalculates the opportunity."""

    model_config = ConfigDict(extra="forbid")
    opportunity_id: str = Field(min_length=1, max_length=200)
    total_capital: Money = Field(gt=0)
    notes: str | None = Field(default=None, max_length=4000)
    idempotency_key: str | None = Field(default=None, min_length=8, max_length=200)


class FillCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    leg_id: str
    quantity: Money = Field(gt=0)
    price: Money = Field(gt=0)
    fee: Money = Field(default=Decimal("0"), ge=0)
    filled_at: datetime | None = None
    idempotency_key: str | None = Field(default=None, min_length=8, max_length=200)

    @field_validator("filled_at")
    @classmethod
    def filled_at_timezone_required(cls, value: datetime | None) -> datetime | None:
        if value is not None and value.tzinfo is None:
            raise ValueError("filled_at must include a timezone")
        return value.astimezone(UTC) if value is not None else None


class SettlementCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    outcome_id: str
    settled_at: datetime | None = None
    result_source_url: str | None = Field(default=None, max_length=2048)
    idempotency_key: str | None = Field(default=None, min_length=8, max_length=200)

    @field_validator("settled_at")
    @classmethod
    def settled_at_timezone_required(cls, value: datetime | None) -> datetime | None:
        if value is not None and value.tzinfo is None:
            raise ValueError("settled_at must include a timezone")
        return value.astimezone(UTC) if value is not None else None


class CancellationCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reason: str = Field(min_length=1, max_length=2000)
    idempotency_key: str | None = Field(default=None, min_length=8, max_length=200)


class WatchlistCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["team", "sport", "league", "event", "market", "provider", "strategy"]
    value: str = Field(min_length=1, max_length=200)
    label: str | None = Field(default=None, min_length=1, max_length=200)
    idempotency_key: str | None = Field(default=None, min_length=8, max_length=200)


class AlertCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    condition: Literal[
        "new_verified_opportunity", "net_roi_above", "liquidity_increase",
        "spread_widens", "quote_stale", "market_closed", "market_settled", "provider_offline"
    ]
    threshold: Money | None = None
    # Optional conjunction filters used by the live opportunity evaluator.
    # ``threshold`` remains the backwards-compatible value for condition-
    # specific rules such as ``net_roi_above``.
    minimum_net_profit: Money | None = Field(default=None, ge=0)
    minimum_net_roi: Money | None = Field(default=None, ge=0)
    minimum_liquidity: Money | None = Field(default=None, ge=0)
    maximum_quote_age_seconds: int | None = Field(default=None, gt=0, le=86_400)
    enabled: bool = True
    name: str | None = Field(default=None, min_length=1, max_length=160)
    cooldown_seconds: int = Field(default=300, ge=0, le=2_592_000)
    idempotency_key: str | None = Field(default=None, min_length=8, max_length=200)


class AlertUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    condition: Literal[
        "new_verified_opportunity", "net_roi_above", "liquidity_increase",
        "spread_widens", "quote_stale", "market_closed", "market_settled", "provider_offline"
    ] | None = None
    threshold: Money | None = None
    minimum_net_profit: Money | None = Field(default=None, ge=0)
    minimum_net_roi: Money | None = Field(default=None, ge=0)
    minimum_liquidity: Money | None = Field(default=None, ge=0)
    maximum_quote_age_seconds: int | None = Field(default=None, gt=0, le=86_400)
    enabled: bool | None = None
    name: str | None = Field(default=None, min_length=1, max_length=160)
    cooldown_seconds: int | None = Field(default=None, ge=0, le=2_592_000)

