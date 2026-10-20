import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiConnectionsPanel } from "./api-connections-panel";

const connection = {
  provider_code: "the_odds_api",
  display_name: "The Odds API",
  product_scope: "Sportsbook odds aggregator",
  enabled: true,
  credential_configured: true,
  credential_mask: "••••aB12",
  status: "connected",
  capabilities: ["Events", "Bookmaker odds"],
  coverage: ["basketball_nba"],
  sports: ["basketball_nba"],
  regions: ["us"],
  markets: ["h2h"],
  poll_interval_seconds: 60,
  request_budget_per_hour: 120,
  estimated_requests_per_hour: 60,
  quota_limit: 500,
  quota_remaining: 421,
  quota_resets_at: null,
  last_tested_at: "2026-10-19T12:00:00Z",
  last_success_at: "2026-10-19T12:00:00Z",
  last_market_update_at: "2026-10-19T11:59:50Z",
  error_code: null,
};

function json(value: unknown, status = 200) {
  return Promise.resolve(new Response(JSON.stringify(value), { status, headers: { "Content-Type": "application/json" } }));
}

afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

describe("API connections panel", () => {
  it("explains a missing live/authenticated dependency and disables setup", () => {
    render(<ApiConnectionsPanel available={false} unavailableReason="Live mode and sign-in are required." />);
    expect(screen.getByText("Live mode and sign-in are required.")).toBeInTheDocument();
    expect(screen.getAllByRole("button", { name: "Add" })[0]).toBeDisabled();
  });

  it("does not claim saved connections are already consumed by the worker", async () => {
    vi.stubGlobal("fetch", vi.fn(() => json({ items: [] })));
    render(<ApiConnectionsPanel available />);
    await waitFor(() => expect(screen.queryByText(/Loading encrypted/i)).not.toBeInTheDocument());
    const article = screen.getByRole("heading", { name: "The Odds API" }).closest("article")!;
    fireEvent.click(within(article).getByRole("button", { name: "Add" }));
    expect(screen.getByText(/does not yet consume per-user schedules/i)).toBeInTheDocument();
    expect(screen.queryByText(/Enable scheduled polling/i)).not.toBeInTheDocument();
  });

  it("opens a modal dialog, traps credential UI in the dialog, and closes with Escape", async () => {
    vi.stubGlobal("fetch", vi.fn(() => json({ items: [] })));
    render(<ApiConnectionsPanel available />);
    await waitFor(() => expect(screen.queryByText(/Loading encrypted/i)).not.toBeInTheDocument());
    const article = screen.getByRole("heading", { name: "The Odds API" }).closest("article");
    expect(article).not.toBeNull();
    const add = within(article!).getByRole("button", { name: "Add" });
    add.focus();
    fireEvent.click(add);
    const dialog = screen.getByRole("dialog", { name: /Configure The Odds API/i });
    expect(within(dialog).getByLabelText("API key")).toHaveAttribute("type", "password");
    fireEvent.keyDown(dialog, { key: "Escape" });
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    await waitFor(() => expect(add).toHaveFocus());
  });

  it("disables credential entry when encrypted server storage is unavailable", async () => {
    vi.stubGlobal("fetch", vi.fn(() => json({ error_code: "credential_storage_unavailable" }, 503)));
    render(<ApiConnectionsPanel available />);
    expect(await screen.findByText(/credential master key/i)).toBeInTheDocument();
    expect(screen.getAllByRole("button", { name: "Add" })[0]).toBeDisabled();
  });

  it("sends a credential only in the write request and never renders it", async () => {
    const fetchMock = vi.fn()
      .mockImplementationOnce(() => json({ items: [] }))
      .mockImplementationOnce(() => json({ item: connection }));
    vi.stubGlobal("fetch", fetchMock);
    render(<ApiConnectionsPanel available />);
    await waitFor(() => expect(screen.queryByText(/Loading encrypted/i)).not.toBeInTheDocument());
    const article = screen.getByRole("heading", { name: "The Odds API" }).closest("article")!;
    fireEvent.click(within(article).getByRole("button", { name: "Add" }));
    const secret = "private-test-key-123";
    fireEvent.change(screen.getByLabelText("API key"), { target: { value: secret } });
    fireEvent.click(screen.getByRole("button", { name: "Save connection" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    const secondCall = fetchMock.mock.calls[1];
    expect(secondCall[0]).toBe("/api/provider-connections");
    expect(JSON.parse(String((secondCall[1] as RequestInit).body)).credentials).toEqual({ api_key: secret });
    expect(document.body.textContent).not.toContain(secret);
    expect(screen.getByText("••••aB12")).toBeInTheDocument();
  });
});
