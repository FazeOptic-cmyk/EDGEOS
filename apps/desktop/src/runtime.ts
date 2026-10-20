import { createServer } from "node:net";
import { createHash } from "node:crypto";
import { createReadStream } from "node:fs";
import { access, lstat, readFile, readdir, stat } from "node:fs/promises";
import { relative, resolve, sep } from "node:path";

export const LOOPBACK_HOST = "127.0.0.1";

export class StartupError extends Error {
  constructor(message: string, readonly guidance: string) {
    super(message);
    this.name = "StartupError";
  }
}

export interface DesktopResources {
  root: string;
  webDirectory: string;
  webServer: string;
  apiExecutable: string;
  integrityManifest: string;
}

export interface RuntimeEndpoints {
  webPort: number;
  apiPort: number;
  webOrigin: string;
  apiOrigin: string;
}

export function createRuntimeEndpoints(webPort: number, apiPort: number): RuntimeEndpoints {
  for (const port of [webPort, apiPort]) {
    if (!Number.isInteger(port) || port < 1 || port > 65_535) {
      throw new StartupError(`Invalid local service port: ${port}.`, "Restart EDGEOS and try again.");
    }
  }
  if (webPort === apiPort) {
    throw new StartupError("The local services selected the same port.", "Restart EDGEOS and try again.");
  }
  return {
    webPort,
    apiPort,
    webOrigin: loopbackOrigin(webPort),
    apiOrigin: loopbackOrigin(apiPort),
  };
}

export function loopbackOrigin(port: number): string {
  if (!Number.isInteger(port) || port < 1 || port > 65_535) {
    throw new StartupError(`Invalid local service port: ${port}.`, "Restart EDGEOS and try again.");
  }
  return `http://${LOOPBACK_HOST}:${port}`;
}

export async function reserveAvailablePort(host = LOOPBACK_HOST): Promise<number> {
  return new Promise<number>((resolve, reject) => {
    const server = createServer();
    server.unref();
    server.once("error", (error: NodeJS.ErrnoException) => reject(new StartupError(
      `EDGEOS could not reserve a local port: ${error.message}`,
      "Check local firewall policy and try again.",
    )));
    server.listen({ host, port: 0, exclusive: true }, () => {
      const address = server.address();
      if (!address || typeof address === "string") {
        server.close();
        reject(new StartupError("EDGEOS could not determine its local port.", "Restart EDGEOS and try again."));
        return;
      }
      const port = address.port;
      server.close((error) => error ? reject(error) : resolve(port));
    });
  });
}

export async function allocateRuntimeEndpoints(): Promise<RuntimeEndpoints> {
  const { webPort, apiPort } = await reserveDistinctPorts();
  return createRuntimeEndpoints(webPort, apiPort);
}

export async function reserveDistinctPorts(
  reserve: () => Promise<number> = reserveAvailablePort,
): Promise<{ webPort: number; apiPort: number }> {
  const webPort = await reserve();
  let apiPort = await reserve();
  while (apiPort === webPort) apiPort = await reserve();
  return { webPort, apiPort };
}

export async function assertResourcesExist(resources: DesktopResources): Promise<void> {
  const required: Array<[string, string]> = [
    [resources.webServer, "bundled web server"],
    [resources.apiExecutable, "bundled API executable"],
  ];
  for (const [file, label] of required) {
    try {
      await access(file);
    } catch {
      throw new StartupError(`The ${label} is missing.`, `Reinstall EDGEOS. Expected resource: ${file}`);
    }
  }
}

interface IntegrityEntry {
  path: string;
  bytes: number;
  sha256: string;
}

function hashFile(path: string): Promise<string> {
  return new Promise((resolveHash, reject) => {
    const hash = createHash("sha256");
    const stream = createReadStream(path);
    stream.once("error", reject);
    stream.on("data", (chunk) => hash.update(chunk));
    stream.once("end", () => resolveHash(hash.digest("hex")));
  });
}

export async function verifyResourceIntegrity(resources: DesktopResources): Promise<void> {
  let parsed: unknown;
  try {
    parsed = JSON.parse(await readFile(resources.integrityManifest, "utf8"));
  } catch (error) {
    throw new StartupError(
      `The bundled resource integrity manifest is unavailable: ${error instanceof Error ? error.message : String(error)}`,
      "Reinstall EDGEOS from a trusted installer.",
    );
  }
  if (typeof parsed !== "object" || !parsed) throw new StartupError("The bundled resource integrity manifest is invalid.", "Reinstall EDGEOS from a trusted installer.");
  const manifest = parsed as Record<string, unknown>;
  if (manifest.schemaVersion !== 1 || manifest.algorithm !== "sha256" || !Array.isArray(manifest.files)) {
    throw new StartupError("The bundled resource integrity manifest has an unsupported format.", "Reinstall EDGEOS from a trusted installer.");
  }
  const entries = manifest.files as IntegrityEntry[];
  if (entries.length === 0) throw new StartupError("The bundled resource integrity manifest is empty.", "Reinstall EDGEOS from a trusted installer.");
  const root = resolve(resources.root);
  const required = new Set(["web/server.js", "api/edgeos-api.exe"]);
  const covered = new Set<string>();

  for (const entry of entries) {
    if (!entry || typeof entry.path !== "string" || !Number.isSafeInteger(entry.bytes) || entry.bytes < 0 || !/^[a-f0-9]{64}$/u.test(entry.sha256)) {
      throw new StartupError("The bundled resource integrity manifest contains an invalid entry.", "Reinstall EDGEOS from a trusted installer.");
    }
    if (covered.has(entry.path)) throw new StartupError("The bundled resource integrity manifest contains a duplicate path.", "Reinstall EDGEOS from a trusted installer.");
    covered.add(entry.path);
    const relativePath = entry.path.replaceAll("/", sep);
    const absolute = resolve(root, relativePath);
    if (absolute !== root && !absolute.startsWith(`${root}${sep}`)) {
      throw new StartupError("The bundled resource integrity manifest contains an unsafe path.", "Reinstall EDGEOS from a trusted installer.");
    }
    try {
      if ((await lstat(absolute)).isSymbolicLink()) throw new Error("symbolic links are not allowed");
      const details = await stat(absolute);
      const digest = await hashFile(absolute);
      if (!details.isFile() || details.size !== entry.bytes || digest !== entry.sha256) throw new Error("hash or size mismatch");
    } catch (error) {
      throw new StartupError(
        `A bundled EDGEOS resource failed its integrity check (${entry.path}): ${error instanceof Error ? error.message : String(error)}`,
        "Reinstall EDGEOS from a trusted installer.",
      );
    }
    required.delete(entry.path);
  }
  if (required.size > 0) throw new StartupError("Required files are absent from the resource integrity manifest.", "Reinstall EDGEOS from a trusted installer.");

  const actual = new Set<string>();
  const walk = async (directory: string): Promise<void> => {
    for (const item of await readdir(directory, { withFileTypes: true })) {
      const absolute = resolve(directory, item.name);
      if (item.isSymbolicLink()) throw new StartupError("A bundled EDGEOS resource unexpectedly uses a symbolic link.", "Reinstall EDGEOS from a trusted installer.");
      if (item.isDirectory()) await walk(absolute);
      else if (item.isFile()) actual.add(relative(root, absolute).split(sep).join("/"));
    }
  };
  await walk(resolve(root, "web"));
  await walk(resolve(root, "api"));
  if (actual.size !== covered.size || [...actual].some((path) => !covered.has(path))) {
    throw new StartupError("The bundled EDGEOS resource set does not match its signed manifest.", "Reinstall EDGEOS from a trusted installer.");
  }
}

export interface JsonHealthResponse {
  status: number;
  body: unknown;
}

export type JsonHealthRequest = (url: string) => Promise<JsonHealthResponse>;

async function defaultJsonHealthRequest(url: string): Promise<JsonHealthResponse> {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 1_500);
  try {
    const response = await fetch(url, { cache: "no-store", redirect: "error", signal: controller.signal });
    let body: unknown = null;
    try {
      body = await response.json();
    } catch {
      // Invalid JSON is handled by the exact response validator.
    }
    return { status: response.status, body };
  } finally {
    clearTimeout(timeout);
  }
}

export async function waitForJsonHealth(options: {
  url: string;
  serviceName: string;
  validate: (body: unknown) => boolean;
  abortReason?: () => string | null;
  processFailure?: () => Error | null;
  timeoutMs?: number;
  intervalMs?: number;
  request?: JsonHealthRequest;
  delay?: (milliseconds: number) => Promise<void>;
}): Promise<void> {
  const timeoutMs = options.timeoutMs ?? 45_000;
  const intervalMs = options.intervalMs ?? 250;
  const request = options.request ?? defaultJsonHealthRequest;
  const delay = options.delay ?? ((milliseconds) => new Promise((resolve) => setTimeout(resolve, milliseconds)));
  const deadline = Date.now() + timeoutMs;
  let lastError = "no response";

  while (Date.now() < deadline) {
    const abortReason = options.abortReason?.();
    if (abortReason) {
      throw new StartupError(
        `${options.serviceName} stopped before it became ready (${abortReason}).`,
        "Review the desktop diagnostic log, then reinstall EDGEOS if the problem continues.",
      );
    }
    const processFailure = options.processFailure?.();
    if (processFailure) throw processFailure;
    try {
      const response = await request(options.url);
      if (response.status >= 200 && response.status < 300 && options.validate(response.body)) return;
      lastError = `unexpected HTTP ${response.status} response`;
    } catch (error) {
      lastError = error instanceof Error ? error.message : String(error);
    }
    await delay(intervalMs);
  }

  throw new StartupError(
    `${options.serviceName} did not become ready within ${Math.ceil(timeoutMs / 1_000)} seconds (${lastError}).`,
    "Restart EDGEOS. If the problem continues, review the desktop diagnostic log.",
  );
}

export function hasReadinessIdentity(value: unknown, service: string, nonce: string): boolean {
  if (typeof value !== "object" || !value) return false;
  const body = value as Record<string, unknown>;
  return body.status === "ok" && body.service === service && body.instance_nonce === nonce;
}

export function isTrustedAppNavigation(rawUrl: string, trustedOrigin: string): boolean {
  try {
    const url = new URL(rawUrl);
    return url.origin === trustedOrigin && !url.username && !url.password;
  } catch {
    return false;
  }
}

export function isSafeExternalUrl(rawUrl: string): boolean {
  try {
    const url = new URL(rawUrl);
    return url.protocol === "https:" && !url.username && !url.password;
  } catch {
    return false;
  }
}

export interface DesktopExport {
  filename: string;
  extension: "csv" | "json" | "edgeos-backup";
  label: string;
}

/** Allow only the exports intentionally produced by the trusted UI. */
export function classifyDesktopExport(
  rawUrl: string,
  rawMimeType: string,
  suggestedFilename: string,
  trustedOrigin: string,
): DesktopExport | null {
  let url: URL;
  try {
    url = new URL(rawUrl);
  } catch {
    return null;
  }
  if (url.protocol !== "blob:" || url.origin !== trustedOrigin) return null;
  if (suggestedFilename.includes("/") || suggestedFilename.includes("\\")) return null;
  const mimeType = rawMimeType.split(";", 1)[0]?.trim().toLowerCase();
  if (
    mimeType === "text/csv"
    && /^edgeos-paper-ledger-\d{4}-\d{2}-\d{2}\.csv$/u.test(suggestedFilename)
  ) {
    return { filename: suggestedFilename, extension: "csv", label: "CSV ledger" };
  }
  if (mimeType === "application/json" && suggestedFilename === "edgeos-account-export.json") {
    return { filename: suggestedFilename, extension: "json", label: "JSON account data" };
  }
  if (
    mimeType === "application/vnd.edgeos.workspace-backup+json"
    && suggestedFilename === "edgeos-local-workspace-backup.edgeos-backup"
  ) {
    return {
      filename: suggestedFilename,
      extension: "edgeos-backup",
      label: "local workspace backup",
    };
  }
  return null;
}

export function redactLogMessage(message: string): string {
  return message
    .replace(/(https?:\/\/[^\s?]+)\?[^\s]*/giu, "$1?[redacted]")
    .replace(/\b(basic|bearer)\s+[A-Za-z0-9._~+\/-]+=*/giu, "$1 [redacted]")
    .replace(
      /(authorization|set-cookie|cookie|password|passwd|token|secret|apikey|api_key)(\s*[:=]\s*)(?:"[^"]*"|'[^']*'|[^\s,;]+)/giu,
      "$1$2[redacted]",
    );
}

export function presentStartupError(error: unknown): string {
  if (error instanceof StartupError) return `${error.message}\n\n${error.guidance}`;
  if (error instanceof Error) return `${error.message}\n\nRestart EDGEOS. If the problem continues, reinstall the application.`;
  return "An unknown startup error occurred.\n\nRestart EDGEOS. If the problem continues, reinstall the application.";
}
