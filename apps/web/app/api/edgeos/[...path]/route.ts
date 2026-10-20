import { NextResponse, type NextRequest } from "next/server";
import {
  assertEdgeosMode,
  buildEdgeosProxyUrl,
  configuredAppMode,
  configuredDesktopApiToken,
  EdgeosProxyConfigurationError,
  EdgeosModeMismatchError,
  hasExpectedDesktopHost,
  isAllowedEdgeosProxyRequest,
  isSameOriginBrowserRequest,
} from "@/lib/edgeos-proxy";
import { createServerSupabaseClient } from "@/lib/supabase/server";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

const NO_STORE_HEADERS = { "Cache-Control": "no-store" };
const MAX_REQUEST_BYTES = 128 * 1024;
const MAX_BACKUP_REQUEST_BYTES = 49 * 1024 * 1024;
const BACKUP_MEDIA_TYPE = "application/vnd.edgeos.workspace-backup+json";

type RouteContext = { params: Promise<{ path: string[] }> };

async function validatedAuthorization(): Promise<string | null> {
  const client = await createServerSupabaseClient();
  if (!client) return null;
  const [{ data: claims, error: claimsError }, { data: session }] = await Promise.all([
    client.auth.getClaims(),
    client.auth.getSession(),
  ]);
  if (claimsError || typeof claims?.claims?.sub !== "string") return null;
  if (!session.session?.access_token || session.session.user.id !== claims.claims.sub) return null;
  return `Bearer ${session.session.access_token}`;
}

async function boundedBody(request: NextRequest, isBackupRestore: boolean): Promise<ArrayBuffer | undefined> {
  if (request.method === "GET" || request.method === "HEAD") return undefined;
  const maximum = isBackupRestore ? MAX_BACKUP_REQUEST_BYTES : MAX_REQUEST_BYTES;
  const declared = Number(request.headers.get("content-length") ?? "0");
  if (!Number.isFinite(declared) || declared < 0 || declared > maximum) {
    throw new RangeError("Request body exceeds the EDGEOS BFF limit");
  }
  const contentType = request.headers.get("content-type")?.split(";", 1)[0]?.trim().toLowerCase();
  const requiredType = isBackupRestore ? BACKUP_MEDIA_TYPE : "application/json";
  if (declared > 0 && contentType !== requiredType) {
    throw new TypeError(`Only ${requiredType} request bodies are accepted`);
  }
  const body = await request.arrayBuffer();
  if (body.byteLength > maximum) throw new RangeError("Request body exceeds the EDGEOS BFF limit");
  if (body.byteLength > 0 && contentType !== requiredType) throw new TypeError(`Only ${requiredType} request bodies are accepted`);
  return body.byteLength ? body : undefined;
}

async function forward(
  request: NextRequest,
  context: RouteContext,
) {
  if (!hasExpectedDesktopHost(request) || !isSameOriginBrowserRequest(request)) {
    return NextResponse.json({ detail: "Cross-origin proxy request rejected." }, {
      status: 403,
      headers: NO_STORE_HEADERS,
    });
  }

  let target: URL;
  let proxyPath: string[];
  try {
    const { path } = await context.params;
    proxyPath = path;
    if (!isAllowedEdgeosProxyRequest(request.method, path)) {
      return NextResponse.json({ detail: "This API operation is not exposed by the EDGEOS browser gateway." }, {
        status: 404,
        headers: NO_STORE_HEADERS,
      });
    }
    target = buildEdgeosProxyUrl(path, request.nextUrl.search);
  } catch (error) {
    if (error instanceof EdgeosProxyConfigurationError) {
      return NextResponse.json({ detail: "The internal EDGEOS API is not configured." }, {
        status: 503,
        headers: NO_STORE_HEADERS,
      });
    }
    return NextResponse.json({ detail: "Only approved /api/v1 endpoints may be proxied." }, {
      status: 404,
      headers: NO_STORE_HEADERS,
    });
  }

  if (request.method !== "GET" && !request.headers.get("origin")) {
    return NextResponse.json({ detail: "Mutation origin rejected." }, { status: 403, headers: NO_STORE_HEADERS });
  }

  let body: ArrayBuffer | undefined;
  const isBackupRestore = request.method === "POST"
    && proxyPath.slice(2).join("/") === "local-workspace/restore";
  try {
    body = await boundedBody(request, isBackupRestore);
  } catch (error) {
    return NextResponse.json({ detail: error instanceof RangeError ? "Request is too large." : "The approved request content type is required." }, {
      status: error instanceof RangeError ? 413 : 415,
      headers: NO_STORE_HEADERS,
    });
  }

  const headers = new Headers({
    Accept: `application/json, text/csv;q=0.9, ${BACKUP_MEDIA_TYPE};q=0.9`,
    "X-EDGEOS-Client-Mode": configuredAppMode(),
  });
  try {
    const desktopApiToken = configuredDesktopApiToken();
    if (desktopApiToken) headers.set("X-EDGEOS-Desktop-Token", desktopApiToken);
  } catch (error) {
    if (error instanceof EdgeosProxyConfigurationError) {
      return NextResponse.json({ detail: "The desktop API gateway is not configured." }, {
        status: 503,
        headers: NO_STORE_HEADERS,
      });
    }
    throw error;
  }
  const authorization = await validatedAuthorization();
  if (authorization) headers.set("Authorization", authorization);
  if (body) headers.set("Content-Type", isBackupRestore ? BACKUP_MEDIA_TYPE : "application/json");
  if (isBackupRestore && request.headers.get("x-edgeos-confirm-restore") === "REPLACE") {
    headers.set("X-EDGEOS-Confirm-Restore", "REPLACE");
  }
  const idempotencyKey = request.headers.get("x-idempotency-key");
  if (idempotencyKey && /^[A-Za-z0-9._:-]{8,200}$/u.test(idempotencyKey)) {
    headers.set("X-Idempotency-Key", idempotencyKey);
  }

  try {
    const upstream = await fetch(target, {
      method: request.method,
      headers,
      body,
      cache: "no-store",
      redirect: "error",
      signal: AbortSignal.timeout(15_000),
    });
    try {
      assertEdgeosMode(upstream.headers);
    } catch (error) {
      const failureHeaders = new Headers(NO_STORE_HEADERS);
      // Preserve a positively observed, versioned upstream mode on the safe
      // error response. The browser mode guard can then distinguish a genuine
      // cross-mode deployment from an offline demo API without exposing the
      // blocked upstream body.
      if (error instanceof EdgeosModeMismatchError) {
        failureHeaders.set("X-EDGEOS-Mode", error.actual);
        failureHeaders.set("X-EDGEOS-Mode-Contract", "1");
      }
      const detail = error instanceof EdgeosModeMismatchError
        ? {
            code: "mode_mismatch",
            message: "The web and API data modes disagree. Results are blocked until configuration is corrected.",
            expected: error.expected,
            actual: error.actual,
          }
        : {
            code: "mode_contract_unavailable",
            message: "The API did not provide a supported EDGEOS mode contract.",
          };
      return NextResponse.json({ detail }, { status: 503, headers: failureHeaders });
    }
    const responseHeaders = new Headers(NO_STORE_HEADERS);
    responseHeaders.set("X-EDGEOS-Mode", upstream.headers.get("x-edgeos-mode")!);
    responseHeaders.set("X-EDGEOS-Mode-Contract", "1");
    const contentType = upstream.headers.get("content-type");
    if (contentType) responseHeaders.set("Content-Type", contentType);
    const contentDisposition = upstream.headers.get("content-disposition");
    if (contentDisposition) responseHeaders.set("Content-Disposition", contentDisposition);
    return new Response(upstream.body, {
      status: upstream.status,
      headers: responseHeaders,
    });
  } catch {
    return NextResponse.json({ detail: "The internal EDGEOS API is unavailable." }, {
      status: 502,
      headers: NO_STORE_HEADERS,
    });
  }
}

export const GET = forward;
export const POST = forward;
export const PATCH = forward;
export const PUT = forward;
export const DELETE = forward;
