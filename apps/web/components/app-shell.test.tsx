import type { AnchorHTMLAttributes, ReactNode } from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { AppShell, formatRefreshTime, summarizeProviderConnection } from "./app-shell";

const mocks = vi.hoisted(() => ({
  push: vi.fn(),
  getProviderHealth: vi.fn(),
  getAlerts: vi.fn(),
}));

vi.mock("next/navigation", () => ({
  usePathname: () => "/scanner",
  useRouter: () => ({ push: mocks.push }),
}));

vi.mock("next/link", () => ({
  default: ({ href, children, ...props }: { href: string; children: ReactNode } & Omit<AnchorHTMLAttributes<HTMLAnchorElement>, "href">) => (
    <a href={href} {...props}>{children}</a>
  ),
}));

vi.mock("@/lib/api", () => ({
  appMode: "live",
  getProviderHealth: mocks.getProviderHealth,
}));

vi.mock("@/lib/workflow-api", () => ({
  getAlerts: mocks.getAlerts,
}));

function renderShell() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <AppShell><div>Page content</div></AppShell>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  mocks.push.mockReset();
  mocks.getProviderHealth.mockReset().mockResolvedValue({
    source: "api",
    data: [{
      id: "kalshi",
      name: "Kalshi",
      status: "connected",
      last_successful_update: "2026-10-20T10:30:00Z",
    }],
  });
  mocks.getAlerts.mockReset().mockResolvedValue({
    mode: "live",
    items: [],
    events: [
      { id: "event-1", title: "Observed update", body: "Persisted event", triggered_at: "2026-10-20T10:31:00Z", read_at: null, is_demo: false },
      { id: "event-2", title: "Read update", body: "Persisted event", triggered_at: "2026-10-20T10:20:00Z", read_at: "2026-10-20T10:21:00Z", is_demo: false },
    ],
  });
});

afterEach(() => cleanup());

describe("desktop application shell", () => {
  it("maps desktop-oriented labels onto the existing working routes", async () => {
    renderShell();

    expect(screen.getByRole("link", { name: "Arbitrage Scanner" })).toHaveAttribute("href", "/scanner");
    expect(screen.getByRole("link", { name: "Prediction Markets" })).toHaveAttribute("href", "/markets");
    expect(screen.getByRole("link", { name: "+EV & Calculator" })).toHaveAttribute("href", "/edge-lab");
    expect(screen.getByRole("link", { name: "Bankroll & Paper" })).toHaveAttribute("href", "/portfolio");
    expect(screen.getByRole("link", { name: "Settings & APIs" })).toHaveAttribute("href", "/settings");
    expect(screen.getByRole("link", { name: "Arbitrage Scanner" })).toHaveAttribute("aria-current", "page");
    await screen.findByRole("link", { name: "1 unread notification" });
  });

  it("shows only observed provider freshness and fetched unread alerts", async () => {
    renderShell();

    expect((await screen.findAllByText("1 provider live")).length).toBeGreaterThan(0);
    expect(screen.getAllByText("Last refresh Oct 20, 10:30 AM UTC").length).toBeGreaterThan(0);
    expect(await screen.findByRole("link", { name: "1 unread notification" })).toBeInTheDocument();
    const alertsLink = screen.getByRole("link", { name: /^Alerts/ });
    expect(within(alertsLink).getByText("1")).toBeInTheDocument();
  });

  it("does not invent a notification badge when the API returns no events", async () => {
    mocks.getAlerts.mockResolvedValue({ mode: "demo", items: [], events: [] });
    renderShell();

    expect(await screen.findByRole("link", { name: "Notifications, no unread alerts" })).toBeInTheDocument();
    expect(within(screen.getByRole("link", { name: /^Alerts/ })).queryByText(/DEMO|3/)).not.toBeInTheDocument();
  });

  it("submits global search, focuses it by keyboard, and refreshes active queries", async () => {
    renderShell();
    await screen.findAllByText("1 provider live");
    expect(mocks.getProviderHealth).toHaveBeenCalledTimes(1);
    const search = screen.getByRole("searchbox", { name: "Search EDGEOS" });

    fireEvent.keyDown(window, { key: "/" });
    expect(search).toHaveFocus();
    fireEvent.change(search, { target: { value: "Lakers totals" } });
    fireEvent.submit(search.closest("form")!);
    expect(mocks.push).toHaveBeenCalledWith("/scanner?q=Lakers%20totals");

    fireEvent.click(screen.getByRole("button", { name: "Refresh active data" }));
    await waitFor(() => expect(mocks.getProviderHealth).toHaveBeenCalledTimes(2));
  });
});

describe("provider connection summaries", () => {
  it("never substitutes the client clock for an unobserved refresh", () => {
    const summary = summarizeProviderConnection(
      [{ id: "provider", name: "Provider", status: "connected", last_successful_update: null }],
      { pending: false, error: false, source: "api" },
    );
    expect(summary.latestSuccessfulUpdate).toBeNull();
    expect(formatRefreshTime(summary.latestSuccessfulUpdate)).toBe("No successful refresh");
  });

  it("labels demonstration provider state without claiming a live connection", () => {
    const summary = summarizeProviderConnection([], { pending: false, error: false, source: "demo" });
    expect(summary).toMatchObject({ label: "DEMO data", detail: "No live provider claim" });
  });

  it("does not keep claiming a live connection after a health refresh fails", () => {
    const summary = summarizeProviderConnection(
      [{ id: "provider", name: "Provider", status: "connected", last_successful_update: "2026-10-20T10:30:00Z" }],
      { pending: false, error: true, source: "api" },
    );
    expect(summary).toMatchObject({ label: "Health refresh failed", detail: "Showing last known status", tone: "red" });
    expect(summary.latestSuccessfulUpdate).toBe("2026-10-20T10:30:00.000Z");
  });
});
