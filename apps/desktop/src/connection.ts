import { createHash, randomUUID } from "node:crypto";
import { readFileSync, renameSync, rmSync, statSync, writeFileSync } from "node:fs";
import { isIP } from "node:net";
import { StartupError } from "./runtime";

export type DesktopConnection =
  | { schemaVersion: 1; mode: "local-demo" }
  | { schemaVersion: 1; mode: "hosted-live"; origin: string };

export const LOCAL_DEMO_CONNECTION: DesktopConnection = { schemaVersion: 1, mode: "local-demo" };
export const HOSTED_HEALTH_PATH = "/api/edgeos/api/v1/health";

/** Accept only a server origin, never a credential, deep link, or API endpoint. */
export function normalizeHostedOrigin(value: unknown): string {
  const invalid = () => new StartupError(
    "Enter an HTTPS web server origin, such as https://edgeos.your-company.com.",
    "Use a deployed EDGEOS web server with a valid certificate. Do not include credentials, a path, a query, or a fragment.",
  );
  if (typeof value !== "string" || value.length > 2048) throw invalid();
  const raw = value.trim();
  if (!/^https:\/\/[^/?#\\@\s]+\/?$/iu.test(raw) || /[\u0000-\u001f\u007f]/u.test(raw)) throw invalid();
  let url: URL;
  try { url = new URL(raw); } catch { throw invalid(); }
  const host = url.hostname;
  if (
    url.protocol !== "https:" || url.username || url.password
    || url.pathname !== "/" || url.search || url.hash
    || isIP(host.replace(/^\[|\]$/gu, "")) !== 0
    || !host.includes(".") || host.endsWith(".")
    || /(?:^|\.)(?:localhost|local|internal)$/iu.test(host)
    || host.split(".").some((label) => !/^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$/iu.test(label))
  ) throw invalid();
  return url.origin;
}

export function parseDesktopConnection(raw: string): DesktopConnection {
  let value: unknown;
  try { value = JSON.parse(raw); } catch { throw new Error("Invalid desktop connection settings."); }
  if (typeof value !== "object" || !value || Array.isArray(value)) throw new Error("Invalid desktop connection settings.");
  const document = value as Record<string, unknown>;
  const keys = Object.keys(document).sort().join(",");
  if (document.schemaVersion === 1 && document.mode === "local-demo" && keys === "mode,schemaVersion") {
    return { ...LOCAL_DEMO_CONNECTION };
  }
  if (document.schemaVersion === 1 && document.mode === "hosted-live" && keys === "mode,origin,schemaVersion") {
    return { schemaVersion: 1, mode: "hosted-live", origin: normalizeHostedOrigin(document.origin) };
  }
  throw new Error("Invalid desktop connection settings.");
}

/** Invalid saved configuration must be repaired explicitly, never silently use demo data. */
export function readDesktopConnection(path: string): DesktopConnection {
  try {
    if (statSync(path).size > 4096) throw new Error("Connection settings are too large.");
    return parseDesktopConnection(readFileSync(path, "utf8"));
  } catch (error) {
    if ((error as NodeJS.ErrnoException).code === "ENOENT") return { ...LOCAL_DEMO_CONNECTION };
    throw new StartupError(
      "Saved desktop connection settings could not be read.",
      "Open Connection settings to reconnect to your server or choose Local demo.",
    );
  }
}

/** Atomic replacement: an interrupted write leaves the previous selection intact. */
export function saveDesktopConnection(path: string, connection: DesktopConnection): void {
  const document = parseDesktopConnection(JSON.stringify(connection));
  const temporary = `${path}.${randomUUID()}.tmp`;
  try {
    writeFileSync(temporary, `${JSON.stringify(document, null, 2)}\n`, { encoding: "utf8", mode: 0o600, flag: "wx" });
    renameSync(temporary, path);
  } finally {
    rmSync(temporary, { force: true });
  }
}

/** Different origins never share cookies or browser storage, including subdomains/ports. */
export function hostedSessionPartition(origin: string): string {
  return `persist:edgeos-live-${createHash("sha256").update(normalizeHostedOrigin(origin)).digest("hex")}`;
}

export function hasHostedLiveIdentity(body: unknown, headers: Headers): boolean {
  if (!body || typeof body !== "object" || Array.isArray(body)) return false;
  const value = body as Record<string, unknown>;
  return headers.get("x-edgeos-mode") === "live"
    && headers.get("x-edgeos-mode-contract") === "1"
    && value.status === "ok" && value.service === "edgeos-api"
    && value.mode_contract_version === 1 && value.mode === "live" && value.is_demo === false
    && value.auth === "supabase_jwks"
    && value.paper_trading_only === true && value.real_money_execution === false;
}

async function boundedJson(response: Response): Promise<unknown> {
  if (!response.body) throw new Error("Empty server health response.");
  const reader = response.body.getReader();
  const chunks: Uint8Array[] = [];
  let bytes = 0;
  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      bytes += value.byteLength;
      if (bytes > 64 * 1024) throw new Error("Server health response exceeded its limit.");
      chunks.push(value);
    }
    return JSON.parse(Buffer.concat(chunks).toString("utf8")) as unknown;
  } finally {
    await reader.cancel().catch(() => undefined);
  }
}

/**
 * This proves the public web/API mode contract and auth configuration, not data
 * freshness or worker health. It must not reuse Electron cookies or local tokens.
 */
export async function verifyHostedConnection(
  input: unknown,
  request: typeof fetch = fetch,
): Promise<Extract<DesktopConnection, { mode: "hosted-live" }>> {
  const origin = normalizeHostedOrigin(input);
  let valid = false;
  try {
    const response = await request(`${origin}${HOSTED_HEALTH_PATH}`, {
      method: "GET",
      headers: { Accept: "application/json" },
      credentials: "omit",
      cache: "no-store",
      redirect: "error",
      signal: AbortSignal.timeout(10_000),
    });
    const body = await boundedJson(response);
    valid = response.status === 200 && !response.redirected
      && (!response.url || new URL(response.url).origin === origin)
      && hasHostedLiveIdentity(body, response.headers);
  } catch {
    // Network errors may contain sensitive upstream URLs. Keep diagnostics generic.
  }
  if (!valid) throw new StartupError(
    "The server did not confirm a supported EDGEOS live connection.",
    "Check the HTTPS web origin, network access, matching live web/API configuration, and Supabase authentication. The public health endpoint must not redirect or require sign-in.",
  );
  return { schemaVersion: 1, mode: "hosted-live", origin };
}

/** The settings IPC is available only to its own top-level, static local document. */
export function isConnectionSettingsSender(
  senderId: number, expectedId: number | null, isMainFrame: boolean, senderUrl: string, settingsUrl: string,
): boolean {
  return expectedId !== null && senderId === expectedId && isMainFrame && senderUrl === settingsUrl;
}
