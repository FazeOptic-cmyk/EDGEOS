import { describe, expect, it } from "vitest";

import { safeRedirectPath } from "./safe-redirect";

describe("safeRedirectPath", () => {
  it.each([
    ["/", "/"],
    ["/scanner", "/scanner"],
    ["/scanner?q=nfl#details", "/scanner?q=nfl#details"],
  ])("keeps internal route %s", (input, expected) => {
    expect(safeRedirectPath(input)).toBe(expected);
  });

  it.each([
    null,
    "",
    "javascript:alert(1)",
    "https://evil.example/",
    "//evil.example/",
    "///evil.example/",
    "/\\evil.example/",
    "/%5cevil.example/",
    "/%2fevil.example/",
    "/scanner\nignored",
  ])("rejects unsafe redirect value %s", (input) => {
    expect(safeRedirectPath(input)).toBe("/");
  });

  it("uses the requested safe fallback", () => {
    expect(safeRedirectPath("//evil.example", "/login")).toBe("/login");
  });
});
