import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { AuthoritativeSimulation } from "@/lib/simulator-api";
import { EdgeLabClient } from "./edge-lab-client";

const simulateManualMock = vi.hoisted(() => vi.fn());
vi.mock("@/lib/simulator-api", async (importOriginal) => ({
  ...await importOriginal<typeof import("@/lib/simulator-api")>(),
  simulateManual: simulateManualMock,
}));

const result: AuthoritativeSimulation = {
  requested_allocation: "210", deployed_capital: "210", unallocated_capital: "0", total_fees: "0",
  legs: [
    { offer_id: "manual-a", provider: "Manual platform A", outcome_id: "outcome_a", price_kind: "decimal_odds", requested_quantity: "100", filled_quantity: "100", unit_cost: "1", cash_cost: "100", gross_payout_if_wins: "220", fees: "0", quote_age_seconds: null, stale: true, limit_reasons: [] },
    { offer_id: "manual-b", provider: "Manual platform B", outcome_id: "outcome_b", price_kind: "decimal_odds", requested_quantity: "110", filled_quantity: "110", unit_cost: "1", cash_cost: "110", gross_payout_if_wins: "220", fees: "0", quote_age_seconds: null, stale: true, limit_reasons: [] },
  ],
  outcomes: [
    { outcome_id: "outcome_a", gross_payout: "220", net_profit: "10" },
    { outcome_id: "outcome_b", gross_payout: "220", net_profit: "10" },
  ],
  equalized_gross_payout: "220", gross_profit: "10", net_profit_worst_case: "10", net_profit_best_case: "10",
  gross_roi: "0.047619", net_roi: "0.047619", maximum_available_allocation: "210", unmatched_exposure: "0",
  outcome_coverage_verified: true, is_theoretical_arbitrage: true, execution_eligible: false,
  rejection_codes: ["settlement_not_verified", "quote_age_unknown"], assumptions: [],
  calculation_version: "edgeos.cashflow.v2", currency: "USD", capital_definition: "cash_cost_plus_fees",
  capital_lockup: "210", expected_lockup_seconds: null, quote_timestamp_skew_seconds: null,
  maximum_quote_timestamp_skew_seconds: "5", allocation_limit_reasons: [], optimization_method: "exhaustive_discrete_maximin",
  break_even_points: [{ offer_id: "manual-a", price_kind: "decimal_odds", current_price: "2.20", fixed_allocation_break_even_price: "2.10", adverse_movement: "0.10", adverse_movement_bps: "454.5", limiting_outcome_id: "outcome_a", methodology: "fixed_allocation_all_outcome_cashflow" }],
};

function renderLab() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={client}><EdgeLabClient /></QueryClientProvider>);
}

describe("Edge Lab authoritative simulator", () => {
  afterEach(() => { cleanup(); simulateManualMock.mockReset(); });

  it("exposes labeled inputs and server validation without presenting execution eligibility", async () => {
    simulateManualMock.mockResolvedValue(result);
    renderLab();

    expect(screen.getByRole("spinbutton", { name: /total simulated capital/i })).toBeInTheDocument();
    expect(screen.getAllByRole("textbox", { name: /^decimal odds$/i })).toHaveLength(2);
    expect(screen.getAllByRole("combobox", { name: /^odds format$/i })).toHaveLength(2);
    expect(await screen.findByText("edgeos.cashflow.v2")).toBeInTheDocument();
    expect(screen.getByText("RESEARCH ONLY")).toBeInTheDocument();
    expect(screen.getByText("Settlement equivalence is not verified")).toBeInTheDocument();
    expect(screen.getAllByText("Unknown").length).toBeGreaterThan(0);
  });

  it("debounces changed inputs before requesting another server result", async () => {
    simulateManualMock.mockResolvedValue(result);
    renderLab();
    await waitFor(() => expect(simulateManualMock).toHaveBeenCalledTimes(1));
    simulateManualMock.mockClear();

    const oddsInputs = screen.getAllByRole("textbox", { name: /^decimal odds$/i });
    fireEvent.change(oddsInputs[0]!, { target: { value: "2.30" } });
    fireEvent.change(oddsInputs[0]!, { target: { value: "2.40" } });
    expect(simulateManualMock).not.toHaveBeenCalled();

    await waitFor(() => expect(simulateManualMock).toHaveBeenCalledTimes(1), { timeout: 1_200 });
    expect(simulateManualMock.mock.calls[0]?.[0].legs[0]).toMatchObject({ price: "2.40" });
  });

  it("adds a third outcome and sends all three legs to the authoritative engine", async () => {
    simulateManualMock.mockResolvedValue(result);
    renderLab();
    await waitFor(() => expect(simulateManualMock).toHaveBeenCalledTimes(1));
    simulateManualMock.mockClear();

    fireEvent.click(screen.getByRole("button", { name: "3-way" }));
    expect(screen.getAllByRole("combobox", { name: /^odds format$/i })).toHaveLength(3);
    await waitFor(() => expect(simulateManualMock).toHaveBeenCalledTimes(1), { timeout: 1_200 });
    expect(simulateManualMock.mock.calls[0]?.[0].legs).toHaveLength(3);
  });

  it("supports American input, binary Yes/No presets, and explicit server rebalancing", async () => {
    simulateManualMock.mockResolvedValue(result);
    renderLab();
    await waitFor(() => expect(simulateManualMock).toHaveBeenCalledTimes(1));
    simulateManualMock.mockClear();

    fireEvent.change(screen.getAllByRole("combobox", { name: /^odds format$/i })[0]!, { target: { value: "american" } });
    fireEvent.change(screen.getByRole("textbox", { name: /^american odds$/i }), { target: { value: "+120" } });
    await waitFor(() => expect(simulateManualMock).toHaveBeenCalledTimes(1), { timeout: 1_200 });
    expect(simulateManualMock.mock.calls[0]?.[0].legs[0]).toMatchObject({ format: "american", price: "+120" });

    simulateManualMock.mockClear();
    fireEvent.click(screen.getByRole("button", { name: /use binary yes \/ no/i }));
    await waitFor(() => expect(simulateManualMock).toHaveBeenCalledTimes(1), { timeout: 1_200 });
    expect(simulateManualMock.mock.calls[0]?.[0].legs).toEqual(expect.arrayContaining([
      expect.objectContaining({ outcomeId: "yes", format: "contract", price: "0.50" }),
      expect.objectContaining({ outcomeId: "no", format: "contract", price: "0.50" }),
    ]));

    const balance = screen.getByRole("button", { name: /auto balance stakes/i });
    await waitFor(() => expect(balance).toBeEnabled());
    simulateManualMock.mockClear();
    fireEvent.click(balance);
    await waitFor(() => expect(simulateManualMock).toHaveBeenCalledTimes(1));
  });
});
