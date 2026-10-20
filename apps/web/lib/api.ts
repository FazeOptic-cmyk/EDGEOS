import { z } from "zod";
import type { Opportunity, ProviderHealth } from "./types";
import { assertEdgeosMode } from "./edgeos-proxy";

export const appMode = process.env.NEXT_PUBLIC_APP_MODE === "live" ? "live" : "demo";

async function apiFetch(path: string, init?: RequestInit): Promise<unknown> {
  const response = await fetch(`/api/edgeos${path}`, { ...init, headers: { Accept: "application/json", ...init?.headers } });
  assertEdgeosMode(response.headers);
  if (!response.ok) throw new Error(`API request failed (${response.status})`);
  return response.status === 204 ? null : response.json();
}

const statusSchema = z.enum(["connected", "delayed", "disconnected", "requires_key", "requires_api_key", "not_supported", "demo_only", "unknown"]);
const rateLimitSchema = z.object({
  used: z.number().nullable().optional(), limit: z.number().nullable().optional(), remaining: z.number().nullable().optional(),
  reset_at: z.string().nullable().optional(), retry_after_seconds: z.number().nullable().optional(),
}).passthrough();
const flatProviderSchema = z.object({
  id: z.string(), name: z.string(), status: statusSchema,
  feed_type: z.string().optional(), last_successful_update: z.string().nullable().optional(), checked_at: z.string().nullable().optional(), last_error: z.string().nullable().optional(),
  known_refresh_interval_seconds: z.number().nullable().optional(), refresh_interval_seconds: z.number().nullable().optional(),
  active_instruments: z.number().nullable().optional(), market_coverage: z.union([z.string(), z.array(z.string())]).optional(),
  measured_data_delay_seconds: z.number().nullable().optional(), api_quota_usage: rateLimitSchema.nullable().optional(),
  quota_used: z.number().nullable().optional(), quota_limit: z.number().nullable().optional(), quota_remaining: z.number().nullable().optional(),
  rate_limit_status: z.string().nullable().optional(), capabilities: z.array(z.string()).optional(), message: z.string().nullable().optional(),
}).passthrough();
const nestedProviderSchema = z.object({
  descriptor: z.object({ code: z.string(), display_name: z.string(), product_scope: z.string().optional(), feed_types: z.array(z.string()).optional(), capabilities: z.array(z.string()).optional() }).passthrough(),
  health: z.object({ state: statusSchema, checked_at: z.string().nullable().optional(), last_success_at: z.string().nullable().optional(), last_error: z.string().nullable().optional(), known_refresh_interval_seconds: z.number().nullable().optional(), measured_data_delay_seconds: z.number().nullable().optional(), rate_limit: rateLimitSchema.nullable().optional(), message: z.string().nullable().optional() }).passthrough(),
});

function normalizeProvider(value: z.infer<typeof flatProviderSchema>): ProviderHealth {
  const quota = value.api_quota_usage;
  return { id: value.id, name: value.name, status: value.status, feed_type: value.feed_type, last_successful_update: value.last_successful_update, checked_at: value.checked_at,
    last_error: value.last_error, refresh_interval_seconds: value.known_refresh_interval_seconds ?? value.refresh_interval_seconds,
    measured_data_delay_seconds: value.measured_data_delay_seconds, active_instruments: value.active_instruments,
    quota_used: quota?.used ?? value.quota_used, quota_limit: quota?.limit ?? value.quota_limit, quota_remaining: quota?.remaining ?? value.quota_remaining,
    quota_reset_at: quota?.reset_at, retry_after_seconds: quota?.retry_after_seconds, rate_limit_status: value.rate_limit_status,
    market_coverage: typeof value.market_coverage === "string" ? [value.market_coverage] : value.market_coverage,
    capabilities: value.capabilities, message: value.message };
}

export async function getProviderHealth(): Promise<{ data: ProviderHealth[]; source: "api" | "demo"; error?: string }> {
  const raw = z.object({ providers: z.array(flatProviderSchema).optional(), items: z.array(nestedProviderSchema).optional() }).parse(await apiFetch("/api/v1/providers/health"));
  if (raw.providers) return { data: raw.providers.map(normalizeProvider), source: "api" };
  const data = (raw.items ?? []).map(({ descriptor, health }) => ({
    id: descriptor.code, name: descriptor.display_name, status: health.state, feed_type: descriptor.feed_types?.join(", "),
    checked_at: health.checked_at, last_successful_update: health.last_success_at, last_error: health.last_error, refresh_interval_seconds: health.known_refresh_interval_seconds,
    measured_data_delay_seconds: health.measured_data_delay_seconds, quota_used: health.rate_limit?.used, quota_limit: health.rate_limit?.limit,
    quota_remaining: health.rate_limit?.remaining, quota_reset_at: health.rate_limit?.reset_at, retry_after_seconds: health.rate_limit?.retry_after_seconds,
    active_instruments: null, market_coverage: descriptor.product_scope ? [descriptor.product_scope] : [], capabilities: descriptor.capabilities,
    message: health.message,
  } satisfies ProviderHealth));
  return { data, source: "api" };
}

const opportunityItemSchema = z.object({
  id: z.string(), event: z.string(), sport: z.string(), league: z.string(), market: z.string(), platform_a: z.string(), platform_b: z.string(),
  market_type: z.string().optional(),
  prices: z.array(z.union([z.string(), z.number()])), gross_roi: z.union([z.string(), z.number()]), net_roi: z.union([z.string(), z.number()]),
  total_allocation: z.union([z.string(), z.number()]).optional(),
  potential_net_profit: z.union([z.string(), z.number()]), available_liquidity: z.union([z.string(), z.number()]).nullable(), observed_at: z.string(),
  event_start_time: z.string().nullable(), phase: z.enum(["prematch", "live"]), live: z.boolean(), provider_timestamp: z.string().nullable().optional(),
  quote_age_seconds: z.union([z.string(), z.number()]).nullable(), data_origin: z.string(), settlement_status: z.string(), research_score: z.union([z.string(), z.number()]),
  currency: z.string().min(3).max(8).optional(),
  source_url: z.string().optional(), not_executable_reason: z.string().optional(),
  status: z.enum(["active", "stale", "invalidated", "expired", "settled"]).nullable().optional(),
  lifecycle_status: z.enum(["active", "stale", "invalidated", "expired", "settled"]).nullable().optional(),
  lifecycleStatus: z.enum(["active", "stale", "invalidated", "expired", "settled"]).nullable().optional(),
  expires_at: z.string().nullable().optional(),
  withdrawn_at: z.string().nullable().optional(), withdrawnAt: z.string().nullable().optional(),
}).passthrough();
const numeric = (value: string | number) => Number(value);
const simulationSchema = z.object({
  requested_allocation: z.union([z.string(), z.number()]),
  deployed_capital: z.union([z.string(), z.number()]),
  total_fees: z.union([z.string(), z.number()]),
  net_profit_worst_case: z.union([z.string(), z.number()]),
  net_profit_best_case: z.union([z.string(), z.number()]),
  currency: z.string().min(3).max(8).optional(),
  legs: z.array(z.object({
    provider: z.string(), outcome_id: z.string(), requested_quantity: z.union([z.string(), z.number()]),
    filled_quantity: z.union([z.string(), z.number()]), cash_cost: z.union([z.string(), z.number()]),
    fees: z.union([z.string(), z.number()]), price_kind: z.enum(["decimal_odds", "contract_ask"]),
    unit_cost: z.union([z.string(), z.number()]).optional(),
  }).passthrough()),
  outcomes: z.array(z.object({ outcome_id: z.string(), gross_payout: z.union([z.string(), z.number()]), net_profit: z.union([z.string(), z.number()]) })),
}).passthrough();
const opportunityDetailSchema = opportunityItemSchema.extend({
  simulation: simulationSchema.optional(),
  settlement_audit: z.object({ reasons: z.array(z.string()).default([]), scope: z.string().optional() }).passthrough().optional(),
  failure_reasons: z.array(z.string()).optional(),
  price_history: z.array(z.object({
    observed_at: z.string(),
    net_roi: z.union([z.string(), z.number()]).optional(),
  }).passthrough()).optional(),
}).passthrough();

export interface OpportunityQuery {
  search?: string;
  sport?: string;
  league?: string;
  provider?: string;
  marketType?: string;
  minimumNetRoiPercent?: number;
  minimumTheoreticalProfit?: number;
  minimumLiquidity?: number;
  maximumQuoteAge?: number;
  verifiedOnly?: boolean;
  phase?: "prematch" | "live" | "all";
  sort?: "research_score" | "theoretical_roi" | "net_roi" | "theoretical_profit" | "liquidity" | "execution_risk" | "match_confidence" | "quote_time";
  descending?: boolean;
  page?: number;
  limit?: number;
}

export interface OpportunityPage {
  data: Opportunity[];
  source: "api" | "demo";
  page: number;
  limit: number;
  total: number;
  hasMore: boolean;
  error?: string;
}

export function normalizeOpportunity(item: z.infer<typeof opportunityItemSchema>): Opportunity {
  const prices = item.prices.map(numeric);
  const dataMode: Opportunity["dataMode"] = item.data_origin === "demo"
    ? "demo"
    : item.data_origin === "cache"
      ? "cached"
      : item.data_origin === "historical"
        ? "historical"
        : item.data_origin === "live_poll" || item.data_origin === "streaming"
          ? "live"
          : "delayed";
  return {
    id: item.id, event: item.event, sport: item.sport, league: item.league, market: item.market, marketType: item.market_type ?? item.market, startTime: item.event_start_time, live: item.live, currency: item.currency ?? "USD",
    legs: [item.platform_a, item.platform_b].map((platform, index) => ({ platform, outcome: `Outcome ${index + 1}`, price: prices[index] ?? 0, decimalOdds: prices[index] ?? 0, size: item.available_liquidity === null ? null : numeric(item.available_liquidity), fee: null, feedType: "snapshot" as const, timestamp: item.provider_timestamp ?? null })),
    grossRoi: numeric(item.gross_roi) * 100, netRoi: numeric(item.net_roi) * 100, potentialProfit: numeric(item.potential_net_profit), liquidity: item.available_liquidity === null ? null : numeric(item.available_liquidity), quoteAgeSeconds: item.quote_age_seconds === null ? null : numeric(item.quote_age_seconds),
    lifecycle: item.status ?? item.lifecycle_status ?? item.lifecycleStatus ?? null,
    expiresAt: item.expires_at ?? null, withdrawnAt: item.withdrawn_at ?? item.withdrawnAt ?? null, observedAt: item.observed_at,
    matchStatus: item.settlement_status === "verified" ? "VERIFIED MATCH" : item.settlement_status === "rejected" ? "REJECTED" : "REVIEW REQUIRED",
    score: numeric(item.research_score), dataMode, totalAllocation: item.total_allocation === undefined ? (item.available_liquidity === null ? 0 : numeric(item.available_liquidity)) : numeric(item.total_allocation), totalAllocationExact: item.total_allocation === undefined ? undefined : String(item.total_allocation), totalFees: null, worstCase: numeric(item.potential_net_profit), bestCase: numeric(item.potential_net_profit),
    settlementSummary: item.not_executable_reason ?? "Settlement details were not included in this list response.", matchReasons: [], riskReasons: item.not_executable_reason ? [item.not_executable_reason] : [], allocations: [], payouts: [],
    sources: item.source_url?.startsWith("http") ? [{ label: "Provider source", url: item.source_url }] : [], history: [],
  };
}

export function normalizeOpportunityDetail(item: z.infer<typeof opportunityDetailSchema>): Opportunity {
  const opportunity = normalizeOpportunity(item);
  const simulation = item.simulation;
  if (!simulation) return opportunity;
  return {
    ...opportunity,
    currency: simulation.currency ?? opportunity.currency,
    totalAllocation: numeric(simulation.requested_allocation),
    totalAllocationExact: String(simulation.requested_allocation),
    totalFees: numeric(simulation.total_fees),
    worstCase: numeric(simulation.net_profit_worst_case),
    bestCase: numeric(simulation.net_profit_best_case),
    allocations: simulation.legs.map((leg, index) => {
      const cashCost = numeric(leg.cash_cost);
      const fee = numeric(leg.fees);
      return {
        platform: leg.provider,
        outcome: leg.outcome_id,
        stake: cashCost + fee,
        cashCost,
        fee,
        quantity: numeric(leg.filled_quantity),
        priceKind: leg.price_kind,
        // Provider name is not a stable join key: complementary contracts may
        // put multiple differently priced legs on the same platform. Preserve
        // the authoritative simulation/list order instead of reusing leg one.
        modeledPrice: leg.price_kind === "contract_ask" && leg.unit_cost !== undefined
          ? numeric(leg.unit_cost)
          : opportunity.legs[index]?.price,
      };
    }),
    payouts: simulation.outcomes.map((outcome) => ({ outcome: outcome.outcome_id, gross: numeric(outcome.gross_payout), net: numeric(outcome.net_profit) })),
    matchReasons: item.settlement_audit?.reasons ?? [],
    riskReasons: item.failure_reasons ?? opportunity.riskReasons,
    history: (item.price_history ?? []).flatMap((point) => point.net_roi === undefined
      ? []
      : [{ time: point.observed_at, roi: numeric(point.net_roi) * 100 }]),
  };
}

export async function getOpportunities(filters: OpportunityQuery = {}): Promise<OpportunityPage> {
  const query = new URLSearchParams();
  const add = (key: string, value: string | number | boolean | undefined) => {
    if (value !== undefined && value !== "") query.set(key, String(value));
  };
  add("search", filters.search?.trim());
  add("sport", filters.sport);
  add("league", filters.league);
  add("provider", filters.provider);
  add("market_type", filters.marketType);
  add("minimum_net_roi", filters.minimumNetRoiPercent === undefined ? undefined : filters.minimumNetRoiPercent / 100);
  add("minimum_theoretical_profit", filters.minimumTheoreticalProfit);
  add("minimum_liquidity", filters.minimumLiquidity);
  add("maximum_quote_age", filters.maximumQuoteAge);
  add("verified_only", filters.verifiedOnly ?? false);
  add("phase", filters.phase ?? "all");
  add("sort", filters.sort ?? "research_score");
  add("descending", filters.descending ?? true);
  add("page", filters.page ?? 1);
  add("limit", filters.limit ?? 50);
  const raw = z.object({
    items: z.array(opportunityItemSchema),
    page: z.number().int().positive(),
    limit: z.number().int().positive(),
    total: z.number().int().nonnegative(),
    has_more: z.boolean(),
  }).parse(await apiFetch(`/api/v1/opportunities?${query}`));
  return { data: raw.items.map(normalizeOpportunity), source: "api", page: raw.page, limit: raw.limit, total: raw.total, hasMore: raw.has_more };
}

export async function getOpportunityDetail(id: string): Promise<Opportunity> {
  const raw = opportunityDetailSchema.parse(await apiFetch(`/api/v1/opportunities/${encodeURIComponent(id)}`));
  return normalizeOpportunityDetail(raw);
}

const savedOpportunitySchema = z.object({
  opportunity_id: z.string().min(1),
  favorited: z.boolean(),
  saved_at: z.string().datetime({ offset: true }),
}).strip();

export type SavedOpportunity = z.infer<typeof savedOpportunitySchema>;

export async function getSavedOpportunities(): Promise<SavedOpportunity[]> {
  const raw = z.object({ items: z.array(savedOpportunitySchema) }).parse(
    await apiFetch("/api/v1/saved-opportunities"),
  );
  return raw.items;
}

export async function putSavedOpportunity(id: string, favorited: boolean): Promise<SavedOpportunity> {
  return savedOpportunitySchema.parse(await apiFetch(
    `/api/v1/saved-opportunities/${encodeURIComponent(id)}`,
    { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ favorited }) },
  ));
}

export async function deleteSavedOpportunity(id: string): Promise<void> {
  await apiFetch(`/api/v1/saved-opportunities/${encodeURIComponent(id)}`, { method: "DELETE" });
}
