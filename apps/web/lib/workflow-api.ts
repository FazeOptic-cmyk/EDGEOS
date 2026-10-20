import { z } from "zod";
import { appMode } from "./api";

const API_URL = "/api/edgeos";
const decimalString = z.union([z.string(), z.number()]).transform(String);

export class WorkflowApiError extends Error {
  constructor(
    message: string,
    readonly code: string,
    readonly status?: number,
  ) {
    super(message);
    this.name = "WorkflowApiError";
  }
}

function errorMessage(payload: unknown, fallback: string): { code: string; message: string } {
  const parsed = z.object({
    detail: z.union([
      z.string(),
      z.object({ code: z.string().optional(), message: z.string().optional() }).passthrough(),
    ]).optional(),
  }).passthrough().safeParse(payload);
  if (!parsed.success || !parsed.data.detail) return { code: "request_failed", message: fallback };
  if (typeof parsed.data.detail === "string") return { code: "request_failed", message: parsed.data.detail };
  return {
    code: parsed.data.detail.code ?? "request_failed",
    message: parsed.data.detail.message ?? fallback,
  };
}

async function workflowRequest(
  path: string,
  init: RequestInit = {},
  options: { expect?: "json" | "blob"; requireModeHandshake?: boolean } = {},
): Promise<unknown> {
  const headers = new Headers(init.headers);
  headers.set("Accept", options.expect === "blob" ? "text/csv" : "application/json");
  headers.set("X-EDGEOS-Client-Mode", appMode);
  if (init.body && !headers.has("Content-Type")) headers.set("Content-Type", "application/json");

  const response = await fetch(`${API_URL}${path}`, {
    ...init,
    headers,
    cache: "no-store",
    credentials: "same-origin",
  });
  const serverMode = response.headers.get("x-edgeos-mode");
  const requireHandshake = options.requireModeHandshake ?? appMode === "live";
  if (serverMode && serverMode !== appMode) {
    throw new WorkflowApiError(
      `Configuration error: the browser is in ${appMode} mode but the API is in ${serverMode} mode.`,
      "mode_mismatch",
      503,
    );
  }
  if (requireHandshake && !serverMode) {
    throw new WorkflowApiError(
      "The API did not provide the required runtime-mode handshake.",
      "mode_handshake_missing",
      503,
    );
  }
  if (!response.ok) {
    let payload: unknown;
    try { payload = await response.json(); } catch { payload = null; }
    const error = errorMessage(payload, `EDGEOS request failed (${response.status}).`);
    throw new WorkflowApiError(error.message, error.code, response.status);
  }
  if (response.status === 204) return null;
  return options.expect === "blob" ? response.blob() : response.json();
}

const paperLegSchema = z.object({
  id: z.string(),
  provider: z.string(),
  outcome: z.string(),
  planned_quantity: decimalString,
  filled_quantity: decimalString,
  average_fill_price: decimalString.nullable().optional(),
  status: z.string(),
}).passthrough();

const paperPositionSchema = z.object({
  id: z.string(),
  opportunity_id: z.string().nullable().optional(),
  title: z.string(),
  strategy: z.string(),
  sport: z.string().nullable().optional(),
  status: z.enum(["draft", "open", "partially_filled", "filled", "closed", "settled", "cancelled"]),
  currency: z.string().min(3).max(8).default("USD"),
  planned_allocation: decimalString,
  committed_capital: decimalString,
  realized_pnl: decimalString,
  unrealized_pnl: decimalString,
  worst_case_exposure: decimalString,
  notes: z.string().nullable().optional(),
  created_at: z.string(),
  opened_at: z.string().nullable().optional(),
  settled_at: z.string().nullable().optional(),
  is_demo: z.boolean().default(false),
  legs: z.array(paperLegSchema).default([]),
}).passthrough();

const portfolioSchema = z.object({
  mode: z.enum(["demo", "live"]),
  currency: z.string().min(3).max(8).default("USD"),
  simulation_only: z.literal(true),
  summary: z.object({
    portfolio_value: decimalString,
    cash_balance: decimalString,
    reserved_capital: decimalString,
    realized_pnl: decimalString,
    unrealized_pnl: decimalString,
    net_roi: decimalString,
    active_positions: z.number().int().nonnegative(),
    settled_positions: z.number().int().nonnegative(),
  }),
  positions: z.array(paperPositionSchema),
}).passthrough();

export type PaperPosition = z.infer<typeof paperPositionSchema>;
export type PortfolioResponse = z.infer<typeof portfolioSchema>;
export type PaperPositionInput = {
  opportunity_id: string;
  total_capital: string;
  notes?: string;
  idempotency_key: string;
};

export async function getPortfolio(): Promise<PortfolioResponse> {
  return portfolioSchema.parse(await workflowRequest("/api/v1/portfolio"));
}

export async function createPaperPosition(input: PaperPositionInput): Promise<PaperPosition> {
  return paperPositionSchema.parse(await workflowRequest("/api/v1/portfolio/positions", {
    method: "POST",
    body: JSON.stringify(input),
    headers: { "X-Idempotency-Key": input.idempotency_key },
  }));
}

export async function recordPaperFill(positionId: string, input: {
  leg_id: string; quantity: string; price: string; fee: string; filled_at?: string; idempotency_key: string;
}): Promise<PaperPosition> {
  return paperPositionSchema.parse(await workflowRequest(
    `/api/v1/portfolio/positions/${encodeURIComponent(positionId)}/fills`,
    { method: "POST", body: JSON.stringify(input), headers: { "X-Idempotency-Key": input.idempotency_key } },
  ));
}

export async function settlePaperPosition(positionId: string, input: {
  outcome_id: string; result_source_url?: string; settled_at?: string; idempotency_key: string;
}): Promise<PaperPosition> {
  return paperPositionSchema.parse(await workflowRequest(
    `/api/v1/portfolio/positions/${encodeURIComponent(positionId)}/settle`,
    { method: "POST", body: JSON.stringify(input), headers: { "X-Idempotency-Key": input.idempotency_key } },
  ));
}

export async function cancelPaperPosition(positionId: string, input: { reason: string; idempotency_key: string }): Promise<PaperPosition> {
  return paperPositionSchema.parse(await workflowRequest(
    `/api/v1/portfolio/positions/${encodeURIComponent(positionId)}/cancel`,
    { method: "POST", body: JSON.stringify(input), headers: { "X-Idempotency-Key": input.idempotency_key } },
  ));
}

export async function exportPaperPortfolio(): Promise<Blob> {
  return await workflowRequest("/api/v1/portfolio/export.csv", {}, { expect: "blob" }) as Blob;
}

const watchlistItemSchema = z.object({
  id: z.string(),
  kind: z.enum(["team", "sport", "league", "event", "market", "provider", "strategy"]),
  value: z.string(),
  label: z.string(),
  detail: z.string().nullable().optional(),
  created_at: z.string(),
  is_demo: z.boolean().default(false),
}).passthrough();
const watchlistResponseSchema = z.object({
  mode: z.enum(["demo", "live"]),
  items: z.array(watchlistItemSchema),
}).passthrough();
export type WatchlistItem = z.infer<typeof watchlistItemSchema>;

export async function getWatchlist(): Promise<z.infer<typeof watchlistResponseSchema>> {
  return watchlistResponseSchema.parse(await workflowRequest("/api/v1/watchlists"));
}
export async function createWatchlistItem(input: { kind: WatchlistItem["kind"]; value: string; label: string; idempotency_key: string }): Promise<WatchlistItem> {
  return watchlistItemSchema.parse(await workflowRequest("/api/v1/watchlists", {
    method: "POST", body: JSON.stringify(input), headers: { "X-Idempotency-Key": input.idempotency_key },
  }));
}
export async function deleteWatchlistItem(id: string): Promise<void> {
  await workflowRequest(`/api/v1/watchlists/${encodeURIComponent(id)}`, { method: "DELETE" });
}

const alertRuleSchema = z.object({
  id: z.string(),
  name: z.string(),
  condition: z.enum([
    "new_verified_opportunity", "net_roi_above", "liquidity_increase", "spread_widens",
    "quote_stale", "market_closed", "market_settled", "provider_offline",
  ]),
  threshold: decimalString.nullable(),
  minimum_net_profit: decimalString.nullable().optional(),
  minimum_net_roi: decimalString.nullable().optional(),
  minimum_liquidity: decimalString.nullable().optional(),
  maximum_quote_age_seconds: z.number().int().positive().max(86_400).nullable().optional(),
  enabled: z.boolean(),
  cooldown_seconds: z.number().int().nonnegative(),
  last_triggered_at: z.string().nullable().optional(),
  is_demo: z.boolean().default(false),
}).passthrough();
const alertEventSchema = z.object({
  id: z.string(), title: z.string(), body: z.string(), triggered_at: z.string(), read_at: z.string().nullable(), is_demo: z.boolean().default(false),
}).passthrough();
const alertsResponseSchema = z.object({
  mode: z.enum(["demo", "live"]), items: z.array(alertRuleSchema), events: z.array(alertEventSchema).default([]),
}).passthrough();
export type AlertRule = z.infer<typeof alertRuleSchema>;
export type AlertEvent = z.infer<typeof alertEventSchema>;
export type AlertEligibilityGates = Pick<
  AlertRule,
  "minimum_net_profit" | "minimum_net_roi" | "minimum_liquidity" | "maximum_quote_age_seconds"
>;

export async function getAlerts(): Promise<z.infer<typeof alertsResponseSchema>> {
  return alertsResponseSchema.parse(await workflowRequest("/api/v1/alerts"));
}
export async function createAlert(input: {
  name: string; condition: AlertRule["condition"]; threshold: string | null; enabled: boolean; cooldown_seconds: number; idempotency_key: string;
  minimum_net_profit?: string | null; minimum_net_roi?: string | null; minimum_liquidity?: string | null; maximum_quote_age_seconds?: number | null;
}): Promise<AlertRule> {
  return alertRuleSchema.parse(await workflowRequest("/api/v1/alerts", {
    method: "POST", body: JSON.stringify(input), headers: { "X-Idempotency-Key": input.idempotency_key },
  }));
}
export async function updateAlert(id: string, input: Partial<Pick<AlertRule,
  "name" | "condition" | "threshold" | "minimum_net_profit" | "minimum_net_roi" |
  "minimum_liquidity" | "maximum_quote_age_seconds" | "enabled" | "cooldown_seconds"
>>): Promise<AlertRule> {
  return alertRuleSchema.parse(await workflowRequest(`/api/v1/alerts/${encodeURIComponent(id)}`, { method: "PATCH", body: JSON.stringify(input) }));
}
export async function deleteAlert(id: string): Promise<void> {
  await workflowRequest(`/api/v1/alerts/${encodeURIComponent(id)}`, { method: "DELETE" });
}

const userSettingsSchema = z.object({
  timezone: z.string(),
  currency: z.string(),
  default_allocation: decimalString,
  maximum_quote_age_seconds: z.number().int().positive(),
  demo_mode: z.boolean(),
  verified_matches_only: z.boolean(),
}).passthrough();

export type UserSettings = z.infer<typeof userSettingsSchema>;

export async function getUserSettings(): Promise<UserSettings> {
  return userSettingsSchema.parse(await workflowRequest("/api/v1/settings"));
}

export async function updateUserSettings(input: {
  default_allocation: string;
  verified_matches_only: boolean;
}): Promise<UserSettings> {
  return userSettingsSchema.parse(await workflowRequest("/api/v1/settings", {
    method: "PUT",
    body: JSON.stringify(input),
  }));
}

export const LOCAL_WORKSPACE_BACKUP_MEDIA_TYPE = "application/vnd.edgeos.workspace-backup+json";
export const LOCAL_WORKSPACE_BACKUP_FILENAME = "edgeos-local-workspace-backup.edgeos-backup";
const MAX_LOCAL_WORKSPACE_BACKUP_BYTES = 49 * 1024 * 1024;

const localRestoreResponseSchema = z.object({
  status: z.literal("restored"),
  restored_namespaces: z.array(z.enum(["paper_ledger", "private_user_state", "saved_opportunities"])),
  restart_required: z.boolean(),
});

export async function exportLocalWorkspaceBackup(): Promise<Blob> {
  return await workflowRequest("/api/v1/local-workspace/backup", {}, { expect: "blob" }) as Blob;
}

export async function restoreLocalWorkspaceBackup(file: Blob) {
  if (file.size < 1 || file.size > MAX_LOCAL_WORKSPACE_BACKUP_BYTES) {
    throw new WorkflowApiError("Backup is empty or exceeds the 49 MiB safety limit.", "invalid_backup_size");
  }
  return localRestoreResponseSchema.parse(await workflowRequest(
    "/api/v1/local-workspace/restore",
    {
      method: "POST",
      body: file,
      headers: {
        "Content-Type": LOCAL_WORKSPACE_BACKUP_MEDIA_TYPE,
        "X-EDGEOS-Confirm-Restore": "REPLACE",
      },
    },
  ));
}

const analyticsPointSchema = z.object({ timestamp: z.string(), portfolio_value: decimalString, cumulative_pnl: decimalString });
const analyticsSliceSchema = z.object({ key: z.string(), label: z.string(), net_pnl: decimalString, count: z.number().int().nonnegative() });
const analyticsSchema = z.object({
  mode: z.enum(["demo", "live"]),
  currency: z.string().min(3).max(8).default("USD"),
  simulation_only: z.literal(true),
  insufficient_data: z.boolean(),
  from: z.string(),
  to: z.string(),
  summary: z.object({
    realized_pnl: decimalString,
    unrealized_pnl: decimalString,
    net_roi: decimalString,
    fees: decimalString,
    turnover: decimalString,
    reserved_capital: decimalString,
    maximum_drawdown: decimalString.nullable(),
    captured_opportunities: z.number().int().nonnegative(),
    attempted_opportunities: z.number().int().nonnegative(),
  }),
  equity: z.array(analyticsPointSchema),
  strategy: z.array(analyticsSliceSchema),
  provider: z.array(analyticsSliceSchema),
  sport: z.array(analyticsSliceSchema),
  market: z.array(analyticsSliceSchema),
  quote_age: z.array(z.object({ bucket: z.string(), count: z.number().int().nonnegative() })),
}).passthrough();
export type AnalyticsResponse = z.infer<typeof analyticsSchema>;

export async function getAnalytics(from: string, to: string): Promise<AnalyticsResponse> {
  const query = new URLSearchParams({ from, to });
  return analyticsSchema.parse(await workflowRequest(`/api/v1/analytics?${query}`));
}
