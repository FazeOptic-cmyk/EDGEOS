"use client";

import { useCallback, useEffect, useMemo, useRef, useState, type FormEvent, type KeyboardEvent as ReactKeyboardEvent } from "react";
import {
  AlertTriangle,
  Check,
  CircleOff,
  Gauge,
  KeyRound,
  LoaderCircle,
  PlugZap,
  RefreshCw,
  Settings2,
  ShieldCheck,
  Trash2,
  X,
} from "lucide-react";
import {
  connectionErrorMessage,
  providerConnectionListSchema,
  providerConnectionResponseSchema,
  providerDescriptors,
  type ProviderCode,
  type ProviderConnection,
  type SaveProviderConnection,
} from "@/lib/provider-connections";
import { Badge, Button, StatusDot } from "@/components/ui";
import { cn } from "@/lib/utils";

const inputClass = "focus-ring mt-2 h-11 w-full rounded-xl border border-[#293e5b] bg-[#0d1829] px-3 text-sm text-[#e7effa] placeholder:text-[#536881]";

type Descriptor = (typeof providerDescriptors)[number];

async function safeJson(response: Response): Promise<unknown> {
  return response.json().catch(() => null);
}

class ProviderConnectionRequestError extends Error {
  constructor(readonly code: string | null) {
    super(connectionErrorMessage(code));
    this.name = "ProviderConnectionRequestError";
  }
}

async function request(path: string, init?: RequestInit): Promise<unknown> {
  const response = await fetch(path, {
    ...init,
    cache: "no-store",
    headers: init?.body ? { "Content-Type": "application/json", ...init.headers } : init?.headers,
  });
  const body = response.status === 204 ? null : await safeJson(response);
  if (!response.ok) {
    const errorCode = body && typeof body === "object" && "error_code" in body
      ? String((body as { error_code: unknown }).error_code)
      : null;
    throw new ProviderConnectionRequestError(errorCode);
  }
  return body;
}

function formatTimestamp(value: string | null): string {
  if (!value) return "Never";
  const timestamp = new Date(value);
  return Number.isNaN(timestamp.getTime()) ? "Unknown" : timestamp.toLocaleString();
}

function statusPresentation(status: ProviderConnection["status"] | "not_configured") {
  if (status === "connected") return { label: "Connected", tone: "green" as const };
  if (status === "ready") return { label: "Ready to test", tone: "blue" as const };
  if (status === "disabled") return { label: "Disabled", tone: "gray" as const };
  if (status === "rate_limited") return { label: "Rate limited", tone: "yellow" as const };
  if (status === "testing") return { label: "Testing", tone: "blue" as const };
  if (status === "not_configured") return { label: "Not configured", tone: "gray" as const };
  return { label: status.split("_").map((part) => `${part[0]?.toUpperCase() ?? ""}${part.slice(1)}`).join(" "), tone: "red" as const };
}

function commaList(value: string): string[] {
  return [...new Set(value.split(",").map((item) => item.trim()).filter(Boolean))];
}

function trapTab(event: ReactKeyboardEvent<HTMLElement>) {
  if (event.key !== "Tab") return;
  const elements = Array.from(event.currentTarget.querySelectorAll<HTMLElement>(
    "button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex='-1'])",
  ));
  if (!elements.length) return;
  const first = elements[0];
  const last = elements[elements.length - 1];
  if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
  else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
}

function ConnectionDialog({ descriptor, connection, busy, onClose, onSave }: {
  descriptor: Descriptor;
  connection?: ProviderConnection;
  busy: boolean;
  onClose: () => void;
  onSave: (input: SaveProviderConnection) => Promise<void>;
}) {
  const previousFocus = useRef<HTMLElement | null>(null);
  const firstInput = useRef<HTMLElement | null>(null);
  const [credential, setCredential] = useState("");
  const [sports, setSports] = useState((connection?.sports ?? descriptor.defaultSports).join(", "));
  const [regions, setRegions] = useState((connection?.regions ?? descriptor.defaultRegions).join(", "));
  const [markets, setMarkets] = useState((connection?.markets ?? descriptor.defaultMarkets).join(", "));
  const [pollSeconds, setPollSeconds] = useState(connection?.poll_interval_seconds ?? descriptor.defaultPollSeconds);
  const [budget, setBudget] = useState(connection?.request_budget_per_hour ?? descriptor.defaultBudget);
  const [enabled, setEnabled] = useState(connection?.enabled ?? true);
  const [formError, setFormError] = useState("");

  useEffect(() => {
    previousFocus.current = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    queueMicrotask(() => firstInput.current?.focus());
    return () => {
      document.body.style.overflow = previousOverflow;
      previousFocus.current?.focus();
    };
  }, []);

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    setFormError("");
    if (descriptor.credentialLabel && !credential && !connection?.credential_configured) {
      setFormError("Enter the API key before saving this connection.");
      return;
    }
    const credentials: Record<string, string> = {};
    if (descriptor.credentialLabel && credential) credentials.api_key = credential;
    try {
      await onSave({
        provider_code: descriptor.code,
        credentials,
        enabled,
        sports: commaList(sports),
        regions: commaList(regions),
        markets: commaList(markets),
        poll_interval_seconds: pollSeconds,
        request_budget_per_hour: budget,
      });
    } catch (error) {
      setCredential("");
      setFormError(error instanceof Error ? error.message : "The connection could not be saved.");
    }
  };

  return <div className="fixed inset-0 z-[80] grid place-items-center bg-[#030812]/80 p-3 backdrop-blur-sm" onMouseDown={(event) => event.target === event.currentTarget && !busy && onClose()}>
    <section
      role="dialog"
      aria-modal="true"
      aria-labelledby="connection-dialog-title"
      aria-describedby="connection-dialog-description"
      onKeyDown={(event) => {
        if (event.key === "Escape" && !busy) { event.preventDefault(); onClose(); return; }
        trapTab(event);
      }}
      className="max-h-[92vh] w-full max-w-2xl overflow-y-auto rounded-2xl border border-[#2a4263] bg-[#0d1829] shadow-[0_24px_90px_rgba(0,0,0,.65)]"
    >
      <header className="sticky top-0 z-10 flex items-start justify-between gap-4 border-b border-[#203149] bg-[#0d1829]/95 px-5 py-4 backdrop-blur">
        <div><p className="text-[10px] font-semibold uppercase tracking-[.14em] text-[#5f91d9]">Secure provider setup</p><h2 id="connection-dialog-title" className="mt-1 text-lg font-bold text-[#eff5ff]">{connection ? "Update" : "Configure"} {descriptor.name}</h2><p id="connection-dialog-description" className="mt-1 text-xs leading-5 text-[#7f92ab]">Credentials are write-only and sent directly to the authenticated server route. They are never returned to this screen.</p></div>
        <button aria-label="Close provider setup" onClick={onClose} disabled={busy} className="focus-ring grid size-9 shrink-0 place-items-center rounded-xl border border-[#293d59] text-[#91a2b8] hover:text-white disabled:opacity-50"><X className="size-4" /></button>
      </header>
      <form onSubmit={submit} className="space-y-5 p-5">
        {formError && <p role="alert" className="rounded-xl border border-[#623641] bg-[#321a22]/60 p-3 text-xs leading-5 text-[#ee91a0]">{formError}</p>}
        {descriptor.credentialLabel ? <label htmlFor="provider-credential" className="block text-xs font-medium text-[#9aabc0]">{descriptor.credentialLabel}
          <input id="provider-credential" aria-label={descriptor.credentialLabel} ref={(node) => { firstInput.current = node; }} value={credential} onChange={(event) => setCredential(event.target.value)} autoComplete="off" spellCheck={false} type="password" className={inputClass} placeholder={connection?.credential_configured ? "Leave blank to keep the saved key" : "Paste the key here"} />
          <span className="mt-2 block text-[10px] leading-4 text-[#647891]">{connection?.credential_configured ? `Saved credential: ${connection.credential_mask ?? "masked"}. Entering a new value replaces it.` : "The key is encrypted by the API before database storage."}</span>
        </label> : <div ref={(node) => { firstInput.current = node; }} tabIndex={-1} className="rounded-xl border border-[#285646] bg-[#123027]/45 p-4"><p className="flex items-center gap-2 text-xs font-semibold text-[#79d8b8]"><ShieldCheck className="size-4" /> No credential required</p><p className="mt-1 text-[10px] leading-4 text-[#79a99a]">EDGEOS uses only the provider&apos;s documented public read-only market-data interface.</p></div>}

        <div className="grid gap-4 sm:grid-cols-2">
          <label className="text-xs font-medium text-[#9aabc0] sm:col-span-2">Sports or provider categories <input value={sports} onChange={(event) => setSports(event.target.value)} className={inputClass} placeholder="basketball_nba, americanfootball_nfl" /><span className="mt-1.5 block text-[10px] text-[#61758e]">Comma-separated official provider identifiers. Empty means no sport-specific polling.</span></label>
          <label className="text-xs font-medium text-[#9aabc0]">Regions <input value={regions} onChange={(event) => setRegions(event.target.value)} className={inputClass} placeholder="us" /></label>
          <label className="text-xs font-medium text-[#9aabc0]">Markets <input value={markets} onChange={(event) => setMarkets(event.target.value)} className={inputClass} placeholder="h2h" /></label>
          <label className="text-xs font-medium text-[#9aabc0]">Polling interval (seconds) <input type="number" min={15} max={86400} value={pollSeconds} onChange={(event) => setPollSeconds(Number(event.target.value))} className={inputClass} /></label>
          <label className="text-xs font-medium text-[#9aabc0]">Request budget per hour <input type="number" min={1} max={100000} value={budget} onChange={(event) => setBudget(Number(event.target.value))} className={inputClass} /></label>
        </div>
        <label className="flex items-center gap-3 rounded-xl border border-[#263b58] bg-[#101d30] p-4"><input type="checkbox" checked={enabled} onChange={(event) => setEnabled(event.target.checked)} className="size-4 accent-[#4f8cff]" /><span><span className="block text-xs font-semibold text-[#d9e4f2]">Enable saved connection</span><span className="mt-1 block text-[10px] text-[#6b7f98]">Saves this polling preference and budget. The current ingestion worker does not yet consume per-user schedules.</span></span></label>
        <div className="flex flex-col-reverse gap-2 border-t border-[#203149] pt-5 sm:flex-row sm:justify-end"><Button onClick={onClose} disabled={busy}>Cancel</Button><Button type="submit" disabled={busy} className="border-[#4f8cff] bg-[#4f8cff] text-white">{busy ? <LoaderCircle className="size-4 animate-spin" /> : <KeyRound className="size-4" />}{connection ? "Save changes" : "Save connection"}</Button></div>
      </form>
    </section>
  </div>;
}

function DeleteDialog({ name, busy, onClose, onConfirm }: { name: string; busy: boolean; onClose: () => void; onConfirm: () => void }) {
  const dialogRef = useRef<HTMLElement | null>(null);
  const previousFocus = useRef<HTMLElement | null>(null);
  useEffect(() => {
    previousFocus.current = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    queueMicrotask(() => dialogRef.current?.querySelector<HTMLButtonElement>("[data-confirm-delete]")?.focus());
    return () => previousFocus.current?.focus();
  }, []);
  return <div className="fixed inset-0 z-[85] grid place-items-center bg-[#030812]/80 p-3 backdrop-blur-sm">
    <section ref={dialogRef} role="alertdialog" aria-modal="true" aria-labelledby="delete-connection-title" onKeyDown={(event) => { if (event.key === "Escape" && !busy) onClose(); else trapTab(event); }} className="w-full max-w-md rounded-2xl border border-[#5b3340] bg-[#171019] p-5 shadow-2xl">
      <Trash2 className="size-6 text-[#ff7183]" /><h2 id="delete-connection-title" className="mt-3 text-base font-bold text-[#f4e8eb]">Delete {name} connection?</h2><p className="mt-2 text-xs leading-5 text-[#ab8e96]">This removes the encrypted credential and polling configuration. It does not delete previously captured market provenance.</p>
      <div className="mt-5 flex justify-end gap-2"><Button onClick={onClose} disabled={busy}>Cancel</Button><Button data-confirm-delete onClick={onConfirm} disabled={busy} className="border-[#653442] bg-[#3a1d27] text-[#ff97a5]">{busy ? <LoaderCircle className="size-4 animate-spin" /> : <Trash2 className="size-4" />} Delete</Button></div>
    </section>
  </div>;
}

export function ApiConnectionsPanel({ available, unavailableReason }: { available: boolean; unavailableReason?: string }) {
  const [connections, setConnections] = useState<ProviderConnection[]>([]);
  const [loading, setLoading] = useState(available);
  const [busyCode, setBusyCode] = useState<ProviderCode | null>(null);
  const [editing, setEditing] = useState<ProviderCode | null>(null);
  const [deleting, setDeleting] = useState<ProviderCode | null>(null);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [backendBlock, setBackendBlock] = useState("");

  const load = useCallback(async () => {
    if (!available) { setLoading(false); return; }
    setLoading(true); setError(""); setBackendBlock("");
    try {
      const parsed = providerConnectionListSchema.parse(await request("/api/provider-connections"));
      setConnections(parsed.items);
    } catch (value) {
      const message = value instanceof Error ? value.message : "Connections could not be loaded.";
      if (value instanceof ProviderConnectionRequestError && new Set(["credential_storage_unavailable", "live_mode_required", "mode_mismatch"]).has(value.code ?? "")) {
        setBackendBlock(message);
        setError("");
      } else {
        setError(message);
      }
    }
    finally { setLoading(false); }
  }, [available]);

  useEffect(() => {
    let active = true;
    queueMicrotask(() => { if (active) void load(); });
    return () => { active = false; };
  }, [load]);

  const byCode = useMemo(() => new Map(connections.map((connection) => [connection.provider_code, connection])), [connections]);
  const canManage = available && !backendBlock;

  const replace = (connection: ProviderConnection) => setConnections((current) => [
    ...current.filter((item) => item.provider_code !== connection.provider_code),
    connection,
  ]);

  const save = async (input: SaveProviderConnection) => {
    setBusyCode(input.provider_code); setError(""); setNotice("");
    try {
      const parsed = providerConnectionResponseSchema.parse(await request("/api/provider-connections", { method: "POST", body: JSON.stringify(input) }));
      replace(parsed.item); setEditing(null); setNotice(`${parsed.item.display_name} was saved. Test it before relying on its status.`);
    } finally { setBusyCode(null); }
  };

  const mutate = async (code: ProviderCode, method: "PATCH" | "DELETE", body?: unknown) => {
    setBusyCode(code); setError(""); setNotice("");
    try {
      const value = await request(`/api/provider-connections/${encodeURIComponent(code)}`, { method, body: body ? JSON.stringify(body) : undefined });
      if (method === "DELETE") {
        setConnections((current) => current.filter((item) => item.provider_code !== code));
        setDeleting(null); setNotice("Connection and encrypted credential deleted.");
      } else {
        const parsed = providerConnectionResponseSchema.parse(value); replace(parsed.item); setNotice(`${parsed.item.display_name} is now ${parsed.item.enabled ? "enabled" : "disabled"}.`);
      }
    } catch (value) { setError(value instanceof Error ? value.message : "The connection could not be updated."); }
    finally { setBusyCode(null); }
  };

  const test = async (code: ProviderCode) => {
    setBusyCode(code); setError(""); setNotice("");
    try {
      const parsed = providerConnectionResponseSchema.parse(await request(`/api/provider-connections/${encodeURIComponent(code)}/test`, { method: "POST" }));
      replace(parsed.item);
      setNotice(parsed.item.status === "connected" ? `${parsed.item.display_name} passed the live read-only test.` : connectionErrorMessage(parsed.item.error_code));
    } catch (value) { setError(value instanceof Error ? value.message : "The provider test failed."); }
    finally { setBusyCode(null); }
  };

  return <section aria-labelledby="api-connections-title">
    <div className="flex flex-col gap-4 border-b border-[#203149] pb-5 sm:flex-row sm:items-start sm:justify-between"><div><h2 id="api-connections-title" className="text-sm font-semibold text-[#e3ebf6]">API connections</h2><p className="mt-1 max-w-2xl text-xs leading-5 text-[#6f829b]">Connect official read-only market-data interfaces. Credentials are encrypted server-side and never returned, exported, or stored in the browser.</p></div><Button onClick={() => void load()} disabled={!available || loading}><RefreshCw className={cn("size-4", loading && "animate-spin")} /> Refresh</Button></div>

    {(!available || backendBlock) && <div className="mt-5 rounded-xl border border-[#634d29] bg-[#2c2413]/55 p-4"><p className="flex items-center gap-2 text-xs font-semibold text-[#e0c077]"><AlertTriangle className="size-4" /> Connection management unavailable</p><p className="mt-2 text-xs leading-5 text-[#b49a66]">{backendBlock || unavailableReason || "Sign in to the live hosted application and configure the encrypted credential store."}</p></div>}
    {error && <p role="alert" className="mt-5 rounded-xl border border-[#623641] bg-[#321a22]/60 p-3 text-xs leading-5 text-[#ee91a0]">{error}</p>}
    {notice && <p role="status" className="mt-5 flex items-center gap-2 rounded-xl border border-[#285646] bg-[#123027]/60 p-3 text-xs text-[#72d6b5]"><Check className="size-4" />{notice}</p>}

    <div className="mt-5 grid gap-4 lg:grid-cols-2">
      {providerDescriptors.map((descriptor) => {
        const connection = byCode.get(descriptor.code);
        const presentation = statusPresentation(connection?.status ?? "not_configured");
        const busy = busyCode === descriptor.code;
        const estimated = connection?.estimated_requests_per_hour;
        return <article key={descriptor.code} className="rounded-2xl border border-[#223651] bg-[#0d1829]/80 p-5">
          <div className="flex items-start justify-between gap-3"><div><div className="flex flex-wrap items-center gap-2"><h3 className="text-sm font-bold text-[#e7eef8]">{descriptor.name}</h3><Badge><StatusDot tone={presentation.tone} />{presentation.label}</Badge></div><p className="mt-1 text-[10px] font-semibold uppercase tracking-[.1em] text-[#59708d]">{descriptor.product}</p></div><PlugZap className="size-5 shrink-0 text-[#5f91d9]" /></div>
          <p className="mt-3 text-xs leading-5 text-[#8193aa]">{descriptor.description}</p>
          <div className="mt-4 grid gap-3 sm:grid-cols-2">
            <div className="rounded-xl border border-[#20344f] bg-[#101c2e] p-3"><p className="text-[9px] font-semibold uppercase tracking-[.1em] text-[#61758f]">Credential</p><p className="mt-1.5 truncate text-xs text-[#bdcadb]">{descriptor.credentialLabel ? (connection?.credential_configured ? connection.credential_mask ?? "Masked" : "Not saved") : "Not required"}</p></div>
            <div className="rounded-xl border border-[#20344f] bg-[#101c2e] p-3"><p className="text-[9px] font-semibold uppercase tracking-[.1em] text-[#61758f]">Last successful request</p><p className="mt-1.5 text-xs text-[#bdcadb]">{formatTimestamp(connection?.last_success_at ?? null)}</p></div>
            <div className="rounded-xl border border-[#20344f] bg-[#101c2e] p-3"><p className="text-[9px] font-semibold uppercase tracking-[.1em] text-[#61758f]">Last market update</p><p className="mt-1.5 text-xs text-[#bdcadb]">{formatTimestamp(connection?.last_market_update_at ?? null)}</p></div>
            <div className="rounded-xl border border-[#20344f] bg-[#101c2e] p-3"><p className="text-[9px] font-semibold uppercase tracking-[.1em] text-[#61758f]">Request budget</p><p className="mt-1.5 text-xs text-[#bdcadb]">{connection ? `${estimated ?? "Unknown"} estimated / ${connection.request_budget_per_hour} max per hour` : `${descriptor.defaultBudget} max per hour`}</p></div>
          </div>
          <div className="mt-4"><p className="text-[9px] font-semibold uppercase tracking-[.1em] text-[#61758f]">Capabilities</p><div className="mt-2 flex flex-wrap gap-1.5">{(connection?.capabilities.length ? connection.capabilities : descriptor.capabilities).map((capability) => <span key={capability} className="rounded-md border border-[#27405f] bg-[#132238] px-2 py-1 text-[9px] text-[#91a9c6]">{capability}</span>)}</div></div>
          {connection?.coverage.length ? <p className="mt-3 text-[10px] leading-4 text-[#6f839c]">Coverage: {connection.coverage.join(", ")}</p> : null}
          {connection?.quota_limit !== null && connection?.quota_limit !== undefined ? <p className="mt-3 flex items-center gap-2 text-[10px] text-[#7f93ad]"><Gauge className="size-3.5" /> Provider quota: {connection.quota_remaining ?? "unknown"} of {connection.quota_limit} remaining</p> : null}
          {connection?.error_code ? <p className="mt-3 rounded-lg border border-[#5a3f2d] bg-[#2a2117] p-2.5 text-[10px] leading-4 text-[#d6ae73]">{connectionErrorMessage(connection.error_code)}</p> : null}
          <div className="mt-5 flex flex-wrap gap-2"><Button onClick={() => setEditing(descriptor.code)} disabled={!canManage || busy}><Settings2 className="size-4" />{connection ? "Configure" : "Add"}</Button>{connection && <><Button onClick={() => void test(descriptor.code)} disabled={!canManage || busy || !connection.enabled}>{busy ? <LoaderCircle className="size-4 animate-spin" /> : <PlugZap className="size-4" />} Test</Button><Button onClick={() => void mutate(descriptor.code, "PATCH", { enabled: !connection.enabled })} disabled={!canManage || busy}>{connection.enabled ? <CircleOff className="size-4" /> : <Check className="size-4" />}{connection.enabled ? "Disable" : "Enable"}</Button><Button onClick={() => setDeleting(descriptor.code)} disabled={!canManage || busy} aria-label={`Delete ${descriptor.name} connection`} className="ml-auto border-[#56333d] text-[#ef8795]"><Trash2 className="size-4" /></Button></>}</div>
        </article>;
      })}
    </div>

    {loading && <div role="status" className="mt-5 flex items-center gap-2 text-xs text-[#8294ab]"><LoaderCircle className="size-4 animate-spin" /> Loading encrypted connection metadata…</div>}
    {editing && (() => { const descriptor = providerDescriptors.find((item) => item.code === editing)!; return <ConnectionDialog descriptor={descriptor} connection={byCode.get(editing)} busy={busyCode === editing} onClose={() => setEditing(null)} onSave={save} />; })()}
    {deleting && (() => { const descriptor = providerDescriptors.find((item) => item.code === deleting)!; return <DeleteDialog name={descriptor.name} busy={busyCode === deleting} onClose={() => setDeleting(null)} onConfirm={() => void mutate(deleting, "DELETE")} />; })()}
  </section>;
}
