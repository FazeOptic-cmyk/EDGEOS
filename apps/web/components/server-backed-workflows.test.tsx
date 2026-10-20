import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { AlertsClient } from "./alerts/alerts-client";
import { AnalyticsClient } from "./analytics/analytics-client";
import { WatchlistClient } from "./watchlist/watchlist-client";

function failedRequest(message: string) {
  return Promise.resolve(new Response(JSON.stringify({ detail: message }), {
    status: 503,
    headers: { "Content-Type": "application/json", "X-EDGEOS-Mode": "demo" },
  }));
}

function renderWithQueryClient(component: React.ReactNode) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  return render(<QueryClientProvider client={client}>{component}</QueryClientProvider>);
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("server-backed private workflow screens", () => {
  it("keeps an alerts API failure visible and does not inject alert fixtures", async () => {
    vi.stubGlobal("fetch", vi.fn(() => failedRequest("alert store offline")));
    renderWithQueryClient(<AlertsClient />);

    expect(await screen.findByRole("alert")).toHaveTextContent(/No browser fixtures were substituted/i);
    expect(screen.queryByText("High-confidence discrepancy")).not.toBeInTheDocument();
    expect(screen.queryByText("Verified opportunity detected")).not.toBeInTheDocument();
  });

  it("keeps a watchlist API failure visible and does not inject saved teams", async () => {
    vi.stubGlobal("fetch", vi.fn(() => failedRequest("watchlist store offline")));
    renderWithQueryClient(<WatchlistClient />);

    expect(await screen.findByRole("alert")).toHaveTextContent(/No browser fixtures were substituted/i);
    expect(screen.queryByText("Buffalo Bills")).not.toBeInTheDocument();
    expect(screen.queryByText("Sportsbook ↔ Exchange")).not.toBeInTheDocument();
  });

  it("keeps an analytics API failure visible and does not fabricate profitability", async () => {
    vi.stubGlobal("fetch", vi.fn(() => failedRequest("analytics store offline")));
    renderWithQueryClient(<AnalyticsClient />);

    expect(await screen.findByRole("alert")).toHaveTextContent(/No performance fixtures were substituted/i);
    expect(screen.queryByText("$91.00")).not.toBeInTheDocument();
    expect(screen.queryByText("0.91%")).not.toBeInTheDocument();
  });

  it("does not claim ordinary demo watchlist storage is persistent", async () => {
    vi.stubEnv("NEXT_PUBLIC_DESKTOP_BUILD", "false");
    vi.stubGlobal("fetch", vi.fn(() => Promise.resolve(new Response(JSON.stringify({ mode: "demo", items: [] }), {
      headers: { "Content-Type": "application/json", "X-EDGEOS-Mode": "demo" },
    }))));
    renderWithQueryClient(<WatchlistClient />);

    expect(await screen.findByText(/0 active · DEMO WORKSPACE/i)).toBeInTheDocument();
    expect(screen.queryByText(/DEMO PERSISTED WORKSPACE/i)).not.toBeInTheDocument();
  });

  it("describes ordinary demo alert records as current-workspace state", async () => {
    vi.stubEnv("NEXT_PUBLIC_DESKTOP_BUILD", "false");
    vi.stubGlobal("fetch", vi.fn(() => Promise.resolve(new Response(JSON.stringify({ mode: "demo", items: [], events: [] }), {
      headers: { "Content-Type": "application/json", "X-EDGEOS-Mode": "demo" },
    }))));
    renderWithQueryClient(<AlertsClient />);

    expect(await screen.findByText("Current demonstration workspace records")).toBeInTheDocument();
    expect(screen.queryByText(/Persisted demonstration workspace records/i)).not.toBeInTheDocument();
  });
});
