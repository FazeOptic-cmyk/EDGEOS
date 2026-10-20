import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { MatchReviewQueue } from "./match-review-queue";

function json(value: unknown, status = 200) {
  return Promise.resolve(new Response(JSON.stringify(value), {
    status,
    headers: {
      "Content-Type": "application/json",
      "X-EDGEOS-Mode": "demo",
      "X-EDGEOS-Mode-Contract": "1",
    },
  }));
}

const candidate = {
  id: "match-17",
  classification: "review_required",
  candidate_score: "0.87",
  settlement_confidence: "0.76",
  checks: {
    complete_outcome_coverage: false,
    all_material_rules_explicit: false,
    overtime_treatment: "unknown",
  },
  explanation: "The event identity agrees, but overtime treatment is unresolved.",
  engine_version: "matcher-3.2",
  decision_version: 4,
  left_rule_fingerprint: "left-fingerprint-123456",
  right_rule_fingerprint: "right-fingerprint-123456",
  decision_fingerprint: null,
  source_evidence: {},
  invalidated_at: null,
  invalidation_reason: null,
  left: { market: "Boston v New York — winner", provider: "Provider A" },
  right: { market: "New York at Boston — winner", provider: "Provider B" },
  history: [{
    id: "decision-3",
    decision: "review_required",
    reviewer_kind: "system",
    rationale: "Overtime treatment was not present in either normalized rule set.",
    reviewed_at: "2026-10-19T12:00:00Z",
    decision_version: 3,
    source_evidence: {},
  }],
};

function renderQueue() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(<QueryClientProvider client={client}><MatchReviewQueue /></QueryClientProvider>);
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("settlement match review queue", () => {
  it("renders a truthful empty demo state instead of fabricated candidates", async () => {
    vi.stubGlobal("fetch", vi.fn(() => json({ items: [], mode: "demo", message: "No persisted demo review records." })));

    renderQueue();

    expect(await screen.findByText("No candidates awaiting review")).toBeInTheDocument();
    expect(screen.getByText(/Demo mode does not fabricate settlement decisions/i)).toBeInTheDocument();
  });

  it("shows source evidence and audit history, and restores focus when Escape closes the dialog", async () => {
    vi.stubGlobal("fetch", vi.fn(() => json({ items: [candidate], mode: "demo" })));
    renderQueue();

    const review = await screen.findByRole("button", { name: "Review" });
    fireEvent.click(screen.getByText("Evidence, fingerprints, and audit history"));
    expect(screen.getByText(/Overtime treatment was not present/i)).toBeInTheDocument();
    review.focus();
    fireEvent.click(review);
    const dialog = screen.getByRole("dialog", { name: "Review settlement equivalence" });
    expect(within(dialog).getByText(/Version 4/i)).toBeInTheDocument();
    fireEvent.keyDown(dialog, { key: "Escape" });
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    await waitFor(() => expect(review).toHaveFocus());
  });

  it("blocks an unsupported verified decision before sending it", async () => {
    const fetchMock = vi.fn(() => json({ items: [candidate], mode: "demo" }));
    vi.stubGlobal("fetch", fetchMock);
    renderQueue();

    fireEvent.click(await screen.findByRole("button", { name: "Review" }));
    const dialog = screen.getByRole("dialog");
    fireEvent.change(within(dialog).getByLabelText("Decision"), { target: { value: "verified" } });
    fireEvent.change(within(dialog).getByLabelText("Rationale"), { target: { value: "These contracts settle identically." } });
    fireEvent.click(within(dialog).getByRole("button", { name: "Record decision" }));

    expect(await within(dialog).findByRole("alert")).toHaveTextContent(/complete outcome coverage/i);
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("submits complete evidence with the displayed version and reports reviewer permission errors", async () => {
    const fetchMock = vi.fn(async (_url: string | URL | Request, init?: RequestInit) => {
      if (init?.method === "PATCH") {
        return json({ detail: { code: "reviewer_permission_required" } }, 403);
      }
      return json({ items: [candidate], mode: "demo" });
    });
    vi.stubGlobal("fetch", fetchMock);
    renderQueue();

    fireEvent.click(await screen.findByRole("button", { name: "Review" }));
    const dialog = screen.getByRole("dialog");
    fireEvent.change(within(dialog).getByLabelText("Decision"), { target: { value: "verified" } });
    fireEvent.click(within(dialog).getByRole("checkbox", { name: /Complete outcome coverage/i }));
    fireEvent.click(within(dialog).getByRole("checkbox", { name: /All material rules explicit/i }));
    fireEvent.change(within(dialog).getByLabelText("Rationale"), { target: { value: "Official rules show identical settlement treatment." } });
    fireEvent.change(within(dialog).getByLabelText("Source evidence JSON"), {
      target: { value: JSON.stringify({ official_rule: "https://rules.example/market-17" }) },
    });
    fireEvent.click(within(dialog).getByRole("button", { name: "Record decision" }));

    expect(await within(dialog).findByRole("alert")).toHaveTextContent(/not allowlisted/i);
    const patchCall = fetchMock.mock.calls.find(([, init]) => init?.method === "PATCH");
    expect(patchCall).toBeDefined();
    expect(JSON.parse(String(patchCall?.[1]?.body))).toMatchObject({
      decision: "verified",
      rationale: "Official rules show identical settlement treatment.",
      expected_version: 4,
      checks: {
        complete_outcome_coverage: true,
        all_material_rules_explicit: true,
      },
      source_evidence: { official_rule: "https://rules.example/market-17" },
    });
    expect(screen.getByRole("dialog")).toBeInTheDocument();
  });
});
