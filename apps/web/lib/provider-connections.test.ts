import { describe, expect, it } from "vitest";
import {
  connectionErrorMessage,
  providerConnectionListSchema,
  saveProviderConnectionSchema,
} from "./provider-connections";

const safeConnection = {
  provider_code: "the_odds_api",
  display_name: "The Odds API",
  product_scope: "Sportsbook odds aggregator",
  enabled: true,
  credential_configured: true,
  credential_mask: "••••aB12",
  status: "connected",
  capabilities: ["events"],
  coverage: ["basketball_nba"],
  sports: ["basketball_nba"],
  regions: ["us"],
  markets: ["h2h"],
  poll_interval_seconds: 60,
  request_budget_per_hour: 120,
  estimated_requests_per_hour: 60,
  quota_limit: 500,
  quota_remaining: 421,
  quota_resets_at: null,
  last_tested_at: "2026-10-19T12:00:00Z",
  last_success_at: "2026-10-19T12:00:00Z",
  last_market_update_at: "2026-10-19T11:59:50Z",
  error_code: null,
};

describe("provider connection DTOs", () => {
  it("keeps only safe connection metadata and drops unexpected secret fields", () => {
    const parsed = providerConnectionListSchema.parse({
      items: [{ ...safeConnection, api_key: "must-not-escape", encrypted_secret: "ciphertext", error_message: "must-not-escape" }],
      debug: { token: "must-not-escape" },
    });
    expect(parsed).toEqual({ items: [safeConnection] });
    expect(JSON.stringify(parsed)).not.toContain("must-not-escape");
  });

  it("rejects a credential value masquerading as a mask", () => {
    expect(() => providerConnectionListSchema.parse({ items: [{ ...safeConnection, credential_mask: "live_api_key_123456789" }] })).toThrow();
  });

  it("accepts only the expected write-only key for The Odds API", () => {
    const base = {
      provider_code: "the_odds_api",
      enabled: true,
      sports: ["basketball_nba"],
      regions: ["us"],
      markets: ["h2h"],
      poll_interval_seconds: 60,
      request_budget_per_hour: 120,
    } as const;
    expect(saveProviderConnectionSchema.safeParse({ ...base, credentials: { api_key: "abcdefgh" } }).success).toBe(true);
    expect(saveProviderConnectionSchema.safeParse({ ...base, credentials: { token: "abcdefgh" } }).success).toBe(false);
    expect(saveProviderConnectionSchema.safeParse({ ...base, provider_code: "kalshi", credentials: { api_key: "abcdefgh" } }).success).toBe(false);
  });

  it("maps server codes to actionable text without displaying an upstream body", () => {
    expect(connectionErrorMessage("invalid_credentials")).toMatch(/rejected/i);
    expect(connectionErrorMessage("unrecognized-provider-error")).toMatch(/could not be completed/i);
  });
});
