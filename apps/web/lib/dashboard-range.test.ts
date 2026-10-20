import { describe, expect, it } from "vitest";
import { dashboardRangeBounds } from "./dashboard-range";

describe("dashboard performance ranges", () => {
  const now = new Date("2026-10-20T12:34:56.000Z");

  it("uses the current UTC calendar day for Today", () => {
    expect(dashboardRangeBounds("Today", now)).toEqual({
      from: "2026-10-20T00:00:00.000Z",
      to: "2026-10-20T23:59:59.999Z",
    });
  });

  it("makes multi-day controls inclusive of today", () => {
    expect(dashboardRangeBounds("7D", now).from).toBe("2026-10-14T00:00:00.000Z");
    expect(dashboardRangeBounds("30D", now).from).toBe("2026-09-21T00:00:00.000Z");
    expect(dashboardRangeBounds("90D", now).from).toBe("2026-07-23T00:00:00.000Z");
  });
});
