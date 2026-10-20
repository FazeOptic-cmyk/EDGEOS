import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const eventPage = {
  items: [{
    provider: "demo", provider_event_id: "evt-1", title: "[DEMO] A vs B", subtitle: null,
    sport: "demo_basketball", league: "Demo League", home_team: "A", away_team: "B",
    scheduled_start: "2026-01-01T12:00:00Z", status: "demo_only", resolution_source: "Fixture",
    source_url: "demo://event/evt-1", provider_timestamp: null, retrieved_at: "2026-01-01T11:00:00Z",
    data_origin: "demo", metadata: { synthetic: true },
  }],
  next_cursor: null, retrieved_at: "2026-01-01T11:00:00Z", data_origin: "demo", is_demo: true,
};

describe("normalized provider browser API", () => {
  beforeEach(() => { vi.resetModules(); vi.stubEnv("NEXT_PUBLIC_APP_MODE", "demo"); });
  afterEach(() => { vi.unstubAllEnvs(); vi.restoreAllMocks(); });

  it("uses the same-origin allowlisted endpoint and preserves absent provider timestamps", async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify(eventPage), {
      status: 200,
      headers: { "Content-Type": "application/json", "X-EDGEOS-Mode": "demo", "X-EDGEOS-Mode-Contract": "1" },
    }));
    vi.stubGlobal("fetch", fetchMock);
    const { getProviderEvents } = await import("./markets-api");
    const result = await getProviderEvents("demo", { sport: "demo_basketball", limit: 25 });
    expect(result.items[0]?.provider_timestamp).toBeNull();
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/edgeos/api/v1/providers/demo/events?limit=25&sport=demo_basketball",
      expect.objectContaining({ cache: "no-store" }),
    );
  });

  it("fails closed on a web/API mode mismatch", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify(eventPage), {
      status: 200,
      headers: { "X-EDGEOS-Mode": "live", "X-EDGEOS-Mode-Contract": "1" },
    })));
    const { getProviderEvents } = await import("./markets-api");
    await expect(getProviderEvents("demo")).rejects.toThrow(/mode mismatch/i);
  });

  it("rejects unknown provider codes before issuing a request", async () => {
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);
    const { getProviderMarkets } = await import("./markets-api");
    await expect(getProviderMarkets("https://attacker.example")).rejects.toThrow(/not available/i);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("rejects external providers before issuing a request in demo mode", async () => {
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);
    const { getProviderMarkets } = await import("./markets-api");
    await expect(getProviderMarkets("kalshi")).rejects.toThrow(/disabled in demo mode/i);
    expect(fetchMock).not.toHaveBeenCalled();
  });
});
