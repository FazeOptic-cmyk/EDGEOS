import "server-only";

import { NextResponse, type NextRequest } from "next/server";
import {
  providerConnectionListSchema,
  providerConnectionResponseSchema,
} from "@/lib/provider-connections";
import {
  buildEdgeosProxyUrl,
  EdgeosProxyConfigurationError,
  hasExpectedDesktopHost,
  isSameOriginBrowserRequest,
} from "@/lib/edgeos-proxy";
import { createServerSupabaseClient } from "@/lib/supabase/server";

const NO_STORE_HEADERS = { "Cache-Control": "no-store" };
const MUTATION_METHODS = new Set(["POST", "PATCH", "DELETE"]);
const MAX_REQUEST_BYTES = 24_576;
const MAX_RESPONSE_BYTES = 262_144;

type ResponseKind = "list" | "item" | "delete";

function jsonError(status: number, errorCode: string) {
  return NextResponse.json({ error_code: errorCode }, { status, headers: NO_STORE_HEADERS });
}

function fallbackErrorCode(status: number): string {
  if (status === 400 || status === 422) return "invalid_request";
  if (status === 401) return "authentication_required";
  if (status === 403 || status === 404) return "forbidden";
  if (status === 409) return "mode_mismatch";
  if (status === 429) return "rate_limited";
  if (status === 502 || status === 504) return "provider_unavailable";
  return "api_unavailable";
}

function extractSafeErrorCode(value: unknown): string | null {
  if (!value || typeof value !== "object") return null;
  const record = value as Record<string, unknown>;
  const detail = record.detail && typeof record.detail === "object"
    ? record.detail as Record<string, unknown>
    : null;
  const candidate = record.error_code ?? record.code ?? detail?.error_code ?? detail?.code;
  return typeof candidate === "string" && /^[a-z][a-z0-9_]{1,79}$/u.test(candidate)
    ? candidate
    : null;
}

async function readBoundedJson(response: Response): Promise<unknown> {
  const text = await response.text();
  if (text.length > MAX_RESPONSE_BYTES) throw new TypeError("Response exceeded the safe size limit");
  return text ? JSON.parse(text) as unknown : null;
}

function mutationHasExpectedOrigin(request: NextRequest): boolean {
  if (!MUTATION_METHODS.has(request.method)) return true;
  return Boolean(request.headers.get("origin")) && isSameOriginBrowserRequest(request);
}

async function bearerToken(): Promise<string | null> {
  const client = await createServerSupabaseClient();
  if (!client) return null;
  const { data: claimsData, error: claimsError } = await client.auth.getClaims();
  if (claimsError || typeof claimsData?.claims?.sub !== "string") return null;
  const { data: sessionData } = await client.auth.getSession();
  return sessionData.session?.access_token ?? null;
}

async function upstreamIsLive(authorization: string): Promise<boolean> {
  const healthUrl = buildEdgeosProxyUrl(["api", "v1", "health"], "");
  const health = await fetch(healthUrl, {
    method: "GET",
    headers: { Accept: "application/json", Authorization: authorization },
    cache: "no-store",
    redirect: "error",
    signal: AbortSignal.timeout(5_000),
  });
  if (!health.ok) return false;
  const value = await readBoundedJson(health);
  return Boolean(value && typeof value === "object" && (value as Record<string, unknown>).mode === "live");
}

export async function forwardProviderConnectionRequest({
  request,
  path,
  method,
  body,
  responseKind,
}: {
  request: NextRequest;
  path: readonly string[];
  method: "GET" | "POST" | "PATCH" | "DELETE";
  body?: unknown;
  responseKind: ResponseKind;
}) {
  if (!hasExpectedDesktopHost(request) || !isSameOriginBrowserRequest(request) || !mutationHasExpectedOrigin(request)) {
    return jsonError(403, "forbidden");
  }
  if (process.env.NEXT_PUBLIC_APP_MODE !== "live") return jsonError(409, "live_mode_required");

  const contentLength = Number(request.headers.get("content-length") ?? "0");
  if (!Number.isFinite(contentLength) || contentLength > MAX_REQUEST_BYTES) return jsonError(413, "invalid_request");

  const token = await bearerToken();
  if (!token) return jsonError(401, "authentication_required");
  const authorization = `Bearer ${token}`;

  let target: URL;
  try {
    if (!await upstreamIsLive(authorization)) return jsonError(409, "mode_mismatch");
    target = buildEdgeosProxyUrl(path, "");
  } catch (error) {
    if (error instanceof EdgeosProxyConfigurationError) return jsonError(503, "api_unavailable");
    return jsonError(502, "api_unavailable");
  }

  const headers = new Headers({ Accept: "application/json", Authorization: authorization });
  const init: RequestInit = {
    method,
    headers,
    cache: "no-store",
    redirect: "error",
    signal: AbortSignal.timeout(method === "POST" ? 20_000 : 15_000),
  };
  if (body !== undefined) {
    headers.set("Content-Type", "application/json");
    init.body = JSON.stringify(body);
  }

  try {
    const upstream = await fetch(target, init);
    if (!upstream.ok) {
      let errorCode: string | null = null;
      try { errorCode = extractSafeErrorCode(await readBoundedJson(upstream)); }
      catch { /* Never forward arbitrary upstream error content. */ }
      return jsonError(upstream.status, errorCode ?? fallbackErrorCode(upstream.status));
    }
    if (responseKind === "delete") return new Response(null, { status: 204, headers: NO_STORE_HEADERS });
    const raw = await readBoundedJson(upstream);
    const parsed = responseKind === "list"
      ? providerConnectionListSchema.safeParse(raw)
      : providerConnectionResponseSchema.safeParse(raw);
    if (!parsed.success) return jsonError(502, "invalid_upstream_response");
    return NextResponse.json(parsed.data, { headers: NO_STORE_HEADERS });
  } catch {
    return jsonError(502, "api_unavailable");
  }
}

export async function parseBoundedRequestJson(request: NextRequest): Promise<unknown> {
  const contentType = request.headers.get("content-type")?.split(";", 1)[0]?.trim().toLowerCase();
  if (contentType !== "application/json") throw new TypeError("JSON content type required");
  const contentLength = Number(request.headers.get("content-length") ?? "0");
  if (!Number.isFinite(contentLength) || contentLength > MAX_REQUEST_BYTES) throw new TypeError("Request too large");
  return request.json();
}
