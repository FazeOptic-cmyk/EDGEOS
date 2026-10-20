import { describe, expect, it } from "vitest";
import { calculateTwoWay } from "./arbitrage";

describe("calculateTwoWay", () => {
  it("validates the 2.20 / 2.00 / $210 reference case", () => {
    const result = calculateTwoWay({ capital: 210, oddsA: 2.2, oddsB: 2 });
    expect(result.allocationA).toBeCloseTo(100, 8);
    expect(result.allocationB).toBeCloseTo(110, 8);
    expect(result.grossPayout).toBeCloseTo(220, 8);
    expect(result.grossProfit).toBeCloseTo(10, 8);
    expect(result.netRoiPercent).toBeCloseTo(4.76190476, 7);
  });
  it("shows fees and slippage can erase apparent arbitrage", () => {
    const result = calculateTwoWay({ capital: 210, oddsA: 2.2, oddsB: 2, feesPercent: 3, slippagePercent: 2 });
    expect(result.grossProfit).toBeGreaterThan(0);
    expect(result.netProfit).toBeLessThan(0);
  });
  it("caps allocation at known size", () => expect(calculateTwoWay({ capital: 1000, oddsA: 2.2, oddsB: 2, maximumSize: 210 }).usableCapital).toBe(210));
});
