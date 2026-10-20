import { describe, expect, it } from "vitest";
import { foregroundLiveRefresh, foregroundRefreshDescription, LIVE_REFRESH_INTERVAL_MS } from "./live-refresh";

describe("foreground live refresh policy", () => {
  it("polls every 15 seconds only in a visible live workspace and refreshes on return", () => {
    expect(foregroundLiveRefresh("live")).toEqual({
      refetchInterval: LIVE_REFRESH_INTERVAL_MS,
      refetchIntervalInBackground: false,
      refetchOnWindowFocus: "always",
    });
    expect(foregroundRefreshDescription("live")).toMatch(/running server ingestion worker/i);
  });

  it("does not continuously poll demonstration records", () => {
    expect(foregroundLiveRefresh("demo")).toEqual({
      refetchInterval: false,
      refetchIntervalInBackground: false,
      refetchOnWindowFocus: false,
    });
    expect(foregroundRefreshDescription("demo")).toMatch(/automatic live polling is off/i);
  });
});
