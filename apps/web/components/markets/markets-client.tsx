"use client";

import { type FormEvent, useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import {
  CalendarClock, ChevronLeft, ChevronRight, Database, ExternalLink, KeyRound,
  ListFilter, RefreshCw, Server, ShieldAlert, Wifi, WifiOff,
} from "lucide-react";
import { appMode, getProviderHealth } from "@/lib/api";
import { foregroundLiveRefresh } from "@/lib/live-refresh";
import {
  getProviderEvents, getProviderMarkets, type ProviderEvent, type ProviderMarket,
} from "@/lib/markets-api";
import type { ProviderHealth } from "@/lib/types";
import { cn } from "@/lib/utils";
import { PageIntro } from "@/components/page-intro";
import { Badge, Button, Panel } from "@/components/ui";

const statusMeta: Record<ProviderHealth["status"], { label: string; tone: string; dot: string }> = {
  connected: { label: "Connected", tone: "border-[#28604e] bg-[#15372e] text-[#4bdbaa]", dot: "bg-[#22c991]" },
  delayed: { label: "Delayed", tone: "border-[#655129] bg-[#322914] text-[#efc663]", dot: "bg-[#e7b444]" },
  disconnected: { label: "Disconnected", tone: "border-[#663743] bg-[#331b23] text-[#ff8190]", dot: "bg-[#ff6478]" },
  requires_key: { label: "Requires API key", tone: "border-[#544474] bg-[#261e38] text-[#b69ae9]", dot: "bg-[#a480e6]" },
  requires_api_key: { label: "Requires API key", tone: "border-[#544474] bg-[#261e38] text-[#b69ae9]", dot: "bg-[#a480e6]" },
  not_supported: { label: "Not supported", tone: "border-[#3b485b] bg-[#1c2533] text-[#8d9caf]", dot: "bg-[#6c7b90]" },
  demo_only: { label: "Demo only", tone: "border-[#365077] bg-[#17263e] text-[#7eacf0]", dot: "bg-[#4f8cff]" },
  unknown: { label: "Not probed", tone: "border-[#3e4b5e] bg-[#1a2432] text-[#929fb0]", dot: "bg-[#77869b]" },
};

function displayTime(value: string | null | undefined): string {
  if (!value) return "Unknown";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? "Invalid timestamp" : date.toLocaleString();
}

function freshness(providerTimestamp: string | null, retrievedAt: string): string {
  if (!providerTimestamp) return "Provider timestamp unavailable";
  const provider = new Date(providerTimestamp).getTime();
  const retrieved = new Date(retrievedAt).getTime();
  if (!Number.isFinite(provider) || !Number.isFinite(retrieved) || retrieved < provider) return "Timestamp comparison unavailable";
  const seconds = Math.round((retrieved - provider) / 1000);
  return seconds < 1 ? "<1s old when retrieved" : `${seconds}s old when retrieved`;
}

function quotaLabel(provider: ProviderHealth): string {
  const parts = [
    provider.quota_used == null ? null : `${provider.quota_used} used`,
    provider.quota_remaining == null ? null : `${provider.quota_remaining} remaining`,
    provider.quota_limit == null ? null : `${provider.quota_limit} limit`,
  ].filter((part): part is string => part !== null);
  return parts.length ? parts.join(" · ") : "Not reported";
}

function rateLimitLabel(provider: ProviderHealth): string {
  if (provider.retry_after_seconds != null) return `Retry in ${provider.retry_after_seconds}s`;
  return provider.rate_limit_status?.replaceAll("_", " ") ?? "Not reported";
}

function SourceLink({ url }: { url: string }) {
  if (!/^https?:\/\//u.test(url)) return <span className="text-[10px] text-[#687b95]">No external source URL</span>;
  return <a href={url} target="_blank" rel="noreferrer" className="focus-ring inline-flex items-center gap-1 text-[10px] font-semibold text-[#7eacf0] hover:text-[#a7c8fa]">Official source <ExternalLink className="size-3" /></a>;
}

function OriginBadge({ origin }: { origin: "live_poll" | "cache" | "historical" | "demo" }) {
  const label = { live_poll: "live poll", cache: "cached", historical: "historical", demo: "demo" }[origin];
  return <Badge className={origin === "demo" ? "border-[#5c4a2d] bg-[#2a2213] text-[#d2b16c]" : origin === "cache" ? "border-[#5c512d] bg-[#292513] text-[#d7c178]" : "border-[#2a5260] bg-[#132c34] text-[#69c9d2]"}>{label}</Badge>;
}

function EmptyBrowser({ noun }: { noun: string }) {
  return <div className="grid min-h-52 place-items-center px-6 py-12 text-center"><div><Database className="mx-auto size-7 text-[#526985]" /><p className="mt-3 text-sm font-semibold text-[#dce6f3]">No {noun} returned</p><p className="mt-1 max-w-md text-xs leading-5 text-[#71849d]">The provider returned a valid empty page for the selected filters. EDGEOS does not generate substitute records.</p></div></div>;
}

function EventList({ items }: { items: ProviderEvent[] }) {
  if (!items.length) return <EmptyBrowser noun="events" />;
  return <div className="divide-y divide-[#1d3049]">{items.map((event) => <article key={`${event.provider}-${event.provider_event_id}`} className="grid gap-4 p-4 sm:grid-cols-[minmax(0,1fr)_auto] sm:p-5"><div className="min-w-0"><div className="flex flex-wrap items-center gap-2"><OriginBadge origin={event.data_origin} /><span className="text-[10px] font-semibold uppercase tracking-[.08em] text-[#677b95]">{event.status ?? "Status unknown"}</span></div><h3 className="mt-2 truncate text-sm font-semibold text-[#e5edf8]">{event.title}</h3>{event.subtitle && <p className="mt-1 text-xs text-[#8293aa]">{event.subtitle}</p>}<div className="mt-3 flex flex-wrap gap-x-4 gap-y-1 text-[11px] text-[#7588a2]"><span>{event.sport ?? "Sport unknown"}</span><span>{event.league ?? "League unknown"}</span><span>ID: {event.provider_event_id}</span></div><p className="mt-2 text-[10px] text-[#657991]">Retrieved {displayTime(event.retrieved_at)} · {freshness(event.provider_timestamp, event.retrieved_at)}</p></div><div className="flex min-w-48 flex-col items-start gap-2 sm:items-end"><span className="inline-flex items-center gap-1.5 text-xs text-[#acbacb]"><CalendarClock className="size-3.5 text-[#6e9fe9]" />{displayTime(event.scheduled_start)}</span><span className="text-[10px] text-[#71849d]">Resolution: {event.resolution_source ?? "Not supplied"}</span><SourceLink url={event.source_url} /></div></article>)}</div>;
}

function quote(value: string | null, currency: string | null): string {
  return value === null ? "Unknown" : `${value}${currency ? ` ${currency}` : ""}`;
}

function MarketList({ items }: { items: ProviderMarket[] }) {
  if (!items.length) return <EmptyBrowser noun="markets" />;
  return <div className="divide-y divide-[#1d3049]">{items.map((market) => <article key={`${market.provider}-${market.provider_market_id}`} className="p-4 sm:p-5"><div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between"><div className="min-w-0"><div className="flex flex-wrap items-center gap-2"><OriginBadge origin={market.data_origin} /><span className="text-[10px] font-semibold uppercase tracking-[.08em] text-[#677b95]">{market.status ?? "Status unknown"}</span></div><h3 className="mt-2 text-sm font-semibold text-[#e5edf8]">{market.title}</h3><p className="mt-1 text-[11px] text-[#71849d]">{market.market_type ?? "Market type unknown"} · {market.period ?? "Period unknown"} · ID {market.provider_market_id}</p></div><div className="shrink-0 text-left sm:text-right"><p className="text-[10px] uppercase tracking-[.08em] text-[#60738e]">Quote freshness</p><p className="mt-1 text-xs font-medium text-[#aab9ca]">{freshness(market.quote_timestamp, market.retrieved_at)}</p><p className="mt-1 text-[10px] text-[#657991]">Retrieved {displayTime(market.retrieved_at)}</p></div></div><div className="mt-4 overflow-x-auto rounded-xl border border-[#20344f]"><table className="w-full min-w-[760px] text-left text-[11px]"><thead className="bg-[#101d30] text-[9px] uppercase tracking-[.09em] text-[#667991]"><tr><th className="px-3 py-2.5">Outcome</th><th className="px-3 py-2.5">Offered</th><th className="px-3 py-2.5">Best bid</th><th className="px-3 py-2.5">Best ask</th><th className="px-3 py-2.5">Bid size</th><th className="px-3 py-2.5">Ask size</th><th className="px-3 py-2.5">Observed limit</th></tr></thead><tbody>{market.outcomes.map((outcome, index) => <tr key={outcome.provider_outcome_id ?? `${outcome.name}-${index}`} className="border-t border-[#1d3049] text-[#aebccd]"><td className="px-3 py-3"><span className="font-semibold text-[#dfe8f4]">{outcome.name ?? "Unnamed outcome"}</span><span className="ml-2 text-[9px] uppercase text-[#61748e]">{outcome.side ?? "side unknown"}</span></td><td className="px-3 py-3">{quote(outcome.offered_price, outcome.currency)}</td><td className="px-3 py-3">{quote(outcome.best_bid, outcome.currency)}</td><td className="px-3 py-3">{quote(outcome.best_ask, outcome.currency)}</td><td className="px-3 py-3">{outcome.bid_size ?? "Unknown"}</td><td className="px-3 py-3">{outcome.ask_size ?? "Unknown"}</td><td className="px-3 py-3">{outcome.maximum_stake === null ? "Unknown" : `${outcome.maximum_stake}${outcome.stake_currency ? ` ${outcome.stake_currency}` : ""}`}</td></tr>)}</tbody></table></div><div className="mt-3 grid gap-2 text-[10px] text-[#71849d] sm:grid-cols-3"><span>Event starts: {displayTime(market.event_start_time)}</span><span>Market closes: {displayTime(market.close_time)}</span><span>Reported liquidity: {market.liquidity ?? "Unknown"}</span></div><details className="mt-3 rounded-lg border border-[#20344f] bg-[#0d1828] px-3 py-2 text-[11px] text-[#8495ab]"><summary className="focus-ring cursor-pointer font-semibold text-[#aab9cb]">Settlement evidence</summary><div className="mt-2 space-y-1 leading-5"><p>{market.settlement?.primary_rules ?? "Primary rules were not supplied."}</p><p>Resolution source: {market.settlement?.resolution_source ?? "Unknown"}</p><p>Void treatment: {market.settlement?.void_treatment ?? "Unknown"} · Push treatment: {market.settlement?.push_treatment ?? "Unknown"}</p></div></details><div className="mt-3"><SourceLink url={market.source_url} /></div></article>)}</div>;
}

function BrowserError({ error, retry }: { error: Error; retry: () => void }) {
  return <div className="grid min-h-52 place-items-center px-6 py-12 text-center"><div><WifiOff className="mx-auto size-7 text-[#ff6478]" /><p className="mt-3 text-sm font-semibold text-[#e5edf8]">Provider data unavailable</p><p className="mt-2 max-w-lg text-xs leading-5 text-[#8798ad]">{error.message}</p><p className="mt-1 text-[10px] text-[#657991]">Check credentials, plan access, provider availability, and configured request budget.</p><Button onClick={retry} className="mt-4">Retry</Button></div></div>;
}

export function MarketsClient() {
  const healthQuery = useQuery({ queryKey: ["providers", "health"], queryFn: getProviderHealth, ...foregroundLiveRefresh(appMode) });
  const health = healthQuery.data;
  const providerOptions = useMemo(() => {
    const rows = health?.data ?? [];
    return rows.filter((item) => appMode === "demo" ? item.id === "demo" : item.id !== "demo");
  }, [health?.data]);
  const [provider, setProvider] = useState(appMode === "demo" ? "demo" : "kalshi");
  const [view, setView] = useState<"events" | "markets">("markets");
  const [sportDraft, setSportDraft] = useState("");
  const [sport, setSport] = useState("");
  const [cursors, setCursors] = useState<Array<string | null>>([null]);
  const cursor = cursors.at(-1) ?? null;
  const resetPage = () => setCursors([null]);
  const eventsQuery = useQuery({ queryKey: ["provider-browser", provider, "events", cursor, sport], queryFn: () => getProviderEvents(provider, { cursor, sport, limit: 50 }), enabled: view === "events" && Boolean(provider), retry: false });
  const marketsQuery = useQuery({ queryKey: ["provider-browser", provider, "markets", cursor, sport], queryFn: () => getProviderMarkets(provider, { cursor, sport, limit: 50 }), enabled: view === "markets" && Boolean(provider), retry: false });
  const activeQuery = view === "events" ? eventsQuery : marketsQuery;
  const connected = health?.source === "api" ? health.data.filter((item) => item.status === "connected").length : 0;
  const applySport = (event: FormEvent) => { event.preventDefault(); setSport(sportDraft.trim()); resetPage(); };
  const changeProvider = (value: string) => { setProvider(value); resetPage(); };
  const changeView = (value: "events" | "markets") => { setView(value); resetPage(); };
  const nextCursor = activeQuery.data?.next_cursor ?? null;

  return <div className="space-y-5">
    <PageIntro badge="data provenance" title="Markets & integrations" description="Inspect normalized provider events and market snapshots without calling an external provider from the browser." actions={<Button onClick={() => { void healthQuery.refetch(); void activeQuery.refetch(); }} disabled={healthQuery.isFetching || activeQuery.isFetching}><RefreshCw className={cn("size-4", (healthQuery.isFetching || activeQuery.isFetching) && "animate-spin")} /> Refresh</Button>} />
    <Panel className={cn("px-4 py-3 text-[11px] leading-5", appMode === "live" ? "border-[#294c49] text-[#8fc4b5]" : "border-[#5d4d2e] text-[#c8ae73]")}>
      {appMode === "live"
        ? `Provider health refreshes from the EDGEOS server every 15 seconds while this window is visible${healthQuery.dataUpdatedAt ? `; response received ${displayTime(new Date(healthQuery.dataUpdatedAt).toISOString())}` : ""}. Market and event pages are point-in-time snapshots; use Refresh to request them again. A running server worker is required for new ingestion.`
        : "DEMO ONLY — provider health and market records are illustrative. Automatic live polling and external-provider requests are off."}
    </Panel>
    <section className="grid gap-3 sm:grid-cols-3"><Panel className="p-4"><div className="flex items-center gap-3"><span className="grid size-9 place-items-center rounded-xl bg-[#4f8cff]/10 text-[#6ca5ff]"><Server className="size-[18px]" /></span><div><p className="text-[10px] uppercase tracking-[.1em] text-[#667993]">Known adapters</p><p className="mt-1 text-lg font-bold text-[#e7eef9]">{health?.data.length ?? "—"}</p></div></div></Panel><Panel className="p-4"><div className="flex items-center gap-3"><span className="grid size-9 place-items-center rounded-xl bg-[#22c991]/10 text-[#22c991]"><Wifi className="size-[18px]" /></span><div><p className="text-[10px] uppercase tracking-[.1em] text-[#667993]">Observed connected</p><p className="mt-1 text-lg font-bold text-[#e7eef9]">{connected}</p></div></div></Panel><Panel className="p-4"><div className="flex items-center gap-3"><span className="grid size-9 place-items-center rounded-xl bg-[#19c9d5]/10 text-[#2ed5df]"><Database className="size-[18px]" /></span><div><p className="text-[10px] uppercase tracking-[.1em] text-[#667993]">Health source</p><p className="mt-1 text-sm font-bold uppercase text-[#e7eef9]">{health?.source ?? "Loading"}</p></div></div></Panel></section>
    {healthQuery.isLoading && <div className="grid gap-4 md:grid-cols-2">{[1, 2, 3, 4].map((item) => <Panel key={item} className="h-48 p-5"><div className="skeleton h-8 w-1/2 rounded-lg" /><div className="skeleton mt-5 h-4 w-full rounded" /><div className="skeleton mt-3 h-4 w-4/5 rounded" /></Panel>)}</div>}
    {healthQuery.isError && <Panel role="alert" className="flex flex-col items-center px-6 py-12 text-center"><WifiOff className="size-8 text-[#ff6478]" /><p className="mt-4 text-sm font-semibold text-[#e5edf8]">Provider health refresh failed</p><p className="mt-2 max-w-md text-xs leading-5 text-[#7789a2]">{health ? "Any provider cards below are the last successfully received status, not a current connection claim." : "No connection status or sample provider data was substituted."}</p><Button onClick={() => healthQuery.refetch()} className="mt-5">Retry health</Button></Panel>}
    {health && <section className="grid gap-3 md:grid-cols-2" aria-label="Provider health">{health.data.map((item) => {
      const meta = statusMeta[item.status];
      return <Panel key={item.id} className="p-4">
        <div className="flex items-start justify-between gap-3"><div><p className="text-sm font-semibold text-[#e5edf8]">{item.name}</p><p className="mt-1 text-[10px] text-[#687b95]">{item.feed_type?.replaceAll("_", " ") ?? "Feed type unknown"}</p></div><Badge className={meta.tone}><span className={cn("size-1.5 rounded-full", meta.dot)} />{meta.label}</Badge></div>
        <dl className="mt-3 grid grid-cols-2 gap-2 text-[10px]">
          <div><dt className="text-[#60738d]">Last successful request</dt><dd className="mt-1 text-[#aab8ca]">{displayTime(item.last_successful_update)}</dd></div>
          <div><dt className="text-[#60738d]">Active instruments</dt><dd className="mt-1 text-[#aab8ca]">{item.active_instruments ?? "Not reported"}</dd></div>
          <div><dt className="text-[#60738d]">Known refresh</dt><dd className="mt-1 text-[#aab8ca]">{item.refresh_interval_seconds == null ? "Unknown" : `${item.refresh_interval_seconds}s`}</dd></div>
          <div><dt className="text-[#60738d]">Measured data delay</dt><dd className="mt-1 text-[#aab8ca]">{item.measured_data_delay_seconds == null ? "Unknown" : `${item.measured_data_delay_seconds}s`}</dd></div>
          <div><dt className="text-[#60738d]">API quota</dt><dd className="mt-1 text-[#aab8ca]">{quotaLabel(item)}</dd></div>
          <div><dt className="text-[#60738d]">Rate limit</dt><dd className="mt-1 capitalize text-[#aab8ca]">{rateLimitLabel(item)}</dd></div>
          <div className="col-span-2"><dt className="text-[#60738d]">Coverage</dt><dd className="mt-1 text-[#aab8ca]">{item.market_coverage?.join(", ") || "Not reported"}</dd></div>
        </dl>
        {item.message && <p className="mt-3 rounded-lg bg-[#0c1727] p-2 text-[10px] leading-4 text-[#71849d]">{item.message}</p>}{item.last_error && <p className="mt-2 rounded-lg bg-[#321b23]/50 p-2 text-[10px] text-[#d9919b]">{item.last_error}</p>}
      </Panel>;
    })}</section>}
    {health && <Panel className="overflow-hidden"><div className="border-b border-[#203149] p-4 sm:p-5"><div className="flex flex-col gap-3 lg:flex-row lg:items-end lg:justify-between"><div><p className="text-[10px] font-bold uppercase tracking-[.1em] text-[#637791]">Provider browser</p><h2 className="mt-1 text-base font-bold text-[#e8f0fb]">Normalized events and market snapshots</h2></div><div className="flex flex-wrap gap-2" role="tablist" aria-label="Provider record type"><button role="tab" aria-selected={view === "markets"} onClick={() => changeView("markets")} className={cn("focus-ring rounded-lg border px-3 py-2 text-xs font-semibold", view === "markets" ? "border-[#3e70b7] bg-[#17325a] text-[#8ab8ff]" : "border-[#273b58] text-[#8293aa]")}>Markets</button><button role="tab" aria-selected={view === "events"} onClick={() => changeView("events")} className={cn("focus-ring rounded-lg border px-3 py-2 text-xs font-semibold", view === "events" ? "border-[#3e70b7] bg-[#17325a] text-[#8ab8ff]" : "border-[#273b58] text-[#8293aa]")}>Events</button></div></div><div className="mt-4 grid gap-3 md:grid-cols-[minmax(220px,1fr)_minmax(260px,1fr)_auto]"><label><span className="mb-1.5 block text-[10px] font-semibold uppercase tracking-[.08em] text-[#657991]">Provider</span><select value={provider} onChange={(event) => changeProvider(event.target.value)} className="focus-ring h-10 w-full rounded-xl border border-[#263b58] bg-[#0d1829] px-3 text-xs text-[#d7e1ee]">{providerOptions.length ? providerOptions.map((item) => <option key={item.id} value={item.id}>{item.name} — {statusMeta[item.status].label}</option>) : <option value={provider}>{provider}</option>}</select></label><form onSubmit={applySport}><label><span className="mb-1.5 block text-[10px] font-semibold uppercase tracking-[.08em] text-[#657991]">Sport filter / official key</span><span className="flex"><input value={sportDraft} onChange={(event) => setSportDraft(event.target.value)} placeholder="Optional (for example basketball_nba)" className="focus-ring h-10 min-w-0 flex-1 rounded-l-xl border border-r-0 border-[#263b58] bg-[#0d1829] px-3 text-xs text-[#d7e1ee] placeholder:text-[#566a84]" /><button className="focus-ring grid h-10 w-11 place-items-center rounded-r-xl border border-[#263b58] bg-[#14233a] text-[#7faaf0]" aria-label="Apply sport filter"><ListFilter className="size-4" /></button></span></label></form><div className="flex items-end gap-2"><Button disabled={cursors.length <= 1 || activeQuery.isFetching} onClick={() => setCursors((value) => value.slice(0, -1))} aria-label="Previous provider page"><ChevronLeft className="size-4" /></Button><Button disabled={!nextCursor || activeQuery.isFetching} onClick={() => nextCursor && setCursors((value) => [...value, nextCursor])} aria-label="Next provider page"><ChevronRight className="size-4" /></Button></div></div></div>
      {activeQuery.isLoading && <div className="space-y-3 p-5"><div className="skeleton h-20 rounded-xl" /><div className="skeleton h-32 rounded-xl" /><div className="skeleton h-24 rounded-xl" /></div>}
      {activeQuery.isError && <BrowserError error={activeQuery.error} retry={() => { void activeQuery.refetch(); }} />}
      {view === "events" && eventsQuery.data && <><div className={cn("border-b border-[#203149] px-4 py-2.5 text-[10px]", eventsQuery.data.is_demo ? "bg-[#292313] text-[#c2ad75]" : "bg-[#11261f] text-[#82b7a5]")}>{eventsQuery.data.is_demo ? "DEMO ONLY — synthetic records, never executable or live." : `${eventsQuery.data.data_origin.replaceAll("_", " ")} · retrieved ${displayTime(eventsQuery.data.retrieved_at)}`}</div><EventList items={eventsQuery.data.items} /></>}
      {view === "markets" && marketsQuery.data && <><div className={cn("border-b border-[#203149] px-4 py-2.5 text-[10px]", marketsQuery.data.is_demo ? "bg-[#292313] text-[#c2ad75]" : "bg-[#11261f] text-[#82b7a5]")}>{marketsQuery.data.is_demo ? "DEMO ONLY — synthetic prices and sizes, never executable or live." : `${marketsQuery.data.data_origin.replaceAll("_", " ")} · retrieved ${displayTime(marketsQuery.data.retrieved_at)}`}</div><MarketList items={marketsQuery.data.items} /></>}
    </Panel>}
    <div className="grid gap-4 md:grid-cols-2"><Panel className="flex items-start gap-3 p-5"><ShieldAlert className="mt-0.5 size-5 shrink-0 text-[#e4b45f]" /><div><p className="text-sm font-semibold text-[#e1eaf5]">Order-book viewer not exposed</p><p className="mt-1 text-xs leading-5 text-[#75879f]">The current normalized HTTP API publishes event and market pages, but no order-book route. Bid, ask, and size values above are only returned snapshot fields; missing depth remains Unknown.</p></div></Panel><Panel className="flex items-start gap-3 p-5"><KeyRound className="mt-0.5 size-5 shrink-0 text-[#8eb6f2]" /><div><p className="text-sm font-semibold text-[#e1eaf5]">Credentials stay server-side</p><p className="mt-1 text-xs leading-5 text-[#75879f]">The browser calls only the same-origin EDGEOS gateway. Provider credentials and external requests remain on the backend.</p></div></Panel></div>
  </div>;
}
