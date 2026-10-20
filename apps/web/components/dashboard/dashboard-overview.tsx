"use client";

import { useMemo, useState } from "react";
import Link from "next/link";
import { useQuery } from "@tanstack/react-query";
import { Area, AreaChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import {
  ArrowRight, BadgeDollarSign, BarChart3, CheckCircle2, Crosshair, LoaderCircle,
  ShieldCheck, TrendingDown, Wallet,
} from "lucide-react";
import { Badge, Panel } from "@/components/ui";
import { getDashboardSummary } from "@/lib/dashboard-api";
import { DASHBOARD_RANGES, dashboardRangeBounds, type DashboardRange } from "@/lib/dashboard-range";
import { getAnalytics } from "@/lib/workflow-api";
import { appMode, getOpportunities } from "@/lib/api";
import { foregroundLiveRefresh, foregroundRefreshDescription } from "@/lib/live-refresh";
import { opportunityDisplayState, opportunityLifecycleLabel } from "@/lib/opportunity-lifecycle";
import { money, percent } from "@/lib/utils";

function ChartTooltip({ active, payload, label }: { active?: boolean; payload?: { value?: number }[]; label?: string }) {
  if (!active || !payload?.[0]) return null;
  return <div className="rounded-xl border border-[#2a405e] bg-[#0b1423]/95 px-3 py-2 shadow-2xl"><p className="text-[10px] text-[#8293aa]">{label}</p><p className="mt-0.5 text-sm font-bold text-[#f1f6ff]">{money(payload[0].value ?? 0)} <span className="font-normal text-[#22c991]">cumulative</span></p></div>;
}

export function DashboardOverview() {
  const [range, setRange] = useState<DashboardRange>("30D");
  const dates = useMemo(() => dashboardRangeBounds(range), [range]);
  const summary = useQuery({ queryKey: ["dashboard-summary"], queryFn: getDashboardSummary, ...foregroundLiveRefresh(appMode) });
  const analytics = useQuery({ queryKey: ["dashboard-analytics", dates], queryFn: () => getAnalytics(dates.from, dates.to), ...foregroundLiveRefresh(appMode) });
  const opportunities = useQuery({
    queryKey: ["dashboard-opportunities"],
    queryFn: () => getOpportunities({ verifiedOnly: true, sort: "research_score", descending: true, page: 1, limit: 3 }),
    ...foregroundLiveRefresh(appMode),
  });

  const overview = summary.data;
  const performance = analytics.data;
  const mode = overview?.is_demo ? "DEMO" : "LIVE";
  const opportunityFallback = opportunities.data?.source === "demo";
  const captureRate = performance?.summary.attempted_opportunities
    ? performance.summary.captured_opportunities / performance.summary.attempted_opportunities
    : null;
  const chartData = (performance?.equity ?? []).map((point) => ({
    timestamp: point.timestamp,
    date: new Date(point.timestamp).toLocaleDateString(undefined, { month: "short", day: "numeric" }),
    pnl: Number(point.cumulative_pnl),
    portfolioValue: Number(point.portfolio_value),
  }));
  const isPending = summary.isPending || analytics.isPending || opportunities.isPending;
  const error = summary.error ?? analytics.error ?? opportunities.error;
  const metrics = overview ? [
    { label: "Paper portfolio", value: money(Number(overview.simulated_portfolio_value)), detail: `${overview.active_simulated_positions} active · ${money(Number(overview.unrealized_simulated_pnl))} unrealized`, icon: Wallet, tone: "text-[#6ca5ff] bg-[#4f8cff]/10" },
    { label: "Realized paper P&L", value: performance ? money(Number(performance.summary.realized_pnl)) : analytics.isError ? "Unavailable" : "Loading…", detail: performance ? `${range} UTC · ${percent(Number(performance.summary.net_roi) * 100)} net ROI` : analytics.isError ? `Could not load the ${range} ledger` : `Loading ${range} records`, icon: BadgeDollarSign, tone: "text-[#35d6a1] bg-[#22c991]/10" },
    { label: "Verified opportunities", value: String(overview.verified_opportunities), detail: `${overview.opportunities_detected} detected by current feed`, icon: CheckCircle2, tone: "text-[#35d8e2] bg-[#19c9d5]/10" },
    { label: "Recorded capture rate", value: captureRate === null ? "Insufficient data" : `${(captureRate * 100).toFixed(1)}%`, detail: performance ? `${performance.summary.captured_opportunities} captured / ${performance.summary.attempted_opportunities} attempted` : "No denominator", icon: Crosshair, tone: "text-[#ad8cff] bg-[#8b6fff]/10" },
  ] : [];

  return <div className="space-y-6">
    <section className="flex flex-col gap-3 sm:flex-row sm:items-end sm:justify-between">
      <div><div className="flex items-center gap-2"><Badge className={overview?.is_demo ? "border-[#665329] bg-[#302713] text-[#f0c76d]" : "border-[#2b5a4c] bg-[#15332b] text-[#51d3a9]"}>{overview ? `${mode} workspace` : "Workspace"}</Badge><span className="text-xs text-[#687b96]">{summary.isFetching ? "Refreshing server records…" : appMode === "live" ? "Server response received" : "Demonstration records"}</span></div><h2 className="font-display mt-3 text-2xl font-bold tracking-tight text-[#f2f6ff] sm:text-[28px]">Market intelligence, distilled.</h2><p className="mt-1 text-sm text-[#7f90aa]">Recorded paper activity and settlement-verified research in one focused workspace.</p><p className="mt-2 max-w-3xl text-[10px] leading-4 text-[#637791]">{foregroundRefreshDescription(appMode)}</p></div>
      <Link href="/scanner" className="focus-ring inline-flex h-10 items-center justify-center gap-2 rounded-xl bg-[#4f8cff] px-4 text-sm font-bold text-white hover:bg-[#68a0ff]">Open scanner <ArrowRight className="size-4" /></Link>
    </section>

    {isPending && !overview && <Panel className="flex items-center gap-3 p-5 text-sm text-[#8294aa]" role="status"><LoaderCircle className="size-4 animate-spin" /> Loading recorded dashboard data…</Panel>}
    {error && <Panel role="alert" className="border-[#603545] p-5 text-sm text-[#ff91a0]"><p className="font-semibold">Dashboard refresh failed</p><p className="mt-1 text-xs">{error instanceof Error ? error.message : "The API request failed."}</p><p className="mt-2 text-xs text-[#bd8290]">{overview ? "Displayed values are from the last successful response and should be treated as last known." : "No demonstration performance was substituted."}</p></Panel>}
    {overview && <section className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">{metrics.map(({ label, value, detail, icon: Icon, tone }) => <Panel key={label} className="p-5"><div className="flex items-start justify-between"><span className={`grid size-9 place-items-center rounded-xl ${tone}`}><Icon className="size-[18px]" /></span><span className="rounded-lg bg-[#0c1728] px-2 py-1 text-[9px] font-semibold text-[#8294ad]">{mode} · PAPER</span></div><p className="mt-5 text-xs font-medium text-[#7d8fa8]">{label}</p><p className="font-display mt-1.5 text-2xl font-bold tracking-tight text-[#eff5ff]">{value}</p><p className="mt-2 text-[11px] text-[#62758f]">{detail}</p></Panel>)}</section>}

    <section className="grid gap-5 xl:grid-cols-[minmax(0,1.65fr)_minmax(310px,.7fr)]">
      <Panel className="overflow-hidden p-5 sm:p-6">
        <div className="flex flex-wrap items-start justify-between gap-3"><div><p className="text-sm font-semibold text-[#dfe8f5]">Recorded portfolio equity</p><p className="mt-1 text-xs text-[#71839d]">Cumulative paper cash flow · projected profit excluded</p></div><div className="flex rounded-lg border border-[#233754] bg-[#0d1728] p-0.5 text-[10px] font-semibold" role="group" aria-label="Dashboard performance range">{DASHBOARD_RANGES.map((item) => <button key={item} type="button" onClick={() => setRange(item)} aria-pressed={range === item} className={`focus-ring rounded-md px-2.5 py-1 ${range === item ? "bg-[#1a2b45] text-[#cde0fb]" : "text-[#74869f]"}`}>{item}</button>)}</div></div>
        {analytics.isPending
          ? <div className="grid min-h-[270px] place-items-center text-sm text-[#8294aa]" role="status"><span className="flex items-center gap-2"><LoaderCircle className="size-4 animate-spin" /> Loading the {range} ledger…</span></div>
          : analytics.isError
            ? <div className="grid min-h-[270px] place-items-center text-center"><div><BarChart3 className="mx-auto size-8 text-[#765767]" /><p className="mt-4 text-sm font-semibold text-[#f0dce2]">Performance data unavailable</p><p className="mt-2 text-xs text-[#9a7b87]">The chart has not been replaced with sample performance.</p></div></div>
          : chartData.length
            ? <><div className="mt-6 h-[270px] w-full" aria-hidden="true"><ResponsiveContainer width="100%" height="100%"><AreaChart data={chartData} margin={{ left: -20, right: 5, top: 10, bottom: 0 }}><defs><linearGradient id="pnl-fill" x1="0" y1="0" x2="0" y2="1"><stop offset="0%" stopColor="#4f8cff" stopOpacity={0.32}/><stop offset="95%" stopColor="#4f8cff" stopOpacity={0}/></linearGradient></defs><CartesianGrid stroke="#1d2c43" strokeDasharray="3 6" vertical={false} /><XAxis dataKey="date" tick={{ fill: "#64768f", fontSize: 10 }} tickLine={false} axisLine={false} /><YAxis tick={{ fill: "#64768f", fontSize: 10 }} tickLine={false} axisLine={false} tickFormatter={(value) => `$${value}`} /><Tooltip content={<ChartTooltip />} /><Area type="monotone" dataKey="pnl" stroke="#4f8cff" strokeWidth={2.2} fill="url(#pnl-fill)" /></AreaChart></ResponsiveContainer></div><details className="mt-3 rounded-xl border border-[#20344f] bg-[#0e192a] p-3"><summary className="focus-ring cursor-pointer text-xs font-semibold text-[#a9bad0]">View equity chart as a table</summary><div className="mt-3 overflow-x-auto"><table className="w-full text-left text-xs"><thead><tr className="text-[#71839b]"><th className="py-2">Timestamp</th><th>Portfolio value</th><th>Cumulative P&amp;L</th></tr></thead><tbody>{chartData.map((point) => <tr key={point.timestamp} className="border-t border-[#203149]"><td className="py-2">{new Date(point.timestamp).toLocaleString()}</td><td>{money(point.portfolioValue)}</td><td>{money(point.pnl)}</td></tr>)}</tbody></table></div></details></>
            : <div className="grid min-h-[270px] place-items-center text-center"><div><BarChart3 className="mx-auto size-8 text-[#536a87]" /><p className="mt-4 text-sm font-semibold text-[#dde7f3]">No recorded activity in this range</p><p className="mt-2 text-xs text-[#71839c]">Create and settle paper positions to populate performance.</p></div></div>}
      </Panel>

      <Panel className="p-5 sm:p-6"><div className="flex items-start gap-3"><ShieldCheck className="mt-0.5 size-5 text-[#35d8e2]" /><div><p className="text-sm font-semibold text-[#dfe8f5]">Validation status</p><p className="mt-1 text-xs text-[#71839d]">Current server-filtered feed</p></div></div>{overview && opportunities.data ? <><dl className="mt-6 space-y-3">{[
        ["Detected opportunities", overview.opportunities_detected],
        ["Settlement verified", overview.verified_opportunities],
        ["Displayed strict candidates", opportunities.data.total],
        ["Active paper positions", overview.active_simulated_positions],
      ].map(([label, value]) => <div key={String(label)} className="flex justify-between rounded-xl border border-[#20344f] bg-[#0e192a] p-3 text-xs"><dt className="text-[#7d8fa7]">{label}</dt><dd className="font-semibold text-[#dce6f3]">{value}</dd></div>)}</dl><p className="mt-5 text-[10px] leading-5 text-[#71839c]">Raw pair and matching-stage counts are intentionally omitted until durable ingestion records those denominators.</p></> : summary.isPending || opportunities.isPending ? <div className="mt-6 flex min-h-40 items-center justify-center gap-2 text-xs text-[#8294aa]" role="status"><LoaderCircle className="size-4 animate-spin" /> Loading validation records…</div> : <div className="mt-6 grid min-h-40 place-items-center text-center text-xs text-[#9a7b87]">Validation records are unavailable.</div>}</Panel>
    </section>

    <Panel className="overflow-hidden"><div className="flex items-center justify-between border-b border-[#203149] px-5 py-4 sm:px-6"><div><p className="text-sm font-semibold text-[#dfe8f5]">Top verified research candidates</p><p className={opportunityFallback ? "mt-1 text-xs text-[#d1a95d]" : "mt-1 text-xs text-[#71839d]"}>{opportunityFallback ? "Bundled demo fixture · scanner API unavailable" : overview?.is_demo ? "Demo API records · backend-ranked" : "Backend-ranked and server-filtered"}</p>{opportunityFallback && opportunities.data?.error && <p className="mt-1 text-[10px] text-[#9a7d4b]">{opportunities.data.error}</p>}</div><Link href="/scanner" className="text-xs font-semibold text-[#6ba3ff]">View all</Link></div>{opportunities.isPending ? <div className="flex min-h-32 items-center justify-center gap-2 text-xs text-[#8294aa]" role="status"><LoaderCircle className="size-4 animate-spin" /> Loading verified candidates…</div> : opportunities.isError ? <div className="px-6 py-12 text-center"><CheckCircle2 className="mx-auto size-8 text-[#765767]" /><p className="mt-3 text-sm font-semibold text-[#f0dce2]">Candidate feed unavailable</p><p className="mt-1 text-xs text-[#9a7b87]">No sample candidates were substituted in live mode.</p></div> : opportunities.data?.data.length ? <div className="overflow-x-auto"><table className="w-full min-w-[820px] text-left"><thead><tr className="border-b border-[#1d2e46] text-[10px] uppercase tracking-[.11em] text-[#60738f]"><th className="px-6 py-3.5">Event</th><th className="px-4 py-3.5">Pair</th><th className="px-4 py-3.5">Status</th><th className="px-4 py-3.5">Net ROI</th><th className="px-4 py-3.5">Modeled net</th><th className="px-4 py-3.5">Liquidity</th><th className="px-6 py-3.5 text-right">Score</th></tr></thead><tbody>{opportunities.data.data.map((opportunity) => { const state = opportunityDisplayState(opportunity.lifecycle); return <tr key={opportunity.id} className="border-b border-[#1b2b42] text-xs last:border-0"><td className="px-6 py-4"><p className="font-semibold text-[#dfe7f3]">{opportunity.event}</p><p className="mt-1 text-[10px] text-[#647791]">{opportunity.league} · {opportunity.market}</p></td><td className="px-4 py-4 text-[#93a3b9]">{opportunity.legs.map((leg) => leg.platform).join(" / ")}</td><td className="px-4 py-4"><span className={opportunity.lifecycle === null ? "text-[#71839b]" : state === "active" ? "text-[#55d7ad]" : state === "stale" ? "text-[#e6bd65]" : "text-[#ff8190]"}>{opportunityLifecycleLabel(opportunity.lifecycle) ?? "Not reported"}</span></td><td className={opportunity.lifecycle === "active" ? "px-4 py-4 font-bold text-[#22c991]" : "px-4 py-4 font-bold text-[#93a3b9]"}>{percent(opportunity.netRoi)}</td><td className="px-4 py-4 font-semibold text-[#dce7f7]">{money(opportunity.potentialProfit)}</td><td className="px-4 py-4 text-[#a0aec0]">{opportunity.liquidity === null ? "Unknown" : money(opportunity.liquidity, 0)}</td><td className="px-6 py-4 text-right"><span className="rounded-lg bg-[#1b3450] px-2 py-1 font-bold text-[#74a9fb]">{opportunity.score}</span></td></tr>; })}</tbody></table></div> : <div className="px-6 py-12 text-center"><CheckCircle2 className="mx-auto size-8 text-[#536a87]" /><p className="mt-3 text-sm font-semibold text-[#dde7f3]">No verified candidates</p><p className="mt-1 text-xs text-[#71839c]">No qualifying server records match the strict feed.</p></div>}</Panel>

    {performance && <div className="grid gap-4 md:grid-cols-3"><Panel className="p-4"><p className="text-xs text-[#7789a2]">Reserved capital</p><p className="mt-1 text-sm font-semibold text-[#e5edf8]">{money(Number(performance.summary.reserved_capital))}</p></Panel><Panel className="p-4"><p className="flex items-center gap-2 text-xs text-[#7789a2]"><TrendingDown className="size-4" /> Maximum recorded drawdown</p><p className="mt-1 text-sm font-semibold text-[#e5edf8]">{performance.summary.maximum_drawdown === null ? "Insufficient data" : money(Number(performance.summary.maximum_drawdown))}</p></Panel><Panel className="p-4"><p className="text-xs text-[#7789a2]">Fees / turnover</p><p className="mt-1 text-sm font-semibold text-[#e5edf8]">{money(Number(performance.summary.fees))} / {money(Number(performance.summary.turnover))}</p></Panel></div>}
  </div>;
}
