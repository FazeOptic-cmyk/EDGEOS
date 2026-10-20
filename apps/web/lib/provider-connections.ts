import { z } from "zod";

export const providerCodeSchema = z.enum([
  "the_odds_api",
  "kalshi",
  "polymarket_international",
  "polymarket_us",
]);

export type ProviderCode = z.infer<typeof providerCodeSchema>;

export const connectionStatusSchema = z.enum([
  "not_configured",
  "ready",
  "connected",
  "disabled",
  "testing",
  "missing_credentials",
  "invalid_credentials",
  "insufficient_plan",
  "rate_limited",
  "provider_unavailable",
  "storage_unavailable",
  "error",
]);

const safeTokenSchema = z.string()
  .trim()
  .min(1)
  .max(80)
  .regex(/^[a-zA-Z0-9][a-zA-Z0-9_.:-]*$/u, "Use provider identifiers, not free-form text.");

export const providerConnectionSchema = z.object({
  provider_code: providerCodeSchema,
  display_name: z.string().min(1).max(120),
  product_scope: z.string().min(1).max(240),
  enabled: z.boolean(),
  credential_configured: z.boolean(),
  credential_mask: z.string().regex(/^(?:••••|\*{4}|…)[a-zA-Z0-9_-]{0,8}$/u).nullable().default(null),
  status: connectionStatusSchema,
  capabilities: z.array(z.string().min(1).max(80)).max(32).default([]),
  coverage: z.array(z.string().min(1).max(120)).max(64).default([]),
  sports: z.array(safeTokenSchema).max(40).default([]),
  regions: z.array(safeTokenSchema).max(20).default([]),
  markets: z.array(safeTokenSchema).max(40).default([]),
  poll_interval_seconds: z.number().int().min(15).max(86_400),
  request_budget_per_hour: z.number().int().min(1).max(100_000),
  estimated_requests_per_hour: z.number().int().min(0).max(100_000).nullable().default(null),
  quota_limit: z.number().int().min(0).nullable().default(null),
  quota_remaining: z.number().int().min(0).nullable().default(null),
  quota_resets_at: z.string().datetime({ offset: true }).nullable().default(null),
  last_tested_at: z.string().datetime({ offset: true }).nullable().default(null),
  last_success_at: z.string().datetime({ offset: true }).nullable().default(null),
  last_market_update_at: z.string().datetime({ offset: true }).nullable().default(null),
  error_code: z.string().max(80).nullable().default(null),
}).strip();

export type ProviderConnection = z.infer<typeof providerConnectionSchema>;

export const providerConnectionListSchema = z.object({
  items: z.array(providerConnectionSchema).max(20),
}).strip();

const identifierListSchema = z.array(safeTokenSchema).max(40);
const credentialValueSchema = z.string().min(8).max(2_048);

export const saveProviderConnectionSchema = z.object({
  provider_code: providerCodeSchema,
  credentials: z.record(z.string(), credentialValueSchema).default({}),
  enabled: z.boolean().default(true),
  sports: identifierListSchema.default([]),
  regions: identifierListSchema.default([]),
  markets: identifierListSchema.default([]),
  poll_interval_seconds: z.number().int().min(15).max(86_400),
  request_budget_per_hour: z.number().int().min(1).max(100_000),
}).superRefine((value, context) => {
  const credentialKeys = Object.keys(value.credentials);
  if (value.provider_code === "the_odds_api") {
    if (credentialKeys.some((key) => key !== "api_key")) {
      context.addIssue({ code: "custom", path: ["credentials"], message: "The Odds API accepts only api_key." });
    }
  } else if (credentialKeys.length) {
    context.addIssue({ code: "custom", path: ["credentials"], message: "This public read-only provider does not require credentials." });
  }
});

export type SaveProviderConnection = z.infer<typeof saveProviderConnectionSchema>;

export const updateProviderConnectionSchema = z.object({
  enabled: z.boolean().optional(),
  sports: identifierListSchema.optional(),
  regions: identifierListSchema.optional(),
  markets: identifierListSchema.optional(),
  poll_interval_seconds: z.number().int().min(15).max(86_400).optional(),
  request_budget_per_hour: z.number().int().min(1).max(100_000).optional(),
}).refine((value) => Object.keys(value).length > 0, { message: "At least one setting is required." });

export const providerConnectionResponseSchema = z.object({ item: providerConnectionSchema }).strip();

export const providerDescriptors: ReadonlyArray<{
  code: ProviderCode;
  name: string;
  product: string;
  description: string;
  credentialLabel: string | null;
  defaultSports: string[];
  defaultRegions: string[];
  defaultMarkets: string[];
  defaultPollSeconds: number;
  defaultBudget: number;
  capabilities: string[];
}> = [
  {
    code: "the_odds_api",
    name: "The Odds API",
    product: "Sportsbook odds aggregator",
    description: "Server-side polling for supported bookmaker odds. This feed normally does not include accepted stake or order-book depth.",
    credentialLabel: "API key",
    defaultSports: ["basketball_nba", "americanfootball_nfl"],
    defaultRegions: ["us"],
    defaultMarkets: ["h2h"],
    defaultPollSeconds: 60,
    defaultBudget: 120,
    capabilities: ["Events", "Bookmaker odds", "Quota telemetry"],
  },
  {
    code: "kalshi",
    name: "Kalshi",
    product: "Public Trade API v2 market data",
    description: "Public read-only REST discovery and order-book research. Private trading credentials are neither requested nor used.",
    credentialLabel: null,
    defaultSports: [],
    defaultRegions: ["us"],
    defaultMarkets: [],
    defaultPollSeconds: 30,
    defaultBudget: 240,
    capabilities: ["Events", "Markets", "Order books"],
  },
  {
    code: "polymarket_international",
    name: "Polymarket International",
    product: "Gamma discovery and CLOB public data",
    description: "Informational international market data with its own identifiers. Access does not imply eligibility to trade.",
    credentialLabel: null,
    defaultSports: [],
    defaultRegions: ["international"],
    defaultMarkets: [],
    defaultPollSeconds: 30,
    defaultBudget: 240,
    capabilities: ["Market discovery", "CLOB order books"],
  },
  {
    code: "polymarket_us",
    name: "Polymarket US",
    product: "US Gateway public data",
    description: "Separate US product and ID namespace. It is never joined to international Polymarket identifiers.",
    credentialLabel: null,
    defaultSports: [],
    defaultRegions: ["us"],
    defaultMarkets: [],
    defaultPollSeconds: 30,
    defaultBudget: 240,
    capabilities: ["Events", "Markets", "BBO and books"],
  },
] as const;

export const connectionErrorMessages: Record<string, string> = {
  authentication_required: "Sign in again to manage provider connections.",
  forbidden: "This provider connection does not belong to the signed-in account.",
  invalid_request: "Review the provider settings and correct the highlighted values.",
  live_mode_required: "API connections are available only in live mode. Demo mode never consumes saved credentials.",
  mode_mismatch: "The web application and EDGEOS API modes disagree. Align both to live before saving credentials.",
  credential_storage_unavailable: "Encrypted credential storage is not configured. Set the server-side database and credential master key first.",
  invalid_credentials: "The provider rejected the credential. Replace it and test again.",
  insufficient_plan: "The credential is valid, but the selected endpoint or coverage is not included in its plan.",
  rate_limited: "The provider request budget or upstream rate limit has been reached.",
  provider_unavailable: "The provider is unavailable or did not respond within the safe timeout.",
  api_unavailable: "The internal EDGEOS API is unavailable.",
  invalid_upstream_response: "EDGEOS received an unexpected provider-connection response and rejected it.",
};

export function connectionErrorMessage(code: string | null | undefined): string {
  return connectionErrorMessages[code ?? ""] ?? "The provider connection request could not be completed.";
}
