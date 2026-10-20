import { afterEach, describe, expect, it, vi } from "vitest";
import { getDashboardSummary } from "./dashboard-api";

afterEach(() => vi.unstubAllGlobals());

describe("dashboard API contract", () => {
  it("uses the same-origin BFF and preserves decimal strings", async () => {
    const fetchMock = vi.fn(async () => new Response(JSON.stringify({
      data_origin: "demo", is_demo: true, simulated_portfolio_value: "10000",
      realized_simulated_pnl: "0", unrealized_simulated_pnl: "0", net_roi: "0",
      active_simulated_positions: 0, opportunities_detected: 1, verified_opportunities: 1,
      historical_capture_rate: null, empty_performance_history: true,
    }), { headers: { "Content-Type": "application/json", "X-EDGEOS-Mode": "demo", "X-EDGEOS-Mode-Contract": "1" } }));
    vi.stubGlobal("fetch", fetchMock);
    const result = await getDashboardSummary();
    expect(result.simulated_portfolio_value).toBe("10000");
    expect(fetchMock).toHaveBeenCalledWith("/api/edgeos/api/v1/dashboard", expect.objectContaining({ cache: "no-store" }));
  });

  it("rejects a missing runtime-mode contract", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response("{}", { headers: { "Content-Type": "application/json" } })));
    await expect(getDashboardSummary()).rejects.toThrow(/mode contract/i);
  });
});
