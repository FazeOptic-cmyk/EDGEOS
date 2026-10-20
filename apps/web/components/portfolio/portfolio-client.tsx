"use client";

import { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Check, Download, FileClock, Layers3, LoaderCircle, Plus, Settings2, TriangleAlert } from "lucide-react";
import { PageIntro } from "@/components/page-intro";
import { WorkflowDialog } from "@/components/workflows/workflow-dialog";
import { Badge, Button, Panel } from "@/components/ui";
import {
  cancelPaperPosition,
  createPaperPosition,
  exportPaperPortfolio,
  getPortfolio,
  recordPaperFill,
  settlePaperPosition,
  type PaperPosition,
} from "@/lib/workflow-api";
import { money } from "@/lib/utils";
import { appMode } from "@/lib/api";

function displayStatus(status: PaperPosition["status"]): string {
  return status.replaceAll("_", " ").replace(/^./, (letter) => letter.toUpperCase());
}

export function PortfolioClient() {
  const queryClient = useQueryClient();
  const desktopBuild = process.env.NEXT_PUBLIC_DESKTOP_BUILD === "true";
  const [tab, setTab] = useState<"All" | "Open" | "Settled">("All");
  const [createOpen, setCreateOpen] = useState(false);
  const [manage, setManage] = useState<PaperPosition | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [exportError, setExportError] = useState<string | null>(null);
  const [opportunityId, setOpportunityId] = useState(appMode === "demo" ? "demo-opportunity-001" : "");
  const [allocation, setAllocation] = useState("210");
  const [note, setNote] = useState("Paper hypothesis created from a verified opportunity snapshot.");
  const [action, setAction] = useState<"fill" | "settle" | "cancel">("fill");
  const [legId, setLegId] = useState("");
  const [quantity, setQuantity] = useState("");
  const [price, setPrice] = useState("");
  const [fee, setFee] = useState("0");
  const [outcomeId, setOutcomeId] = useState("");
  const [reason, setReason] = useState("");

  const portfolio = useQuery({
    queryKey: ["paper-portfolio"],
    queryFn: getPortfolio,
  });

  const current = portfolio.data;

  const createMutation = useMutation({
    mutationFn: async () => {
      const input = { opportunity_id: opportunityId.trim(), total_capital: allocation, notes: note.trim() || undefined, idempotency_key: crypto.randomUUID() };
      return createPaperPosition(input);
    },
    onSuccess: () => { void queryClient.invalidateQueries({ queryKey: ["paper-portfolio"] }); setCreateOpen(false); setNotice("Paper position recorded. Projected profit remains unrealized until settlement."); },
  });

  const positionMutation = useMutation({
    mutationFn: async () => {
      if (!manage) throw new Error("No position selected");
      if (action === "fill") return recordPaperFill(manage.id, { leg_id: legId, quantity, price, fee, filled_at: new Date().toISOString(), idempotency_key: crypto.randomUUID() });
      if (action === "settle") return settlePaperPosition(manage.id, { outcome_id: outcomeId, settled_at: new Date().toISOString(), idempotency_key: crypto.randomUUID() });
      return cancelPaperPosition(manage.id, { reason, idempotency_key: crypto.randomUUID() });
    },
    onSuccess: () => { void queryClient.invalidateQueries({ queryKey: ["paper-portfolio"] }); setManage(null); setNotice(action === "fill" ? "Simulated fill appended." : action === "settle" ? "Position settled from recorded fills." : "Position cancelled with an audit reason."); },
  });

  const filtered = useMemo(() => (current?.positions ?? []).filter((position) => {
    if (tab === "All") return true;
    if (tab === "Settled") return position.status === "settled";
    return !["settled", "closed", "cancelled"].includes(position.status);
  }), [current?.positions, tab]);

  const exportCsv = async () => {
    setExportError(null);
    try {
      const blob = await exportPaperPortfolio();
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url; link.download = `edgeos-paper-ledger-${new Date().toISOString().slice(0, 10)}.csv`; link.click();
      URL.revokeObjectURL(url);
      setNotice(desktopBuild
        ? "Ledger export prepared. Complete the native Save dialog to write the file."
        : "Ledger download requested. Confirm the file appears in your browser downloads.");
    } catch (error) {
      setNotice(null);
      setExportError(error instanceof Error ? error.message : "Export failed.");
    }
  };

  const summary = current?.summary;
  const error = portfolio.error instanceof Error ? portfolio.error.message : null;
  return <div className="space-y-5">
    <PageIntro badge="simulation ledger" title="Paper portfolio" description="Record linked paper legs, fills, exposure, and settlement without placing a real-money order." actions={<div className="flex flex-wrap gap-2"><Button onClick={exportCsv} disabled={!current}><Download className="size-4"/> Export ledger</Button><Button onClick={() => setCreateOpen(true)} className="border-[#4f8cff] bg-[#4f8cff] text-white hover:bg-[#669eff]"><Plus className="size-4"/> New paper position</Button></div>} />
    {notice && <div role="status" className="flex items-center justify-between rounded-xl border border-[#275645] bg-[#112d25]/60 px-4 py-3 text-xs text-[#75d5b5]"><span className="flex items-center gap-2"><Check className="size-4"/> {notice}</span><button type="button" onClick={() => setNotice(null)} aria-label="Dismiss notification" className="focus-ring rounded p-1">×</button></div>}
    {exportError && <div role="alert" className="flex items-center justify-between rounded-xl border border-[#603545] bg-[#321a22]/60 px-4 py-3 text-xs text-[#ff91a0]"><span className="flex items-center gap-2"><TriangleAlert className="size-4"/> {exportError}</span><button type="button" onClick={() => setExportError(null)} aria-label="Dismiss export error" className="focus-ring rounded p-1">×</button></div>}
    {portfolio.isPending && <Panel className="flex items-center gap-3 p-5 text-sm text-[#8294aa]"><LoaderCircle className="size-4 animate-spin"/> Loading workspace paper ledger…</Panel>}
    {error && <Panel role="alert" className="border-[#603545] p-5 text-sm text-[#ff91a0]"><p className="font-semibold">Portfolio unavailable</p><p className="mt-1 text-xs">{error}</p></Panel>}
    {summary && <section className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">{[
      {label:"Portfolio value", value:money(Number(summary.portfolio_value), 2, current.currency), sub:`Cash ${money(Number(summary.cash_balance), 2, current.currency)}`},
      {label:"Realized paper P&L", value:money(Number(summary.realized_pnl), 2, current.currency), sub:`${summary.settled_positions} settled`, positive:Number(summary.realized_pnl)>0},
      {label:"Unrealized paper P&L", value:money(Number(summary.unrealized_pnl), 2, current.currency), sub:"Open positions only"},
      {label:"Reserved capital", value:money(Number(summary.reserved_capital), 2, current.currency), sub:`${summary.active_positions} active`},
    ].map((item) => <Panel key={item.label} className="p-5"><p className="text-xs text-[#788aa3]">{item.label}</p><p className={`font-display mt-2 text-2xl font-bold ${item.positive ? "text-[#22c991]" : "text-[#eef4ff]"}`}>{item.value}</p><p className="mt-2 text-xs text-[#60738d]">{item.sub}{current?.mode === "demo" ? " · DEMO" : ""}</p></Panel>)}</section>}
    <Panel className="overflow-hidden"><div className="flex flex-col gap-3 border-b border-[#203149] px-5 py-4 sm:flex-row sm:items-center sm:justify-between"><div><p className="text-sm font-semibold text-[#e0e9f5]">Positions</p><p className="mt-1 text-xs text-[#6c7f99]">Projected profit is never included in realized P&L</p></div><div className="flex rounded-lg border border-[#253a57] bg-[#0d1829] p-0.5" role="group" aria-label="Filter positions">{(["All","Open","Settled"] as const).map((item) => <button key={item} type="button" onClick={() => setTab(item)} aria-pressed={tab===item} className={`focus-ring rounded-md px-3 py-1.5 text-xs font-semibold ${tab === item ? "bg-[#1a2d48] text-[#cfe0f8]" : "text-[#71839c]"}`}>{item}</button>)}</div></div>
      {filtered.length ? <div className="overflow-x-auto"><table className="w-full min-w-[860px] text-left text-xs"><thead><tr className="border-b border-[#203149] uppercase tracking-[.08em] text-[#71839b]"><th className="px-5 py-3">Position</th><th className="px-3 py-3">Strategy</th><th className="px-3 py-3">Planned / committed</th><th className="px-3 py-3">Exposure</th><th className="px-3 py-3">Status</th><th className="px-3 py-3">Realized P&L</th><th className="px-5 py-3 text-right">Action</th></tr></thead><tbody>{filtered.map((position) => <tr key={position.id} className="border-b border-[#1b2c43] last:border-0 hover:bg-[#142239]/60"><td className="px-5 py-4"><p className="font-semibold text-[#dce6f3]">{position.title}</p><p className="mt-1 text-xs text-[#71839b]">{position.id} · {position.is_demo ? "DEMO" : "PERSISTED"}</p></td><td className="px-3 py-4 text-[#91a1b6]">{position.strategy}</td><td className="px-3 py-4 text-[#d2deed]">{money(Number(position.planned_allocation), 2, position.currency)} / {money(Number(position.committed_capital), 2, position.currency)}</td><td className="px-3 py-4 text-[#ff9aaa]">{money(Number(position.worst_case_exposure), 2, position.currency)}</td><td className="px-3 py-4"><Badge>{displayStatus(position.status)}</Badge></td><td className={`px-3 py-4 font-bold ${Number(position.realized_pnl)>=0?"text-[#22c991]":"text-[#ff6478]"}`}>{position.status === "settled" ? money(Number(position.realized_pnl), 2, position.currency) : "Not realized"}</td><td className="px-5 py-4 text-right"><Button onClick={() => { setManage(position); setAction("fill"); setLegId(position.legs[0]?.id ?? ""); setOutcomeId(position.legs[0]?.outcome ?? ""); }} className="h-8 px-3 text-xs"><Settings2 className="size-3.5"/> Manage</Button></td></tr>)}</tbody></table></div> : !portfolio.isPending && <div className="px-6 py-16 text-center"><Layers3 className="mx-auto size-8 text-[#4c6079]"/><p className="mt-4 text-sm font-semibold text-[#dce6f2]">No paper positions in this view</p><p className="mt-1 text-xs text-[#71839b]">Create one from a verified opportunity snapshot.</p></div>}
    </Panel>
    <Panel className="flex items-start gap-3 p-5"><FileClock className="mt-0.5 size-5 text-[#79aaf4]"/><div><p className="text-sm font-semibold text-[#e2eaf5]">Append-only accounting</p><p className="mt-1 text-xs leading-5 text-[#75879f]">Fills and settlements append immutable records; cancellation is allowed only before a fill. Durable manual correction entries are not exposed in this version. {desktopBuild ? "The installed desktop workspace persists its private records locally between launches." : appMode === "demo" ? "Persistence depends on the configured demo API store; do not assume a hosted development process is durable." : "Records are persisted by the configured private data store."}</p></div></Panel>

    <WorkflowDialog open={createOpen} onClose={() => setCreateOpen(false)} title="Create paper position" description="The server recalculates the selected opportunity. No real order is sent.">
      <form className="mt-5 space-y-4" onSubmit={(event) => { event.preventDefault(); createMutation.mutate(); }}>
        <label className="block text-xs text-[#8192a9]">Verified opportunity ID<input autoFocus required value={opportunityId} onChange={(event)=>setOpportunityId(event.target.value)} placeholder={appMode === "demo" ? "demo-opportunity-001" : "Paste a persisted opportunity UUID"} className="focus-ring mt-2 h-11 w-full rounded-xl border border-[#293e5b] bg-[#0d1829] px-3 text-[#e5edf8]"/><span className="mt-1.5 block text-[10px] leading-4 text-[#667991]">{appMode === "demo" ? "The bundled demonstration opportunity is selected." : "Open a verified scanner candidate and use Create paper position, or paste its persisted ID here."}</span></label>
        <label className="block text-xs text-[#8192a9]">Total paper capital<input required min="0.01" step="0.01" value={allocation} onChange={(event)=>setAllocation(event.target.value)} type="number" className="focus-ring mt-2 h-11 w-full rounded-xl border border-[#293e5b] bg-[#0d1829] px-3 text-[#e5edf8]"/></label>
        <label className="block text-xs text-[#8192a9]">Research note<textarea value={note} onChange={(event)=>setNote(event.target.value)} className="focus-ring mt-2 min-h-24 w-full rounded-xl border border-[#293e5b] bg-[#0d1829] p-3 text-[#e5edf8]"/></label>
        {createMutation.error && <p role="alert" className="text-xs text-[#ff91a0]">{createMutation.error.message}</p>}
        <Button type="submit" disabled={createMutation.isPending || !opportunityId.trim() || Number(allocation)<=0} className="h-11 w-full border-[#4f8cff] bg-[#4f8cff] text-white"><Layers3 className="size-4"/> {createMutation.isPending ? "Calculating…" : "Calculate and create bundle"}</Button>
      </form>
    </WorkflowDialog>

    <WorkflowDialog open={Boolean(manage)} onClose={() => setManage(null)} title={`Manage ${manage?.title ?? "position"}`} description="Record actual simulated activity. Planned profit remains separate from realized P&L.">
      <div className="mt-5 flex rounded-lg border border-[#253a57] bg-[#0d1829] p-1" role="tablist" aria-label="Position action">{(["fill","settle","cancel"] as const).map((item)=><button key={item} type="button" role="tab" aria-selected={action===item} onClick={()=>setAction(item)} className={`focus-ring flex-1 rounded-md px-3 py-2 text-xs font-semibold ${action===item?"bg-[#1a2d48] text-[#cfe0f8]":"text-[#71839c]"}`}>{item === "fill" ? "Record fill" : item === "settle" ? "Settle" : "Cancel"}</button>)}</div>
      <form className="mt-4 space-y-4" onSubmit={(event)=>{event.preventDefault();positionMutation.mutate();}}>
        {action === "fill" && <><label className="block text-xs text-[#8192a9]">Position leg<select required value={legId} onChange={(event)=>setLegId(event.target.value)} className="focus-ring mt-2 h-11 w-full rounded-xl border border-[#293e5b] bg-[#0d1829] px-3 text-[#e5edf8]"><option value="" disabled>Select a leg</option>{manage?.legs.map((leg)=><option key={leg.id} value={leg.id}>{leg.provider} · {leg.outcome}</option>)}</select></label><div className="grid grid-cols-3 gap-3"><label className="text-xs text-[#8192a9]">Quantity<input required min="0.00000001" step="any" value={quantity} onChange={(event)=>setQuantity(event.target.value)} type="number" className="focus-ring mt-2 h-11 w-full rounded-xl border border-[#293e5b] bg-[#0d1829] px-3 text-[#e5edf8]"/></label><label className="text-xs text-[#8192a9]">Fill price<input required min="0.00000001" step="any" value={price} onChange={(event)=>setPrice(event.target.value)} type="number" className="focus-ring mt-2 h-11 w-full rounded-xl border border-[#293e5b] bg-[#0d1829] px-3 text-[#e5edf8]"/></label><label className="text-xs text-[#8192a9]">Fee<input required min="0" step="any" value={fee} onChange={(event)=>setFee(event.target.value)} type="number" className="focus-ring mt-2 h-11 w-full rounded-xl border border-[#293e5b] bg-[#0d1829] px-3 text-[#e5edf8]"/></label></div></>}
        {action === "settle" && <label className="block text-xs text-[#8192a9]">Resolved outcome ID<input required value={outcomeId} onChange={(event)=>setOutcomeId(event.target.value)} list="settlement-outcomes" className="focus-ring mt-2 h-11 w-full rounded-xl border border-[#293e5b] bg-[#0d1829] px-3 text-[#e5edf8]"/><datalist id="settlement-outcomes">{[...new Set(manage?.legs.map((leg)=>leg.outcome) ?? [])].map((outcome)=><option key={outcome} value={outcome}/>)}</datalist><span className="mt-1.5 block text-[10px] leading-4 text-[#667991]">Choose a covered outcome, or enter an explicit push/void scenario ID recorded by the opportunity snapshot.</span></label>}
        {action === "cancel" && <label className="block text-xs text-[#8192a9]">Audit reason<textarea required value={reason} onChange={(event)=>setReason(event.target.value)} className="focus-ring mt-2 min-h-24 w-full rounded-xl border border-[#293e5b] bg-[#0d1829] p-3 text-[#e5edf8]"/></label>}
        {positionMutation.error && <p role="alert" className="text-xs text-[#ff91a0]">{positionMutation.error.message}</p>}
        <Button type="submit" disabled={positionMutation.isPending} className={`h-11 w-full text-white ${action === "cancel" ? "border-[#6f3445] bg-[#572738]" : "border-[#4f8cff] bg-[#4f8cff]"}`}>{positionMutation.isPending ? "Saving…" : action === "fill" ? "Append simulated fill" : action === "settle" ? "Settle from recorded fills" : "Cancel with audit record"}</Button>
      </form>
    </WorkflowDialog>
  </div>;
}
