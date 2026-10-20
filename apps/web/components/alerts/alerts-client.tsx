"use client";

import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { BellRing, CheckCircle2, LoaderCircle, Plus, ToggleLeft, ToggleRight, Trash2 } from "lucide-react";
import { PageIntro } from "@/components/page-intro";
import { WorkflowDialog } from "@/components/workflows/workflow-dialog";
import { Badge, Button, Panel } from "@/components/ui";
import { appMode } from "@/lib/api";
import { foregroundLiveRefresh } from "@/lib/live-refresh";
import { createAlert, deleteAlert, getAlerts, updateAlert, type AlertRule } from "@/lib/workflow-api";

const conditionLabels: Record<AlertRule["condition"], string> = {
  new_verified_opportunity: "New verified opportunity",
  net_roi_above: "Modeled net ROI above",
  liquidity_increase: "Supported liquidity increases",
  spread_widens: "Modeled spread widens",
  quote_stale: "Quote becomes stale",
  market_closed: "Market closes",
  market_settled: "Market settles",
  provider_offline: "Provider becomes unavailable",
};
const thresholdConditions = new Set<AlertRule["condition"]>([
  "net_roi_above", "liquidity_increase", "spread_widens", "quote_stale",
]);
const inputClass = "focus-ring mt-2 h-11 w-full rounded-xl border border-[#293e5b] bg-[#0d1829] px-3 text-[#e5edf8]";

function ratioPercent(value: string): string {
  const number = Number(value);
  return Number.isFinite(number) ? `${(number * 100).toFixed(2)}%` : value;
}

function percentageInputToRatio(value: string): string | null {
  const trimmed = value.trim();
  return trimmed ? (Number(trimmed) / 100).toString() : null;
}

function optionalDecimal(value: string): string | null {
  const trimmed = value.trim();
  return trimmed || null;
}

function thresholdText(rule: AlertRule): string {
  if (rule.threshold === null) return conditionLabels[rule.condition];
  if (rule.condition === "net_roi_above" || rule.condition === "spread_widens") {
    return `${conditionLabels[rule.condition]} ${ratioPercent(rule.threshold)}`;
  }
  if (rule.condition === "quote_stale") return `${conditionLabels[rule.condition]} after ${rule.threshold}s`;
  return `${conditionLabels[rule.condition]} to ${rule.threshold}`;
}

export function alertGateLabels(rule: AlertRule): string[] {
  const labels: string[] = [];
  if (rule.minimum_net_profit != null) labels.push(`Net profit ≥ ${rule.minimum_net_profit} workspace currency`);
  if (rule.minimum_net_roi != null) labels.push(`Net ROI ≥ ${ratioPercent(rule.minimum_net_roi)}`);
  if (rule.minimum_liquidity != null) labels.push(`Liquidity ≥ ${rule.minimum_liquidity} workspace currency`);
  if (rule.maximum_quote_age_seconds != null) labels.push(`Quote age ≤ ${rule.maximum_quote_age_seconds}s`);
  return labels;
}

export function AlertsClient() {
  const queryClient = useQueryClient();
  const desktopBuild = process.env.NEXT_PUBLIC_DESKTOP_BUILD === "true";
  const [dialog, setDialog] = useState(false);
  const [name, setName] = useState("");
  const [condition, setCondition] = useState<AlertRule["condition"]>("net_roi_above");
  const [threshold, setThreshold] = useState("2.00");
  const [cooldown, setCooldown] = useState("300");
  const [minimumNetProfit, setMinimumNetProfit] = useState("");
  const [minimumNetRoi, setMinimumNetRoi] = useState("");
  const [minimumLiquidity, setMinimumLiquidity] = useState("");
  const [maximumQuoteAge, setMaximumQuoteAge] = useState("");
  const alerts = useQuery({ queryKey: ["alerts"], queryFn: getAlerts, retry: false, ...foregroundLiveRefresh(appMode) });
  const addMutation = useMutation({
    mutationFn: async () => {
      const normalizedThreshold = thresholdConditions.has(condition)
        ? condition === "net_roi_above" || condition === "spread_widens"
          ? percentageInputToRatio(threshold)
          : threshold
        : null;
      return createAlert({
        name: name.trim(),
        condition,
        threshold: normalizedThreshold,
        minimum_net_profit: optionalDecimal(minimumNetProfit),
        minimum_net_roi: percentageInputToRatio(minimumNetRoi),
        minimum_liquidity: optionalDecimal(minimumLiquidity),
        maximum_quote_age_seconds: maximumQuoteAge.trim() ? Number(maximumQuoteAge) : null,
        enabled: true,
        cooldown_seconds: Number(cooldown),
        idempotency_key: crypto.randomUUID(),
      });
    },
    onSuccess: (rule) => {
      queryClient.setQueryData<Awaited<ReturnType<typeof getAlerts>>>(["alerts"], (old) => old ? { ...old, items: [...old.items, rule] } : old);
      void queryClient.invalidateQueries({ queryKey: ["alerts"] });
      setName("");
      setMinimumNetProfit("");
      setMinimumNetRoi("");
      setMinimumLiquidity("");
      setMaximumQuoteAge("");
      setDialog(false);
    },
  });
  const updateMutation = useMutation({
    mutationFn: ({ rule, enabled }: { rule: AlertRule; enabled: boolean }) => updateAlert(rule.id, { enabled }),
    onSuccess: (rule) => queryClient.setQueryData<Awaited<ReturnType<typeof getAlerts>>>(["alerts"], (old) => old ? { ...old, items: old.items.map((item) => item.id === rule.id ? rule : item) } : old),
  });
  const deleteMutation = useMutation({
    mutationFn: async (rule: AlertRule) => { await deleteAlert(rule.id); return rule.id; },
    onSuccess: (id) => queryClient.setQueryData<Awaited<ReturnType<typeof getAlerts>>>(["alerts"], (old) => old ? { ...old, items: old.items.filter((rule) => rule.id !== id) } : old),
  });
  const rules = alerts.data?.items ?? [];
  const events = alerts.data?.events ?? [];

  return <div className="space-y-5">
    <PageIntro
      badge="in-app monitoring"
      title="Alerts"
      description={appMode === "live" ? "Configure server-side research rules and review persisted in-app events." : "Configure demonstration rules and review demonstration event records."}
      actions={<Button onClick={() => setDialog(true)} className="border-[#4f8cff] bg-[#4f8cff] text-white"><Plus className="size-4" /> New alert</Button>}
    />
    <Panel className={`px-4 py-3 text-[11px] leading-5 ${appMode === "live" ? "border-[#294c49] text-[#8fc4b5]" : "border-[#5d4d2e] text-[#c8ae73]"}`}>
      {appMode === "live"
        ? `This page polls persisted rules and events every 15 seconds while visible${alerts.dataUpdatedAt ? ` (last checked ${new Date(alerts.dataUpdatedAt).toLocaleString()})` : ""}. This window does not run the evaluator; after it closes, monitoring continues only if a separately deployed EDGEOS server worker remains healthy.`
        : "DEMO ONLY — saved rules and notifications are illustrative workspace records. No live opportunity feed is evaluated, and automatic polling is off."}
    </Panel>
    {alerts.isPending && <Panel className="flex items-center gap-3 p-5 text-sm text-[#8294aa]"><LoaderCircle className="size-4 animate-spin" /> Loading alert rules…</Panel>}
    {alerts.error && <Panel role="alert" className="flex flex-col gap-3 border-[#603545] p-5 text-sm text-[#f17e90] sm:flex-row sm:items-center sm:justify-between"><span>{alerts.data ? `Latest alert refresh failed: ${alerts.error.message}. Displayed records are last known.` : `Alert data unavailable: ${alerts.error.message}. No browser fixtures were substituted.`}</span><Button onClick={() => void alerts.refetch()} className="shrink-0">Retry</Button></Panel>}
    {(updateMutation.error || deleteMutation.error) && <Panel role="alert" className="border-[#603545] p-4 text-xs text-[#ff91a0]">{(updateMutation.error ?? deleteMutation.error)?.message}</Panel>}
    {(alerts.data || !alerts.isError) && <div className="grid gap-5 xl:grid-cols-[1.3fr_.7fr]">
      <Panel className="overflow-hidden">
        <div className="border-b border-[#203149] px-5 py-4"><p className="text-sm font-semibold text-[#e2eaf5]">Alert rules</p><p className="mt-1 text-xs text-[#6d8099]">{alerts.data?.mode === "demo" ? (desktopBuild ? "Device-local demonstration records" : "Current demonstration workspace records") : "Private persisted rules"}</p></div>
        {rules.length ? <div className="divide-y divide-[#1d2f48]">{rules.map((rule) => {
          const gates = alertGateLabels(rule);
          return <div key={rule.id} className="flex items-start gap-4 p-5">
            <button type="button" onClick={() => updateMutation.mutate({ rule, enabled: !rule.enabled })} disabled={updateMutation.isPending} aria-label={`${rule.enabled ? "Disable" : "Enable"} ${rule.name}`} aria-pressed={rule.enabled} className={`focus-ring rounded-lg ${rule.enabled ? "text-[#4f8cff]" : "text-[#576981]"}`}>{rule.enabled ? <ToggleRight className="size-7" /> : <ToggleLeft className="size-7" />}</button>
            <div className="min-w-0 flex-1"><div className="flex flex-wrap items-center gap-2"><p className="truncate text-sm font-semibold text-[#dfe8f4]">{rule.name}</p><Badge className={rule.enabled ? "border-[#2d5a4d] bg-[#15342b] text-[#50d4aa]" : ""}>{rule.enabled ? "ENABLED" : "DISABLED"}</Badge>{rule.is_demo && <Badge className="border-[#665329] bg-[#302713] text-[#f0c76d]">DEMO</Badge>}</div><p className="mt-1 text-xs text-[#6d8099]">{thresholdText(rule)} · cooldown {rule.cooldown_seconds}s</p><div className="mt-2 flex flex-wrap gap-1.5">{gates.length ? gates.map((gate) => <span key={gate} className="rounded-md border border-[#2a405e] bg-[#111e31] px-2 py-1 text-[9px] text-[#8fa4bd]">{gate}</span>) : <span className="text-[10px] text-[#596d86]">No additional eligibility gates</span>}</div>{rule.last_triggered_at && <p className="mt-2 text-[10px] text-[#657891]">Last triggered {new Date(rule.last_triggered_at).toLocaleString()}</p>}</div>
            <button type="button" onClick={() => deleteMutation.mutate(rule)} disabled={deleteMutation.isPending} className="focus-ring rounded-lg p-2 text-[#677b94] hover:bg-[#2b1b24] hover:text-[#ff7183]" aria-label={`Delete ${rule.name}`}><Trash2 className="size-4" /></button>
          </div>;
        })}</div> : !alerts.isPending && <div className="px-6 py-16 text-center"><BellRing className="mx-auto size-8 text-[#4c6079]" /><p className="mt-4 text-sm font-semibold text-[#dce6f2]">No alert rules</p><p className="mt-1 text-xs text-[#71839b]">{appMode === "live" ? "Create a server-side rule. Events appear only when the configured evaluator observes a qualifying live snapshot." : "Create a demonstration workspace rule; it will not monitor live feeds."}</p></div>}
      </Panel>
      <Panel className="p-5"><div className="flex items-center gap-3"><span className="grid size-10 place-items-center rounded-xl bg-[#4f8cff]/10 text-[#70a8ff]"><BellRing className="size-5" /></span><div><p className="text-sm font-semibold text-[#e2eaf5]">Recent notifications</p><p className="mt-1 text-xs text-[#6d8099]">In-app event records for this workspace</p></div></div><div className="mt-5 space-y-3">{events.length ? events.map((item) => <div key={item.id} className="flex gap-3 rounded-xl border border-[#20344f] bg-[#0e192a] p-3"><CheckCircle2 className="mt-0.5 size-4 shrink-0 text-[#22c991]" /><div><p className="text-xs font-medium text-[#cbd7e7]">{item.title}</p><p className="mt-1 text-xs leading-4 text-[#71839b]">{item.body}</p><p className="mt-1 text-xs text-[#657891]">{new Date(item.triggered_at).toLocaleString()}{item.is_demo ? " · DEMO" : ""}</p></div></div>) : <p className="rounded-xl border border-dashed border-[#29405e] px-4 py-8 text-center text-xs text-[#71839b]">No alert events in this range.</p>}</div><p className="mt-4 text-xs leading-4 text-[#657891]">Only in-app delivery is represented. No email, SMS, or push message is sent without separate explicit setup.</p></Panel>
    </div>}
    <WorkflowDialog open={dialog} onClose={() => setDialog(false)} title="New alert rule" description={appMode === "live" ? "The rule and cooldown are saved to the server. Event generation requires a healthy, separately deployed evaluator worker." : "The rule is saved as demonstration workspace data and does not evaluate live feeds."}>
      <form className="mt-5 space-y-4" onSubmit={(event) => { event.preventDefault(); addMutation.mutate(); }}>
        <label className="block text-xs text-[#8192a9]">Rule name<input autoFocus required value={name} onChange={(event) => setName(event.target.value)} className={inputClass} placeholder="e.g. NFL executable edge" /></label>
        <label className="block text-xs text-[#8192a9]">Trigger condition<select value={condition} onChange={(event) => setCondition(event.target.value as AlertRule["condition"])} className={inputClass}>{Object.entries(conditionLabels).map(([key, value]) => <option key={key} value={key}>{value}</option>)}</select></label>
        {thresholdConditions.has(condition) && <label className="block text-xs text-[#8192a9]">{condition === "net_roi_above" || condition === "spread_widens" ? "Trigger threshold (%)" : condition === "quote_stale" ? "Stale trigger age (seconds)" : "Liquidity change threshold"}<input required min={condition === "quote_stale" ? "1" : "0"} step={condition === "quote_stale" ? "1" : "0.01"} value={threshold} onChange={(event) => setThreshold(event.target.value)} type="number" className={inputClass} /></label>}
        <fieldset className="rounded-xl border border-[#263b58] bg-[#0c1727]/55 p-3">
          <legend className="px-1 text-[10px] font-bold uppercase tracking-[.09em] text-[#8192a9]">Opportunity eligibility gates</legend>
          <p className="mb-3 text-[10px] leading-4 text-[#61758e]">Optional. Every filled gate must pass for the trigger to create an event.</p>
          <div className="grid gap-3 sm:grid-cols-2">
            <label className="text-xs text-[#8192a9]">Minimum net profit (workspace currency)<input min="0" step="0.01" value={minimumNetProfit} onChange={(event) => setMinimumNetProfit(event.target.value)} type="number" inputMode="decimal" className={inputClass} placeholder="No minimum" /></label>
            <label className="text-xs text-[#8192a9]">Minimum net ROI (%)<input min="0" step="0.01" value={minimumNetRoi} onChange={(event) => setMinimumNetRoi(event.target.value)} type="number" inputMode="decimal" className={inputClass} placeholder="e.g. 2.00" /></label>
            <label className="text-xs text-[#8192a9]">Minimum liquidity (workspace currency)<input min="0" step="0.01" value={minimumLiquidity} onChange={(event) => setMinimumLiquidity(event.target.value)} type="number" inputMode="decimal" className={inputClass} placeholder="No minimum" /></label>
            <label className="text-xs text-[#8192a9]">Maximum quote age (seconds)<input min="1" max="86400" step="1" value={maximumQuoteAge} onChange={(event) => setMaximumQuoteAge(event.target.value)} type="number" inputMode="numeric" className={inputClass} placeholder="No freshness gate" /></label>
          </div>
        </fieldset>
        <label className="block text-xs text-[#8192a9]">Cooldown (seconds)<input required min="0" max="2592000" step="1" value={cooldown} onChange={(event) => setCooldown(event.target.value)} type="number" className={inputClass} /></label>
        {addMutation.error && <p role="alert" className="text-xs text-[#ff91a0]">{addMutation.error.message}</p>}
        <Button type="submit" disabled={!name.trim() || addMutation.isPending} className="h-11 w-full border-[#4f8cff] bg-[#4f8cff] text-white"><BellRing className="size-4" /> {addMutation.isPending ? "Saving…" : "Create alert"}</Button>
      </form>
    </WorkflowDialog>
  </div>;
}
