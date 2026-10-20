import { afterEach, describe, expect, it, vi } from "vitest";
import {
  decideMatchReview,
  getMatchReviews,
  MatchReviewRequestError,
} from "./match-reviews";

function response(value: unknown, status = 200) {
  return new Response(JSON.stringify(value), {
    status,
    headers: {
      "Content-Type": "application/json",
      "X-EDGEOS-Mode": "demo",
      "X-EDGEOS-Mode-Contract": "1",
    },
  });
}

const candidate = {
  id: "match-17",
  classification: "review_required",
  candidate_score: 0.87,
  settlement_confidence: "0.76",
  checks: { complete_outcome_coverage: false },
  explanation: "The event identity agrees, but overtime treatment is unresolved.",
  engine_version: "matcher-3.2",
  decision_version: 4,
  left_rule_fingerprint: "left-fingerprint",
  right_rule_fingerprint: "right-fingerprint",
  decision_fingerprint: null,
  source_evidence: {},
  invalidated_at: null,
  invalidation_reason: null,
  left: { market: "Boston v New York — winner", provider: "Provider A" },
  right: { market: "New York at Boston — winner", provider: "Provider B" },
  history: [],
  unexpected_secret: "must not cross the adapter",
};

afterEach(() => vi.unstubAllGlobals());

describe("match review API adapter", () => {
  it("loads typed review evidence through the same-origin BFF and strips unknown fields", async () => {
    const fetchMock = vi.fn(async () => response({ items: [candidate], mode: "demo" }));
    vi.stubGlobal("fetch", fetchMock);

    const result = await getMatchReviews(999);

    expect(fetchMock).toHaveBeenCalledWith("/api/edgeos/api/v1/match-reviews?limit=200", expect.objectContaining({
      cache: "no-store",
      credentials: "same-origin",
    }));
    expect(result.items[0]?.candidate_score).toBe("0.87");
    expect(result.items[0]).not.toHaveProperty("unexpected_secret");
  });

  it("preserves the review version and maps authorization failures to an actionable error", async () => {
    const fetchMock = vi.fn(async (_url: string | URL | Request, init?: RequestInit) => {
      expect(init?.method).toBe("PATCH");
      expect(JSON.parse(String(init?.body))).toMatchObject({
        decision: "rejected",
        expected_version: 4,
      });
      return response({ detail: { code: "reviewer_permission_required" } }, 403);
    });
    vi.stubGlobal("fetch", fetchMock);

    await expect(decideMatchReview("match/17", {
      decision: "rejected",
      rationale: "The cancellation rules conflict.",
      checks: { cancellation_rules_match: false },
      source_evidence: { official_rule: "https://rules.example/cancellation" },
      expected_version: 4,
    })).rejects.toEqual(expect.objectContaining<Partial<MatchReviewRequestError>>({
      code: "reviewer_permission_required",
      status: 403,
      message: expect.stringMatching(/allowlisted/i),
    }));
    expect(fetchMock.mock.calls[0]?.[0]).toBe("/api/edgeos/api/v1/match-reviews/match%2F17");
  });

  it("surfaces optimistic concurrency conflicts without overwriting newer evidence", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => response({ detail: { code: "review_version_conflict" } }, 409)));

    await expect(decideMatchReview("match-17", {
      decision: "review_required",
      rationale: "A newer rules document must be reviewed.",
      checks: {},
      source_evidence: {},
      expected_version: 4,
    })).rejects.toMatchObject({
      code: "review_version_conflict",
      status: 409,
      message: expect.stringMatching(/changed after you opened/i),
    });
  });
});
