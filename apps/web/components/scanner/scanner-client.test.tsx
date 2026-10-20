import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { normalizeOpportunity } from "@/lib/api";
import { DetailDrawer, MatchBadge } from "./scanner-client";

afterEach(cleanup);

describe("scanner settlement status badge", () => {
  it.each([
    ["VERIFIED MATCH", "VERIFIED"],
    ["REVIEW REQUIRED", "REVIEW"],
    ["REJECTED", "REJECTED"],
  ] as const)("renders %s without collapsing its meaning", (status, label) => {
    render(<MatchBadge status={status} />);
    expect(screen.getByText(label)).toBeInTheDocument();
  });
});

describe("scanner detail safety gate", () => {
  it("does not create a paper position from incomplete list data after detail failure", async () => {
    const opportunity = normalizeOpportunity({
      id: "o1", event: "A vs B", sport: "football", league: "NFL", market: "winner",
      platform_a: "A", platform_b: "B", prices: ["2.20", "2.00"], gross_roi: "0.047619",
      net_roi: "0.041", total_allocation: "210", potential_net_profit: "8.61",
      available_liquidity: "210", event_start_time: "2026-10-20T18:00:00Z", phase: "prematch",
      live: false, observed_at: "2026-10-20T16:00:00Z", quote_age_seconds: "2",
      data_origin: "live_poll", settlement_status: "verified", research_score: "91",
    });
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });

    render(
      <QueryClientProvider client={client}>
        <DetailDrawer
          opportunity={opportunity}
          detailState="error"
          onClose={() => undefined}
          isSaved={false}
          onToggleSaved={() => undefined}
          saving={false}
        />
      </QueryClientProvider>,
    );

    expect(await screen.findByRole("alert")).toHaveTextContent(/authoritative opportunity detail is unavailable/i);
    expect(screen.getByRole("button", { name: /detail unavailable/i })).toBeDisabled();
    expect(screen.getByText(/fees Unknown/i)).toBeInTheDocument();
  });

  it("keeps a stale opportunity visible for research but disables paper creation", async () => {
    const opportunity = normalizeOpportunity({
      id: "o-stale", event: "A vs B", sport: "football", league: "NFL", market: "winner",
      platform_a: "A", platform_b: "B", prices: ["2.20", "2.00"], gross_roi: "0.047619",
      net_roi: "0.041", total_allocation: "210", potential_net_profit: "8.61",
      available_liquidity: "210", event_start_time: "2026-10-20T18:00:00Z", phase: "prematch",
      live: false, observed_at: "2026-10-20T16:00:00Z", quote_age_seconds: "90",
      data_origin: "live_poll", settlement_status: "verified", research_score: "91", status: "stale",
    });
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });

    render(
      <QueryClientProvider client={client}>
        <DetailDrawer
          opportunity={opportunity}
          detailState="stale"
          onClose={() => undefined}
          isSaved={false}
          onToggleSaved={() => undefined}
          saving={false}
        />
      </QueryClientProvider>,
    );

    expect(await screen.findByRole("alert")).toHaveTextContent(/not treated as currently executable/i);
    expect(screen.getByText("STALE")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /stale quotes/i })).toBeDisabled();
  });
});
