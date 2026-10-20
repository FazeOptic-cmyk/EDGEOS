import { NextResponse } from "next/server";
import { hasExpectedDesktopHost } from "@/lib/edgeos-proxy";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

export function GET(request: Request) {
  if (process.env.EDGEOS_ENV !== "desktop") {
    return NextResponse.json({ detail: "Not found." }, {
      status: 404,
      headers: { "Cache-Control": "no-store" },
    });
  }

  if (!hasExpectedDesktopHost(request)) {
    return NextResponse.json({ detail: "Desktop host rejected." }, {
      status: 403,
      headers: { "Cache-Control": "no-store" },
    });
  }

  const instanceNonce = process.env.EDGEOS_DESKTOP_INSTANCE_NONCE;
  if (!instanceNonce || instanceNonce.length < 32) {
    return NextResponse.json({ detail: "Desktop instance identity is unavailable." }, {
      status: 503,
      headers: { "Cache-Control": "no-store" },
    });
  }

  return NextResponse.json({
    status: "ok",
    service: "edgeos-web",
    instance_nonce: instanceNonce,
  }, {
    headers: { "Cache-Control": "no-store" },
  });
}
