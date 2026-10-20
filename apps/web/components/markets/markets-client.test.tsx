import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const health = {
  providers: [
    { id: "demo", name: "EDGEOS Demonstration Feed", status: "demo_only", feed_type: "demo" },
    { id: "kalshi", name: "Kalshi", status: "unknown", feed_type: "rest_polling" },
    { id: "polymarket_international", name: "Polymarket International", status: "unknown", feed_type: "rest_polling" },
    { id: "the_odds_api", name: "The Odds API", status: "requires_api_key", feed_type: "rest_polling" },
  ],
};

const demoMarkets = {
  items: [], next_cursor: null, retrieved_at: "2026-10-20T12:00:00Z",
  data_origin: "demo", is_demo: true,
};

function response(body: unknown) {
  return Promise.resolve(new Response(JSON.stringify(body), {
    status: 200,
    headers: {
      "Content-Type": "application/json",
      "X-EDGEOS-Mode": "demo",
      "X-EDGEOS-Mode-Contract": "1",
    },
  }));
}

describe("demo market browser isolation", () => {
  beforeEach(() => {
    vi.resetModules();
    vi.stubEnv("NEXT_PUBLIC_APP_MODE", "demo");
  });

  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
    vi.unstubAllEnvs();
    vi.restoreAllMocks();
  });

  it("offers only the demonstration provider and never requests an external provider", async () => {
    const fetchMock = vi.fn((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith("/api/v1/providers/health")) return response(health);
      if (url.includes("/api/v1/providers/demo/markets")) return response(demoMarkets);
      throw new Error(`Unexpected outbound provider selection: ${url}`);
    });
    vi.stubGlobal("fetch", fetchMock);
    const { MarketsClient } = await import("./markets-client");
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });

    render(<QueryClientProvider client={client}><MarketsClient /></QueryClientProvider>);

    const provider = await screen.findByRole("combobox", { name: /provider/i });
    expect(provider).toHaveValue("demo");
    expect(screen.getByRole("option", { name: /EDGEOS Demonstration Feed/i })).toBeInTheDocument();
    expect(screen.queryByRole("option", { name: /Kalshi/i })).not.toBeInTheDocument();
    expect(screen.queryByRole("option", { name: /Polymarket/i })).not.toBeInTheDocument();
    expect(screen.queryByRole("option", { name: /Odds API/i })).not.toBeInTheDocument();
    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith(
      expect.stringContaining("/providers/demo/markets"),
      expect.anything(),
    ));
    expect(fetchMock.mock.calls.some(([input]) => /providers\/(kalshi|polymarket|the_odds_api)\//u.test(String(input)))).toBe(false);
  });
});
