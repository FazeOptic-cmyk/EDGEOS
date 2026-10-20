export const LIVE_REFRESH_INTERVAL_MS = 15_000;

export type EdgeosRuntimeMode = "live" | "demo";

/**
 * Shared query policy for records that can change while the UI is open.
 * TanStack Query pauses these timers when the document is hidden and the
 * explicit focus policy requests a fresh server read when the user returns.
 */
export function foregroundLiveRefresh(mode: EdgeosRuntimeMode) {
  const live = mode === "live";
  const refetchInterval: number | false = live ? LIVE_REFRESH_INTERVAL_MS : false;
  const refetchOnWindowFocus: "always" | false = live ? "always" : false;
  return {
    refetchInterval,
    refetchIntervalInBackground: false,
    refetchOnWindowFocus,
  };
}

export function foregroundRefreshDescription(mode: EdgeosRuntimeMode): string {
  return mode === "live"
    ? "Refreshes from the EDGEOS server every 15 seconds while this window is visible. New snapshots require a running server ingestion worker."
    : "Demonstration records are static; automatic live polling is off.";
}
