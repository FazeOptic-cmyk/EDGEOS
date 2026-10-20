import { z } from "zod";
import { appMode } from "./api";
import { assertEdgeosMode } from "./edgeos-proxy";
import { normalizeOddsInput, type OddsInputFormat } from "./odds";

const decimalString = z.union([z.string(), z.number().finite()])
  .transform(String)
  .pipe(z.string().regex(/^-?\d+(?:\.\d+)?(?:e[+-]?\d+)?$/iu));
const nullableDecimalString = decimalString.nullable();

const simulationLegSchema = z.object({
  offer_id: z.string(),
  provider: z.string(),
  outcome_id: z.string(),
  price_kind: z.enum(["decimal_odds", "contract_ask"]),
  requested_quantity: decimalString,
  filled_quantity: decimalString,
  unit_cost: decimalString,
  cash_cost: decimalString,
  gross_payout_if_wins: decimalString,
  fees: decimalString,
  quote_age_seconds: nullableDecimalString,
  stale: z.boolean(),
  limit_reasons: z.array(z.string()).default([]),
}).passthrough();

const breakEvenSchema = z.object({
  offer_id: z.string(),
  price_kind: z.enum(["decimal_odds", "contract_ask"]),
  current_price: decimalString,
  fixed_allocation_break_even_price: nullableDecimalString,
  adverse_movement: nullableDecimalString,
  adverse_movement_bps: nullableDecimalString,
  limiting_outcome_id: z.string().nullable(),
  methodology: z.string(),
}).passthrough();

export const authoritativeSimulationSchema = z.object({
  requested_allocation: decimalString,
  deployed_capital: decimalString,
  unallocated_capital: decimalString,
  total_fees: decimalString,
  legs: z.array(simulationLegSchema).min(2),
  outcomes: z.array(z.object({
    outcome_id: z.string(), gross_payout: decimalString, net_profit: decimalString,
  })).min(2),
  equalized_gross_payout: decimalString,
  gross_profit: decimalString,
  net_profit_worst_case: decimalString,
  net_profit_best_case: decimalString,
  gross_roi: decimalString,
  net_roi: decimalString,
  maximum_available_allocation: nullableDecimalString,
  unmatched_exposure: decimalString,
  outcome_coverage_verified: z.boolean(),
  is_theoretical_arbitrage: z.boolean(),
  execution_eligible: z.boolean(),
  rejection_codes: z.array(z.string()),
  assumptions: z.array(z.string()),
  calculation_version: z.string(),
  currency: z.string().regex(/^[A-Z]{3}$/u),
  capital_definition: z.string(),
  capital_lockup: decimalString,
  expected_lockup_seconds: nullableDecimalString,
  quote_timestamp_skew_seconds: nullableDecimalString,
  maximum_quote_timestamp_skew_seconds: decimalString,
  allocation_limit_reasons: z.array(z.string()),
  optimization_method: z.string(),
  break_even_points: z.array(breakEvenSchema),
}).passthrough();

export type AuthoritativeSimulation = z.infer<typeof authoritativeSimulationSchema>;

export interface TwoWaySimulationInput {
  capital: string;
  oddsA: string;
  oddsB: string;
  feePercentA: string;
  feePercentB: string;
  slippagePercentA: string;
  slippagePercentB: string;
  maximumStakeA: string;
  maximumStakeB: string;
}

export interface ManualSimulationLegInput {
  id: string;
  provider: string;
  outcomeId: string;
  format: OddsInputFormat;
  price: string;
  feePercent: string;
  slippagePercent: string;
  /** User-entered cash ceiling for this platform, including modeled fees. */
  maximumAllocation: string;
}

export interface ManualSimulationInput {
  capital: string;
  legs: ManualSimulationLegInput[];
}

function shiftDecimal(raw: string, places: number): string {
  const value = raw.trim();
  if (!/^\d+(?:\.\d+)?$/u.test(value)) throw new Error("Enter non-negative decimal values.");
  const [whole, fraction = ""] = value.split(".");
  const digits = `${whole}${fraction}`.replace(/^0+(?=\d)/u, "");
  const scale = fraction.length - places;
  let result: string;
  if (scale <= 0) result = `${digits}${"0".repeat(-scale)}`;
  else if (digits.length <= scale) result = `0.${"0".repeat(scale - digits.length)}${digits}`;
  else result = `${digits.slice(0, digits.length - scale)}.${digits.slice(digits.length - scale)}`;
  return result.replace(/\.0+$/u, "").replace(/(\.\d*?)0+$/u, "$1") || "0";
}

function positiveDecimal(raw: string, label: string): string {
  const value = raw.trim();
  if (!/^\d+(?:\.\d+)?$/u.test(value) || Number(value) <= 0 || !Number.isFinite(Number(value))) {
    throw new Error(`${label} must be a positive decimal value.`);
  }
  return value;
}

function floorDecimalRatio(numerator: string, denominator: string): string {
  const parts = (raw: string) => {
    const [whole, fraction = ""] = raw.split(".");
    return { digits: BigInt(`${whole}${fraction}`), scale: fraction.length };
  };
  const left = parts(numerator);
  const right = parts(denominator);
  const scaledNumerator = left.digits * (10n ** BigInt(right.scale));
  const scaledDenominator = right.digits * (10n ** BigInt(left.scale));
  return (scaledNumerator / scaledDenominator).toString();
}

export function buildManualSimulationRequest(input: ManualSimulationInput) {
  positiveDecimal(input.capital, "Total simulated capital");
  if (input.legs.length !== 2 && input.legs.length !== 3) {
    throw new Error("Manual simulations require exactly two or three outcomes.");
  }
  const unique = (values: string[]) => new Set(values).size === values.length;
  if (!unique(input.legs.map((leg) => leg.id)) || !unique(input.legs.map((leg) => leg.outcomeId))) {
    throw new Error("Every simulation leg and outcome must have a unique identifier.");
  }
  if (!unique(input.legs.map((leg) => leg.provider))) {
    throw new Error("Use one distinct manual platform for each modeled outcome.");
  }

  const offers = input.legs.map((leg) => {
    if (!leg.id.trim() || !leg.provider.trim() || !leg.outcomeId.trim()) {
      throw new Error("Every simulation leg requires a platform and outcome identifier.");
    }
    const normalized = normalizeOddsInput(leg.format, leg.price);
    const maximumAllocation = positiveDecimal(leg.maximumAllocation, `${leg.provider} allocation cap`);
    const feeRate = shiftDecimal(leg.feePercent, -2);
    const slippageBps = shiftDecimal(leg.slippagePercent, 2);
    if (Number(leg.slippagePercent) >= 100) {
      throw new Error(`${leg.provider} adverse price movement must be below 100%.`);
    }
    if (normalized.priceKind === "contract_ask" && Number(normalized.price) * (1 + Number(leg.slippagePercent) / 100) > 1) {
      throw new Error(`${leg.provider} slippage would move the contract ask above its $1 payout.`);
    }

    const maximumQuantity = normalized.priceKind === "contract_ask"
      ? floorDecimalRatio(maximumAllocation, normalized.price)
      : maximumAllocation;
    if (normalized.priceKind === "contract_ask" && BigInt(maximumQuantity) < 1n) {
      throw new Error(`${leg.provider} allocation cap cannot fund one contract at the entered ask.`);
    }

    return {
      offer_id: `manual-${leg.id}`,
      provider: leg.provider,
      outcome_id: leg.outcomeId,
      price_kind: normalized.priceKind,
      price: normalized.price,
      quote_timestamp: null,
      fee_schedule: {
        schedule_id: `manual-${leg.id}-fee-assumption`,
        provider: leg.provider,
        effective_from: "2000-01-01",
        proportional_rate: feeRate,
        per_contract: "0",
        fixed_per_order: "0",
        rounding_increment: "0.01",
        known: true,
      },
      currency: "USD",
      minimum_quantity: normalized.priceKind === "contract_ask" ? "1" : "0",
      quantity_increment: normalized.priceKind === "contract_ask" ? "1" : "0.01",
      maximum_quantity: maximumQuantity,
      slippage_bps: slippageBps,
      data_origin: "manual_user_input",
    };
  });

  return {
    total_capital: input.capital,
    offers,
    expected_outcome_ids: input.legs.map((leg) => leg.outcomeId),
    settlement_status: "review_required",
    maximum_quote_age_seconds: 30,
    maximum_quote_timestamp_skew_seconds: 5,
    provider_capital_limits: Object.fromEntries(
      input.legs.map((leg) => [leg.provider, leg.maximumAllocation.trim()]),
    ),
    allow_fee_assumptions: false,
    require_observed_depth: true,
  };
}

export function buildTwoWaySimulationRequest(input: TwoWaySimulationInput) {
  return buildManualSimulationRequest({
    capital: input.capital,
    legs: [
      { id: "a", provider: "Manual platform A", outcomeId: "outcome_a", format: "decimal", price: input.oddsA, feePercent: input.feePercentA, slippagePercent: input.slippagePercentA, maximumAllocation: input.maximumStakeA },
      { id: "b", provider: "Manual platform B", outcomeId: "outcome_b", format: "decimal", price: input.oddsB, feePercent: input.feePercentB, slippagePercent: input.slippagePercentB, maximumAllocation: input.maximumStakeB },
    ],
  });
}

async function submitSimulation(request: ReturnType<typeof buildManualSimulationRequest>, signal?: AbortSignal): Promise<AuthoritativeSimulation> {
  const response = await fetch("/api/edgeos/api/v1/simulate", {
    method: "POST",
    cache: "no-store",
    credentials: "same-origin",
    signal,
    headers: { "Content-Type": "application/json", "X-EDGEOS-Client-Mode": appMode },
    body: JSON.stringify(request),
  });
  assertEdgeosMode(response.headers);
  if (!response.ok) {
    let message = `Authoritative calculation failed (${response.status}).`;
    try {
      const payload = await response.json() as { detail?: string | { message?: string } };
      message = typeof payload.detail === "string" ? payload.detail : payload.detail?.message ?? message;
    } catch { /* Keep the bounded generic error. */ }
    throw new Error(message);
  }
  return authoritativeSimulationSchema.parse(await response.json());
}

export function simulateManual(input: ManualSimulationInput, signal?: AbortSignal): Promise<AuthoritativeSimulation> {
  return submitSimulation(buildManualSimulationRequest(input), signal);
}

/** Backward-compatible helper for callers that still provide two decimal-odds legs. */
export function simulateTwoWay(input: TwoWaySimulationInput, signal?: AbortSignal): Promise<AuthoritativeSimulation> {
  return submitSimulation(buildTwoWaySimulationRequest(input), signal);
}
