import { NextResponse, type NextRequest } from "next/server";
import { safeRedirectPath } from "@/lib/safe-redirect";
import { createServerSupabaseClient } from "@/lib/supabase/server";

export async function GET(request: NextRequest) {
  const url = new URL(request.url); const code = url.searchParams.get("code"); const next = safeRedirectPath(url.searchParams.get("next"));
  const supabase = await createServerSupabaseClient();
  if (!supabase) return NextResponse.redirect(new URL("/login?error=configuration", url.origin));
  if (code) { const { error } = await supabase.auth.exchangeCodeForSession(code); if (!error) return NextResponse.redirect(new URL(next, url.origin)); }
  return NextResponse.redirect(new URL("/login?error=callback", url.origin));
}
