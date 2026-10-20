import { NextResponse, type NextRequest } from "next/server";
import { providerCodeSchema, updateProviderConnectionSchema } from "@/lib/provider-connections";
import { forwardProviderConnectionRequest, parseBoundedRequestJson } from "@/lib/provider-connections-server";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

const NO_STORE_HEADERS = { "Cache-Control": "no-store" };

async function providerFrom(context: { params: Promise<{ provider: string }> }) {
  return providerCodeSchema.safeParse((await context.params).provider);
}

export async function PATCH(request: NextRequest, context: { params: Promise<{ provider: string }> }) {
  const provider = await providerFrom(context);
  if (!provider.success) return NextResponse.json({ error_code: "invalid_request" }, { status: 404, headers: NO_STORE_HEADERS });
  let raw: unknown;
  try { raw = await parseBoundedRequestJson(request); }
  catch { return NextResponse.json({ error_code: "invalid_request" }, { status: 400, headers: NO_STORE_HEADERS }); }
  const parsed = updateProviderConnectionSchema.safeParse(raw);
  if (!parsed.success) return NextResponse.json({ error_code: "invalid_request" }, { status: 422, headers: NO_STORE_HEADERS });
  return forwardProviderConnectionRequest({
    request,
    path: ["api", "v1", "provider-connections", provider.data],
    method: "PATCH",
    body: parsed.data,
    responseKind: "item",
  });
}

export async function DELETE(request: NextRequest, context: { params: Promise<{ provider: string }> }) {
  const provider = await providerFrom(context);
  if (!provider.success) return NextResponse.json({ error_code: "invalid_request" }, { status: 404, headers: NO_STORE_HEADERS });
  return forwardProviderConnectionRequest({
    request,
    path: ["api", "v1", "provider-connections", provider.data],
    method: "DELETE",
    responseKind: "delete",
  });
}
