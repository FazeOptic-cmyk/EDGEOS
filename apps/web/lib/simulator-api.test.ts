import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { buildManualSimulationRequest, buildTwoWaySimulationRequest } from "./simulator-api";

const input = {
  capital: "210.00", oddsA: "2.20", oddsB: "2.00", feePercentA: "0.25",
  feePercentB: "1.5", slippagePercentA: "0.10", slippagePercentB: "0.20",
  maximumStakeA: "100", maximumStakeB: "110",
};

describe("authoritative simulator API", () => {
  beforeEach(() => { vi.resetModules(); vi.stubEnv("NEXT_PUBLIC_APP_MODE", "demo"); });
  afterEach(() => { vi.unstubAllEnvs(); vi.restoreAllMocks(); });

  it("builds exact decimal fee, slippage, and depth assumptions", () => {
    const request = buildTwoWaySimulationRequest(input);
    expect(request.total_capital).toBe("210.00");
    expect(request.offers[0]).toMatchObject({
      price: "2.2", maximum_quantity: "100", slippage_bps: "10",
      fee_schedule: { proportional_rate: "0.0025" }, quote_timestamp: null,
    });
    expect(request.offers[1].fee_schedule.proportional_rate).toBe("0.015");
    expect(request.offers[1].slippage_bps).toBe("20");
    expect(request.expected_outcome_ids).toEqual(["outcome_a", "outcome_b"]);
    expect(request.provider_capital_limits).toEqual({ "Manual platform A": "100", "Manual platform B": "110" });
  });

  it("normalizes a three-way mixed-format request and sizes whole contracts conservatively", () => {
    const request = buildManualSimulationRequest({
      capital: "500",
      legs: [
        { id: "home", provider: "Book A", outcomeId: "home", format: "american", price: "+220", feePercent: "0.25", slippagePercent: "0.5", maximumAllocation: "120" },
        { id: "draw", provider: "Book B", outcomeId: "draw", format: "fractional", price: "5/2", feePercent: "1", slippagePercent: "0", maximumAllocation: "180" },
        { id: "away", provider: "Exchange C", outcomeId: "away", format: "contract", price: "0.31", feePercent: "0", slippagePercent: "1", maximumAllocation: "100" },
      ],
    });

    expect(request.expected_outcome_ids).toEqual(["home", "draw", "away"]);
    expect(request.offers).toHaveLength(3);
    expect(request.offers[0]).toMatchObject({ price_kind: "decimal_odds", price: "3.2", slippage_bps: "50" });
    expect(request.offers[1]).toMatchObject({ price_kind: "decimal_odds", price: "3.5" });
    expect(request.offers[2]).toMatchObject({
      price_kind: "contract_ask", price: "0.31", minimum_quantity: "1",
      quantity_increment: "1", maximum_quantity: "322", slippage_bps: "100",
    });
    expect(request.provider_capital_limits).toEqual({ "Book A": "120", "Book B": "180", "Exchange C": "100" });
  });

  it("rejects incomplete, duplicate, and impossible contract inputs before transport", () => {
    expect(() => buildManualSimulationRequest({ capital: "100", legs: [] })).toThrow(/exactly two or three/u);
    const duplicate = {
      capital: "100",
      legs: [
        { id: "a", provider: "Same", outcomeId: "yes", format: "contract" as const, price: "0.6", feePercent: "0", slippagePercent: "0", maximumAllocation: "50" },
        { id: "b", provider: "Same", outcomeId: "no", format: "contract" as const, price: "0.4", feePercent: "0", slippagePercent: "0", maximumAllocation: "50" },
      ],
    };
    expect(() => buildManualSimulationRequest(duplicate)).toThrow(/distinct manual platform/u);
    expect(() => buildManualSimulationRequest({
      ...duplicate,
      legs: duplicate.legs.map((leg, index) => ({ ...leg, provider: `P${index}`, maximumAllocation: index ? "50" : "0.10" })),
    })).toThrow(/cannot fund one contract/u);
    expect(() => buildManualSimulationRequest({
      ...duplicate,
      legs: duplicate.legs.map((leg, index) => ({ ...leg, provider: `P${index}`, slippagePercent: index ? "0" : "80" })),
    })).toThrow(/above its \$1 payout/u);
  });

  it("uses the same-origin gateway and requires its mode contract", async () => {
    const payload = {
      requested_allocation:"210",deployed_capital:"210",unallocated_capital:"0",total_fees:"0",
      legs:[
        {offer_id:"manual-a",provider:"Manual platform A",outcome_id:"outcome_a",price_kind:"decimal_odds",requested_quantity:"100",filled_quantity:"100",unit_cost:"1",cash_cost:"100",gross_payout_if_wins:"220",fees:"0",quote_age_seconds:null,stale:true,limit_reasons:[]},
        {offer_id:"manual-b",provider:"Manual platform B",outcome_id:"outcome_b",price_kind:"decimal_odds",requested_quantity:"110",filled_quantity:"110",unit_cost:"1",cash_cost:"110",gross_payout_if_wins:"220",fees:"0",quote_age_seconds:null,stale:true,limit_reasons:[]},
      ],outcomes:[{outcome_id:"outcome_a",gross_payout:"220",net_profit:"10"},{outcome_id:"outcome_b",gross_payout:"220",net_profit:"10"}],
      equalized_gross_payout:"220",gross_profit:"10",net_profit_worst_case:"10",net_profit_best_case:"10",gross_roi:"0.047619",net_roi:"0.047619",maximum_available_allocation:"210",unmatched_exposure:"0",outcome_coverage_verified:true,is_theoretical_arbitrage:true,execution_eligible:false,rejection_codes:["settlement_not_verified","quote_age_unknown"],assumptions:[],calculation_version:"edgeos.cashflow.v2",currency:"USD",capital_definition:"cash_cost_plus_fees",capital_lockup:"210",expected_lockup_seconds:null,quote_timestamp_skew_seconds:null,maximum_quote_timestamp_skew_seconds:"5",allocation_limit_reasons:[],optimization_method:"exhaustive_discrete_maximin",break_even_points:[],
    };
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify(payload), {
      status: 200, headers: { "Content-Type":"application/json", "X-EDGEOS-Mode":"demo", "X-EDGEOS-Mode-Contract":"1" },
    }));
    vi.stubGlobal("fetch", fetchMock);
    const { simulateTwoWay } = await import("./simulator-api");
    const result = await simulateTwoWay(input);
    expect(result.calculation_version).toBe("edgeos.cashflow.v2");
    expect(fetchMock.mock.calls[0][0]).toBe("/api/edgeos/api/v1/simulate");
    expect(new Headers(fetchMock.mock.calls[0][1].headers).get("X-EDGEOS-Client-Mode")).toBe("demo");
  });

  it("rejects malformed authoritative decimal fields", async () => {
    const { authoritativeSimulationSchema } = await import("./simulator-api");
    expect(() => authoritativeSimulationSchema.parse({ requested_allocation: "not-a-decimal" })).toThrow();
  });
});
