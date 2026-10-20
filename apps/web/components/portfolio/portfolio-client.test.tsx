import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { PortfolioClient } from "./portfolio-client";

const mocks = vi.hoisted(() => ({
  getPortfolio: vi.fn(),
  exportPaperPortfolio: vi.fn(),
}));

vi.mock("@/lib/workflow-api", () => ({
  getPortfolio: mocks.getPortfolio,
  exportPaperPortfolio: mocks.exportPaperPortfolio,
  createPaperPosition: vi.fn(),
  recordPaperFill: vi.fn(),
  settlePaperPosition: vi.fn(),
  cancelPaperPosition: vi.fn(),
}));

function renderPortfolio() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <PortfolioClient />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  vi.stubEnv("NEXT_PUBLIC_DESKTOP_BUILD", "true");
  mocks.getPortfolio.mockReset().mockResolvedValue({
    mode: "demo",
    currency: "USD",
    simulation_only: true,
    summary: {
      portfolio_value: "1000",
      cash_balance: "1000",
      reserved_capital: "0",
      realized_pnl: "0",
      unrealized_pnl: "0",
      net_roi: "0",
      active_positions: 0,
      settled_positions: 0,
    },
    positions: [],
  });
  mocks.exportPaperPortfolio.mockReset();
  Object.defineProperty(URL, "createObjectURL", {
    configurable: true,
    value: vi.fn(() => "blob:http://localhost/export"),
  });
  Object.defineProperty(URL, "revokeObjectURL", {
    configurable: true,
    value: vi.fn(),
  });
  vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => undefined);
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.unstubAllEnvs();
});

describe("paper-ledger export feedback", () => {
  it("announces an export failure as an alert rather than a success notice", async () => {
    mocks.exportPaperPortfolio.mockRejectedValue(new Error("Ledger export service unavailable."));
    renderPortfolio();

    const button = await screen.findByRole("button", { name: "Export ledger" });
    await waitFor(() => expect(button).toBeEnabled());
    fireEvent.click(button);

    expect(await screen.findByRole("alert")).toHaveTextContent("Ledger export service unavailable.");
    expect(screen.queryByRole("status", { name: /Ledger export service unavailable/i })).not.toBeInTheDocument();
  });

  it("describes a desktop export as awaiting the native save dialog", async () => {
    mocks.exportPaperPortfolio.mockResolvedValue(new Blob(["ledger"], { type: "text/csv" }));
    renderPortfolio();

    const button = await screen.findByRole("button", { name: "Export ledger" });
    await waitFor(() => expect(button).toBeEnabled());
    fireEvent.click(button);

    expect(await screen.findByRole("status")).toHaveTextContent(/prepared.*native Save dialog/i);
    expect(screen.queryByText(/ledger downloaded/i)).not.toBeInTheDocument();
  });
});
