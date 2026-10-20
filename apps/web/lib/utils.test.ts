import { describe, expect, it } from "vitest";
import { money } from "./utils";

describe("money formatting", () => {
  it("uses standard currency formatting for ISO codes", () => {
    expect(money(12.5, 2, "USD")).toBe("$12.50");
  });

  it("keeps provider settlement units explicit instead of inventing dollars", () => {
    expect(money(12.5, 2, "pUSD")).toBe("pUSD 12.50");
  });
});
