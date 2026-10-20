import { afterEach, describe, expect, it, vi } from "vitest";
import { GET } from "./route";

describe("desktop health proof", () => {
  afterEach(() => vi.unstubAllEnvs());

  const request = () => new Request("http://127.0.0.1:3210/api/desktop-health", {
    headers: { Host: "127.0.0.1:3210" },
  });

  it("is hidden outside the desktop runtime", async () => {
    vi.stubEnv("EDGEOS_ENV", "production");
    vi.stubEnv("EDGEOS_DESKTOP_INSTANCE_NONCE", "a".repeat(43));
    const response = GET(request());
    expect(response.status).toBe(404);
    expect(response.headers.get("cache-control")).toBe("no-store");
  });

  it("fails closed when the desktop nonce is absent", async () => {
    vi.stubEnv("EDGEOS_ENV", "desktop");
    vi.stubEnv("EDGEOS_DESKTOP_WEB_ORIGIN", "http://127.0.0.1:3210");
    vi.stubEnv("EDGEOS_DESKTOP_INSTANCE_NONCE", "");
    expect(GET(request()).status).toBe(503);
  });

  it("returns the exact per-launch nonce without allowing caching", async () => {
    const nonce = "desktop-instance-" + "b".repeat(32);
    vi.stubEnv("EDGEOS_ENV", "desktop");
    vi.stubEnv("EDGEOS_DESKTOP_WEB_ORIGIN", "http://127.0.0.1:3210");
    vi.stubEnv("EDGEOS_DESKTOP_INSTANCE_NONCE", nonce);
    const response = GET(request());
    expect(response.status).toBe(200);
    expect(response.headers.get("cache-control")).toBe("no-store");
    await expect(response.json()).resolves.toEqual({
      status: "ok",
      service: "edgeos-web",
      instance_nonce: nonce,
    });
  });

  it("rejects a hostile desktop Host even with a valid nonce", () => {
    vi.stubEnv("EDGEOS_ENV", "desktop");
    vi.stubEnv("EDGEOS_DESKTOP_WEB_ORIGIN", "http://127.0.0.1:3210");
    vi.stubEnv("EDGEOS_DESKTOP_INSTANCE_NONCE", "c".repeat(43));
    const hostile = new Request("http://attacker.example/api/desktop-health", {
      headers: { Host: "attacker.example" },
    });
    expect(GET(hostile).status).toBe(403);
  });
});
