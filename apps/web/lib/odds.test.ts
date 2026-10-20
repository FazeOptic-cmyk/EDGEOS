import { describe, expect, it } from "vitest";
import { americanToDecimal, expectedValue, fractionalToDecimal, impliedProbabilityPercent, normalizeOddsInput, removeVig } from "./odds";

describe("transparent odds tools", () => {
  it("converts common odds formats", () => {
    expect(americanToDecimal(-110)).toBeCloseTo(1.9090909, 6);
    expect(americanToDecimal(150)).toBe(2.5);
    expect(fractionalToDecimal("10/11")).toBeCloseTo(1.9090909, 6);
    expect(impliedProbabilityPercent(2)).toBe(50);
  });

  it("normalizes bookmaker margin without inventing a forecast", () => {
    const probabilities = removeVig([1.91, 1.91]);
    expect(probabilities[0]).toBeCloseTo(0.5, 8);
    expect(probabilities[1]).toBeCloseTo(0.5, 8);
  });

  it("uses an explicit probability assumption for EV", () => {
    expect(expectedValue(2, 55, 100)).toBeCloseTo(10, 8);
  });

  it("normalizes supported calculator input formats without changing price kind semantics", () => {
    expect(normalizeOddsInput("decimal", "2.20")).toMatchObject({ priceKind: "decimal_odds", price: "2.2" });
    expect(normalizeOddsInput("american", "+120")).toMatchObject({ priceKind: "decimal_odds", price: "2.2" });
    expect(normalizeOddsInput("fractional", "6/5")).toMatchObject({ priceKind: "decimal_odds", price: "2.2" });
    expect(normalizeOddsInput("contract", "0.45")).toMatchObject({ priceKind: "contract_ask", price: "0.45" });
    expect(normalizeOddsInput("contract", "0.45").decimalEquivalent).toBeCloseTo(2.222222, 6);
    expect(normalizeOddsInput("contract", "1")).toMatchObject({ priceKind: "contract_ask", decimalEquivalent: 1 });
    expect(removeVig([1, 2])).toEqual([2 / 3, 1 / 3]);
  });

  it("rejects impossible or malformed calculator prices", () => {
    expect(() => normalizeOddsInput("decimal", "1")).toThrow(/greater than 1/u);
    expect(() => normalizeOddsInput("american", "+90")).toThrow(/at least/u);
    expect(() => normalizeOddsInput("fractional", "10:11")).toThrow(/fractional odds/u);
    expect(() => normalizeOddsInput("fractional", "0/1")).toThrow(/numerator/u);
    expect(() => normalizeOddsInput("contract", "1.01")).toThrow(/at most 1/u);
  });
});
