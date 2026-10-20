import { createClient } from "@supabase/supabase-js";
import { NextResponse, type NextRequest } from "next/server";
import { createServerSupabaseClient } from "@/lib/supabase/server";
import { supabaseUrl } from "@/lib/supabase/config";
import { isSameOriginBrowserRequest } from "@/lib/edgeos-proxy";

export async function DELETE(request: NextRequest) {
  const origin = request.headers.get("origin");
  if (origin && !isSameOriginBrowserRequest(request)) return NextResponse.json({ detail: "Origin rejected." }, { status: 403 });
  const sessionClient = await createServerSupabaseClient();
  if (!sessionClient) return NextResponse.json({ detail: "Supabase is not configured." }, { status: 503 });
  const { data, error: claimsError } = await sessionClient.auth.getClaims();
  const claims = data?.claims;
  const subject = claims?.sub;
  if (claimsError || !claims || typeof subject !== "string") return NextResponse.json({ detail: "Authentication required." }, { status: 401 });
  const allowedMethods = new Set(["password", "otp", "oauth", "sso/saml", "totp", "webauthn"]);
  const amr = (claims as Record<string, unknown>).amr;
  const authenticationTimes = Array.isArray(amr) ? amr.flatMap((entry) => {
    if (!entry || typeof entry !== "object") return [];
    const method = (entry as Record<string, unknown>).method;
    const timestamp = (entry as Record<string, unknown>).timestamp;
    return typeof method === "string" && allowedMethods.has(method) && typeof timestamp === "number"
      ? [timestamp]
      : [];
  }) : [];
  const authenticatedAt = authenticationTimes.length ? Math.max(...authenticationTimes) : null;
  const now = Math.floor(Date.now() / 1000);
  if (authenticatedAt === null || authenticatedAt > now + 60 || now - authenticatedAt > 15 * 60) {
    return NextResponse.json({ detail: "Recent authentication is required. Sign out, sign in again, and retry within 15 minutes." }, { status: 403 });
  }
  const secret = process.env.SUPABASE_SECRET_KEY;
  if (!supabaseUrl || !secret) return NextResponse.json({ detail: "Account deletion needs the server-only SUPABASE_SECRET_KEY." }, { status: 503 });
  const admin = createClient(supabaseUrl, secret, { auth: { autoRefreshToken: false, persistSession: false } });
  const { data: sessionData } = await sessionClient.auth.getSession();
  const accessToken = sessionData.session?.access_token;
  if (!accessToken) return NextResponse.json({ detail: "Authenticated session is unavailable." }, { status: 401 });
  const { error: revokeError } = await admin.auth.admin.signOut(accessToken, "global");
  if (revokeError) return NextResponse.json({ detail: "Account sessions could not be revoked." }, { status: 500 });
  const { error } = await admin.auth.admin.deleteUser(subject);
  if (error) return NextResponse.json({ detail: "Account deletion failed." }, { status: 500 });
  return NextResponse.json({ deleted: true });
}
