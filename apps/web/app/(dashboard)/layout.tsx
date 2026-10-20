import { AppShell } from "@/components/app-shell";
import { redirect } from "next/navigation";
import { isSupabaseConfigured } from "@/lib/supabase/config";
import { createServerSupabaseClient } from "@/lib/supabase/server";
import { RuntimeModeGuard } from "@/components/runtime-mode-guard";

export default async function DashboardLayout({ children }: { children: React.ReactNode }) {
  if (process.env.NEXT_PUBLIC_APP_MODE === "live" && isSupabaseConfigured) {
    const client = await createServerSupabaseClient();
    const { data, error } = await client!.auth.getClaims();
    if (error || typeof data?.claims?.sub !== "string") redirect("/login");
  }
  return <AppShell><RuntimeModeGuard>{children}</RuntimeModeGuard></AppShell>;
}
