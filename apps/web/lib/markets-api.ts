import { z } from "zod";
import { assertEdgeosMode } from "./edgeos-proxy";

const providerCodeSchema = z.enum([
  "kalshi",
  "polymarket_international",
  "polymarket_us",
  "the_odds_api",
  "demo",
]);
const browserMode = process.env.NEXT_PUBLIC_APP_MODE === "live" ? "live" : "demo";
const originSchema = z.enum(["live_poll", "cache", "historical", "demo"]);
const nullableDecimal = z.union([z.string(), z.number()]).transform(String).nullable();

const eventSchema = z.object({
  provider: providerCodeSchema,
  provider_event_id: z.string(),
  title: z.string(),
  subtitle: z.string().nullable(),
  sport: z.string().nullable(),
  league: z.string().nullable(),
  home_team: z.string().nullable(),
  away_team: z.string().nullable(),
  scheduled_start: z.string().nullable(),
  status: z.string().nullable(),
  resolution_source: z.string().nullable(),
  source_url: z.string(),
  provider_timestamp: z.string().nullable(),
  retrieved_at: z.string(),
  data_origin: originSchema,
  metadata: z.record(z.string(), z.unknown()).default({}),
});

const outcomeSchema = z.object({
  provider_outcome_id: z.string().nullable(),
  name: z.string().nullable(),
  description: z.string().nullable(),
  side: z.string().nullable(),
  line: nullableDecimal,
  offered_price: nullableDecimal,
  best_bid: nullableDecimal,
  best_ask: nullableDecimal,
  bid_size: nullableDecimal,
  ask_size: nullableDecimal,
  last_price: nullableDecimal,
  price_type: z.enum(["contract_dollars", "decimal_odds"]),
  currency: z.string().nullable(),
  maximum_stake: nullableDecimal.optional().default(null),
  stake_currency: z.string().nullable().optional().default(null),
});

const settlementSchema = z.object({
  primary_rules: z.string().nullable(),
  secondary_rules: z.string().nullable(),
  resolution_source: z.string().nullable(),
  early_close_condition: z.string().nullable(),
  can_close_early: z.boolean().nullable(),
  expiration_value: z.string().nullable(),
  void_treatment: z.string().nullable(),
  push_treatment: z.string().nullable(),
});

const marketSchema = z.object({
  provider: providerCodeSchema,
  provider_market_id: z.string(),
  provider_market_slug: z.string().nullable(),
  provider_event_id: z.string().nullable(),
  title: z.string(),
  market_type: z.string().nullable(),
  period: z.string().nullable(),
  status: z.string().nullable(),
  event_start_time: z.string().nullable(),
  close_time: z.string().nullable(),
  outcomes: z.array(outcomeSchema),
  settlement: settlementSchema.nullable(),
  liquidity: nullableDecimal,
  quote_timestamp: z.string().nullable(),
  retrieved_at: z.string(),
  data_origin: originSchema,
  source_url: z.string(),
  metadata: z.record(z.string(), z.unknown()).default({}),
});

function pageSchema<T extends z.ZodType>(item: T) {
  return z.object({
    items: z.array(item),
    next_cursor: z.string().nullable(),
    retrieved_at: z.string(),
    data_origin: originSchema,
    is_demo: z.boolean(),
  });
}

const eventPageSchema = pageSchema(eventSchema);
const marketPageSchema = pageSchema(marketSchema);

export type ProviderEvent = z.infer<typeof eventSchema>;
export type ProviderMarket = z.infer<typeof marketSchema>;
export type ProviderEventPage = z.infer<typeof eventPageSchema>;
export type ProviderMarketPage = z.infer<typeof marketPageSchema>;

export class MarketBrowserError extends Error {
  constructor(message: string, readonly status?: number) {
    super(message);
    this.name = "MarketBrowserError";
  }
}

function safeDetail(payload: unknown): string | null {
  const parsed = z.object({ detail: z.union([z.string(), z.object({ message: z.string().optional() }).passthrough()]).optional() })
    .passthrough().safeParse(payload);
  if (!parsed.success || !parsed.data.detail) return null;
  return typeof parsed.data.detail === "string" ? parsed.data.detail : parsed.data.detail.message ?? null;
}

async function providerPage(
  provider: string,
  resource: "events" | "markets",
  options: { cursor?: string | null; sport?: string; limit?: number } = {},
): Promise<unknown> {
  const code = providerCodeSchema.safeParse(provider);
  if (!code.success) throw new MarketBrowserError("This provider is not available through the normalized EDGEOS adapter.");
  if (browserMode === "demo" && code.data !== "demo") {
    throw new MarketBrowserError(
      "External provider browsing is disabled in demo mode. Only labeled demonstration records are available.",
      403,
    );
  }
  const query = new URLSearchParams({ limit: String(options.limit ?? 50) });
  if (options.cursor) query.set("cursor", options.cursor);
  if (options.sport?.trim()) query.set("sport", options.sport.trim());
  const response = await fetch(
    `/api/edgeos/api/v1/providers/${encodeURIComponent(code.data)}/${resource}?${query}`,
    { cache: "no-store", headers: { Accept: "application/json" } },
  );
  assertEdgeosMode(response.headers);
  if (!response.ok) {
    let message: string | null = null;
    try { message = safeDetail(await response.json()); } catch { /* The status remains actionable. */ }
    throw new MarketBrowserError(message ?? `Provider request failed (${response.status}).`, response.status);
  }
  return response.json();
}

export async function getProviderEvents(provider: string, options?: { cursor?: string | null; sport?: string; limit?: number }): Promise<ProviderEventPage> {
  return eventPageSchema.parse(await providerPage(provider, "events", options));
}

export async function getProviderMarkets(provider: string, options?: { cursor?: string | null; sport?: string; limit?: number }): Promise<ProviderMarketPage> {
  return marketPageSchema.parse(await providerPage(provider, "markets", options));
}

