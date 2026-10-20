import type { EmailOtpType } from "@supabase/supabase-js";
import { NextResponse, type NextRequest } from "next/server";
import { safeRedirectPath } from "@/lib/safe-redirect";
import { createServerSupabaseClient } from "@/lib/supabase/server";

export async function GET(request: NextRequest) {
  const url = new URL(request.url); const tokenHash = url.searchParams.get("token_hash"); const type = url.searchParams.get("type") as EmailOtpType | null; const next = safeRedirectPath(url.searchParams.get("next"));
  const supabase = await createServerSupabaseClient();
  if (!supabase) return NextResponse.redirect(new URL("/login?error=configuration", url.origin));
  if (tokenHash && type) { const { error } = await supabase.auth.verifyOtp({ type, token_hash: tokenHash }); if (!error) return NextResponse.redirect(new URL(next, url.origin)); }
  return NextResponse.redirect(new URL("/login?error=confirmation", url.origin));
}
