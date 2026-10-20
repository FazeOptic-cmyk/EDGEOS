"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Check, Download, KeyRound, LoaderCircle, LogOut, PlugZap, Save, ShieldCheck, Trash2, Upload, UserRound } from "lucide-react";
import { createBrowserSupabaseClient } from "@/lib/supabase/client";
import { cn } from "@/lib/utils";
import { PageIntro } from "@/components/page-intro";
import { Button, Panel } from "@/components/ui";
import { ApiConnectionsPanel } from "@/components/settings/api-connections-panel";
import { collectAccountExport, type AccountExportClient } from "@/lib/account-export";
import {
  exportLocalWorkspaceBackup,
  getUserSettings,
  LOCAL_WORKSPACE_BACKUP_FILENAME,
  restoreLocalWorkspaceBackup,
  updateUserSettings,
} from "@/lib/workflow-api";

const inputClass = "focus-ring mt-2 h-11 w-full rounded-xl border border-[#293e5b] bg-[#0d1829] px-3 text-sm text-[#e7effa]";

export function SettingsClient({ configured, userId, email, appMode, internalApiConfigured, initialName = "", initialTimezone = "UTC", initialCapital = 1000, initialVerified = true }: {
  configured: boolean;
  userId?: string;
  email?: string;
  appMode: "demo" | "live";
  internalApiConfigured: boolean;
  initialName?: string;
  initialTimezone?: string;
  initialCapital?: number;
  initialVerified?: boolean;
}) {
  const router = useRouter();
  const [tab, setTab] = useState<"profile" | "preferences" | "connections" | "data">("profile");
  const [name, setName] = useState(initialName);
  const [timezone, setTimezone] = useState(initialTimezone);
  const [capitalOverride, setCapital] = useState<number | null>(null);
  const [verifiedOverride, setVerified] = useState<boolean | null>(null);
  const [status, setStatus] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState("");
  const [restoreFile, setRestoreFile] = useState<File | null>(null);
  const [confirmRestore, setConfirmRestore] = useState("");
  const queryClient = useQueryClient();
  const client = createBrowserSupabaseClient();
  const authenticated = Boolean(configured && userId && client);
  const desktopBuild = process.env.NEXT_PUBLIC_DESKTOP_BUILD === "true";
  const localWorkspace = appMode === "demo";
  const canSavePreferences = localWorkspace || authenticated;
  const localSettings = useQuery({
    queryKey: ["user-settings"],
    queryFn: getUserSettings,
    enabled: localWorkspace,
  });
  const capital = capitalOverride ?? (localSettings.data ? Number(localSettings.data.default_allocation) : initialCapital);
  const verified = verifiedOverride ?? localSettings.data?.verified_matches_only ?? initialVerified;

  const run = async (task: () => Promise<void>) => {
    setBusy(true); setStatus(""); setError("");
    try { await task(); }
    catch (value) { setError(value instanceof Error ? value.message : "Request failed"); }
    finally { setBusy(false); }
  };

  const saveProfile = () => run(async () => {
    if (!client || !userId) throw new Error("Sign in to save and sync profile data.");
    const { error: updateError } = await client.from("profiles").update({
      display_name: name || null,
      timezone,
    }).eq("id", userId);
    if (updateError) throw updateError;
    setStatus("Profile saved to your Supabase workspace.");
  });

  const savePreferences = () => run(async () => {
    if (!localWorkspace && (!client || !userId)) throw new Error("Sign in to save account preferences.");
    if (!Number.isFinite(capital) || capital < 0) throw new Error("Default simulated capital must be a non-negative number.");
    const updated = await updateUserSettings({
      default_allocation: String(capital),
      verified_matches_only: verified,
    });
    queryClient.setQueryData(["user-settings"], updated);
    setCapital(Number(updated.default_allocation));
    setVerified(updated.verified_matches_only);
    setStatus(localWorkspace
      ? desktopBuild
        ? "Preferences saved to this device's local EDGEOS workspace."
        : "Preferences saved to the current demonstration workspace."
      : "Preferences validated and saved to your account workspace.");
  });

  const exportData = () => run(async () => {
    if (!client || !userId) throw new Error("Sign in to export account data.");
    const output = await collectAccountExport(client as unknown as AccountExportClient, userId);
    const url = URL.createObjectURL(new Blob([JSON.stringify(output, null, 2)], { type: "application/json" }));
    const link = document.createElement("a"); link.href = url; link.download = "edgeos-account-export.json"; link.click();
    URL.revokeObjectURL(url);
    setStatus(desktopBuild
      ? "Complete account export prepared. Complete the native Save dialog to write the file."
      : "Complete account export download requested. Confirm the file appears in your browser downloads.");
  });

  const exportLocalBackup = () => run(async () => {
    const backup = await exportLocalWorkspaceBackup();
    const url = URL.createObjectURL(backup);
    const link = document.createElement("a");
    link.href = url;
    link.download = LOCAL_WORKSPACE_BACKUP_FILENAME;
    link.click();
    URL.revokeObjectURL(url);
    setStatus(desktopBuild
      ? "Device-local workspace backup prepared. Complete the native Save dialog to write the file."
      : "Device-local workspace backup download requested. Confirm the file appears in your browser downloads.");
  });

  const restoreLocalBackup = () => run(async () => {
    if (!restoreFile) throw new Error("Choose an EDGEOS backup first.");
    if (confirmRestore !== "RESTORE") throw new Error("Type RESTORE to confirm replacement.");
    await restoreLocalWorkspaceBackup(restoreFile);
    await queryClient.invalidateQueries();
    setRestoreFile(null);
    setConfirmRestore("");
    setStatus("Device-local workspace restored. Current views have been refreshed.");
    router.refresh();
  });

  const deleteAccount = () => run(async () => {
    if (confirmDelete !== "DELETE") throw new Error("Type DELETE to confirm.");
    if (!client) throw new Error("Supabase is not configured.");
    const response = await fetch("/api/account", { method: "DELETE", headers: { "Content-Type": "application/json" } });
    if (!response.ok) {
      const body = await response.json().catch(() => ({ detail: "Account deletion is unavailable." }));
      throw new Error(body.detail || "Account deletion failed");
    }
    await client.auth.signOut();
    router.replace("/login");
    router.refresh();
  });

  return <div className="space-y-5">
    <PageIntro badge="account & workspace" title="Settings" description={localWorkspace ? desktopBuild ? "Manage device-local research preferences, optional cloud identity, data portability, and workspace security." : "Manage demonstration-workspace preferences, optional cloud identity, data portability, and workspace security." : "Manage identity, account research preferences, data portability, and account lifecycle."} />
    {!configured && <div className="rounded-xl border border-[#634d29] bg-[#2c2413]/55 px-4 py-3 text-xs leading-5 text-[#d5b56b]">{localWorkspace&&desktopBuild?"Accounts are not configured. Add the Supabase public URL and publishable key to enable optional cloud identity; local research records remain device-local.":"Accounts are not configured. Add the Supabase public URL and publishable key to enable secure sign-in and account cloud sync."}</div>}
    {configured && !userId && <div className="flex flex-col gap-3 rounded-xl border border-[#335078] bg-[#15243c]/60 px-4 py-4 text-xs text-[#a9bddb] sm:flex-row sm:items-center sm:justify-between"><span>{localWorkspace?desktopBuild?"Sign in to manage the optional cloud profile. This installed workspace's research records and preferences remain device-local.":"Sign in to manage an optional cloud profile. Demonstration workspace data is not account-synced.":"Sign in to sync your profile and research preferences across devices."}</span><Link href="/login" className="inline-flex h-9 items-center justify-center rounded-lg bg-[#4f8cff] px-4 font-semibold text-white">Sign in</Link></div>}
    <div className="grid gap-5 xl:grid-cols-[230px_minmax(0,1fr)]">
      <Panel className="h-fit p-2">
        <nav className="space-y-1">{[
          { id: "profile", label: "Profile", icon: UserRound },
          { id: "preferences", label: "Preferences", icon: Save },
          { id: "connections", label: "API connections", icon: PlugZap },
          { id: "data", label: "Data & security", icon: ShieldCheck },
        ].map(({ id, label, icon: Icon }) => <button key={id} onClick={() => setTab(id as typeof tab)} className={cn("flex h-11 w-full items-center gap-3 rounded-xl px-3 text-sm font-medium transition", tab === id ? "bg-[#192b46] text-[#dce8f8]" : "text-[#788aa3] hover:bg-[#121f33]")}><Icon className={cn("size-[18px]", tab === id && "text-[#6ca5ff]")} />{label}</button>)}</nav>
        <div className="mt-2 border-t border-[#203149] p-2">
          <div className="rounded-xl bg-[#0d1829] p-3"><p className="truncate text-xs font-semibold text-[#cbd7e6]">{email || "Demo visitor"}</p><p className="mt-1 text-[9px] uppercase tracking-[.1em] text-[#60738c]">{authenticated ? "Authenticated" : "Not synced"}</p></div>
          {authenticated && <form action="/auth/signout" method="post"><button className="mt-2 flex h-9 w-full items-center gap-2 rounded-lg px-2 text-xs text-[#8193aa] hover:bg-[#17253a] hover:text-white"><LogOut className="size-4" /> Sign out</button></form>}
        </div>
      </Panel>
      <Panel className="p-5 sm:p-7">
        {status && <p role="status" className="mb-5 flex items-center gap-2 rounded-xl border border-[#285646] bg-[#123027]/60 p-3 text-xs text-[#72d6b5]"><Check className="size-4" />{status}</p>}
        {error && <p role="alert" className="mb-5 rounded-xl border border-[#623641] bg-[#321a22]/60 p-3 text-xs leading-5 text-[#ee91a0]">{error}</p>}
        {localSettings.error && <p role="alert" className="mb-5 rounded-xl border border-[#623641] bg-[#321a22]/60 p-3 text-xs leading-5 text-[#ee91a0]">Local preferences unavailable: {localSettings.error.message}. Displayed defaults have not been confirmed by the local store.</p>}
        {tab === "profile" && <section>
          <div className="flex items-center gap-3 border-b border-[#203149] pb-5"><span className="grid size-11 place-items-center rounded-xl bg-gradient-to-br from-[#345f9f] to-[#203557] font-bold text-white">{email?.slice(0, 2).toUpperCase() || "EO"}</span><div><p className="text-sm font-semibold text-[#e3ebf6]">Account profile</p><p className="mt-1 text-xs text-[#6f829b]">{authenticated ? "Stored in the RLS-protected profiles table" : "Cloud profile (sign-in required)"}</p></div></div>
          <div className="mt-6 grid gap-5 sm:grid-cols-2"><label className="text-xs text-[#8294ab]">Display name<input value={name} onChange={(event) => setName(event.target.value)} className={inputClass} placeholder="Your name" /></label><label className="text-xs text-[#8294ab]">Email<input value={email || "Not signed in"} readOnly className={`${inputClass} opacity-60`} /></label><label className="text-xs text-[#8294ab]">Display timezone<select value={timezone} onChange={(event) => setTimezone(event.target.value)} className={inputClass}><option>UTC</option><option>America/New_York</option><option>America/Chicago</option><option>America/Denver</option><option>America/Los_Angeles</option><option>Europe/London</option></select></label></div>
          <Button onClick={saveProfile} disabled={busy || !authenticated} className="mt-6 border-[#4f8cff] bg-[#4f8cff] text-white">{busy ? <LoaderCircle className="size-4 animate-spin" /> : <Save className="size-4" />} Save profile</Button>
        </section>}
        {tab === "preferences" && <section>
          <p className="text-sm font-semibold text-[#e3ebf6]">Research defaults</p><p className="mt-1 text-xs text-[#6f829b]">{localWorkspace ? desktopBuild ? "Persisted by the device-local EDGEOS private-data API" : "Stored in the current demonstration API workspace" : "Applied to new scanner sessions and simulations"}</p>
          <label className="mt-6 block text-xs text-[#8294ab]">Default simulated capital<input type="number" min="0" value={capital} onChange={(event) => setCapital(Number(event.target.value))} className={inputClass} /></label>
          <label className="mt-5 flex items-center gap-3 rounded-xl border border-[#243954] bg-[#0e192a] p-4"><input type="checkbox" checked={verified} onChange={(event) => setVerified(event.target.checked)} className="size-4 accent-[#4f8cff]" /><span><span className="block text-xs font-semibold text-[#d6e1ef]">Verified matches only</span><span className="mt-1 block text-[10px] text-[#6d8098]">{localWorkspace ? desktopBuild ? "Saved in device-local private settings" : "Saved in the current demo workspace" : "Stored in account research preferences"}</span></span></label>
          <Button onClick={savePreferences} disabled={busy || !canSavePreferences || localSettings.isPending} className="mt-6 border-[#57b9f5] bg-[#57b9f5] text-[#08131d] hover:bg-[#76cdff]"><Save className="size-4" /> {localWorkspace ? "Save local preferences" : "Save preferences"}</Button>
        </section>}
        {tab === "connections" && <ApiConnectionsPanel
          available={authenticated && appMode === "live" && internalApiConfigured}
          unavailableReason={appMode !== "live"
            ? "Switch both the hosted web application and FastAPI service to live mode. Demo mode never reads or saves provider credentials."
            : !configured || !userId
              ? "Configure Supabase and sign in so every connection is owned and authorized by your account."
              : !internalApiConfigured
                ? "Set the server-only EDGEOS_INTERNAL_API_ORIGIN so the authenticated web route can reach FastAPI."
                : undefined}
        />}
        {tab === "data" && <section>
          <p className="text-sm font-semibold text-[#e3ebf6]">Data & security</p><p className="mt-1 text-xs text-[#6f829b]">Portable backup, export, and irreversible account actions</p>
          <div className="mt-6 space-y-4">
            {localWorkspace && desktopBuild && <div className="rounded-xl border border-[#31506f] bg-[#0e192a] p-4"><div className="flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between"><div><p className="text-xs font-semibold text-[#d8e3f1]">Device-local workspace backup</p><p className="mt-1 max-w-2xl text-[10px] leading-4 text-[#7890aa]">Exports paper positions and fills, saved opportunities, watchlists, alert rules, and local preferences. Provider credentials, cloud sessions, and signing secrets are not stored in this workspace. The SHA-256 integrity check detects damage; the file is not encrypted or authenticated.</p></div><Button onClick={exportLocalBackup} disabled={busy}><Download className="size-4" /> Download backup</Button></div><div className="mt-4 border-t border-[#253a57] pt-4"><p className="text-[10px] font-semibold uppercase tracking-[.1em] text-[#f5bc69]">Restore replaces current local records</p><div className="mt-3 grid gap-3 lg:grid-cols-[minmax(0,1fr)_180px_auto]"><label className="text-[10px] text-[#8294ab]">EDGEOS backup<input type="file" accept=".edgeos-backup,application/vnd.edgeos.workspace-backup+json" onChange={(event) => setRestoreFile(event.target.files?.[0] ?? null)} className="focus-ring mt-1 block h-10 w-full rounded-xl border border-[#293e5b] bg-[#0d1829] px-3 py-2 text-[10px] text-[#aebdd0] file:mr-3 file:border-0 file:bg-transparent file:text-[#76cdff]" /></label><label className="text-[10px] text-[#8294ab]">Type RESTORE<input value={confirmRestore} onChange={(event) => setConfirmRestore(event.target.value)} className="focus-ring mt-1 h-10 w-full rounded-xl border border-[#5b4331] bg-[#1c1813] px-3 text-xs text-[#f1ddc1]" /></label><Button onClick={restoreLocalBackup} disabled={busy || !restoreFile || confirmRestore !== "RESTORE"} className="self-end border-[#714f2e] bg-[#312316] text-[#f5bc69]"><Upload className="size-4" /> Restore</Button></div></div></div>}
            <div className="flex flex-col gap-4 rounded-xl border border-[#253a57] bg-[#0e192a] p-4 sm:flex-row sm:items-center sm:justify-between"><div><p className="text-xs font-semibold text-[#d8e3f1]">Export account data</p><p className="mt-1 text-[10px] text-[#6c7f98]">Downloads all accessible RLS-scoped rows as paginated JSON. Avoid editing the account until the export finishes; no file is downloaded if any table fails.</p></div><Button onClick={exportData} disabled={busy || !authenticated}><Download className="size-4" /> Export JSON</Button></div>
            <div className="rounded-xl border border-[#5b3340] bg-[#281820]/35 p-4"><div className="flex items-start gap-3"><Trash2 className="mt-0.5 size-5 text-[#ff7183]" /><div><p className="text-xs font-semibold text-[#edcbd1]">Delete account</p><p className="mt-1 text-[10px] leading-4 text-[#9e7c84]">Uses an authenticated server-only admin route to delete your own Auth user and cascading private records. This cannot be undone.</p></div></div><div className="mt-4 flex flex-col gap-2 sm:flex-row"><input value={confirmDelete} onChange={(event) => setConfirmDelete(event.target.value)} placeholder="Type DELETE" className="focus-ring h-10 flex-1 rounded-xl border border-[#583440] bg-[#1b1217] px-3 text-xs text-[#efdce0]" /><Button onClick={deleteAccount} disabled={busy || !authenticated || confirmDelete !== "DELETE"} className="border-[#653442] bg-[#3a1d27] text-[#ff97a5]"><Trash2 className="size-4" /> Delete account</Button></div></div>
            <div className="flex gap-3 rounded-xl border border-[#263b58] bg-[#0e192a] p-4"><KeyRound className="mt-0.5 size-5 text-[#79aaf4]" /><div><p className="text-xs font-semibold text-[#d8e3f1]">Session security</p><p className="mt-1 text-[10px] leading-4 text-[#6c7f98]">Server requests validate signed claims. Secret and service-role keys are never included in the browser bundle.</p></div></div>
          </div>
        </section>}
      </Panel>
    </div>
  </div>;
}
