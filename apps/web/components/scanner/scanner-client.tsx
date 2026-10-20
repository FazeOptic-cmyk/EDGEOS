"use client";

import { useDeferredValue, useEffect, useMemo, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Area, AreaChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import {
  ArrowDownUp, Bookmark, Check, CheckCircle2, ChevronDown, Clock3, ExternalLink, Filter,
  Heart, Info, Layers3, LoaderCircle, Search, ShieldCheck, SlidersHorizontal, Sparkles, TriangleAlert, X,
} from "lucide-react";
import {
  appMode, deleteSavedOpportunity, getOpportunities, getOpportunityDetail,
  getSavedOpportunities, putSavedOpportunity,
} from "@/lib/api";
import type { Opportunity } from "@/lib/types";
import { createPaperPosition } from "@/lib/workflow-api";
import { foregroundLiveRefresh, foregroundRefreshDescription } from "@/lib/live-refresh";
import { opportunityDisplayState, opportunityLifecycleLabel } from "@/lib/opportunity-lifecycle";
import { cn, money, percent } from "@/lib/utils";
import { Badge, Button, Panel, StatusDot } from "@/components/ui";

type SortKey = "score" | "grossRoi" | "netRoi" | "potentialProfit" | "liquidity" | "quoteAgeSeconds";

function PlatformBadge({ name }: { name: string }) {
  const initials = name.split(/\s+/).map((part) => part[0]).join("").slice(0, 2);
  return <span className="inline-flex items-center gap-1.5 whitespace-nowrap text-[11px] font-medium text-[#a9b7c9]"><span className="grid size-5 place-items-center rounded-md border border-[#2c4160] bg-[#17263e] text-[8px] font-bold text-[#8db5f8]">{initials}</span>{name}</span>;
}

export function MatchBadge({ status }: { status: Opportunity["matchStatus"] }) {
  const presentation = status === "VERIFIED MATCH"
    ? { label: "VERIFIED", tone: "border-[#22c991]/25 bg-[#22c991]/8 text-[#4bdbaa]", icon: CheckCircle2 }
    : status === "REJECTED"
      ? { label: "REJECTED", tone: "border-[#ff6478]/30 bg-[#ff6478]/10 text-[#ff8b9a]", icon: TriangleAlert }
      : { label: "REVIEW", tone: "border-[#f5b942]/25 bg-[#f5b942]/8 text-[#efc66c]", icon: TriangleAlert };
  const Icon = presentation.icon;
  return <span className={cn("inline-flex items-center gap-1.5 whitespace-nowrap rounded-full border px-2 py-1 text-[9px] font-bold tracking-[.07em]", presentation.tone)}><Icon className="size-3" />{presentation.label}</span>;
}

function eventStartLabel(value: string | null): string {
  if (!value) return "Start time unknown";
  const timestamp = new Date(value);
  return Number.isNaN(timestamp.getTime()) ? "Start time unknown" : timestamp.toLocaleString();
}

function timestampLabel(value: string | null): string {
  if (!value) return "Unknown";
  const timestamp = new Date(value);
  return Number.isNaN(timestamp.getTime()) ? "Invalid timestamp" : timestamp.toLocaleString();
}

export function LifecycleBadge({ opportunity }: { opportunity: Opportunity }) {
  const state = opportunityDisplayState(opportunity.lifecycle);
  const label = opportunityLifecycleLabel(opportunity.lifecycle);
  if (!label) return null;
  return <Badge className={state === "active"
    ? "border-[#2d5a4d] bg-[#15342b] text-[#50d4aa]"
    : state === "stale"
      ? "border-[#655129] bg-[#322914] text-[#efc663]"
      : "border-[#663743] bg-[#331b23] text-[#ff8190]"
  }>{label.toUpperCase()}</Badge>;
}

function Filters({ search, setSearch, sport, setSport, league, setLeague, platform, setPlatform, market, setMarket, minRoi, setMinRoi, minProfit, setMinProfit, minLiquidity, setMinLiquidity, maxAge, setMaxAge, phase, setPhase, leagueOptions, platformOptions, marketOptions, verifiedOnly, setVerifiedOnly, sort, setSort, onReset }: {
  search: string; setSearch: (value: string) => void;
  sport: string; setSport: (value: string) => void;
  league: string; setLeague: (value: string) => void;
  platform: string; setPlatform: (value: string) => void;
  market: string; setMarket: (value: string) => void;
  minRoi: number; setMinRoi: (value: number) => void;
  minProfit: number; setMinProfit: (value: number) => void;
  minLiquidity: number; setMinLiquidity: (value: number) => void;
  maxAge: number; setMaxAge: (value: number) => void;
  phase: "all" | "prematch" | "live"; setPhase: (value: "all" | "prematch" | "live") => void;
  leagueOptions: string[]; platformOptions: string[]; marketOptions: string[];
  verifiedOnly: boolean; setVerifiedOnly: (value: boolean) => void;
  sort: SortKey; setSort: (value: SortKey) => void; onReset: () => void;
}) {
  const selectClass = "focus-ring h-10 appearance-none rounded-xl border border-[#243956] bg-[#0d1829] pl-3 pr-8 text-xs font-medium text-[#aab8ca]";
  const select = (label: string, value: string | number, onChange: (value: string) => void, options: { value: string | number; label: string }[]) => (
    <label className="relative min-w-0"><span className="sr-only">{label}</span><select value={value} onChange={(event) => onChange(event.target.value)} className={cn(selectClass, "w-full")}>
      {options.map((option) => <option key={`${label}-${option.value}`} value={option.value}>{option.label}</option>)}
    </select><ChevronDown className="pointer-events-none absolute right-2.5 top-1/2 size-3.5 -translate-y-1/2 text-[#657991]" /></label>
  );
  return (
    <div className="space-y-3 border-b border-[#203149]/80 p-4 sm:p-5">
      <div className="flex flex-col gap-3 xl:flex-row xl:items-center">
        <label className="relative min-w-0 flex-1 xl:max-w-sm"><span className="sr-only">Search opportunities</span><Search className="absolute left-3 top-1/2 size-4 -translate-y-1/2 text-[#62748d]" /><input value={search} onChange={(event) => setSearch(event.target.value)} placeholder="Search event or market…" className="focus-ring h-10 w-full rounded-xl border border-[#243956] bg-[#0d1829] pl-9 pr-3 text-xs text-[#e1e9f5] placeholder:text-[#5c6e87]" /></label>
        <div className="grid grid-cols-2 gap-2 sm:grid-cols-4 xl:w-[650px]">
          {select("Sport", sport, setSport, [{ value: "all", label: "All sports" }, ...["Football", "Basketball", "Hockey", "Soccer"].map((label) => ({ value: label.toLowerCase(), label }))])}
          {select("League", league, setLeague, [{ value: "all", label: "All leagues" }, ...leagueOptions.map((value) => ({ value, label: value }))])}
          {select("Platform", platform, setPlatform, [{ value: "all", label: "All observed platforms" }, ...platformOptions.map((value) => ({ value, label: value }))])}
          {select("Market type", market, setMarket, [{ value: "all", label: "All markets" }, ...marketOptions.map((value) => ({ value, label: value }))])}
        </div>
      </div>
      <div className="grid grid-cols-2 gap-2 md:grid-cols-3 xl:grid-cols-6">
        {select("Minimum ROI", minRoi, (value) => setMinRoi(Number(value)), [{ value: 0, label: "Any net ROI" }, { value: 1, label: "≥ 1% ROI" }, { value: 2, label: "≥ 2% ROI" }, { value: 4, label: "≥ 4% ROI" }])}
        {select("Minimum profit", minProfit, (value) => setMinProfit(Number(value)), [{ value: 0, label: "Any net profit" }, { value: 5, label: "≥ $5 profit" }, { value: 10, label: "≥ $10 profit" }, { value: 25, label: "≥ $25 profit" }])}
        {select("Minimum liquidity", minLiquidity, (value) => setMinLiquidity(Number(value)), [{ value: 0, label: "Any liquidity" }, { value: 100, label: "≥ $100 liquid" }, { value: 250, label: "≥ $250 liquid" }, { value: 500, label: "≥ $500 liquid" }])}
        {select("Maximum quote age", maxAge, (value) => setMaxAge(Number(value)), [{ value: 30, label: "≤ 30s old" }, { value: 60, label: "≤ 1m old" }, { value: 120, label: "≤ 2m old" }, { value: 300, label: "≤ 5m old" }])}
        {select("Market phase", phase, (value) => setPhase(value as "all" | "prematch" | "live"), [{ value: "all", label: "Prematch & live" }, { value: "prematch", label: "Prematch only" }, { value: "live", label: "Live only" }])}
        <label className="relative"><span className="sr-only">Sort opportunities</span><select value={sort} onChange={(event) => setSort(event.target.value as SortKey)} className={cn(selectClass, "w-full")}><option value="score">Research score</option><option value="grossRoi">Theoretical ROI</option><option value="netRoi">Highest net ROI</option><option value="potentialProfit">Highest profit</option><option value="liquidity">Most liquid</option><option value="quoteAgeSeconds">Lowest execution risk</option></select><ArrowDownUp className="pointer-events-none absolute right-2.5 top-1/2 size-3.5 -translate-y-1/2 text-[#657991]" /></label>
      </div>
      <div className="flex flex-wrap items-center gap-2.5 text-[11px]">
        <button onClick={() => setVerifiedOnly(!verifiedOnly)} className={cn("focus-ring inline-flex h-8 items-center gap-2 rounded-lg border px-3 font-semibold transition", verifiedOnly ? "border-[#2a6a5b] bg-[#14342e] text-[#52d9ae]" : "border-[#263a57] bg-[#101c2f] text-[#8294ad]")}><span className={cn("grid size-3.5 place-items-center rounded border", verifiedOnly ? "border-[#22c991] bg-[#22c991] text-[#071a13]" : "border-[#52647d]")}>{verifiedOnly && <Check className="size-2.5" strokeWidth={3} />}</span>Verified only</button>
        <span className="inline-flex h-8 items-center gap-2 rounded-lg border border-[#263a57] bg-[#101c2f] px-3 text-[#8294ad]"><Clock3 className="size-3.5" /> Hard freshness gate <strong className="text-[#b5c3d5]">{maxAge}s</strong></span>
        <span className="inline-flex h-8 items-center gap-2 rounded-lg border border-[#263a57] bg-[#101c2f] px-3 text-[#8294ad]"><Filter className="size-3.5" /> {phase === "all" ? "All phases" : phase}</span>
        {(search || sport !== "all" || league !== "all" || platform !== "all" || market !== "all" || minRoi > 0 || minProfit > 0 || minLiquidity > 0 || maxAge !== 300 || phase !== "all" || !verifiedOnly) && <button onClick={onReset} className="px-2 font-semibold text-[#6e9fe9] hover:text-[#8db8fb]">Reset filters</button>}
      </div>
    </div>
  );
}

export function DetailDrawer({ opportunity, detailState, onClose, isSaved, onToggleSaved, saving }: {
  opportunity: Opportunity;
  detailState: "loading" | "ready" | "stale" | "withdrawn" | "error";
  onClose: () => void;
  isSaved: boolean;
  onToggleSaved: () => void;
  saving: boolean;
}) {
  const queryClient = useQueryClient();
  const [tab, setTab] = useState<"model" | "settlement" | "history">("model");
  const paperMutation = useMutation({
    mutationFn: () => createPaperPosition({
      opportunity_id: opportunity.id,
      total_capital: opportunity.totalAllocationExact ?? String(opportunity.totalAllocation),
      notes: "Created from the scanner's persisted opportunity snapshot.",
      idempotency_key: crypto.randomUUID(),
    }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["paper-portfolio"] }),
  });
  const dialogRef = useRef<HTMLElement>(null);
  useEffect(() => {
    const previous = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    dialogRef.current?.focus();
    return () => {
      document.body.style.overflow = previousOverflow;
      previous?.focus();
    };
  }, [onClose]);
  return (
    <div className="fixed inset-0 z-[70] flex justify-end">
      <button aria-label="Close opportunity details" className="absolute inset-0 bg-[#030710]/75 backdrop-blur-[3px]" onClick={onClose} />
      <aside
        ref={dialogRef}
        role="dialog"
        aria-modal="true"
        tabIndex={-1}
        onKeyDown={(event) => {
          if (event.key === "Escape") {
            event.preventDefault();
            onClose();
            return;
          }
          if (event.key !== "Tab") return;
          const focusable = Array.from(event.currentTarget.querySelectorAll<HTMLElement>(
            "button:not([disabled]), a[href], input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex='-1'])",
          ));
          if (!focusable.length) {
            event.preventDefault();
            event.currentTarget.focus();
            return;
          }
          const first = focusable[0];
          const last = focusable[focusable.length - 1];
          if (event.shiftKey && (document.activeElement === first || document.activeElement === event.currentTarget)) {
            event.preventDefault();
            last.focus();
          } else if (!event.shiftKey && document.activeElement === last) {
            event.preventDefault();
            first.focus();
          }
        }}
        className="animate-slide-in relative flex h-full w-full max-w-[640px] flex-col overflow-hidden border-l border-[#29405e] bg-[#0a1220] shadow-[-20px_0_80px_rgba(0,0,0,.4)] outline-none"
        aria-label="Opportunity details"
      >
        <header className="border-b border-[#203149] px-5 py-5 sm:px-7">
          <div className="flex items-start justify-between gap-4"><div className="min-w-0"><div className="mb-3 flex flex-wrap items-center gap-2"><MatchBadge status={opportunity.matchStatus} /><Badge className="border-[#344c6f] bg-[#14233a] text-[#7eabef]">{opportunity.dataMode.toUpperCase()} DATA</Badge><LifecycleBadge opportunity={opportunity} /><span className="text-[10px] text-[#677a94]">{opportunity.quoteAgeSeconds === null ? "Quote time unknown" : `${opportunity.quoteAgeSeconds}s old`}</span></div><h2 className="font-display text-xl font-bold leading-tight text-[#f0f5ff] sm:text-2xl">{opportunity.event}</h2><p className="mt-2 text-xs text-[#7588a2]">{opportunity.league} · {opportunity.market} · {opportunity.live ? "Live" : "Prematch"}</p><p className="mt-1 text-[10px] text-[#657991]">{eventStartLabel(opportunity.startTime)} · observed {timestampLabel(opportunity.observedAt)}{opportunity.expiresAt ? ` · expires ${timestampLabel(opportunity.expiresAt)}` : ""}{opportunity.withdrawnAt ? ` · withdrawn ${timestampLabel(opportunity.withdrawnAt)}` : ""}</p></div><button onClick={onClose} className="focus-ring grid size-9 shrink-0 place-items-center rounded-xl border border-[#273b58] text-[#8596ae] hover:bg-[#17253a] hover:text-white"><X className="size-4" /></button></div>
          <div className="mt-5 grid grid-cols-3 gap-2"><div className="rounded-xl border border-[#233853] bg-[#101c2e] p-3"><p className="text-[9px] font-bold uppercase tracking-[.1em] text-[#667991]">Net ROI</p><p className="mt-1 font-display text-lg font-bold text-[#22c991]">{percent(opportunity.netRoi)}</p></div><div className="rounded-xl border border-[#233853] bg-[#101c2e] p-3"><p className="text-[9px] font-bold uppercase tracking-[.1em] text-[#667991]">Net profit</p><p className="mt-1 font-display text-lg font-bold text-[#e9f1fc]">{money(opportunity.potentialProfit, 2, opportunity.currency)}</p></div><div className="rounded-xl border border-[#233853] bg-[#101c2e] p-3"><p className="text-[9px] font-bold uppercase tracking-[.1em] text-[#667991]">Score</p><p className="mt-1 font-display text-lg font-bold text-[#76aaff]">{opportunity.score}<span className="text-xs text-[#61748e]">/100</span></p></div></div>
        </header>

        <div className="flex border-b border-[#203149] px-5 sm:px-7">{(["model", "settlement", "history"] as const).map((item) => <button key={item} onClick={() => setTab(item)} className={cn("relative px-3 py-3.5 text-xs font-semibold capitalize transition", tab === item ? "text-[#72a9ff]" : "text-[#70829c] hover:text-[#b7c5d8]")}>{item === "model" ? "Cash-flow model" : item}{tab === item && <span className="absolute inset-x-2 bottom-0 h-0.5 rounded-full bg-[#4f8cff]" />}</button>)}</div>

        <div className="flex-1 overflow-y-auto p-5 sm:p-7">
          {detailState !== "ready" && <div role={detailState === "loading" ? "status" : "alert"} className={cn(
            "mb-5 rounded-xl border p-3 text-xs leading-5",
            detailState === "loading" ? "border-[#2b4668] bg-[#13233a] text-[#8eb2e5]" : detailState === "stale" ? "border-[#64502c] bg-[#2e2615]/55 text-[#e8c274]" : "border-[#623641] bg-[#321a22]/55 text-[#ed97a4]",
          )}>{detailState === "error"
            ? "Authoritative opportunity detail is unavailable. Allocations, fees, and outcome payouts below are incomplete, and paper-position creation is disabled."
            : detailState === "stale"
              ? "This opportunity is stale. The model remains visible for research, but its quotes are not treated as currently executable and paper-position creation is disabled."
              : detailState === "withdrawn"
                ? `This opportunity was ${opportunity.lifecycle ?? "withdrawn"} and is no longer actionable. The last model is retained for audit only.`
                : "Loading authoritative allocations, fees, and payout scenarios…"}</div>}
          {tab === "model" && <div className="space-y-6">
            <section><div className="mb-3 flex flex-wrap items-center justify-between gap-2"><h3 className="text-xs font-bold uppercase tracking-[.1em] text-[#788ba5]">Simulated allocation</h3><span className="text-xs text-[#8597ad]">Total {money(opportunity.totalAllocation, 2, opportunity.currency)} · fees {opportunity.totalFees === null ? "Unknown" : money(opportunity.totalFees, 2, opportunity.currency)}</span></div><div className="space-y-2">{opportunity.allocations.map((allocation, index) => <div key={`${allocation.platform}-${allocation.outcome}`} className="flex items-center gap-3 rounded-xl border border-[#20344f] bg-[#0f1a2c] p-3"><span className="grid size-7 place-items-center rounded-lg bg-[#182a44] text-[10px] font-bold text-[#82b2fd]">{index + 1}</span><div className="min-w-0 flex-1"><p className="text-xs font-semibold text-[#dce5f2]">{allocation.platform} · {allocation.outcome}</p><p className="mt-1 text-[10px] text-[#71849d]">{allocation.priceKind === "contract_ask" ? `${allocation.quantity} contracts` : `${money(allocation.quantity, 2, opportunity.currency)} stake basis`}{allocation.modeledPrice === undefined ? "" : ` · modeled price ${allocation.modeledPrice}`}</p><p className="mt-1 text-[10px] text-[#657991]">Cash cost {money(allocation.cashCost, 2, opportunity.currency)} + fee {money(allocation.fee, 2, opportunity.currency)}</p><p className="mt-1 text-[10px] text-[#657991]">Provider quote: {timestampLabel(opportunity.legs[index]?.timestamp ?? null)}</p></div><p className="font-display text-sm font-bold text-[#edf4ff]">{money(allocation.stake, 2, opportunity.currency)}</p></div>)}</div></section>
            <section><h3 className="mb-3 text-xs font-bold uppercase tracking-[.1em] text-[#788ba5]">Outcome cash flows</h3><div className="overflow-hidden rounded-xl border border-[#20344f]"><table className="w-full text-left text-xs"><thead className="bg-[#111e31] text-[9px] uppercase tracking-[.09em] text-[#697c95]"><tr><th className="px-4 py-3">Settlement outcome</th><th className="px-3 py-3 text-right">Gross payout</th><th className="px-4 py-3 text-right">Net result</th></tr></thead><tbody>{opportunity.payouts.map((payout) => <tr key={payout.outcome} className="border-t border-[#1d3049]"><td className="px-4 py-3 text-[#b3c0d2]">{payout.outcome}</td><td className="px-3 py-3 text-right text-[#dbe4f1]">{money(payout.gross, 2, opportunity.currency)}</td><td className={cn("px-4 py-3 text-right font-bold", payout.net >= 0 ? "text-[#22c991]" : "text-[#ff6478]")}>{payout.net >= 0 ? "+" : ""}{money(payout.net, 2, opportunity.currency)}</td></tr>)}</tbody></table></div></section>
            <section className="grid grid-cols-2 gap-3"><div className="rounded-xl border border-[#20344f] bg-[#0f1a2c] p-3"><p className="text-[10px] text-[#71839c]">Worst-case result</p><p className={cn("mt-1 text-sm font-bold", opportunity.worstCase >= 0 ? "text-[#22c991]" : "text-[#ff6478]")}>{money(opportunity.worstCase, 2, opportunity.currency)}</p></div><div className="rounded-xl border border-[#20344f] bg-[#0f1a2c] p-3"><p className="text-[10px] text-[#71839c]">Available modeled depth</p><p className="mt-1 text-sm font-bold text-[#e5edf9]">{opportunity.liquidity === null ? "Unknown" : money(opportunity.liquidity, 2, opportunity.currency)}</p></div></section>
            <section><h3 className="mb-3 text-xs font-bold uppercase tracking-[.1em] text-[#788ba5]">Why it may fail</h3><div className="space-y-2">{opportunity.riskReasons.map((reason) => <div key={reason} className="flex gap-2.5 rounded-xl border border-[#583842]/50 bg-[#2a1820]/35 p-3 text-[11px] leading-5 text-[#c3a3aa]"><TriangleAlert className="mt-0.5 size-3.5 shrink-0 text-[#ff7183]" />{reason}</div>)}</div></section>
          </div>}

          {tab === "settlement" && <div className="space-y-5"><div className={cn("rounded-xl border p-4", opportunity.matchStatus === "VERIFIED MATCH" ? "border-[#285346] bg-[#102b25]/55" : "border-[#64502c] bg-[#2e2615]/45")}><div className="flex items-start gap-3"><ShieldCheck className={cn("mt-0.5 size-5 shrink-0", opportunity.matchStatus === "VERIFIED MATCH" ? "text-[#36d7a2]" : "text-[#efbe5b]")} /><div><p className="text-sm font-semibold text-[#e4edf8]">{opportunity.matchStatus}</p><p className="mt-2 text-xs leading-5 text-[#90a0b5]">{opportunity.settlementSummary}</p></div></div></div><section><h3 className="mb-3 text-xs font-bold uppercase tracking-[.1em] text-[#788ba5]">Verification record</h3><div className="space-y-2">{opportunity.matchReasons.length ? opportunity.matchReasons.map((reason) => <div key={reason} className="flex items-center gap-2.5 rounded-xl border border-[#20344f] bg-[#0f1a2c] p-3 text-xs text-[#aebcd0]"><CheckCircle2 className="size-4 shrink-0 text-[#22c991]" />{reason}</div>) : <p className="rounded-xl border border-[#20344f] bg-[#0f1a2c] p-3 text-xs text-[#71849d]">No verification audit was included in this response.</p>}</div></section><section><h3 className="mb-3 text-xs font-bold uppercase tracking-[.1em] text-[#788ba5]">Data provenance</h3>{opportunity.sources.length ? opportunity.sources.map((source) => <a key={source.label} href={source.url} target="_blank" rel="noreferrer" className="focus-ring flex items-center justify-between rounded-xl border border-[#20344f] bg-[#0f1a2c] p-3 text-xs text-[#8fb8f5] hover:border-[#315179]"><span>{source.label}</span><ExternalLink className="size-3.5" /></a>) : <p className="rounded-xl border border-[#20344f] bg-[#0f1a2c] p-3 text-xs text-[#71849d]">No source reference was included in this response.</p>}<p className="mt-3 text-[10px] leading-4 text-[#647791]">{opportunity.dataMode === "demo" ? "This record is illustrative. Documentation links are not proof of the displayed sample quote." : "Source links identify the upstream reference supplied by the API; freshness and execution constraints still apply."}</p></section></div>}

          {tab === "history" && <div className="space-y-4"><div><h3 className="text-xs font-bold uppercase tracking-[.1em] text-[#788ba5]">Net ROI history</h3><p className="mt-1 text-[11px] text-[#687b95]">{opportunity.dataMode === "demo" ? "Demonstration snapshots · not historical provider data" : "Timestamped observations supplied by the API"}</p></div>{opportunity.history.length ? <div className="h-[250px] rounded-xl border border-[#20344f] bg-[#0e192a] p-3"><ResponsiveContainer width="100%" height="100%"><AreaChart data={opportunity.history} margin={{ left: -26, right: 8, top: 15, bottom: 0 }}><defs><linearGradient id={`history-${opportunity.id}`} x1="0" y1="0" x2="0" y2="1"><stop offset="0%" stopColor="#19c9d5" stopOpacity={.28}/><stop offset="100%" stopColor="#19c9d5" stopOpacity={0}/></linearGradient></defs><CartesianGrid stroke="#1d3049" strokeDasharray="3 5" vertical={false}/><XAxis dataKey="time" axisLine={false} tickLine={false} tick={{ fill: "#647791", fontSize: 10 }}/><YAxis axisLine={false} tickLine={false} tick={{ fill: "#647791", fontSize: 10 }} tickFormatter={(value) => `${value}%`}/><Tooltip contentStyle={{ background: "#0b1423", border: "1px solid #2a405e", borderRadius: 10, fontSize: 11 }} formatter={(value) => [`${Number(value).toFixed(2)}%`, "Net ROI"]}/><Area type="monotone" dataKey="roi" stroke="#19c9d5" strokeWidth={2} fill={`url(#history-${opportunity.id})`}/></AreaChart></ResponsiveContainer></div> : <div className="grid h-[180px] place-items-center rounded-xl border border-[#20344f] bg-[#0e192a] text-xs text-[#71849d]">No historical snapshots were included.</div>}<div className="rounded-xl border border-[#51472e] bg-[#282313]/45 p-3 text-[11px] leading-5 text-[#b7aa82]"><Info className="mr-2 inline size-3.5 text-[#e5bd5d]" />Historical depth must be captured explicitly. Missing depth cannot be inferred from displayed prices.</div></div>}
        </div>

        <footer className="border-t border-[#203149] bg-[#0b1524] p-4 sm:px-7">
          <div className="flex gap-3">
            <Button
              onClick={() => paperMutation.mutate()}
              disabled={paperMutation.isPending || paperMutation.isSuccess || detailState !== "ready" || opportunity.matchStatus !== "VERIFIED MATCH" || opportunity.totalAllocation <= 0 || opportunity.totalFees === null || opportunity.allocations.length === 0 || opportunity.payouts.length === 0}
              className="h-11 flex-1 border-[#4f8cff] bg-[#4f8cff] text-white hover:bg-[#659cff] disabled:border-[#28405f] disabled:bg-[#16253a]"
            >
              {paperMutation.isSuccess ? <><Check className="size-4" /> Paper position recorded</> : detailState === "loading" ? <><LoaderCircle className="size-4 animate-spin" /> Loading detail</> : detailState === "error" ? <><TriangleAlert className="size-4" /> Detail unavailable</> : detailState === "stale" ? <><Clock3 className="size-4" /> Stale quotes</> : detailState === "withdrawn" ? <><TriangleAlert className="size-4" /> Opportunity withdrawn</> : <><Layers3 className="size-4" /> Create paper position</>}
            </Button>
            <Button onClick={onToggleSaved} disabled={saving} className="h-11 px-3.5" aria-label={isSaved ? "Remove saved opportunity" : "Save opportunity"}>
              <Bookmark className={cn("size-4", isSaved && "fill-[#4f8cff] text-[#4f8cff]")} />
            </Button>
          </div>
          {opportunity.matchStatus !== "VERIFIED MATCH" && <p className="mt-2 text-center text-[10px] text-[#9a8050]">Simulation is disabled until settlement equivalence is verified.</p>}
          {detailState === "error" && <p className="mt-2 text-center text-[10px] text-[#c28691]">Retry by closing and reopening this opportunity after the detail API is healthy.</p>}
          {detailState === "stale" && <p className="mt-2 text-center text-[10px] text-[#b99a5b]">Wait for a fresh active server snapshot before recording a new paper position.</p>}
          {detailState === "withdrawn" && <p className="mt-2 text-center text-[10px] text-[#c28691]">Withdrawn records remain visible only as research history.</p>}
          {paperMutation.error && <p role="alert" className="mt-2 text-center text-[10px] text-[#ff91a0]">{paperMutation.error.message}</p>}
        </footer>
      </aside>
    </div>
  );
}

export function ScannerClient({ initialSearch = "" }: { initialSearch?: string }) {
  const queryClient = useQueryClient();
  const [search, setSearch] = useState(initialSearch);
  const [sport, setSport] = useState("all");
  const [league, setLeague] = useState("all");
  const [platform, setPlatform] = useState("all");
  const [market, setMarket] = useState("all");
  const [minRoi, setMinRoi] = useState(0);
  const [minProfit, setMinProfit] = useState(0);
  const [minLiquidity, setMinLiquidity] = useState(0);
  const [maxAge, setMaxAge] = useState(300);
  const [phase, setPhase] = useState<"all" | "prematch" | "live">("all");
  const [verifiedOnly, setVerifiedOnly] = useState(true);
  const [sort, setSort] = useState<SortKey>("score");
  const [page, setPage] = useState(1);
  const [selected, setSelected] = useState<Opportunity | null>(null);
  const [filtersOpen, setFiltersOpen] = useState(false);
  const deferredSearch = useDeferredValue(search);
  const sortParameters = sort === "quoteAgeSeconds"
    ? { sort: "execution_risk" as const, descending: false }
    : {
        sort: ({
          score: "research_score", grossRoi: "theoretical_roi", netRoi: "net_roi",
          potentialProfit: "theoretical_profit", liquidity: "liquidity",
        } as const)[sort],
        descending: true,
      };
  const requestFilters = {
    search: deferredSearch,
    sport: sport === "all" ? undefined : sport,
    league: league === "all" ? undefined : league,
    provider: platform === "all" ? undefined : platform,
    marketType: market === "all" ? undefined : market,
    minimumNetRoiPercent: minRoi || undefined,
    minimumTheoreticalProfit: minProfit || undefined,
    minimumLiquidity: minLiquidity || undefined,
    maximumQuoteAge: maxAge,
    verifiedOnly,
    phase,
    page,
    limit: 50,
    ...sortParameters,
  };
  const query = useQuery({
    queryKey: ["opportunities", requestFilters],
    queryFn: () => getOpportunities(requestFilters),
    ...foregroundLiveRefresh(appMode),
    placeholderData: (previous) => previous,
  });
  const opportunities = useMemo(() => query.data?.data ?? [], [query.data?.data]);
  const savedQuery = useQuery({
    queryKey: ["saved-opportunities"],
    queryFn: getSavedOpportunities,
  });
  const savedMutation = useMutation({
    mutationFn: async ({ id, action, favorited }: { id: string; action: "save" | "delete"; favorited?: boolean }) => {
      if (action === "delete") return deleteSavedOpportunity(id);
      return putSavedOpportunity(id, Boolean(favorited));
    },
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["saved-opportunities"] }),
  });
  const detailQuery = useQuery({
    queryKey: ["opportunity", selected?.id],
    queryFn: () => getOpportunityDetail(selected!.id),
    enabled: Boolean(selected && query.data?.source === "api"),
    retry: false,
    ...foregroundLiveRefresh(appMode),
  });

  const selectedFromList = selected
    ? opportunities.find((item) => item.id === selected.id) ?? selected
    : null;
  const drawerOpportunity = detailQuery.data && selectedFromList
    ? {
        ...detailQuery.data,
        lifecycle: detailQuery.data.lifecycle ?? selectedFromList.lifecycle,
        expiresAt: detailQuery.data.expiresAt ?? selectedFromList.expiresAt,
        withdrawnAt: detailQuery.data.withdrawnAt ?? selectedFromList.withdrawnAt,
      }
    : detailQuery.data ?? selectedFromList;
  const detailLifecycle = drawerOpportunity?.lifecycle ?? null;
  const detailState = detailQuery.isError
    ? "error" as const
    : !detailQuery.data
      ? "loading" as const
      : opportunityDisplayState(detailLifecycle) === "stale"
        ? "stale" as const
        : opportunityDisplayState(detailLifecycle) === "withdrawn"
          ? "withdrawn" as const
          : "ready" as const;
  const leagueOptions = useMemo(() => [...new Set(opportunities.map((item) => item.league))].sort(), [opportunities]);
  const platformOptions = useMemo(() => {
    const observed = opportunities.flatMap((item) => item.legs.map((leg) => leg.platform));
    if (platform !== "all") observed.push(platform);
    return [...new Set(observed)].sort();
  }, [opportunities, platform]);
  const marketOptions = useMemo(() => [...new Set(opportunities.map((item) => item.marketType))].sort(), [opportunities]);
  const filtered = opportunities;
  const savedRecords = useMemo(
    () => new Map((savedQuery.data ?? []).map((item) => [item.opportunity_id, item])),
    [savedQuery.data],
  );
  const saved = useMemo(() => new Set(savedRecords.keys()), [savedRecords]);
  const favorites = useMemo(
    () => new Set([...savedRecords.values()].filter((item) => item.favorited).map((item) => item.opportunity_id)),
    [savedRecords],
  );
  const toggleFavorite = (id: string) => savedMutation.mutate({ id, action: "save", favorited: !favorites.has(id) });
  const toggleSaved = (id: string) => savedMutation.mutate(saved.has(id) ? { id, action: "delete" } : { id, action: "save", favorited: false });
  const reset = () => { setSearch(""); setSport("all"); setLeague("all"); setPlatform("all"); setMarket("all"); setMinRoi(0); setMinProfit(0); setMinLiquidity(0); setMaxAge(300); setPhase("all"); setVerifiedOnly(true); setSort("score"); setPage(1); };

  return (
    <div className="space-y-5">
      <section className="flex flex-col gap-4 lg:flex-row lg:items-end lg:justify-between"><div><div className="flex flex-wrap items-center gap-2"><Badge className="border-[#38537a] bg-[#14243c] text-[#7eaef7]"><Sparkles className="size-3" /> strict research feed</Badge><span className={cn("flex items-center gap-1.5 text-[11px]", query.isError ? "text-[#ff8190]" : "text-[#7eaef7]")}><StatusDot tone={query.isError ? "red" : "blue"} /> {query.isFetching ? "refreshing endpoint" : query.isError ? "API unavailable" : appMode === "live" ? "live endpoint reachable" : "demo endpoint reachable"}</span></div><h2 className="font-display mt-3 text-2xl font-bold tracking-tight text-[#f2f6ff]">Validated pricing discrepancies</h2><p className="mt-1 max-w-2xl text-sm text-[#7c8ea8]">Every strict-feed candidate passes explicit outcome and settlement checks before cost-aware modeling.</p><p className="mt-2 max-w-3xl text-[10px] leading-4 text-[#637791]">{foregroundRefreshDescription(appMode)}</p></div><div className="grid grid-cols-3 gap-2"><div className="rounded-xl border border-[#233854] bg-[#101b2d] px-4 py-2.5"><p className="text-[9px] font-bold uppercase tracking-[.1em] text-[#647791]">Results</p><p className="mt-1 text-base font-bold text-[#e7effa]">{query.data?.total ?? 0}</p></div><div className="rounded-xl border border-[#233854] bg-[#101b2d] px-4 py-2.5"><p className="text-[9px] font-bold uppercase tracking-[.1em] text-[#647791]">Verified on page</p><p className="mt-1 text-base font-bold text-[#22c991]">{opportunities.filter((item) => item.matchStatus === "VERIFIED MATCH").length}</p></div><div className="rounded-xl border border-[#233854] bg-[#101b2d] px-4 py-2.5"><p className="text-[9px] font-bold uppercase tracking-[.1em] text-[#647791]">Feed</p><p className="mt-1 text-base font-bold uppercase text-[#6da6ff]">{appMode === "demo" ? "DEMO" : query.data?.source ?? "—"}</p></div></div></section>
      {query.isError && <div role="alert" className="rounded-xl border border-[#623641] bg-[#321a22]/50 p-4 text-xs text-[#ed97a4]">{query.data ? "The latest opportunity refresh failed. Displayed rows are from the last successful server response and must not be treated as current." : "Opportunity API unavailable. No browser fixtures were substituted."}</div>}
      {(savedQuery.isError || savedMutation.isError) && <div role="alert" className="rounded-xl border border-[#634d29] bg-[#2c2413]/55 p-4 text-xs text-[#d7b66f]">Saved-opportunity sync is unavailable. No browser-only bookmark was substituted.</div>}

      <Panel className="overflow-hidden">
        <button onClick={() => setFiltersOpen(!filtersOpen)} className="flex w-full items-center justify-between border-b border-[#203149] p-4 text-xs font-semibold text-[#a8b7ca] sm:hidden"><span className="flex items-center gap-2"><SlidersHorizontal className="size-4" /> Filters & sorting</span><ChevronDown className={cn("size-4 transition", filtersOpen && "rotate-180")} /></button>
        <div className={cn("hidden sm:block", filtersOpen && "block")}><Filters
          search={search} setSearch={(value) => { setSearch(value); setPage(1); }}
          sport={sport} setSport={(value) => { setSport(value); setPage(1); }}
          league={league} setLeague={(value) => { setLeague(value); setPage(1); }}
          platform={platform} setPlatform={(value) => { setPlatform(value); setPage(1); }}
          market={market} setMarket={(value) => { setMarket(value); setPage(1); }}
          minRoi={minRoi} setMinRoi={(value) => { setMinRoi(value); setPage(1); }}
          minProfit={minProfit} setMinProfit={(value) => { setMinProfit(value); setPage(1); }}
          minLiquidity={minLiquidity} setMinLiquidity={(value) => { setMinLiquidity(value); setPage(1); }}
          maxAge={maxAge} setMaxAge={(value) => { setMaxAge(value); setPage(1); }}
          phase={phase} setPhase={(value) => { setPhase(value); setPage(1); }}
          leagueOptions={leagueOptions} platformOptions={platformOptions} marketOptions={marketOptions}
          verifiedOnly={verifiedOnly} setVerifiedOnly={(value) => { setVerifiedOnly(value); setPage(1); }}
          sort={sort} setSort={(value) => { setSort(value); setPage(1); }} onReset={reset}
        /></div>
        <div className="overflow-x-auto">
          <table className="w-full min-w-[1180px] text-left">
            <thead><tr className="border-b border-[#203149] bg-[#0e192a]/70 text-[9px] font-bold uppercase tracking-[.11em] text-[#61748e]"><th className="w-10 px-4 py-3.5"></th><th className="px-3 py-3.5">Event / market</th><th className="px-3 py-3.5">Platforms & prices</th><th className="px-3 py-3.5">Gross ROI</th><th className="px-3 py-3.5">Net ROI</th><th className="px-3 py-3.5">Net profit</th><th className="px-3 py-3.5">Liquidity</th><th className="px-3 py-3.5">Freshness</th><th className="px-3 py-3.5">Match</th><th className="px-3 py-3.5">Score</th><th className="w-12 px-4 py-3.5"></th></tr></thead>
            <tbody>{filtered.map((opportunity) => { const displayState = opportunityDisplayState(opportunity.lifecycle); return <tr key={opportunity.id} tabIndex={0} aria-label={`Inspect ${opportunity.event}`} onKeyDown={(event) => { if (event.key === "Enter" || event.key === " ") { event.preventDefault(); setSelected(opportunity); } }} onClick={() => setSelected(opportunity)} className={cn("focus-ring group cursor-pointer border-b border-[#1b2c43] text-xs transition last:border-0 hover:bg-[#142239]/70", displayState === "withdrawn" && "opacity-70")}><td className="px-4 py-4"><button onClick={(event) => { event.stopPropagation(); toggleFavorite(opportunity.id); }} aria-label={favorites.has(opportunity.id) ? "Remove favorite" : "Favorite opportunity"} className="focus-ring rounded-md p-1"><Heart className={cn("size-4 transition", favorites.has(opportunity.id) ? "fill-[#ff6478] text-[#ff6478]" : "text-[#50627a] group-hover:text-[#7d8ea6]")} /></button></td><td className="max-w-[260px] px-3 py-4"><p className="truncate font-semibold text-[#dde7f4]">{opportunity.event}</p><p className="mt-1 truncate text-[10px] text-[#677b95]">{opportunity.league} · {opportunity.market}</p></td><td className="px-3 py-4"><div className="space-y-1.5">{opportunity.legs.slice(0,3).map((leg) => <div key={`${leg.platform}-${leg.outcome}`} className="flex min-w-[190px] items-center justify-between gap-3"><PlatformBadge name={leg.platform} /><span className="font-mono text-[11px] font-semibold text-[#d6e0ed]">{leg.decimalOdds.toFixed(2)}</span></div>)}</div></td><td className="px-3 py-4 font-medium text-[#9eadbf]">{percent(opportunity.grossRoi)}</td><td className={cn("px-3 py-4 font-bold", displayState === "active" ? "text-[#22c991]" : "text-[#9eadbf]")}>{percent(opportunity.netRoi)}</td><td className="px-3 py-4 font-semibold text-[#dfe8f4]">{money(opportunity.potentialProfit, 2, opportunity.currency)}</td><td className="px-3 py-4 text-[#a0afc1]">{opportunity.liquidity === null ? "Unknown" : money(opportunity.liquidity, 0, opportunity.currency)}</td><td className="px-3 py-4"><span className={cn("inline-flex items-center gap-1.5", displayState === "withdrawn" ? "text-[#ff8190]" : displayState === "stale" || (opportunity.quoteAgeSeconds ?? Number.POSITIVE_INFINITY) > 60 ? "text-[#d0a75c]" : "text-[#7fbf9f]")}><span className={cn("size-1.5 rounded-full", displayState === "withdrawn" ? "bg-[#ff6478]" : displayState === "stale" || (opportunity.quoteAgeSeconds ?? Number.POSITIVE_INFINITY) > 60 ? "bg-[#e4aa43]" : "bg-[#22c991]")} />{displayState === "withdrawn" ? opportunityLifecycleLabel(opportunity.lifecycle) : displayState === "stale" ? "Stale" : opportunity.quoteAgeSeconds === null ? "Unknown" : `${opportunity.quoteAgeSeconds}s`}</span><p className="mt-1 text-[9px] uppercase tracking-wide text-[#596b84]">{opportunity.dataMode}{opportunity.expiresAt ? ` · expires ${timestampLabel(opportunity.expiresAt)}` : ""}</p></td><td className="px-3 py-4"><MatchBadge status={opportunity.matchStatus} /></td><td className="px-3 py-4"><span className="inline-flex min-w-9 justify-center rounded-lg bg-[#1a3150] px-2 py-1.5 font-bold text-[#79acfa]">{opportunity.score}</span></td><td className="px-4 py-4"><button onClick={(event) => { event.stopPropagation(); toggleSaved(opportunity.id); }} aria-label={saved.has(opportunity.id) ? "Unsave opportunity" : "Save opportunity"} className="focus-ring rounded-md p-1"><Bookmark className={cn("size-4", saved.has(opportunity.id) ? "fill-[#4f8cff] text-[#4f8cff]" : "text-[#566981]")} /></button></td></tr>; })}</tbody>
          </table>
          {filtered.length === 0 && <div className="flex flex-col items-center justify-center px-6 py-16 text-center"><span className="grid size-12 place-items-center rounded-2xl bg-[#16253b] text-[#6987b1]"><Search className="size-5" /></span><p className="mt-4 text-sm font-semibold text-[#d9e3f0]">No opportunities match</p><p className="mt-1 text-xs text-[#70829b]">Broaden the filters or include review-required candidates.</p><Button onClick={reset} className="mt-4 h-9">Reset filters</Button></div>}
        </div>
        <div className="flex flex-col gap-3 border-t border-[#203149] bg-[#0d1727] px-5 py-3 text-[10px] text-[#667991] sm:flex-row sm:items-center sm:justify-between"><span>Showing {filtered.length} of {query.data?.total ?? 0} server-filtered {appMode === "demo" ? "demonstration" : "API"} candidates</span><div className="flex items-center gap-2"><span>{appMode === "demo" ? "Illustrative—not executable." : "API provenance and freshness."}</span><button className="focus-ring rounded-lg border border-[#263a57] px-2.5 py-1.5 disabled:opacity-40" disabled={page <= 1 || query.isFetching} onClick={() => setPage((value) => Math.max(1, value - 1))}>Previous</button><span>Page {page}</span><button className="focus-ring rounded-lg border border-[#263a57] px-2.5 py-1.5 disabled:opacity-40" disabled={!query.data?.hasMore || query.isFetching} onClick={() => setPage((value) => value + 1)}>Next</button></div></div>
      </Panel>
      {selected && drawerOpportunity && <DetailDrawer key={selected.id}
        opportunity={drawerOpportunity}
        detailState={detailState}
        onClose={() => setSelected(null)}
        isSaved={saved.has(selected.id)}
        onToggleSaved={() => toggleSaved(selected.id)}
        saving={savedMutation.isPending}
      />}
    </div>
  );
}
