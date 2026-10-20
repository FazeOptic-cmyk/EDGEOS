import assert from "node:assert/strict";
import { mkdtempSync, readFileSync, readdirSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";
import {
  hasHostedLiveIdentity, HOSTED_HEALTH_PATH, hostedSessionPartition, isConnectionSettingsSender,
  LOCAL_DEMO_CONNECTION, normalizeHostedOrigin, parseDesktopConnection, readDesktopConnection,
  saveDesktopConnection, verifyHostedConnection,
} from "../src/connection";
import { isTrustedAppNavigation } from "../src/runtime";

const origin = "https://edgeos.example.com";
const health = {
  status: "ok", service: "edgeos-api", mode: "live", mode_contract_version: 1,
  is_demo: false, auth: "supabase_jwks", paper_trading_only: true, real_money_execution: false,
};
const headers = new Headers({ "X-EDGEOS-Mode": "live", "X-EDGEOS-Mode-Contract": "1" });
const healthResponse = (body: unknown = health, responseHeaders = headers, status = 200) =>
  new Response(JSON.stringify(body), { status, headers: responseHeaders });

test("hosted origins normalize HTTPS DNS origins and explicit ports", () => {
  assert.equal(normalizeHostedOrigin("  HTTPS://EDGEOS.EXAMPLE.COM:443/  "), origin);
  assert.equal(normalizeHostedOrigin(`${origin}:8443`), `${origin}:8443`);
});

test("hosted settings reject credentials, local addresses, non-HTTPS schemes and non-origin URLs", () => {
  for (const invalid of [
    null, {}, 123, "", "edgeos.example.com", "http://edgeos.example.com",
    "file:///etc/passwd", "javascript:alert(1)", "https://user:password@edgeos.example.com",
    "https://@edgeos.example.com", `${origin}/dashboard`, `${origin}/../`,
    `${origin}?access_token=secret`, `${origin}?`, `${origin}#`, `${origin}#fragment`,
    "https://localhost", "https://edgeos.localhost", "https://edgeos.local", "https://edgeos.internal",
    "https://127.0.0.1", "https://127.1", "https://2130706433", "https://0x7f000001",
    "https://[::1]", "https://192.168.1.10", "https://10.0.0.1", "https://server",
    "https://edgeos.example.com.", "https://edgeos.example.com\\@attacker.example",
    "https://edgeos.\nexample.com", "https://bad_.example.com",
  ]) assert.throws(() => normalizeHostedOrigin(invalid), /HTTPS web server origin/u, String(invalid));
});

test("saved connection persistence is versioned, secret-free, atomic, and resettable", () => {
  const directory = mkdtempSync(join(tmpdir(), "edgeos-connection-"));
  const path = join(directory, "connection.json");
  try {
    assert.deepEqual(readDesktopConnection(path), LOCAL_DEMO_CONNECTION);
    saveDesktopConnection(path, { schemaVersion: 1, mode: "hosted-live", origin: `${origin}/` });
    assert.deepEqual(readDesktopConnection(path), { schemaVersion: 1, mode: "hosted-live", origin });
    assert.deepEqual(Object.keys(JSON.parse(readFileSync(path, "utf8"))).sort(), ["mode", "origin", "schemaVersion"]);
    assert.deepEqual(readdirSync(directory), ["connection.json"]);
    saveDesktopConnection(path, LOCAL_DEMO_CONNECTION);
    assert.deepEqual(readDesktopConnection(path), LOCAL_DEMO_CONNECTION);
    writeFileSync(path, "invalid json");
    assert.throws(() => readDesktopConnection(path), /Saved desktop connection settings could not be read/u);
  } finally {
    rmSync(directory, { recursive: true, force: true });
  }
});

test("invalid or contaminated config cannot silently become local demo or be persisted", () => {
  for (const document of [
    null, [], {}, { schemaVersion: 2, mode: "local-demo" },
    { schemaVersion: 1, mode: "live", origin },
    { schemaVersion: 1, mode: "local-demo", origin },
    { schemaVersion: 1, mode: "hosted-live", origin: `${origin}/api/v1` },
    { schemaVersion: 1, mode: "hosted-live", origin, apiToken: "secret" },
  ]) assert.throws(() => parseDesktopConnection(JSON.stringify(document)));
});

test("hosted sessions persist per exact origin and never share the default demo session", () => {
  const partition = hostedSessionPartition(origin);
  assert.match(partition, /^persist:edgeos-live-[a-f0-9]{64}$/u);
  assert.equal(hostedSessionPartition(`${origin}/`), partition);
  for (const other of ["https://other.example.com", `${origin}:8443`, "https://edgeos.example.com.evil.example"]) {
    assert.notEqual(hostedSessionPartition(other), partition);
  }
  assert.equal(isTrustedAppNavigation(`${origin}/auth/callback?code=one-time`, origin), true);
  for (const other of ["https://other.example.com/auth/callback", `${origin}:8443/`, "https://edgeos.example.com.evil.example", "http://edgeos.example.com"]) {
    assert.equal(isTrustedAppNavigation(other, origin), false);
  }
});

test("live health validation requires versioned matching headers, auth, and paper-only guarantees", () => {
  assert.equal(hasHostedLiveIdentity(health, headers), true);
  for (const replacement of [
    { status: "degraded" }, { service: "impostor" }, { mode: "demo" }, { mode_contract_version: 2 },
    { is_demo: true }, { auth: "not_configured" }, { auth: "demo_local_identity" },
    { paper_trading_only: false }, { real_money_execution: true },
  ]) assert.equal(hasHostedLiveIdentity({ ...health, ...replacement }, headers), false);
  for (const key of Object.keys(health)) {
    const missing = { ...health } as Record<string, unknown>;
    delete missing[key];
    assert.equal(hasHostedLiveIdentity(missing, headers), false);
  }
  assert.equal(hasHostedLiveIdentity(health, new Headers()), false);
  assert.equal(hasHostedLiveIdentity(health, new Headers({ "X-EDGEOS-Mode": "demo", "X-EDGEOS-Mode-Contract": "1" })), false);
  assert.equal(hasHostedLiveIdentity(null, headers), false);
});

test("hosted probe uses only public BFF health and sends no account cookies or local service tokens", async () => {
  let calls = 0;
  const connection = await verifyHostedConnection(origin, async (url, options) => {
    calls++;
    assert.equal(url, `${origin}${HOSTED_HEALTH_PATH}`);
    assert.equal(options?.method, "GET");
    assert.equal(options?.redirect, "error");
    assert.equal(options?.credentials, "omit");
    assert.equal(options?.cache, "no-store");
    assert.equal(options?.body, undefined);
    assert.deepEqual(options?.headers, { Accept: "application/json" });
    assert.ok(options?.signal instanceof AbortSignal);
    return healthResponse();
  });
  assert.equal(calls, 1);
  assert.deepEqual(connection, { schemaVersion: 1, mode: "hosted-live", origin });
});

test("invalid URLs are rejected before network access", async () => {
  let requested = false;
  await assert.rejects(verifyHostedConnection("https://user:secret@edgeos.example.com", async () => {
    requested = true;
    return healthResponse();
  }));
  assert.equal(requested, false);
});

test("hosted verification rejects redirects, unavailable APIs, malformed and oversized responses", async () => {
  const redirected = healthResponse();
  Object.defineProperty(redirected, "redirected", { value: true });
  const wrongOrigin = healthResponse();
  Object.defineProperty(wrongOrigin, "url", { value: "https://other.example.com/api/edgeos/api/v1/health" });
  for (const response of [
    healthResponse(health, headers, 503), healthResponse({ ...health, mode: "demo" }),
    healthResponse(health, new Headers()), new Response("<html>Sign in</html>"),
    new Response("a".repeat(65_537)), redirected, wrongOrigin,
  ]) await assert.rejects(verifyHostedConnection(origin, async () => response), /did not confirm/u);
});

test("network failures do not echo credential-bearing URLs in error messages", async () => {
  await assert.rejects(
    verifyHostedConnection(origin, async () => { throw new Error("https://user:secret@private.example/?token=secret"); }),
    (error: unknown) => error instanceof Error && !/secret|private\.example/u.test(error.message),
  );
});

test("settings IPC requires exact owned document, WebContents, and top-level frame", () => {
  const settings = "data:text/html;charset=utf-8,static-only";
  assert.equal(isConnectionSettingsSender(7, 7, true, settings, settings), true);
  assert.equal(isConnectionSettingsSender(8, 7, true, settings, settings), false);
  assert.equal(isConnectionSettingsSender(7, null, true, settings, settings), false);
  assert.equal(isConnectionSettingsSender(7, 7, false, settings, settings), false);
  assert.equal(isConnectionSettingsSender(7, 7, true, origin, settings), false);
  assert.equal(isConnectionSettingsSender(7, 7, true, `${settings}evil`, settings), false);
});
