import { createServerClient } from "@supabase/ssr";
import { NextResponse, type NextRequest } from "next/server";
import { isSupabaseConfigured, supabasePublishableKey, supabaseUrl } from "./config";

export async function refreshSupabaseSession(request: NextRequest) {
  let response = NextResponse.next({ request });
  if (!isSupabaseConfigured || !supabaseUrl || !supabasePublishableKey) return response;
  const supabase = createServerClient(supabaseUrl, supabasePublishableKey, {
    cookies: {
      getAll: () => request.cookies.getAll(),
      setAll: (cookiesToSet) => {
        cookiesToSet.forEach(({ name, value }) => request.cookies.set(name, value));
        response = NextResponse.next({ request });
        cookiesToSet.forEach(({ name, value, options }) => response.cookies.set(name, value, options));
      },
    },
  });
  // getClaims validates the JWT; never trust an unverified server getSession result.
  await supabase.auth.getClaims();
  return response;
}
