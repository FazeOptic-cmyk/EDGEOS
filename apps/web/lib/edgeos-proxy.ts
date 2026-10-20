const ALLOWED_PROTOCOLS = new Set(["http:", "https:"]);

export class EdgeosProxyConfigurationError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "EdgeosProxyConfigurationError";
  }
}

export type EdgeosMode = "demo" | "live";

export class EdgeosModeMismatchError extends Error {
  constructor(
    readonly expected: EdgeosMode,
    readonly actual: EdgeosMode,
  ) {
    super(`EDGEOS mode mismatch: web is ${expected}, API is ${actual}`);
    this.name = "EdgeosModeMismatchError";
  }
}

export function configuredAppMode(
  value = process.env.NEXT_PUBLIC_APP_MODE,
): EdgeosMode {
  return value === "live" ? "live" : "demo";
}

/**
 * Return the per-launch API credential used by the desktop BFF.
 *
 * This value is deliberately server-only: it must never be prefixed with
 * NEXT_PUBLIC or forwarded to the renderer/browser. A desktop process without
 * a strong token is treated as a configuration failure rather than silently
 * opening the loopback API to other local processes.
 */
export function configuredDesktopApiToken(
  desktopMode = process.env.EDGEOS_ENV === "desktop",
  value = process.env.EDGEOS_DESKTOP_API_TOKEN,
): string | null {
  if (!desktopMode) return null;
  if (!value || value.length < 32) {
    throw new EdgeosProxyConfigurationError(
      "EDGEOS_DESKTOP_API_TOKEN is not configured for the desktop gateway",
    );
  }
  return value;
}

/** Validate the versioned mode header before exposing an upstream response. */
export function assertEdgeosMode(
  headers: Pick<Headers, "get">,
  expected = configuredAppMode(),
): EdgeosMode {
  const contract = headers.get("x-edgeos-mode-contract");
  const actual = headers.get("x-edgeos-mode");
  if (contract !== "1" || (actual !== "demo" && actual !== "live")) {
    throw new EdgeosProxyConfigurationError(
      "The EDGEOS API did not provide a supported mode contract",
    );
  }
  if (actual !== expected) throw new EdgeosModeMismatchError(expected, actual);
  return actual;
}

function internalOrigin(rawOrigin: string | undefined): URL {
  if (!rawOrigin?.trim()) {
    throw new EdgeosProxyConfigurationError("EDGEOS_INTERNAL_API_ORIGIN is not configured");
  }

  let origin: URL;
  try {
    origin = new URL(rawOrigin);
  } catch {
    throw new EdgeosProxyConfigurationError("EDGEOS_INTERNAL_API_ORIGIN is not a valid URL");
  }

  if (
    !ALLOWED_PROTOCOLS.has(origin.protocol)
    || origin.username
    || origin.password
    || origin.search
    || origin.hash
    || (origin.pathname !== "/" && origin.pathname !== "")
  ) {
    throw new EdgeosProxyConfigurationError(
      "EDGEOS_INTERNAL_API_ORIGIN must be an HTTP(S) origin without credentials, path, query, or fragment",
    );
  }
  return origin;
}

function isSafeSegment(segment: string): boolean {
  return Boolean(segment)
    && segment !== "."
    && segment !== ".."
    && !segment.includes("/")
    && !segment.includes("\\")
    && !/[\u0000-\u001f\u007f]/u.test(segment);
}

/** Build a fixed-origin upstream URL without allowing path traversal. */
export function buildEdgeosProxyUrl(
  path: readonly string[],
  search: string,
  rawOrigin = process.env.EDGEOS_INTERNAL_API_ORIGIN,
): URL {
  if (
    path.length < 3
    || path[0] !== "api"
    || path[1] !== "v1"
    || !path.every(isSafeSegment)
  ) {
    throw new TypeError("Only normalized /api/v1/... paths can be proxied");
  }

  const target = internalOrigin(rawOrigin);
  target.pathname = `/${path.map((segment) => encodeURIComponent(segment)).join("/")}`;
  target.search = search;
  return target;
}

const EXACT_PROXY_ROUTES: Readonly<Record<string, ReadonlySet<string>>> = {
  GET: new Set([
    "health", "dashboard", "opportunities", "providers/health", "portfolio",
    "portfolio/export.csv", "analytics", "watchlists", "alerts", "settings",
    "match-reviews", "saved-opportunities", "local-workspace/backup",
  ]),
  POST: new Set([
    "simulate", "simulate/scenarios", "portfolio/positions", "watchlists", "alerts",
    "backtests", "local-workspace/restore",
  ]),
  PUT: new Set(["settings"]),
};

/**
 * Keep the browser-facing BFF deliberately narrower than the FastAPI namespace.
 * Adding a backend endpoint never exposes it to browsers by accident.
 */
export function isAllowedEdgeosProxyRequest(method: string, path: readonly string[]): boolean {
  if (path.length < 3 || path[0] !== "api" || path[1] !== "v1") return false;
  const normalizedMethod = method.toUpperCase();
  const resource = path.slice(2).join("/");
  if (EXACT_PROXY_ROUTES[normalizedMethod]?.has(resource)) return true;

  if (normalizedMethod === "GET") {
    return (path.length === 4 && path[2] === "opportunities")
      || (path.length === 5 && path[2] === "providers" && (path[4] === "events" || path[4] === "markets"))
      || (path.length === 5 && path[2] === "markets" && path[4] === "orderbook")
      || (path.length === 4 && path[2] === "match-reviews");
  }
  if (normalizedMethod === "POST") {
    return path.length === 6
      && path[2] === "portfolio"
      && path[3] === "positions"
      && ["fills", "settle", "cancel"].includes(path[5]!);
  }
  if (normalizedMethod === "PATCH") {
    return (path.length === 4 && path[2] === "alerts")
      || (path.length === 4 && path[2] === "match-reviews");
  }
  if (normalizedMethod === "PUT") {
    return path.length === 4 && path[2] === "saved-opportunities";
  }
  if (normalizedMethod === "DELETE") {
    return path.length === 4 && (
      path[2] === "watchlists" || path[2] === "alerts" || path[2] === "saved-opportunities"
    );
  }
  return false;
}

export function isSameOriginBrowserRequest(request: Request): boolean {
  const requestUrl = new URL(request.url);
  // NextURL normalizes all loopback hosts to "localhost". The actual browser
  // origin and Host header retain 127.0.0.1, including in the Windows package.
  // Restore only that narrowly defined normalization, never trust a general
  // forwarded Host or accept localhost/127.0.0.1 interchangeably.
  const host = request.headers.get("host");
  if (requestUrl.hostname === "localhost" && host) {
    try {
      const actual = new URL(`${requestUrl.protocol}//${host}`);
      if (
        /^(?:127(?:\.\d{1,3}){3}|localhost)$/u.test(actual.hostname)
        && actual.host === host && actual.port === requestUrl.port
        && !actual.username && !actual.password && actual.pathname === "/"
        && !actual.search && !actual.hash
      ) {
        requestUrl.host = actual.host;
      }
    } catch {
      return false;
    }
  }
  const origin = request.headers.get("origin");
  if (origin && origin !== requestUrl.origin) return false;

  const fetchSite = request.headers.get("sec-fetch-site");
  return fetchSite !== "cross-site" && fetchSite !== "same-site";
}

/** Reject DNS-rebound requests before they can reach a desktop-only route. */
export function hasExpectedDesktopHost(
  request: Request,
  desktopMode = process.env.EDGEOS_ENV === "desktop",
  rawExpectedOrigin = process.env.EDGEOS_DESKTOP_WEB_ORIGIN,
): boolean {
  if (!desktopMode) return true;
  if (!rawExpectedOrigin) return false;

  try {
    const expected = new URL(rawExpectedOrigin);
    return expected.protocol === "http:"
      && expected.hostname === "127.0.0.1"
      && !expected.username
      && !expected.password
      && !expected.search
      && !expected.hash
      && (expected.pathname === "/" || expected.pathname === "")
      && request.headers.get("host") === expected.host;
  } catch {
    return false;
  }
}
