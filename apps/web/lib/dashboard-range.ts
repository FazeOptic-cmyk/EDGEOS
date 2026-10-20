export const DASHBOARD_RANGES = ["Today", "7D", "30D", "90D"] as const;

export type DashboardRange = (typeof DASHBOARD_RANGES)[number];

/** Return an inclusive UTC interval ending at the close of the current UTC day. */
export function dashboardRangeBounds(
  range: DashboardRange,
  currentTime = new Date(),
): { from: string; to: string } {
  const end = new Date(Date.UTC(
    currentTime.getUTCFullYear(),
    currentTime.getUTCMonth(),
    currentTime.getUTCDate(),
    23,
    59,
    59,
    999,
  ));
  const daysBeforeToday = range === "Today" ? 0 : Number.parseInt(range, 10) - 1;
  const start = new Date(Date.UTC(
    currentTime.getUTCFullYear(),
    currentTime.getUTCMonth(),
    currentTime.getUTCDate() - daysBeforeToday,
  ));
  return { from: start.toISOString(), to: end.toISOString() };
}
