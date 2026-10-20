export const ACCOUNT_EXPORT_PAGE_SIZE = 500;

export const ACCOUNT_EXPORT_TABLES = [
  ["profiles", "id"],
  ["user_settings", "user_id"],
  ["user_provider_connections", "id"],
  ["user_saved_filters", "id"],
  ["user_watchlists", "id"],
  ["user_watchlist_items", "id"],
  ["user_alerts", "id"],
  ["user_alert_events", "id"],
  ["user_saved_opportunities", "opportunity_id"],
  ["user_research_notes", "id"],
  ["paper_portfolios", "id"],
  ["paper_positions", "id"],
  ["paper_position_legs", "id"],
  ["paper_fills", "id"],
  ["paper_settlements", "id"],
  ["paper_ledger_entries", "id"],
  ["performance_snapshots", "id"],
  ["model_runs", "id"],
  ["backtest_runs", "id"],
  ["backtest_signals", "id"],
  ["backtest_results", "id"],
] as const;

type ExportPage = {
  data: readonly unknown[] | null;
  error: { message: string } | null;
};

export type AccountExportClient = {
  from(table: string): {
    select(columns: "*"): {
      order(column: string, options: { ascending: true }): {
        range(from: number, to: number): PromiseLike<ExportPage>;
      };
    };
  };
};

/**
 * Collect every RLS-visible account row without relying on PostgREST's
 * configured maximum response size. Ordering by an immutable key makes page
 * boundaries deterministic for a quiescent account. This is not a database
 * snapshot: callers should avoid editing the account while export is running.
 */
export async function collectAccountExport(
  client: AccountExportClient,
  userId: string,
  pageSize = ACCOUNT_EXPORT_PAGE_SIZE,
): Promise<Record<string, unknown>> {
  if (!Number.isSafeInteger(pageSize) || pageSize < 1 || pageSize > 1_000) {
    throw new RangeError("Account export page size must be between 1 and 1000.");
  }

  const output: Record<string, unknown> = {
    exported_at: new Date().toISOString(),
    user_id: userId,
    consistency: "RLS-scoped paginated read; avoid account changes until export completes",
  };

  for (const [table, orderColumn] of ACCOUNT_EXPORT_TABLES) {
    const rows: unknown[] = [];
    for (let from = 0; ; from += pageSize) {
      const { data, error } = await client
        .from(table)
        .select("*")
        .order(orderColumn, { ascending: true })
        .range(from, from + pageSize - 1);
      if (error) {
        // Do not create a deceptively complete file with embedded table errors.
        throw new Error(`Account export stopped before completion at ${table}. No partial export was downloaded.`);
      }
      const page = data ? [...data] : [];
      rows.push(...page);
      if (page.length < pageSize) break;
    }
    output[table] = rows;
  }

  return output;
}
