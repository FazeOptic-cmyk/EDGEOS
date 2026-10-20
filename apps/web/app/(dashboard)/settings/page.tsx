import type { Metadata } from "next";
import { SettingsClient } from "@/components/settings/settings-client";
import { isSupabaseConfigured } from "@/lib/supabase/config";
import { createServerSupabaseClient } from "@/lib/supabase/server";

export const metadata: Metadata = { title: "Settings" };

export default async function SettingsPage() {
  const client = await createServerSupabaseClient();
  let userId: string | undefined;
  let email: string | undefined;
  let initialName = "";
  let initialTimezone = "UTC";
  let initialCapital = 1000;
  let initialVerified = true;

  if (client) {
    const { data } = await client.auth.getClaims();
    const claims = data?.claims as Record<string, unknown> | undefined;
    userId = typeof claims?.sub === "string" ? claims.sub : undefined;
    email = typeof claims?.email === "string" ? claims.email : undefined;
    if (userId) {
      const [profileResult, settingsResult] = await Promise.all([
        client.from("profiles").select("display_name,timezone").eq("id", userId).maybeSingle(),
        client.from("user_settings").select("default_capital,analytics_preferences").eq("user_id", userId).maybeSingle(),
      ]);
      initialName = profileResult.data?.display_name ?? "";
      initialTimezone = profileResult.data?.timezone ?? "UTC";
      initialCapital = Number(settingsResult.data?.default_capital ?? 1000);
      const preferences = settingsResult.data?.analytics_preferences;
      if (preferences && typeof preferences === "object" && "verified_matches_only" in preferences) {
        initialVerified = Boolean(preferences.verified_matches_only);
      }
    }
  }

  return (
    <SettingsClient
      configured={isSupabaseConfigured}
      userId={userId}
      email={email}
      appMode={process.env.NEXT_PUBLIC_APP_MODE === "live" ? "live" : "demo"}
      internalApiConfigured={Boolean(process.env.EDGEOS_INTERNAL_API_ORIGIN)}
      initialName={initialName}
      initialTimezone={initialTimezone}
      initialCapital={initialCapital}
      initialVerified={initialVerified}
    />
  );
}
