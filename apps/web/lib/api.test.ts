import { afterEach, describe, expect, it, vi } from "vitest";
import { getOpportunities, getProviderHealth, getSavedOpportunities, normalizeOpportunity, normalizeOpportunityDetail } from "./api";

describe("API opportunity adapter", () => {
  afterEach(() => vi.unstubAllGlobals());
  it("converts backend ratio ROI into UI percentage points", () => {
    const result = normalizeOpportunity({ id:"o1",event:"A vs B",sport:"football",league:"NFL",market:"winner",market_type:"moneyline",platform_a:"A",platform_b:"B",prices:["2.20","2.00"],gross_roi:"0.047619",net_roi:"0.041",potential_net_profit:"8.61",available_liquidity:"210",event_start_time:"2026-10-16T02:00:00Z",phase:"prematch",live:false,observed_at:"2026-10-16T00:00:00Z",quote_age_seconds:"2",data_origin:"live_poll",settlement_status:"verified",research_score:"91" });
    expect(result.grossRoi).toBeCloseTo(4.7619, 4);
    expect(result.netRoi).toBeCloseTo(4.1, 5);
    expect(result.dataMode).toBe("live");
    expect(result.startTime).toBe("2026-10-16T02:00:00Z");
    expect(result.marketType).toBe("moneyline");
  });

  it("preserves cached provenance instead of upgrading it to live", () => {
    const result = normalizeOpportunity({ id:"o2",event:"A vs B",sport:"football",league:"NFL",market:"winner",platform_a:"A",platform_b:"B",prices:["2.20","2.00"],gross_roi:"0.04",net_roi:"0.03",potential_net_profit:"6",available_liquidity:"200",event_start_time:"2026-10-16T00:00:00Z",phase:"live",live:true,observed_at:"2026-10-16T00:00:00Z",quote_age_seconds:"20",data_origin:"cache",settlement_status:"review_required",research_score:"40" });
    expect(result.dataMode).toBe("cached");
    expect(result.matchStatus).toBe("REVIEW REQUIRED");
    expect(result.live).toBe(true);
  });

  it("preserves the server opportunity lifecycle and expiry without requiring them from older demo payloads", () => {
    const current = normalizeOpportunity({ id:"o-lifecycle",event:"A vs B",sport:"football",league:"NFL",market:"winner",platform_a:"A",platform_b:"B",prices:["2.20","2.00"],gross_roi:"0.04",net_roi:"0.03",potential_net_profit:"6",available_liquidity:"200",event_start_time:"2026-10-20T20:00:00Z",phase:"prematch",live:false,observed_at:"2026-10-20T19:00:00Z",quote_age_seconds:"5",data_origin:"live_poll",settlement_status:"verified",research_score:"80",status:"stale",expires_at:"2026-10-20T19:00:30Z" });
    const legacy = normalizeOpportunity({ id:"o-legacy",event:"C vs D",sport:"football",league:"NFL",market:"winner",platform_a:"A",platform_b:"B",prices:["2.10","2.05"],gross_roi:"0.02",net_roi:"0.01",potential_net_profit:"2",available_liquidity:"100",event_start_time:null,phase:"prematch",live:false,observed_at:"2026-10-20T19:00:00Z",quote_age_seconds:null,data_origin:"demo",settlement_status:"verified",research_score:"50" });

    expect(current).toMatchObject({ lifecycle: "stale", expiresAt: "2026-10-20T19:00:30Z", withdrawnAt: null, observedAt: "2026-10-20T19:00:00Z" });
    expect(legacy).toMatchObject({ lifecycle: null, expiresAt: null, withdrawnAt: null });
  });

  it("accepts the lifecycle aliases used by persisted lifecycle events", () => {
    const result = normalizeOpportunity({ id:"o-withdrawn",event:"A vs B",sport:"football",league:"NFL",market:"winner",platform_a:"A",platform_b:"B",prices:["2.20","2.00"],gross_roi:"0.04",net_roi:"0.03",potential_net_profit:"6",available_liquidity:"200",event_start_time:null,phase:"prematch",live:false,observed_at:"2026-10-20T19:00:00Z",quote_age_seconds:"20",data_origin:"live_poll",settlement_status:"verified",research_score:"80",lifecycleStatus:"invalidated",withdrawnAt:"2026-10-20T19:00:10Z" });
    expect(result).toMatchObject({ lifecycle: "invalidated", withdrawnAt: "2026-10-20T19:00:10Z" });
  });

  it("preserves unknown liquidity and provider quote age", () => {
    const result = normalizeOpportunity({ id:"o-unknown",event:"A vs B",sport:"football",league:"NFL",market:"winner",platform_a:"A",platform_b:"B",prices:["2.20","2.00"],gross_roi:"0.047619",net_roi:"0.041",total_allocation:"210",potential_net_profit:"8.61",available_liquidity:null,event_start_time:null,phase:"prematch",live:false,observed_at:"2026-10-16T00:00:00Z",provider_timestamp:null,quote_age_seconds:null,data_origin:"live_poll",settlement_status:"verified",research_score:"91" });
    expect(result.liquidity).toBeNull();
    expect(result.quoteAgeSeconds).toBeNull();
    expect(result.legs[0].timestamp).toBeNull();
    expect(result.totalAllocation).toBe(210);
    expect(result.totalAllocationExact).toBe("210");
    expect(result.startTime).toBeNull();
  });

  it("preserves non-ISO provider settlement units without labeling them USD", () => {
    const result = normalizeOpportunity({ id:"o-pusd",event:"A vs B",sport:"basketball",league:"NBA",market:"winner",platform_a:"A",platform_b:"B",prices:["2.20","2.00"],gross_roi:"0.04",net_roi:"0.03",potential_net_profit:"6",available_liquidity:"200",event_start_time:null,phase:"prematch",live:false,observed_at:"2026-10-16T00:00:00Z",quote_age_seconds:"20",data_origin:"live_poll",settlement_status:"verified",research_score:"80",currency:"pUSD" });
    expect(result.currency).toBe("pUSD");
  });

  it("maps detailed Decimal cash flows into the opportunity drawer", () => {
    const result = normalizeOpportunityDetail({
      id:"o3",event:"A vs B",sport:"football",league:"NFL",market:"winner",platform_a:"A",platform_b:"A",prices:["2.20","2.00"],gross_roi:"0.047619",net_roi:"0.041",potential_net_profit:"8.61",available_liquidity:"210",event_start_time:"2026-10-16T02:00:00Z",phase:"prematch",live:false,observed_at:"2026-10-16T00:00:00Z",quote_age_seconds:"2",data_origin:"demo",settlement_status:"verified",research_score:"91",
      simulation: { requested_allocation:"210", deployed_capital:"210", total_fees:"1.25", net_profit_worst_case:"8.75", net_profit_best_case:"8.75", legs:[{provider:"A",outcome_id:"home",price_kind:"decimal_odds",unit_cost:"1",requested_quantity:"100",filled_quantity:"100",cash_cost:"100",fees:"1.25"},{provider:"A",outcome_id:"away",price_kind:"contract_ask",unit_cost:"0.45",requested_quantity:"108.75",filled_quantity:"108.75",cash_cost:"108.75",fees:"0"}], outcomes:[{outcome_id:"home",gross_payout:"220",net_profit:"8.75"}] },
      settlement_audit: { reasons:["Rules agree"] }, failure_reasons:["Prices can move"],
      price_history: [
        { observed_at:"2026-10-16T00:00:00Z",net_roi:"0.031" },
        { observed_at:"2026-10-16T00:00:30Z",net_roi:"0.041" },
      ],
    });
    expect(result.totalFees).toBe(1.25);
    expect(result.allocations[0]).toMatchObject({
      platform: "A", stake: 101.25, cashCost: 100, fee: 1.25,
      quantity: 100, priceKind: "decimal_odds", modeledPrice: 2.2,
    });
    expect(result.allocations[1]).toMatchObject({
      platform: "A", priceKind: "contract_ask", modeledPrice: 0.45,
    });
    expect(result.payouts[0]).toEqual({ outcome: "home", gross: 220, net: 8.75 });
    expect(result.matchReasons).toEqual(["Rules agree"]);
    expect(result.history.map((point) => point.time)).toEqual([
      "2026-10-16T00:00:00Z", "2026-10-16T00:00:30Z",
    ]);
    expect(result.history[0]?.roi).toBeCloseTo(3.1, 10);
    expect(result.history[1]?.roi).toBeCloseTo(4.1, 10);
  });

  it("loads persisted saved-opportunity metadata through the same-origin gateway", async () => {
    const fetchMock = vi.fn(async () => new Response(JSON.stringify({
      items: [{ opportunity_id: "o1", favorited: true, saved_at: "2026-10-20T00:00:00Z" }],
    }), { headers: { "Content-Type": "application/json", "X-EDGEOS-Mode": "demo", "X-EDGEOS-Mode-Contract": "1" } }));
    vi.stubGlobal("fetch", fetchMock);
    await expect(getSavedOpportunities()).resolves.toEqual([
      { opportunity_id: "o1", favorited: true, saved_at: "2026-10-20T00:00:00Z" },
    ]);
    expect(fetchMock).toHaveBeenCalledWith("/api/edgeos/api/v1/saved-opportunities", expect.any(Object));
  });

  it("does not replace an unavailable opportunity API with browser fixtures", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new Error("desktop gateway offline")));
    await expect(getOpportunities()).rejects.toThrow("desktop gateway offline");
  });

  it("does not replace unavailable provider health with browser fixtures", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new Error("provider gateway offline")));
    await expect(getProviderHealth()).rejects.toThrow("provider gateway offline");
  });

  it("preserves provider delay, quota, rate-limit, and timestamp diagnostics", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({ providers: [{
      id: "provider", name: "Provider", status: "delayed", feed_type: "rest_polling",
      last_successful_update: "2026-10-20T10:30:00Z", measured_data_delay_seconds: 18,
      api_quota_usage: { used: 20, remaining: 80, limit: 100, retry_after_seconds: null, reset_at: "2026-10-20T11:00:00Z" },
      rate_limit_status: "ok", market_coverage: "basketball", active_instruments: 12,
    }] }), { headers: { "Content-Type": "application/json", "X-EDGEOS-Mode": "demo", "X-EDGEOS-Mode-Contract": "1" } })));

    await expect(getProviderHealth()).resolves.toMatchObject({ data: [{
      id: "provider", status: "delayed", measured_data_delay_seconds: 18,
      quota_used: 20, quota_remaining: 80, quota_limit: 100,
      quota_reset_at: "2026-10-20T11:00:00Z", rate_limit_status: "ok",
      last_successful_update: "2026-10-20T10:30:00Z", active_instruments: 12,
    }] });
  });
});
