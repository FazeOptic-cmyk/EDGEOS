import { z } from "zod";
import { assertEdgeosMode } from "./edgeos-proxy";

const decimalString = z.union([z.string(), z.number()]).transform(String);

const dashboardSummarySchema = z.object({
  data_origin: z.string(),
  is_demo: z.boolean(),
  currency: z.string().min(3).max(8).default("USD"),
  simulated_portfolio_value: decimalString,
  realized_simulated_pnl: decimalString,
  unrealized_simulated_pnl: decimalString,
  net_roi: decimalString,
  active_simulated_positions: z.number().int().nonnegative(),
  opportunities_detected: z.number().int().nonnegative(),
  verified_opportunities: z.number().int().nonnegative(),
  historical_capture_rate: z.union([decimalString, z.null()]),
  empty_performance_history: z.boolean(),
}).passthrough();

export type DashboardSummary = z.infer<typeof dashboardSummarySchema>;

export async function getDashboardSummary(): Promise<DashboardSummary> {
  const response = await fetch("/api/edgeos/api/v1/dashboard", {
    cache: "no-store",
    headers: { Accept: "application/json" },
  });
  assertEdgeosMode(response.headers);
  if (!response.ok) throw new Error(`Dashboard API request failed (${response.status}).`);
  return dashboardSummarySchema.parse(await response.json());
}
