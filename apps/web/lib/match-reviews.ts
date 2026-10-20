import { z } from "zod";
import { appMode } from "./api";

const decimalString = z.union([z.string(), z.number()]).transform(String);
const jsonObject = z.record(z.string(), z.unknown());

const reviewHistorySchema = z.object({
  id: z.string(),
  decision: z.enum(["verified", "review_required", "rejected"]),
  reviewer_kind: z.enum(["system", "operator"]),
  rationale: z.string(),
  reviewed_at: z.string().datetime({ offset: true }),
  decision_version: z.number().int().positive(),
  source_evidence: jsonObject.default({}),
}).passthrough();

export const matchReviewSchema = z.object({
  id: z.string(),
  classification: z.enum(["verified", "review_required", "rejected"]),
  candidate_score: decimalString.nullable(),
  settlement_confidence: decimalString,
  checks: jsonObject,
  explanation: z.string(),
  engine_version: z.string(),
  decision_version: z.number().int().positive(),
  left_rule_fingerprint: z.string().nullable(),
  right_rule_fingerprint: z.string().nullable(),
  decision_fingerprint: z.string().nullable(),
  source_evidence: jsonObject,
  invalidated_at: z.string().datetime({ offset: true }).nullable(),
  invalidation_reason: z.string().nullable(),
  left: z.object({ market: z.string(), provider: z.string() }),
  right: z.object({ market: z.string(), provider: z.string() }),
  history: z.array(reviewHistorySchema).default([]),
}).strip();

const matchReviewListSchema = z.object({
  items: z.array(matchReviewSchema),
  mode: z.enum(["demo", "live"]),
  message: z.string().optional(),
}).strip();

const matchReviewDecisionSchema = z.object({
  id: z.string(),
  match_id: z.string(),
  decision: z.enum(["verified", "review_required", "rejected"]),
  decision_version: z.number().int().positive(),
  reviewed_at: z.string().datetime({ offset: true }),
}).strip();

export type MatchReview = z.infer<typeof matchReviewSchema>;
export type MatchReviewList = z.infer<typeof matchReviewListSchema>;
export type MatchDecision = "verified" | "review_required" | "rejected";

export class MatchReviewRequestError extends Error {
  constructor(
    message: string,
    readonly code: string,
    readonly status?: number,
  ) {
    super(message);
    this.name = "MatchReviewRequestError";
  }
}

const errorMessages: Record<string, string> = {
  reviewer_permission_required: "This account can inspect evidence but is not allowlisted to record global settlement decisions.",
  review_version_conflict: "This candidate changed after you opened it. Refresh the queue and review the new evidence before deciding.",
  incomplete_review_evidence: "A verified decision requires complete outcome coverage, explicit material rules, and source evidence.",
  persistent_store_not_configured: "The durable research database and migrations are required for the review queue.",
  research_store_unavailable: "The durable research store is temporarily unavailable.",
  authentication_required: "Sign in again before reviewing settlement evidence.",
  mode_mismatch: "The web application and API data modes disagree, so review data is blocked.",
};

function parseError(payload: unknown, status: number): MatchReviewRequestError {
  const parsed = z.object({
    detail: z.union([
      z.string(),
      z.object({ code: z.string().optional(), message: z.string().optional() }).passthrough(),
    ]).optional(),
  }).passthrough().safeParse(payload);
  const detail = parsed.success ? parsed.data.detail : undefined;
  const code = typeof detail === "object" && detail ? detail.code ?? "request_failed" : "request_failed";
  const message = errorMessages[code]
    ?? (typeof detail === "object" && detail?.message ? detail.message : undefined)
    ?? (typeof detail === "string" ? detail : undefined)
    ?? `Settlement review request failed (${status}).`;
  return new MatchReviewRequestError(message, code, status);
}

async function request(path: string, init: RequestInit = {}): Promise<unknown> {
  const response = await fetch(`/api/edgeos/api/v1${path}`, {
    ...init,
    cache: "no-store",
    credentials: "same-origin",
    headers: {
      Accept: "application/json",
      ...(init.body ? { "Content-Type": "application/json" } : {}),
      ...init.headers,
    },
  });
  const mode = response.headers.get("x-edgeos-mode");
  const contract = response.headers.get("x-edgeos-mode-contract");
  if (mode && mode !== appMode) {
    throw new MatchReviewRequestError(errorMessages.mode_mismatch!, "mode_mismatch", 409);
  }
  if (appMode === "live" && (mode !== "live" || contract !== "1")) {
    throw new MatchReviewRequestError("The API did not provide the required live-mode contract.", "mode_handshake_missing", 503);
  }
  let payload: unknown;
  try { payload = await response.json(); } catch { payload = null; }
  if (!response.ok) throw parseError(payload, response.status);
  return payload;
}

export async function getMatchReviews(limit = 100): Promise<MatchReviewList> {
  return matchReviewListSchema.parse(await request(`/match-reviews?limit=${Math.max(1, Math.min(200, Math.trunc(limit)))}`));
}

export async function decideMatchReview(matchId: string, input: {
  decision: MatchDecision;
  rationale: string;
  checks: Record<string, unknown>;
  source_evidence: Record<string, unknown>;
  expected_version: number;
}) {
  return matchReviewDecisionSchema.parse(await request(`/match-reviews/${encodeURIComponent(matchId)}`, {
    method: "PATCH",
    body: JSON.stringify(input),
  }));
}

export function reviewErrorMessage(error: unknown): string {
  return error instanceof Error ? error.message : "Settlement review could not be completed.";
}

