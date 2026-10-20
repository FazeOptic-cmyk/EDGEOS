import { NextResponse, type NextRequest } from "next/server";
import { saveProviderConnectionSchema } from "@/lib/provider-connections";
import { forwardProviderConnectionRequest, parseBoundedRequestJson } from "@/lib/provider-connections-server";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

const NO_STORE_HEADERS = { "Cache-Control": "no-store" };

export async function GET(request: NextRequest) {
  return forwardProviderConnectionRequest({
    request,
    path: ["api", "v1", "provider-connections"],
    method: "GET",
    responseKind: "list",
  });
}

export async function POST(request: NextRequest) {
  let raw: unknown;
  try { raw = await parseBoundedRequestJson(request); }
  catch { return NextResponse.json({ error_code: "invalid_request" }, { status: 400, headers: NO_STORE_HEADERS }); }
  const parsed = saveProviderConnectionSchema.safeParse(raw);
  if (!parsed.success) {
    return NextResponse.json({ error_code: "invalid_request" }, { status: 422, headers: NO_STORE_HEADERS });
  }
  return forwardProviderConnectionRequest({
    request,
    path: ["api", "v1", "provider-connections"],
    method: "POST",
    body: parsed.data,
    responseKind: "item",
  });
}
