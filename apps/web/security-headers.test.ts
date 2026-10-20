import { describe, expect, it } from "vitest";
import { contentSecurityPolicy, productionSecurityHeaders } from "./security-headers";

describe("production security headers", () => {
  it("locks framing, plugins, and production eval while allowing configured data services", () => {
    const policy = contentSecurityPolicy({
      NODE_ENV: "production",
      NEXT_PUBLIC_API_URL: "/api/edgeos",
      NEXT_PUBLIC_SUPABASE_URL: "https://project.supabase.co",
    });
    expect(policy).toContain("object-src 'none'");
    expect(policy).toContain("frame-ancestors 'none'");
    expect(policy).toContain("connect-src 'self' https://project.supabase.co wss://project.supabase.co");
    expect(policy).not.toContain("'unsafe-eval'");
  });

  it("does not interpolate malformed configured origins into CSP", () => {
    const policy = contentSecurityPolicy({
      NODE_ENV: "production",
      NEXT_PUBLIC_SUPABASE_URL: "https://example.com; script-src *",
    });
    expect(policy).not.toContain("example.com");
  });

  it("sets defense-in-depth browser headers", () => {
    const headers = new Map(productionSecurityHeaders({ NODE_ENV: "production" }).map(({ key, value }) => [key, value]));
    expect(headers.get("X-Frame-Options")).toBe("DENY");
    expect(headers.get("X-Content-Type-Options")).toBe("nosniff");
    expect(headers.get("Permissions-Policy")).toContain("camera=()");
  });
});
