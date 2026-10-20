import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { mkdtemp, mkdir, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";
import "./connection.test";
import "./smoke-profile.test";
import {
  StartupError,
  classifyDesktopExport,
  hasReadinessIdentity,
  isSafeExternalUrl,
  isTrustedAppNavigation,
  loopbackOrigin,
  presentStartupError,
  redactLogMessage,
  reserveDistinctPorts,
  verifyResourceIntegrity,
  waitForJsonHealth,
  type DesktopResources,
} from "../src/runtime";
import {
  parsePersistedWindowState, visibleWindowBounds, windowStateDocument,
} from "../src/window-state";

test("only the active random EDGEOS origin is accepted for in-window navigation", () => {
  const origin = loopbackOrigin(43_210);
  assert.equal(isTrustedAppNavigation(`${origin}/scanner?verified=true`, origin), true);
  assert.equal(isTrustedAppNavigation("http://localhost:43210/", origin), false);
  assert.equal(isTrustedAppNavigation("http://user:secret@127.0.0.1:43210/", origin), false);
  assert.equal(isTrustedAppNavigation("http://127.0.0.1:43210.evil.example/", origin), false);
  assert.equal(isTrustedAppNavigation("javascript:alert(1)", origin), false);
});

test("external opening accepts HTTPS URLs without embedded userinfo only", () => {
  assert.equal(isSafeExternalUrl("https://docs.kalshi.com/"), true);
  assert.equal(isSafeExternalUrl("https://user:secret@example.com/"), false);
  assert.equal(isSafeExternalUrl("http://example.com/"), false);
  assert.equal(isSafeExternalUrl("file:///C:/Windows/System32/calc.exe"), false);
});

test("desktop downloads allow only trusted EDGEOS CSV, JSON, and backup blob exports", () => {
  const origin = "http://127.0.0.1:43210";
  assert.deepEqual(classifyDesktopExport(
    `blob:${origin}/123`, "text/csv; charset=utf-8", "edgeos-paper-ledger-2026-10-20.csv", origin,
  ), { filename: "edgeos-paper-ledger-2026-10-20.csv", extension: "csv", label: "CSV ledger" });
  assert.deepEqual(classifyDesktopExport(
    `blob:${origin}/456`, "application/json", "edgeos-account-export.json", origin,
  ), { filename: "edgeos-account-export.json", extension: "json", label: "JSON account data" });
  assert.deepEqual(classifyDesktopExport(
    `blob:${origin}/789`, "application/vnd.edgeos.workspace-backup+json",
    "edgeos-local-workspace-backup.edgeos-backup", origin,
  ), {
    filename: "edgeos-local-workspace-backup.edgeos-backup",
    extension: "edgeos-backup",
    label: "local workspace backup",
  });
  assert.equal(classifyDesktopExport(
    "blob:https://attacker.example/123", "text/csv", "edgeos-paper-ledger-2026-10-20.csv", origin,
  ), null);
  assert.equal(classifyDesktopExport(
    `blob:${origin}/123`, "application/x-msdownload", "EDGEOS.exe", origin,
  ), null);
  assert.equal(classifyDesktopExport(
    `blob:${origin}/123`, "text/csv", "../../edgeos-paper-ledger-2026-10-20.csv", origin,
  ), null);
});

test("readiness identity requires the exact service and per-launch nonce", () => {
  const nonce = "n".repeat(43);
  assert.equal(hasReadinessIdentity({ status: "ok", service: "edgeos-api", instance_nonce: nonce }, "edgeos-api", nonce), true);
  assert.equal(hasReadinessIdentity({ status: "ok", service: "edgeos-web", instance_nonce: nonce }, "edgeos-api", nonce), false);
  assert.equal(hasReadinessIdentity({ status: "ok", service: "edgeos-api", instance_nonce: "wrong" }, "edgeos-api", nonce), false);
  assert.equal(hasReadinessIdentity(null, "edgeos-api", nonce), false);
});

test("health polling resolves only after a valid identity proof", async () => {
  const nonce = "n".repeat(43);
  const responses = [
    { status: 503, body: null },
    { status: 200, body: { status: "ok", service: "impostor", instance_nonce: nonce } },
    { status: 200, body: { status: "ok", service: "edgeos-api", instance_nonce: nonce } },
  ];
  await waitForJsonHealth({
    url: "http://127.0.0.1:43123/api/v1/desktop/readiness",
    serviceName: "API",
    timeoutMs: 1_000,
    intervalMs: 0,
    request: async () => responses.shift() ?? { status: 500, body: null },
    validate: (body) => hasReadinessIdentity(body, "edgeos-api", nonce),
    delay: async () => undefined,
  });
  assert.deepEqual(responses, []);
});

test("health polling aborts immediately when its owned child exits", async () => {
  await assert.rejects(
    waitForJsonHealth({
      url: "http://127.0.0.1:43123/health",
      serviceName: "API",
      timeoutMs: 1_000,
      validate: () => true,
      request: async () => ({ status: 200, body: {} }),
      abortReason: () => "code 1",
    }),
    (error: unknown) => error instanceof StartupError && /stopped before it became ready/.test(error.message),
  );
});

test("health polling reports a useful timeout", async () => {
  let now = 0;
  const originalNow = Date.now;
  Date.now = () => now;
  try {
    await assert.rejects(
      waitForJsonHealth({
        url: "http://127.0.0.1:43123/health",
        serviceName: "API",
        timeoutMs: 10,
        intervalMs: 5,
        request: async () => ({ status: 503, body: null }),
        validate: () => false,
        delay: async (milliseconds) => { now += milliseconds; },
      }),
      (error: unknown) => error instanceof StartupError && /API did not become ready/.test(error.message),
    );
  } finally {
    Date.now = originalNow;
  }
});

test("ephemeral port selection returns distinct loopback endpoints", async () => {
  const candidates = [43_210, 43_210, 48_765];
  const selected = await reserveDistinctPorts(async () => candidates.shift() ?? 55_555);
  assert.notEqual(selected.webPort, selected.apiPort);
  assert.match(loopbackOrigin(selected.webPort), /^http:\/\/127\.0\.0\.1:\d+$/u);
  assert.match(loopbackOrigin(selected.apiPort), /^http:\/\/127\.0\.0\.1:\d+$/u);
});

test("signed resource manifest detects payload tampering", async () => {
  const temporary = await mkdtemp(join(tmpdir(), "edgeos-integrity-"));
  try {
    const resourcesRoot = join(temporary, "resources");
    const webDirectory = join(resourcesRoot, "web");
    const apiDirectory = join(resourcesRoot, "api");
    const webServer = join(webDirectory, "server.js");
    const apiExecutable = join(apiDirectory, "edgeos-api.exe");
    const integrityManifest = join(temporary, "resource-integrity.json");
    await mkdir(webDirectory, { recursive: true });
    await mkdir(apiDirectory, { recursive: true });
    await writeFile(webServer, "web payload", "utf8");
    await writeFile(apiExecutable, "api payload", "utf8");
    const payloads = [
      { path: "web/server.js", value: "web payload" },
      { path: "api/edgeos-api.exe", value: "api payload" },
    ];
    await writeFile(integrityManifest, JSON.stringify({
      schemaVersion: 1,
      algorithm: "sha256",
      files: payloads.map(({ path, value }) => ({
        path,
        bytes: Buffer.byteLength(value),
        sha256: createHash("sha256").update(value).digest("hex"),
      })),
    }), "utf8");
    const resources: DesktopResources = {
      root: resourcesRoot,
      webDirectory,
      webServer,
      apiExecutable,
      integrityManifest,
    };

    await verifyResourceIntegrity(resources);
    await writeFile(webServer, "tampered payload", "utf8");
    await assert.rejects(
      verifyResourceIntegrity(resources),
      (error: unknown) => error instanceof StartupError && /integrity check/.test(error.message),
    );
    await writeFile(webServer, "web payload", "utf8");
    await writeFile(join(webDirectory, "unexpected.js"), "untrusted extra", "utf8");
    await assert.rejects(
      verifyResourceIntegrity(resources),
      (error: unknown) => error instanceof StartupError && /does not match its signed manifest/.test(error.message),
    );
  } finally {
    await rm(temporary, { force: true, recursive: true });
  }
});

test("startup errors include actionable guidance", () => {
  const output = presentStartupError(new StartupError("Payload is invalid.", "Reinstall the app."));
  assert.match(output, /Payload is invalid/);
  assert.match(output, /Reinstall the app/);
});

test("desktop diagnostics redact common credential and session formats", () => {
  const output = redactLogMessage(
    "Authorization: Bearer ey.token Cookie=session=abc; password='hunter2' https://example.test/path?access_token=secret\nAuthorization=Basic dXNlcjpwYXNz",
  );
  assert.doesNotMatch(output, /ey\.token|session=abc|hunter2|access_token=secret|dXNlcjpwYXNz/u);
  assert.match(output, /Authorization:\s*\[redacted\]/u);
  assert.match(output, /Cookie=\[redacted\]/u);
  assert.match(output, /password=\[redacted\]/u);
  assert.match(output, /\?\[redacted\]/u);
});

test("window state parsing rejects corrupted or unsafe geometry", () => {
  assert.equal(parsePersistedWindowState("not-json"), null);
  assert.equal(parsePersistedWindowState(JSON.stringify({
    schemaVersion: 1,
    maximized: false,
    bounds: { x: 10, y: 10, width: 10, height: 10 },
  })), null);
  const parsed = parsePersistedWindowState(windowStateDocument(
    { x: 120, y: 80, width: 1440, height: 900 },
    true,
  ));
  assert.deepEqual(parsed, {
    schemaVersion: 1,
    bounds: { x: 120, y: 80, width: 1440, height: 900 },
    maximized: true,
  });
});

test("window geometry is brought back on screen after a monitor is removed", () => {
  const primary = { x: 0, y: 0, width: 1920, height: 1080 };
  assert.deepEqual(
    visibleWindowBounds({ x: 4000, y: 50, width: 1480, height: 940 }, [primary]),
    { x: 440, y: 50, width: 1480, height: 940 },
  );
  assert.deepEqual(
    visibleWindowBounds({ x: 100, y: 100, width: 3000, height: 2000 }, [primary]),
    { x: 0, y: 0, width: 1920, height: 1080 },
  );
});
