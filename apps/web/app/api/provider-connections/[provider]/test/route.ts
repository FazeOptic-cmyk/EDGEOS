import { NextResponse, type NextRequest } from "next/server";
import { providerCodeSchema } from "@/lib/provider-connections";
import { forwardProviderConnectionRequest } from "@/lib/provider-connections-server";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

const NO_STORE_HEADERS = { "Cache-Control": "no-store" };

export async function POST(request: NextRequest, context: { params: Promise<{ provider: string }> }) {
  const provider = providerCodeSchema.safeParse((await context.params).provider);
  if (!provider.success) return NextResponse.json({ error_code: "invalid_request" }, { status: 404, headers: NO_STORE_HEADERS });
  return forwardProviderConnectionRequest({
    request,
    path: ["api", "v1", "provider-connections", provider.data, "test"],
    method: "POST",
    responseKind: "item",
  });
}
