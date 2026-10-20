"use client";

import {
  type FormEvent,
  type KeyboardEvent as ReactKeyboardEvent,
  useEffect,
  useRef,
  useState,
} from "react";
import { useQuery } from "@tanstack/react-query";
import {
  AlertTriangle,
  CheckCircle2,
  FileSearch,
  History,
  LoaderCircle,
  RefreshCw,
  Scale,
  ShieldCheck,
  X,
} from "lucide-react";
import {
  decideMatchReview,
  getMatchReviews,
  type MatchDecision,
  type MatchReview,
  reviewErrorMessage,
} from "@/lib/match-reviews";
import { cn } from "@/lib/utils";
import { Badge, Button, Panel } from "@/components/ui";

function displayTime(value: string | null): string {
  if (!value) return "Never";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? "Invalid timestamp" : date.toLocaleString();
}

function pretty(value: Record<string, unknown>): string {
  return JSON.stringify(value, null, 2);
}

function shortFingerprint(value: string | null): string {
  if (!value) return "Not recorded";
  return value.length > 18 ? `${value.slice(0, 10)}…${value.slice(-6)}` : value;
}

function trapTab(event: ReactKeyboardEvent<HTMLElement>) {
  if (event.key !== "Tab") return;
  const focusable = Array.from(event.currentTarget.querySelectorAll<HTMLElement>(
    "button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [href], [tabindex]:not([tabindex='-1'])",
  ));
  if (!focusable.length) return;
  const first = focusable[0];
  const last = focusable[focusable.length - 1];
  if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
  else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
}

function containsHttpsEvidence(value: unknown): boolean {
  if (typeof value === "string") return /^https:\/\//iu.test(value);
  if (Array.isArray(value)) return value.some(containsHttpsEvidence);
  return Boolean(value && typeof value === "object" && Object.values(value).some(containsHttpsEvidence));
}

function parseObject(value: string, label: string): Record<string, unknown> {
  let parsed: unknown;
  try { parsed = JSON.parse(value); }
  catch { throw new Error(`${label} must be valid JSON.`); }
  if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
    throw new Error(`${label} must be a JSON object.`);
  }
  return parsed as Record<string, unknown>;
}

function DecisionDialog({ candidate, busy, onClose, onSubmit }: {
  candidate: MatchReview;
  busy: boolean;
  onClose: () => void;
  onSubmit: (input: {
    decision: MatchDecision;
    rationale: string;
    checks: Record<string, unknown>;
    source_evidence: Record<string, unknown>;
    expected_version: number;
  }) => Promise<void>;
}) {
  const dialog = useRef<HTMLElement | null>(null);
  const previousFocus = useRef<HTMLElement | null>(null);
  const [decision, setDecision] = useState<MatchDecision>("review_required");
  const [rationale, setRationale] = useState("");
  const [completeCoverage, setCompleteCoverage] = useState(Boolean(candidate.checks.complete_outcome_coverage));
  const [explicitRules, setExplicitRules] = useState(Boolean(candidate.checks.all_material_rules_explicit));
  const [checksText, setChecksText] = useState(pretty(candidate.checks));
  const [evidenceText, setEvidenceText] = useState(pretty(candidate.source_evidence));
  const [error, setError] = useState("");

  useEffect(() => {
    previousFocus.current = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    const priorOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    queueMicrotask(() => dialog.current?.querySelector<HTMLElement>("select")?.focus());
    return () => {
      document.body.style.overflow = priorOverflow;
      previousFocus.current?.focus();
    };
  }, []);

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    setError("");
    try {
      if (rationale.trim().length < 8) throw new Error("Record a rationale of at least eight characters.");
      const checks = parseObject(checksText, "Equivalence checks");
      checks.complete_outcome_coverage = completeCoverage;
      checks.all_material_rules_explicit = explicitRules;
      const sourceEvidence = parseObject(evidenceText, "Source evidence");
      if (decision === "verified") {
        if (!completeCoverage || !explicitRules) {
          throw new Error("Verification requires complete outcome coverage and every material rule to be explicit.");
        }
        if (!containsHttpsEvidence(sourceEvidence)) {
          throw new Error("Verification requires at least one HTTPS source reference in the evidence object.");
        }
      }
      await onSubmit({
        decision,
        rationale: rationale.trim(),
        checks,
        source_evidence: sourceEvidence,
        expected_version: candidate.decision_version,
      });
    } catch (value) {
      setError(reviewErrorMessage(value));
    }
  };

  return <div className="fixed inset-0 z-[90] grid place-items-center bg-[#030812]/85 p-3 backdrop-blur-sm" onMouseDown={(event) => event.target === event.currentTarget && !busy && onClose()}>
    <section
      ref={dialog}
      role="dialog"
      aria-modal="true"
      aria-labelledby="review-dialog-title"
      aria-describedby="review-dialog-description"
      onKeyDown={(event) => {
        if (event.key === "Escape" && !busy) { event.preventDefault(); onClose(); return; }
        trapTab(event);
      }}
      className="max-h-[94vh] w-full max-w-3xl overflow-y-auto rounded-2xl border border-[#2b4262] bg-[#0c1728] shadow-[0_28px_100px_rgba(0,0,0,.7)]"
    >
      <header className="sticky top-0 z-10 flex items-start justify-between gap-4 border-b border-[#203149] bg-[#0c1728]/95 px-5 py-4 backdrop-blur">
        <div><p className="text-[10px] font-semibold uppercase tracking-[.14em] text-[#6799e3]">Versioned operator decision</p><h2 id="review-dialog-title" className="mt-1 text-lg font-bold text-[#eff5ff]">Review settlement equivalence</h2><p id="review-dialog-description" className="mt-1 text-xs leading-5 text-[#7f92ab]">Version {candidate.decision_version} · a changed rule or competing decision will reject this write.</p></div>
        <button aria-label="Close settlement review" onClick={onClose} disabled={busy} className="focus-ring grid size-9 shrink-0 place-items-center rounded-xl border border-[#293d59] text-[#91a2b8] hover:text-white disabled:opacity-50"><X className="size-4" /></button>
      </header>
      <form onSubmit={submit} className="space-y-5 p-5">
        {error && <p role="alert" className="rounded-xl border border-[#623641] bg-[#321a22]/60 p-3 text-xs leading-5 text-[#ee91a0]">{error}</p>}
        <div className="grid gap-3 sm:grid-cols-2"><div className="rounded-xl border border-[#253b58] bg-[#101d30] p-3"><p className="text-[9px] uppercase tracking-[.1em] text-[#60758f]">Left market</p><p className="mt-1 text-xs font-semibold text-[#dce7f5]">{candidate.left.provider}</p><p className="mt-1 text-[11px] text-[#8092aa]">{candidate.left.market}</p></div><div className="rounded-xl border border-[#253b58] bg-[#101d30] p-3"><p className="text-[9px] uppercase tracking-[.1em] text-[#60758f]">Right market</p><p className="mt-1 text-xs font-semibold text-[#dce7f5]">{candidate.right.provider}</p><p className="mt-1 text-[11px] text-[#8092aa]">{candidate.right.market}</p></div></div>
        <label className="block text-xs font-medium text-[#9aabc0]">Decision<select value={decision} onChange={(event) => setDecision(event.target.value as MatchDecision)} className="focus-ring mt-2 h-11 w-full rounded-xl border border-[#293e5b] bg-[#0d1829] px-3 text-sm text-[#e7effa]"><option value="review_required">Review required</option><option value="rejected">Rejected</option><option value="verified">Verified match</option></select></label>
        <div className="grid gap-3 sm:grid-cols-2"><label className="flex items-start gap-3 rounded-xl border border-[#263b58] bg-[#101d30] p-4"><input type="checkbox" checked={completeCoverage} onChange={(event) => setCompleteCoverage(event.target.checked)} className="mt-0.5 size-4 accent-[#4f8cff]" /><span><span className="block text-xs font-semibold text-[#dce7f4]">Complete outcome coverage</span><span className="mt-1 block text-[10px] leading-4 text-[#71849d]">Every mutually exclusive settlement outcome is represented.</span></span></label><label className="flex items-start gap-3 rounded-xl border border-[#263b58] bg-[#101d30] p-4"><input type="checkbox" checked={explicitRules} onChange={(event) => setExplicitRules(event.target.checked)} className="mt-0.5 size-4 accent-[#4f8cff]" /><span><span className="block text-xs font-semibold text-[#dce7f4]">All material rules explicit</span><span className="mt-1 block text-[10px] leading-4 text-[#71849d]">Period, line, OT, draw, push, void, and cancellation rules are known.</span></span></label></div>
        <label className="block text-xs font-medium text-[#9aabc0]">Rationale<textarea value={rationale} onChange={(event) => setRationale(event.target.value)} rows={3} maxLength={4000} className="focus-ring mt-2 w-full rounded-xl border border-[#293e5b] bg-[#0d1829] p-3 text-sm leading-5 text-[#e7effa]" placeholder="Explain why the settlement contracts match, remain unresolved, or conflict." /></label>
        <div className="grid gap-4 lg:grid-cols-2"><label className="block text-xs font-medium text-[#9aabc0]">Equivalence checks (JSON object)<textarea aria-label="Equivalence checks JSON" value={checksText} onChange={(event) => setChecksText(event.target.value)} rows={10} spellCheck={false} className="focus-ring mt-2 w-full rounded-xl border border-[#293e5b] bg-[#091321] p-3 font-mono text-[11px] leading-5 text-[#b8c9dc]" /></label><label className="block text-xs font-medium text-[#9aabc0]">Source evidence (JSON object)<textarea aria-label="Source evidence JSON" value={evidenceText} onChange={(event) => setEvidenceText(event.target.value)} rows={10} spellCheck={false} className="focus-ring mt-2 w-full rounded-xl border border-[#293e5b] bg-[#091321] p-3 font-mono text-[11px] leading-5 text-[#b8c9dc]" /><span className="mt-1.5 block text-[10px] leading-4 text-[#687c95]">Verified decisions require an HTTPS official rules reference. EDGEOS never fetches an operator-entered URL from this form.</span></label></div>
        <div className="flex flex-col-reverse gap-2 border-t border-[#203149] pt-5 sm:flex-row sm:justify-end"><Button onClick={onClose} disabled={busy}>Cancel</Button><Button type="submit" disabled={busy} className="border-[#4f8cff] bg-[#4f8cff] text-white">{busy ? <LoaderCircle className="size-4 animate-spin" /> : <Scale className="size-4" />} Record decision</Button></div>
      </form>
    </section>
  </div>;
}

function statusBadge(candidate: MatchReview) {
  if (candidate.invalidated_at) return <Badge className="border-[#694c2d] bg-[#302313] text-[#e5b562]">Invalidated</Badge>;
  if (candidate.classification === "verified") return <Badge className="border-[#28604e] bg-[#15372e] text-[#4bdbaa]">Verified</Badge>;
  if (candidate.classification === "rejected") return <Badge className="border-[#663743] bg-[#331b23] text-[#ff8190]">Rejected</Badge>;
  return <Badge className="border-[#64512f] bg-[#302815] text-[#e6bf68]">Review required</Badge>;
}

export function MatchReviewQueue() {
  const queue = useQuery({ queryKey: ["match-reviews"], queryFn: () => getMatchReviews(), retry: false });
  const [active, setActive] = useState<MatchReview | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");

  const decide = async (input: Parameters<typeof decideMatchReview>[1]) => {
    if (!active) return;
    setBusy(true); setError(""); setNotice("");
    try {
      const result = await decideMatchReview(active.id, input);
      setActive(null);
      setNotice(`Decision ${result.decision.replaceAll("_", " ")} recorded as version ${result.decision_version}.`);
      await queue.refetch();
    } catch (value) {
      // Keep the dialog open so evidence is not lost; the dialog presents the
      // actionable permission/version/evidence error next to the form.
      throw value;
    } finally {
      setBusy(false);
    }
  };

  return <section aria-labelledby="match-review-queue-title" className="space-y-4">
    <div className="flex flex-col gap-3 sm:flex-row sm:items-end sm:justify-between"><div><p className="text-[10px] font-bold uppercase tracking-[.12em] text-[#637994]">Operator controls</p><h2 id="match-review-queue-title" className="mt-1 text-base font-bold text-[#e8f0fb]">Settlement match review queue</h2><p className="mt-1 max-w-3xl text-xs leading-5 text-[#74879f]">Inspect explicit rule evidence and record versioned decisions. Similar titles never prove equivalence, and unresolved candidates remain outside the strict feed.</p></div><Button onClick={() => { setError(""); void queue.refetch(); }} disabled={queue.isFetching}><RefreshCw className={cn("size-4", queue.isFetching && "animate-spin")} /> Refresh queue</Button></div>
    {error && <p role="alert" className="rounded-xl border border-[#623641] bg-[#321a22]/60 p-3 text-xs leading-5 text-[#ee91a0]">{error}</p>}
    {notice && <p role="status" className="flex items-center gap-2 rounded-xl border border-[#285646] bg-[#123027]/60 p-3 text-xs text-[#72d6b5]"><CheckCircle2 className="size-4" />{notice}</p>}
    {queue.isLoading && <div className="grid gap-3 md:grid-cols-2">{[1, 2].map((item) => <Panel key={item} className="h-56 p-5"><div className="skeleton h-5 w-32 rounded" /><div className="skeleton mt-5 h-7 w-3/4 rounded" /><div className="skeleton mt-4 h-20 w-full rounded-xl" /></Panel>)}</div>}
    {queue.isError && <Panel className="grid min-h-48 place-items-center p-6 text-center"><div><AlertTriangle className="mx-auto size-7 text-[#e8b85e]" /><p className="mt-3 text-sm font-semibold text-[#e5edf8]">Review queue unavailable</p><p className="mt-2 max-w-xl text-xs leading-5 text-[#7e90a8]">{reviewErrorMessage(queue.error)}</p><Button onClick={() => { setError(""); void queue.refetch(); }} className="mt-4">Retry</Button></div></Panel>}
    {queue.data && !queue.data.items.length && <Panel className="grid min-h-48 place-items-center p-6 text-center"><div><FileSearch className="mx-auto size-7 text-[#57708f]" /><p className="mt-3 text-sm font-semibold text-[#e5edf8]">No candidates awaiting review</p><p className="mt-2 max-w-lg text-xs leading-5 text-[#7689a1]">{queue.data.mode === "demo" ? "Demo mode does not fabricate settlement decisions. Connect the live durable research store to review ingested candidates." : "No persisted candidate currently needs an operator decision. This is a valid empty state."}</p></div></Panel>}
    {queue.data?.items.length ? <div className="grid gap-4 xl:grid-cols-2">{queue.data.items.map((candidate) => <Panel key={candidate.id} className="p-5"><div className="flex items-start justify-between gap-3"><div className="min-w-0"><div className="flex flex-wrap items-center gap-2">{statusBadge(candidate)}<span className="text-[10px] uppercase tracking-[.1em] text-[#617590]">Decision v{candidate.decision_version}</span></div><h3 className="mt-3 text-sm font-bold text-[#e8eff9]">{candidate.left.provider} ↔ {candidate.right.provider}</h3><p className="mt-1 text-xs text-[#8193a9]">{candidate.left.market} / {candidate.right.market}</p></div><ShieldCheck className="size-5 shrink-0 text-[#6d9ee8]" /></div>{candidate.invalidated_at && <p className="mt-3 rounded-lg border border-[#5a4328] bg-[#2b2114] p-2.5 text-[10px] leading-4 text-[#d8ad68]">Invalidated {displayTime(candidate.invalidated_at)} · {candidate.invalidation_reason ?? "material evidence changed"}</p>}<p className="mt-3 text-xs leading-5 text-[#91a2b7]">{candidate.explanation}</p><dl className="mt-4 grid grid-cols-2 gap-3 text-[10px]"><div><dt className="text-[#60748e]">Candidate score</dt><dd className="mt-1 text-[#bac7d7]">{candidate.candidate_score ?? "Unknown"}</dd></div><div><dt className="text-[#60748e]">Settlement confidence</dt><dd className="mt-1 text-[#bac7d7]">{candidate.settlement_confidence}</dd></div><div><dt className="text-[#60748e]">Engine</dt><dd className="mt-1 text-[#bac7d7]">{candidate.engine_version}</dd></div><div><dt className="text-[#60748e]">Prior decisions</dt><dd className="mt-1 text-[#bac7d7]">{candidate.history.length}</dd></div></dl><details className="mt-4 rounded-xl border border-[#213550] bg-[#0d1829] px-3 py-2"><summary className="focus-ring cursor-pointer text-xs font-semibold text-[#afbed0]">Evidence, fingerprints, and audit history</summary><div className="mt-3 space-y-3 text-[10px] leading-4 text-[#73869f]"><div className="grid gap-2 sm:grid-cols-3"><span title={candidate.left_rule_fingerprint ?? undefined}>Left: {shortFingerprint(candidate.left_rule_fingerprint)}</span><span title={candidate.right_rule_fingerprint ?? undefined}>Right: {shortFingerprint(candidate.right_rule_fingerprint)}</span><span title={candidate.decision_fingerprint ?? undefined}>Decision: {shortFingerprint(candidate.decision_fingerprint)}</span></div><div><p className="mb-1 font-semibold text-[#98a9be]">Current checks</p><pre className="max-h-44 overflow-auto whitespace-pre-wrap rounded-lg bg-[#091321] p-2 font-mono text-[10px] text-[#9eb1c8]">{pretty(candidate.checks)}</pre></div><div><p className="mb-1 font-semibold text-[#98a9be]">Current source evidence</p><pre className="max-h-44 overflow-auto whitespace-pre-wrap rounded-lg bg-[#091321] p-2 font-mono text-[10px] text-[#9eb1c8]">{pretty(candidate.source_evidence)}</pre></div>{candidate.history.length ? <div><p className="mb-1 flex items-center gap-1 font-semibold text-[#98a9be]"><History className="size-3" /> Audit history</p><ul className="space-y-2">{candidate.history.map((entry) => <li key={entry.id} className="rounded-lg border border-[#1f324c] p-2"><span className="font-semibold text-[#b8c7da]">v{entry.decision_version} · {entry.decision.replaceAll("_", " ")}</span><span className="ml-2">{displayTime(entry.reviewed_at)}</span><p className="mt-1">{entry.rationale}</p></li>)}</ul></div> : null}</div></details><div className="mt-4 flex items-center justify-between gap-3 border-t border-[#203149] pt-4"><p className="text-[10px] leading-4 text-[#687b94]">Only allowlisted reviewers can mutate the global decision.</p><Button onClick={() => { setError(""); setNotice(""); setActive(candidate); }}><Scale className="size-4" /> Review</Button></div></Panel>)}</div> : null}
    {active && <DecisionDialog candidate={active} busy={busy} onClose={() => !busy && setActive(null)} onSubmit={async (input) => {
      try { await decide(input); }
      catch (value) { throw value; }
    }} />}
  </section>;
}

