import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { AlertsClient } from "./alerts-client";

function response(body: unknown, status = 200) {
  return Promise.resolve(new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json", "X-EDGEOS-Mode": "demo" },
  }));
}

function renderClient() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  return render(<QueryClientProvider client={client}><AlertsClient /></QueryClientProvider>);
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("alert opportunity gates", () => {
  it("shows all persisted conjunctive gates with ratio ROI rendered as a percentage", async () => {
    vi.stubGlobal("fetch", vi.fn(() => response({
      mode: "demo",
      items: [{
        id: "alert-1", name: "Executable edge", condition: "new_verified_opportunity", threshold: null,
        minimum_net_profit: "12.50", minimum_net_roi: "0.02", minimum_liquidity: "250",
        maximum_quote_age_seconds: 45, enabled: true, cooldown_seconds: 300,
        last_triggered_at: null, is_demo: true,
      }],
      events: [],
    })));
    renderClient();

    expect(await screen.findByText("Executable edge")).toBeInTheDocument();
    expect(screen.getByText("Net profit ≥ 12.50 workspace currency")).toBeInTheDocument();
    expect(screen.getByText("Net ROI ≥ 2.00%")).toBeInTheDocument();
    expect(screen.getByText("Liquidity ≥ 250 workspace currency")).toBeInTheDocument();
    expect(screen.getByText("Quote age ≤ 45s")).toBeInTheDocument();
  });

  it("sends all filled gates conjunctively and converts entered ROI percentage to a ratio", async () => {
    let posted: Record<string, unknown> | null = null;
    const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      if (init?.method === "POST") {
        posted = JSON.parse(String(init.body)) as Record<string, unknown>;
        return response({
          id: "alert-2", name: "Fresh executable edge", condition: "net_roi_above", threshold: "0.02",
          minimum_net_profit: "10.50", minimum_net_roi: "0.015", minimum_liquidity: "300",
          maximum_quote_age_seconds: 30, enabled: true, cooldown_seconds: 300,
          last_triggered_at: null, is_demo: true,
        }, 201);
      }
      if (String(input).endsWith("/api/v1/alerts")) return response({ mode: "demo", items: [], events: [] });
      throw new Error(`Unexpected request: ${input}`);
    });
    vi.stubGlobal("fetch", fetchMock);
    renderClient();

    await screen.findByText("No alert rules");
    fireEvent.click(screen.getByRole("button", { name: "New alert" }));
    fireEvent.change(screen.getByLabelText("Rule name"), { target: { value: "Fresh executable edge" } });
    fireEvent.change(screen.getByLabelText("Minimum net profit (workspace currency)"), { target: { value: "10.50" } });
    fireEvent.change(screen.getByLabelText("Minimum net ROI (%)"), { target: { value: "1.50" } });
    fireEvent.change(screen.getByLabelText("Minimum liquidity (workspace currency)"), { target: { value: "300" } });
    fireEvent.change(screen.getByLabelText("Maximum quote age (seconds)"), { target: { value: "30" } });
    fireEvent.click(screen.getByRole("button", { name: "Create alert" }));

    await waitFor(() => expect(posted).not.toBeNull());
    expect(posted).toMatchObject({
      name: "Fresh executable edge",
      threshold: "0.02",
      minimum_net_profit: "10.50",
      minimum_net_roi: "0.015",
      minimum_liquidity: "300",
      maximum_quote_age_seconds: 30,
    });
  });
});
