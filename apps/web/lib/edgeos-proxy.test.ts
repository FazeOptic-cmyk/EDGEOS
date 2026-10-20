import { describe, expect, it } from "vitest";
import {
  assertEdgeosMode,
  buildEdgeosProxyUrl,
  configuredDesktopApiToken,
  EdgeosProxyConfigurationError,
  EdgeosModeMismatchError,
  hasExpectedDesktopHost,
  isAllowedEdgeosProxyRequest,
  isSameOriginBrowserRequest,
} from "./edgeos-proxy";

describe("EDGEOS same-origin API proxy", () => {
  it("constructs only normalized API v1 upstream URLs", () => {
    const result = buildEdgeosProxyUrl(
      ["api", "v1", "opportunities", "demo id"],
      "?verified_only=false",
      "http://127.0.0.1:8765",
    );
    expect(result.href).toBe(
      "http://127.0.0.1:8765/api/v1/opportunities/demo%20id?verified_only=false",
    );
  });

  it.each([
    ["api", "v2", "health"],
    ["api", "v1", "..", "health"],
    ["api", "v1", "markets/health"],
    ["internal", "admin", "secrets"],
  ])("rejects a path outside the API v1 namespace: %j", (...path) => {
    expect(() => buildEdgeosProxyUrl(path, "", "http://127.0.0.1:8765")).toThrow(TypeError);
  });

  it("fails closed when the internal origin is absent or malformed", () => {
    expect(() => buildEdgeosProxyUrl(["api", "v1", "health"], "", undefined))
      .toThrow(EdgeosProxyConfigurationError);
    expect(() => buildEdgeosProxyUrl(["api", "v1", "health"], "", "file:///tmp/api"))
      .toThrow(EdgeosProxyConfigurationError);
    expect(() => buildEdgeosProxyUrl(["api", "v1", "health"], "", "https://user:secret@example.com"))
      .toThrow(EdgeosProxyConfigurationError);
  });

  it("rejects browser requests originating outside the local web origin", () => {
    expect(isSameOriginBrowserRequest(new Request("http://127.0.0.1:3210/api/edgeos/api/v1/health", {
      headers: { Origin: "https://attacker.example", "Sec-Fetch-Site": "cross-site" },
    }))).toBe(false);
    expect(isSameOriginBrowserRequest(new Request("http://127.0.0.1:3210/api/edgeos/api/v1/health", {
      headers: { Origin: "http://127.0.0.1:3210", "Sec-Fetch-Site": "same-origin" },
    }))).toBe(true);
  });

  it("requires the exact loopback Host in desktop mode", () => {
    const expected = "http://127.0.0.1:3210";
    expect(hasExpectedDesktopHost(new Request(`${expected}/api/edgeos/api/v1/health`, {
      headers: { Host: "127.0.0.1:3210" },
    }), true, expected)).toBe(true);
    expect(hasExpectedDesktopHost(new Request("http://attacker.example/api/edgeos/api/v1/health", {
      headers: { Host: "attacker.example" },
    }), true, expected)).toBe(false);
    expect(hasExpectedDesktopHost(new Request(`${expected}/api/edgeos/api/v1/health`, {
      headers: { Host: "localhost:3210" },
    }), true, expected)).toBe(false);
    expect(hasExpectedDesktopHost(new Request(`${expected}/api/edgeos/api/v1/health`), false, undefined)).toBe(true);
  });

  it("restores NextURL loopback normalization without equating distinct browser origins", () => {
    const request = (origin: string, host = "127.0.0.1:3210") => new Request(
      "http://localhost:3210/api/edgeos/api/v1/portfolio/positions",
      { headers: { Host: host, Origin: origin, "Sec-Fetch-Site": "same-origin" } },
    );
    expect(isSameOriginBrowserRequest(request("http://127.0.0.1:3210"))).toBe(true);
    expect(isSameOriginBrowserRequest(request("http://localhost:3210"))).toBe(false);
    expect(isSameOriginBrowserRequest(request("http://127.0.0.1:3211"))).toBe(false);
    expect(isSameOriginBrowserRequest(request("https://attacker.example", "attacker.example"))).toBe(false);
  });

  it("requires a versioned matching backend mode", () => {
    const matching = new Headers({ "x-edgeos-mode": "live", "x-edgeos-mode-contract": "1" });
    expect(assertEdgeosMode(matching, "live")).toBe("live");

    const mismatched = new Headers({ "x-edgeos-mode": "demo", "x-edgeos-mode-contract": "1" });
    expect(() => assertEdgeosMode(mismatched, "live")).toThrow(EdgeosModeMismatchError);
    expect(() => assertEdgeosMode(new Headers(), "demo")).toThrow(/mode contract/i);
  });

  it("keeps the per-launch desktop API token server-side and fails closed", () => {
    const token = "t".repeat(43);
    expect(configuredDesktopApiToken(true, token)).toBe(token);
    expect(configuredDesktopApiToken(false, undefined)).toBeNull();
    expect(() => configuredDesktopApiToken(true, undefined))
      .toThrow(EdgeosProxyConfigurationError);
    expect(() => configuredDesktopApiToken(true, "too-short"))
      .toThrow(EdgeosProxyConfigurationError);
  });

  it("exposes only explicit method and route pairs", () => {
    expect(isAllowedEdgeosProxyRequest("GET", ["api", "v1", "opportunities"])).toBe(true);
    expect(isAllowedEdgeosProxyRequest("POST", ["api", "v1", "portfolio", "positions", "p1", "fills"])).toBe(true);
    expect(isAllowedEdgeosProxyRequest("DELETE", ["api", "v1", "alerts", "a1"])).toBe(true);
    expect(isAllowedEdgeosProxyRequest("GET", ["api", "v1", "saved-opportunities"])).toBe(true);
    expect(isAllowedEdgeosProxyRequest("PUT", ["api", "v1", "saved-opportunities", "o1"])).toBe(true);
    expect(isAllowedEdgeosProxyRequest("DELETE", ["api", "v1", "saved-opportunities", "o1"])).toBe(true);
    expect(isAllowedEdgeosProxyRequest("GET", ["api", "v1", "local-workspace", "backup"])).toBe(true);
    expect(isAllowedEdgeosProxyRequest("POST", ["api", "v1", "local-workspace", "restore"])).toBe(true);
    expect(isAllowedEdgeosProxyRequest("PUT", ["api", "v1", "local-workspace", "restore"])).toBe(false);
    expect(isAllowedEdgeosProxyRequest("POST", ["api", "v1", "providers", "kalshi", "markets"])).toBe(false);
    expect(isAllowedEdgeosProxyRequest("GET", ["api", "v1", "internal", "secrets"])).toBe(false);
    expect(isAllowedEdgeosProxyRequest("TRACE", ["api", "v1", "health"])).toBe(false);
  });
});
