"use client";

import { useEffect, useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Calculator, Info, LoaderCircle, RefreshCw, ShieldAlert, TriangleAlert } from "lucide-react";
import { PageIntro } from "@/components/page-intro";
import { Badge, Button, Panel } from "@/components/ui";
import { americanToDecimal, expectedValue, fractionalToDecimal, impliedProbabilityPercent, normalizeOddsInput, removeVig, type OddsInputFormat } from "@/lib/odds";
import { buildManualSimulationRequest, simulateManual, type AuthoritativeSimulation, type ManualSimulationInput, type ManualSimulationLegInput } from "@/lib/simulator-api";
import { cn, money } from "@/lib/utils";

const inputClass = "focus-ring mt-2 h-11 w-full rounded-xl border border-[#263b59] bg-[#0d1829] px-3 text-sm font-semibold text-[#e7effa] placeholder:text-[#53667f] aria-[invalid=true]:border-[#8e4050]";
const decimalPattern = /^\d+(?:\.\d+)?$/u;
const malformedDecimal = (value: string) => !decimalPattern.test(value.trim()) || !Number.isFinite(Number(value));

function useDebouncedValue<T>(value: T, delayMs: number): T {
  const [debounced, setDebounced] = useState(value);
  useEffect(() => {
    const timer = window.setTimeout(() => setDebounced(value), delayMs);
    return () => window.clearTimeout(timer);
  }, [delayMs, value]);
  return debounced;
}

function inputProblem(input: ManualSimulationInput): string | null {
  try {
    buildManualSimulationRequest(input);
    return null;
  } catch (value) {
    return value instanceof Error ? value.message : "Check every simulator input.";
  }
}

function NumericField({ id, label, value, onChange, min = "0", step = "0.01", hint, invalid = false }: {
  id: string;
  label: string;
  value: string;
  onChange: (value: string) => void;
  min?: string;
  step?: string;
  hint?: string;
  invalid?: boolean;
}) {
  const hintId = hint ? `${id}-hint` : undefined;
  return <label htmlFor={id} className="text-xs font-medium text-[#8495ac]">
    {label}
    <input
      id={id}
      type="number"
      inputMode="decimal"
      min={min}
      step={step}
      value={value}
      onChange={(event) => onChange(event.target.value)}
      aria-describedby={hintId}
      aria-invalid={invalid}
      className={inputClass}
    />
    {hint ? <span id={hintId} className="mt-1.5 block text-[10px] leading-4 text-[#667991]">{hint}</span> : null}
  </label>;
}

const formatLabels: Record<OddsInputFormat, string> = {
  decimal: "Decimal",
  american: "American",
  fractional: "Fractional",
  contract: "Binary contract $",
};

const priceLabels: Record<OddsInputFormat, string> = {
  decimal: "Decimal odds",
  american: "American odds",
  fractional: "Fractional odds",
  contract: "Executable contract ask (USD)",
};

function PriceField({ leg, onChange }: {
  leg: ManualSimulationLegInput;
  onChange: (field: "format" | "price", value: string) => void;
}) {
  let conversion: string | null = null;
  let invalid = false;
  try {
    const normalized = normalizeOddsInput(leg.format, leg.price);
    const implied = leg.format === "contract" ? Number(normalized.price) * 100 : impliedProbabilityPercent(normalized.decimalEquivalent);
    conversion = `${normalized.decimalEquivalent.toFixed(4)} decimal · ${implied.toFixed(2)}% raw implied`;
  } catch {
    invalid = true;
  }
  return <div className="grid gap-3 sm:grid-cols-[minmax(130px,.7fr)_minmax(0,1.3fr)] lg:grid-cols-1 2xl:grid-cols-[minmax(130px,.7fr)_minmax(0,1.3fr)]">
    <label htmlFor={`sim-format-${leg.id}`} className="text-xs font-medium text-[#8495ac]">
      Odds format
      <select
        id={`sim-format-${leg.id}`}
        value={leg.format}
        onChange={(event) => onChange("format", event.target.value)}
        className={cn(inputClass, "appearance-none")}
      >
        {(Object.entries(formatLabels) as [OddsInputFormat, string][]).map(([value, label]) => <option key={value} value={value}>{label}</option>)}
      </select>
    </label>
    <label htmlFor={`sim-price-${leg.id}`} className="text-xs font-medium text-[#8495ac]">
      {priceLabels[leg.format]}
      <input
        id={`sim-price-${leg.id}`}
        type="text"
        inputMode={leg.format === "fractional" ? "text" : "decimal"}
        value={leg.price}
        onChange={(event) => onChange("price", event.target.value)}
        aria-label={priceLabels[leg.format]}
        aria-invalid={invalid}
        aria-describedby={`sim-price-${leg.id}-conversion`}
        className={inputClass}
      />
      <span id={`sim-price-${leg.id}-conversion`} className={cn("mt-1.5 block text-[10px] leading-4", invalid ? "text-[#bc7380]" : "text-[#667991]")}>{conversion ?? "Enter a valid price in the selected format."}</span>
    </label>
  </div>;
}

function moneyFromServer(value: string, currency = "USD"): string {
  const number = Number(value);
  if (!Number.isFinite(number)) return `${value} ${currency}`;
  return new Intl.NumberFormat("en-US", { style: "currency", currency, minimumFractionDigits: 2, maximumFractionDigits: 2 }).format(number);
}

function ratioFromServer(value: string, digits = 4): string {
  const number = Number(value);
  return Number.isFinite(number) ? `${(number * 100).toFixed(digits)}%` : value;
}

function codeLabel(code: string): string {
  const labels: Record<string, string> = {
    settlement_not_verified: "Settlement equivalence is not verified",
    quote_age_unknown: "Manual prices do not have provider quote timestamps",
    stale_quote: "At least one quote exceeds the configured age limit",
    quote_timestamp_in_future: "At least one quote timestamp is in the future",
    quote_timestamp_skew_exceeded: "Quote timestamps exceed the allowed skew",
    fee_schedule_unknown: "At least one official fee schedule is unknown",
    orderbook_depth_unknown: "Executable order-book depth is unknown",
    incomplete_outcome_coverage: "The modeled outcomes are incomplete",
    unexpected_outcome_coverage: "An unexpected modeled outcome is present",
    unmatched_exposure: "Outcome payouts are not fully equalized",
  };
  return labels[code] ?? code.replaceAll("_", " ");
}

function ResultSummary({ result }: { result: AuthoritativeSimulation }) {
  const positive = Number(result.net_profit_worst_case) > 0;
  return <>
    <div className="border-b border-[#203149] p-5 sm:p-6">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <p className="text-sm font-semibold text-[#e0e9f5]">Authoritative modeled result</p>
          <p className="mt-1 text-xs text-[#6f819a]">FastAPI Decimal cash-flow engine</p>
        </div>
        <div className="flex flex-wrap justify-end gap-2">
          <Badge className={result.is_theoretical_arbitrage ? "border-[#28654f] bg-[#17362d] text-[#62deb4]" : "border-[#5a3e49] bg-[#2b1c23] text-[#e79aa9]"}>{result.is_theoretical_arbitrage ? "THEORETICAL GROSS ARB" : "NO GROSS ARB"}</Badge>
          <Badge className="border-[#6f5a2b] bg-[#332a17] text-[#e8bd58]">RESEARCH ONLY</Badge>
        </div>
      </div>
      <p className={cn("font-display mt-6 text-4xl font-extrabold", positive ? "text-[#22c991]" : "text-[#ff6478]")}>{positive ? "+" : ""}{moneyFromServer(result.net_profit_worst_case, result.currency)}</p>
      <p className="mt-1 text-xs text-[#70829b]">server-modeled worst-case cash flow · {ratioFromServer(result.net_roi)} net ROI</p>
      <div className="mt-4 flex items-start gap-2 rounded-xl border border-[#644957] bg-[#2b1921] p-3 text-[11px] leading-5 text-[#e4a4b0]">
        <ShieldAlert aria-hidden="true" className="mt-0.5 size-4 shrink-0" />
        <span>{result.execution_eligible ? "This research surface never authorizes execution, even when an upstream record passes server gates." : "Not execution eligible. Manual prices fail settlement and freshness verification by design."}</span>
      </div>
    </div>
    <div className="space-y-3 p-5 sm:p-6">
      {[
        ["Requested capital", moneyFromServer(result.requested_allocation, result.currency)],
        ["Deployed capital", moneyFromServer(result.deployed_capital, result.currency)],
        ["Capital lockup", moneyFromServer(result.capital_lockup, result.currency)],
        ["Unallocated capital", moneyFromServer(result.unallocated_capital, result.currency)],
        ["Total modeled fees", moneyFromServer(result.total_fees, result.currency)],
        ["Equalized gross payout", moneyFromServer(result.equalized_gross_payout, result.currency)],
        ["Gross profit", moneyFromServer(result.gross_profit, result.currency)],
        ["Gross ROI", ratioFromServer(result.gross_roi)],
        ["Net worst-case status", Number(result.net_profit_worst_case) > 0 ? "Positive after entered costs" : "Not profitable after entered costs"],
        ["Maximum allocation from entered caps", result.maximum_available_allocation === null ? "Unknown" : moneyFromServer(result.maximum_available_allocation, result.currency)],
      ].map(([label, value]) => <div key={label} className="flex items-center justify-between gap-4 border-b border-[#1d2e45] pb-3 text-xs last:border-0">
        <span className="text-[#7f91a9]">{label}</span><span className="text-right font-semibold text-[#dae4f1]">{value}</span>
      </div>)}
      <div className="rounded-xl border border-[#263a56] bg-[#0d192a] p-3 text-[10px] leading-4 text-[#7689a2]">
        Calculation <span className="font-mono text-[#b8c8dc]">{result.calculation_version}</span> · {result.optimization_method.replaceAll("_", " ")}
      </div>
    </div>
  </>;
}

function ResultDetails({ result }: { result: AuthoritativeSimulation }) {
  const unknowns = [
    result.quote_timestamp_skew_seconds === null ? "Quote timestamp skew: unknown because manual prices have no timestamps." : null,
    result.expected_lockup_seconds === null ? "Expected capital lockup: unknown; no event/settlement time was supplied." : null,
    "Fee provenance: unverified user assumptions, modeled as a percentage of each leg's cash cost and rounded upward to $0.01.",
    "Liquidity provenance: user-entered cash allocation caps converted to quantities, not observed order-book depth.",
    "Settlement equivalence: not established for generic manual outcomes.",
  ].filter((item): item is string => item !== null);

  return <div className="space-y-5" aria-label="Authoritative simulation detail">
    <div className="grid gap-5 xl:grid-cols-[1.25fr_.75fr]">
      <Panel className="overflow-hidden">
        <div className="border-b border-[#203149] p-5">
          <p className="text-sm font-semibold text-[#e0e9f5]">Server allocations</p>
          <p className="mt-1 text-xs text-[#6f819a]">Filled quantities, costs, fees, and freshness returned by the API</p>
        </div>
        <div className="overflow-x-auto">
          <table className="w-full min-w-[720px] text-left text-xs">
            <caption className="sr-only">Authoritative allocation for each modeled leg</caption>
            <thead className="bg-[#0d1828] text-[10px] uppercase tracking-[.08em] text-[#667991]"><tr>
              <th scope="col" className="px-5 py-3 font-semibold">Leg</th><th scope="col" className="px-3 py-3 font-semibold">Outcome</th><th scope="col" className="px-3 py-3 text-right font-semibold">Cost / qty</th><th scope="col" className="px-3 py-3 text-right font-semibold">Fee</th><th scope="col" className="px-3 py-3 text-right font-semibold">Win payout</th><th scope="col" className="px-3 py-3 text-right font-semibold">Effective odds</th><th scope="col" className="px-5 py-3 text-right font-semibold">Quote age</th>
            </tr></thead>
            <tbody>{result.legs.map((leg) => <tr key={leg.offer_id} className="border-t border-[#1c2c42] text-[#cbd7e6]">
              <th scope="row" className="px-5 py-3.5 font-semibold">{leg.provider}</th><td className="px-3 py-3.5 font-mono text-[11px] text-[#91a4bd]">{leg.outcome_id}</td><td className="px-3 py-3.5 text-right">{moneyFromServer(leg.cash_cost, result.currency)}<span className="block text-[10px] text-[#667991]">qty {leg.filled_quantity}</span></td><td className="px-3 py-3.5 text-right">{moneyFromServer(leg.fees, result.currency)}</td><td className="px-3 py-3.5 text-right">{moneyFromServer(leg.gross_payout_if_wins, result.currency)}</td><td className="px-3 py-3.5 text-right">{Number(leg.cash_cost) > 0 ? `${(Number(leg.gross_payout_if_wins) / Number(leg.cash_cost)).toFixed(4)}×` : "—"}</td><td className="px-5 py-3.5 text-right"><span className="text-[#e8bd58]">{leg.quote_age_seconds === null ? "Unknown" : `${leg.quote_age_seconds}s`}</span><span className="block text-[10px] text-[#667991]">{leg.stale ? "rejected" : "within limit"}</span></td>
            </tr>)}</tbody>
          </table>
        </div>
      </Panel>
      <Panel className="p-5">
        <p className="text-sm font-semibold text-[#e0e9f5]">Validation gates</p>
        <p className="mt-1 text-xs text-[#6f819a]">All failures remain visible; none are hidden by ROI</p>
        {result.rejection_codes.length ? <ul className="mt-4 space-y-2">{result.rejection_codes.map((code) => <li key={code} className="flex gap-2 rounded-lg border border-[#553944] bg-[#251820] px-3 py-2 text-[11px] leading-4 text-[#e0a0ad]"><TriangleAlert aria-hidden="true" className="mt-0.5 size-3.5 shrink-0" /><span>{codeLabel(code)}<span className="mt-0.5 block font-mono text-[9px] text-[#956b77]">{code}</span></span></li>)}</ul> : <p className="mt-4 text-xs text-[#83d8ba]">No server rejection codes.</p>}
        <dl className="mt-4 grid grid-cols-2 gap-3 border-t border-[#203149] pt-4 text-xs">
          <div><dt className="text-[#6f819a]">Outcome coverage</dt><dd className="mt-1 font-semibold text-[#dbe5f2]">{result.outcome_coverage_verified ? "Complete by input ID" : "Unverified"}</dd></div>
          <div><dt className="text-[#6f819a]">Settlement</dt><dd className="mt-1 font-semibold text-[#e8bd58]">Review required</dd></div>
          <div><dt className="text-[#6f819a]">Timestamp skew</dt><dd className="mt-1 font-semibold text-[#dbe5f2]">{result.quote_timestamp_skew_seconds === null ? "Unknown" : `${result.quote_timestamp_skew_seconds}s`}</dd></div>
          <div><dt className="text-[#6f819a]">Skew limit</dt><dd className="mt-1 font-semibold text-[#dbe5f2]">{result.maximum_quote_timestamp_skew_seconds}s</dd></div>
        </dl>
      </Panel>
    </div>

    <div className="grid gap-5 xl:grid-cols-2">
      <Panel className="overflow-hidden">
        <div className="border-b border-[#203149] p-5"><p className="text-sm font-semibold text-[#e0e9f5]">Outcome cash flows</p><p className="mt-1 text-xs text-[#6f819a]">Every mutually exclusive outcome modeled by the server</p></div>
        <div className="overflow-x-auto"><table className="w-full min-w-[480px] text-left text-xs">
          <caption className="sr-only">Gross payout and net result for each modeled outcome</caption>
          <thead className="bg-[#0d1828] text-[10px] uppercase tracking-[.08em] text-[#667991]"><tr><th scope="col" className="px-5 py-3 font-semibold">Outcome</th><th scope="col" className="px-3 py-3 text-right font-semibold">Gross payout</th><th scope="col" className="px-5 py-3 text-right font-semibold">Net result</th></tr></thead>
          <tbody>{result.outcomes.map((outcome) => { const positive = Number(outcome.net_profit) >= 0; return <tr key={outcome.outcome_id} className="border-t border-[#1c2c42]"><th scope="row" className="px-5 py-3.5 font-mono text-[11px] font-medium text-[#a9bbd0]">{outcome.outcome_id}</th><td className="px-3 py-3.5 text-right text-[#dbe5f2]">{moneyFromServer(outcome.gross_payout, result.currency)}</td><td className={cn("px-5 py-3.5 text-right font-semibold", positive ? "text-[#4bdbaa]" : "text-[#ff8090]")}>{positive ? "+" : ""}{moneyFromServer(outcome.net_profit, result.currency)}</td></tr>; })}</tbody>
        </table></div>
        <div className="grid grid-cols-2 gap-3 border-t border-[#203149] p-5 text-xs"><div><p className="text-[#6f819a]">Best case</p><p className="mt-1 font-semibold text-[#dbe5f2]">{moneyFromServer(result.net_profit_best_case, result.currency)}</p></div><div><p className="text-[#6f819a]">Unmatched exposure</p><p className="mt-1 font-semibold text-[#dbe5f2]">{moneyFromServer(result.unmatched_exposure, result.currency)}</p></div></div>
      </Panel>
      <Panel className="overflow-hidden">
        <div className="border-b border-[#203149] p-5"><p className="text-sm font-semibold text-[#e0e9f5]">Fixed-allocation break-even</p><p className="mt-1 text-xs text-[#6f819a]">Adverse movement each leg can absorb with other allocations held fixed</p></div>
        {result.break_even_points.length ? <div className="overflow-x-auto"><table className="w-full min-w-[540px] text-left text-xs">
          <caption className="sr-only">Server-calculated break-even price for each leg</caption>
          <thead className="bg-[#0d1828] text-[10px] uppercase tracking-[.08em] text-[#667991]"><tr><th scope="col" className="px-5 py-3 font-semibold">Leg</th><th scope="col" className="px-3 py-3 text-right font-semibold">Current</th><th scope="col" className="px-3 py-3 text-right font-semibold">Break-even</th><th scope="col" className="px-5 py-3 text-right font-semibold">Adverse move</th></tr></thead>
          <tbody>{result.break_even_points.map((point) => <tr key={point.offer_id} className="border-t border-[#1c2c42] text-[#cbd7e6]"><th scope="row" className="px-5 py-3.5 font-mono text-[11px] font-medium">{point.offer_id}</th><td className="px-3 py-3.5 text-right">{point.current_price}</td><td className="px-3 py-3.5 text-right">{point.fixed_allocation_break_even_price ?? "Unknown"}</td><td className="px-5 py-3.5 text-right">{point.adverse_movement_bps === null ? "Unknown" : `${point.adverse_movement_bps} bps`}</td></tr>)}</tbody>
        </table></div> : <p className="p-5 text-xs leading-5 text-[#7c8ea6]">The server did not return break-even points for this allocation.</p>}
        <div className="border-t border-[#203149] p-5 text-[10px] leading-4 text-[#667991]">Methodology is supplied per leg by the authoritative response. Break-even is not a fill guarantee.</div>
      </Panel>
    </div>

    <Panel className="p-5">
      <div className="flex items-start gap-3"><Info aria-hidden="true" className="mt-0.5 size-5 shrink-0 text-[#6ba5ff]" /><div className="min-w-0"><p className="text-sm font-semibold text-[#e2eaf5]">Known unknowns and explicit assumptions</p><ul className="mt-3 grid gap-2 text-xs leading-5 text-[#75879f] lg:grid-cols-2">{unknowns.map((item) => <li key={item} className="rounded-lg border border-[#223650] bg-[#0d192a] px-3 py-2">{item}</li>)}{result.assumptions.map((item) => <li key={item} className="rounded-lg border border-[#223650] bg-[#0d192a] px-3 py-2">Server assumption: {item}</li>)}</ul>{result.allocation_limit_reasons.length ? <p className="mt-3 break-words text-[10px] text-[#667991]">Allocation limits: {result.allocation_limit_reasons.map(codeLabel).join(" · ")}</p> : null}</div></div>
    </Panel>
  </div>;
}

const defaultLegs: ManualSimulationLegInput[] = [
  { id: "a", provider: "Manual platform A", outcomeId: "outcome_a", format: "decimal", price: "2.20", feePercent: "0", slippagePercent: "0", maximumAllocation: "1000" },
  { id: "b", provider: "Manual platform B", outcomeId: "outcome_b", format: "decimal", price: "2.00", feePercent: "0", slippagePercent: "0", maximumAllocation: "1000" },
  { id: "c", provider: "Manual platform C", outcomeId: "outcome_c", format: "decimal", price: "3.40", feePercent: "0", slippagePercent: "0", maximumAllocation: "1000" },
];

export function EdgeLabClient() {
  const [capital, setCapital] = useState("210.00");
  const [outcomeCount, setOutcomeCount] = useState<2 | 3>(2);
  const [legs, setLegs] = useState<ManualSimulationLegInput[]>(() => defaultLegs.map((leg) => ({ ...leg })));
  const [american, setAmerican] = useState(-110);
  const [fractional, setFractional] = useState("10/11");
  const [probabilityAssumption, setProbabilityAssumption] = useState("");
  const [evStake, setEvStake] = useState(100);

  const simulationInput = useMemo<ManualSimulationInput>(() => ({
    capital,
    legs: legs.slice(0, outcomeCount),
  }), [capital, legs, outcomeCount]);
  const debouncedInput = useDebouncedValue(simulationInput, 400);
  const waitingForDebounce = simulationInput !== debouncedInput;
  const problem = inputProblem(simulationInput);
  const debouncedProblem = inputProblem(debouncedInput);
  const simulation = useQuery({
    queryKey: ["edge-lab", "authoritative-simulation", debouncedInput],
    queryFn: ({ signal }) => simulateManual(debouncedInput, signal),
    enabled: debouncedProblem === null,
    retry: false,
    staleTime: Number.POSITIVE_INFINITY,
  });
  const result = !waitingForDebounce && problem === null ? simulation.data : undefined;

  const updateLeg = (index: number, patch: Partial<ManualSimulationLegInput>) => {
    setLegs((current) => current.map((leg, legIndex) => legIndex === index ? { ...leg, ...patch } : leg));
  };

  const useBinaryPair = () => {
    setOutcomeCount(2);
    setLegs((current) => current.map((leg, index) => {
      if (index === 0) return { ...leg, outcomeId: "yes", format: "contract", price: "0.50" };
      if (index === 1) return { ...leg, outcomeId: "no", format: "contract", price: "0.50" };
      return leg;
    }));
  };

  const research = useMemo(() => {
    try {
      const decimalOdds = simulationInput.legs.map((leg) => normalizeOddsInput(leg.format, leg.price).decimalEquivalent);
      const americanDecimal = americanToDecimal(american);
      const fractionalDecimal = fractionalToDecimal(fractional);
      const consensus = removeVig(decimalOdds);
      const assumed = probabilityAssumption === "" ? null : Number(probabilityAssumption);
      return {
        americanDecimal,
        fractionalDecimal,
        americanImplied: impliedProbabilityPercent(americanDecimal),
        fractionalImplied: impliedProbabilityPercent(fractionalDecimal),
        consensus,
        ev: assumed === null || !Number.isFinite(assumed) ? null : expectedValue(decimalOdds[0]!, assumed, evStake),
        error: null,
      };
    } catch (value) {
      return { americanDecimal: null, fractionalDecimal: null, americanImplied: null, fractionalImplied: null, consensus: null, ev: null, error: value instanceof Error ? value.message : "Invalid odds input" };
    }
  }, [american, evStake, fractional, probabilityAssumption, simulationInput]);

  return <div className="space-y-5">
    <PageIntro badge="transparent methods" title="Arbitrage calculator" description="Model two- or three-way hypotheses across sportsbook and binary-contract price formats. Allocations come from the authoritative Decimal cash-flow engine; manual inputs remain research-only." />
    <div className="grid gap-5 xl:grid-cols-[minmax(0,1fr)_minmax(380px,.75fr)]">
      <Panel className="p-5 sm:p-6">
        <div className="mb-6 flex flex-wrap items-start justify-between gap-4"><div><p className="text-sm font-semibold text-[#e0e9f5]">Manual model inputs</p><p className="mt-1 text-xs text-[#6f819a]">Multiple odds formats · USD simulated capital · server-balanced stakes</p></div><span className="grid size-9 place-items-center rounded-xl bg-[#4f8cff]/10 text-[#6da6ff]"><Calculator aria-hidden="true" className="size-[18px]" /></span></div>
        <div className="mb-5 flex flex-wrap items-center justify-between gap-3 rounded-xl border border-[#243954] bg-[#0d192a] p-3">
          <div role="group" aria-label="Outcome count" className="inline-flex rounded-xl border border-[#263b59] bg-[#091421] p-1">
            {([2, 3] as const).map((count) => <button key={count} type="button" aria-pressed={outcomeCount === count} onClick={() => setOutcomeCount(count)} className={cn("focus-ring rounded-lg px-3 py-2 text-xs font-semibold transition", outcomeCount === count ? "bg-[#4f8cff] text-white shadow-[0_4px_18px_rgba(79,140,255,.24)]" : "text-[#7f92aa] hover:text-[#dbe7f6]")}>{count}-way</button>)}
          </div>
          <button type="button" onClick={useBinaryPair} className="focus-ring rounded-lg px-2 py-1.5 text-xs font-semibold text-[#72dce3] transition hover:bg-[#19c9d5]/10">Use binary Yes / No</button>
        </div>
        <NumericField id="sim-capital" label="Total simulated capital" value={capital} onChange={setCapital} min="0.01" step="10" invalid={malformedDecimal(capital) || Number(capital) <= 0} hint="Hard ceiling for stake plus modeled fees." />
        <div className="mt-5 grid gap-4 md:grid-cols-2">
          {simulationInput.legs.map((leg, index) => <fieldset key={leg.id} className="rounded-xl border border-[#233650] bg-[#0c1727] p-4"><legend className={cn("px-1 text-[10px] font-semibold uppercase tracking-[.1em]", index === 0 ? "text-[#6da6ff]" : index === 1 ? "text-[#19c9d5]" : "text-[#a994ff]")}>{leg.provider} · Outcome {index + 1}</legend><div className="space-y-4">
            <PriceField leg={leg} onChange={(field, value) => field === "format" ? updateLeg(index, { format: value as OddsInputFormat }) : updateLeg(index, { price: value })} />
            <NumericField id={`sim-fee-${leg.id}`} label="Cash-cost fee assumption (%)" value={leg.feePercent} onChange={(value) => updateLeg(index, { feePercent: value })} invalid={malformedDecimal(leg.feePercent)} hint="Manual assumption, not an official provider fee schedule." />
            <NumericField id={`sim-slippage-${leg.id}`} label="Adverse price movement (%)" value={leg.slippagePercent} onChange={(value) => updateLeg(index, { slippagePercent: value })} invalid={malformedDecimal(leg.slippagePercent) || Number(leg.slippagePercent) >= 100} hint={leg.format === "contract" ? "Raises the executable ask before sizing." : "Reduces the payout odds before sizing."} />
            <NumericField id={`sim-cap-${leg.id}`} label="Platform allocation cap (USD)" value={leg.maximumAllocation} onChange={(value) => updateLeg(index, { maximumAllocation: value })} min="0.01" step="10" invalid={malformedDecimal(leg.maximumAllocation) || Number(leg.maximumAllocation) <= 0} hint="Cash ceiling including modeled fees; not observed liquidity." />
          </div></fieldset>)}
        </div>
        <div className="mt-5 rounded-xl border border-[#29415f] bg-[#0d192b] p-4 text-[11px] leading-5 text-[#7e90a8]"><Info aria-hidden="true" className="mr-2 inline size-3.5 text-[#6ba5ff]" />The optimizer maximizes the minimum net outcome. Sportsbook payout = stake × effective decimal odds; binary payout = filled contracts × $1; outcome net = covered payout − every leg&apos;s cost − every modeled fee.</div>
        <div className="mt-4 flex flex-wrap items-center justify-between gap-3">
          <p className="max-w-xl text-[10px] leading-4 text-[#667991]">Inputs recalculate after a 400 ms stabilization window. Auto Balance reruns the same authoritative maximin solver; it never estimates final stakes in the browser.</p>
          <Button className="border-[#315f9d] bg-[#18365f] text-[#eaf2ff] hover:bg-[#204675]" disabled={problem !== null || waitingForDebounce || simulation.isFetching} onClick={() => void simulation.refetch()}><RefreshCw aria-hidden="true" className={cn("size-4", simulation.isFetching && "animate-spin")} />Auto Balance Stakes</Button>
        </div>
      </Panel>

      <Panel className="overflow-hidden" aria-busy={waitingForDebounce || simulation.isPending || simulation.isFetching}>
        {problem ? <div role="alert" className="flex min-h-[360px] items-center justify-center p-6"><div className="max-w-sm text-center"><TriangleAlert aria-hidden="true" className="mx-auto size-7 text-[#ff8090]" /><p className="mt-3 text-sm font-semibold text-[#f0dce1]">Input needs attention</p><p className="mt-1 text-xs leading-5 text-[#a6828c]">{problem}</p></div></div> : waitingForDebounce || simulation.isPending || (simulation.isFetching && !simulation.data) ? <div role="status" className="flex min-h-[360px] items-center justify-center p-6"><div className="text-center"><LoaderCircle aria-hidden="true" className="mx-auto size-7 animate-spin text-[#6da6ff]" /><p className="mt-3 text-sm font-semibold text-[#dce6f2]">{waitingForDebounce ? "Waiting for inputs to settle" : "Running authoritative simulation"}</p><p className="mt-1 text-xs text-[#6f819a]">No browser-calculated result is shown as final.</p></div></div> : simulation.isError ? <div role="alert" className="flex min-h-[360px] items-center justify-center p-6"><div className="max-w-sm text-center"><TriangleAlert aria-hidden="true" className="mx-auto size-7 text-[#ff8090]" /><p className="mt-3 text-sm font-semibold text-[#f0dce1]">Authoritative engine unavailable</p><p className="mt-1 text-xs leading-5 text-[#a6828c]">{simulation.error instanceof Error ? simulation.error.message : "The calculation could not be completed."}</p><Button className="mt-4" onClick={() => void simulation.refetch()}><RefreshCw aria-hidden="true" className="size-4" />Retry calculation</Button></div></div> : result ? <ResultSummary result={result} /> : null}
      </Panel>
    </div>

    {result ? <ResultDetails result={result} /> : null}

    <div className="grid gap-5 xl:grid-cols-3">
      <Panel className="p-5"><p className="text-sm font-semibold text-[#e0e9f5]">Odds conversion</p><p className="mt-1 text-xs text-[#6f819a]">Equivalent prices and raw implied probability</p><div className="mt-5 grid gap-4 sm:grid-cols-2 xl:grid-cols-1"><label htmlFor="american-odds" className="text-xs font-medium text-[#8495ac]">American odds<input id="american-odds" type="number" step="5" value={american} onChange={(event) => setAmerican(Number(event.target.value))} className={inputClass}/></label><label htmlFor="fractional-odds" className="text-xs font-medium text-[#8495ac]">Fractional odds<input id="fractional-odds" value={fractional} onChange={(event) => setFractional(event.target.value)} className={inputClass}/></label></div>{research.error ? <p role="alert" className="mt-4 text-xs text-[#ff8090]">{research.error}</p> : <div className="mt-4 space-y-2 text-xs"><div className="flex justify-between"><span className="text-[#71839b]">American → decimal</span><span className="font-semibold text-[#dce6f2]">{research.americanDecimal?.toFixed(4)} · {research.americanImplied?.toFixed(2)}%</span></div><div className="flex justify-between"><span className="text-[#71839b]">Fractional → decimal</span><span className="font-semibold text-[#dce6f2]">{research.fractionalDecimal?.toFixed(4)} · {research.fractionalImplied?.toFixed(2)}%</span></div></div>}</Panel>
      <Panel className="p-5"><p className="text-sm font-semibold text-[#e0e9f5]">Vig-normalized weights</p><p className="mt-1 text-xs text-[#6f819a]">Proportional normalization of all current outcomes</p><div className="mt-5 space-y-3">{research.consensus?.map((probability, index) => <div key={index}><div className="mb-1.5 flex justify-between text-xs"><span className="text-[#8192a9]">Outcome {index + 1}</span><span className="font-semibold text-[#dce6f2]">{(probability * 100).toFixed(2)}%</span></div><div className="h-1.5 overflow-hidden rounded-full bg-[#1b2d45]"><div className="h-full rounded-full bg-[#19c9d5]" style={{width:`${probability * 100}%`}}/></div></div>)}</div><p className="mt-4 text-[10px] leading-4 text-[#667991]">Valid only for a complete, mutually exclusive outcome set. These are normalized market weights, not known win probabilities.</p></Panel>
      <Panel className="p-5"><p className="text-sm font-semibold text-[#e0e9f5]">Explicit-assumption EV</p><p className="mt-1 text-xs text-[#6f819a]">Uses Outcome 1&apos;s decimal equivalent; EDGEOS supplies no forecast</p><div className="mt-5 grid grid-cols-2 gap-3"><label htmlFor="ev-probability" className="text-xs font-medium text-[#8495ac]">Your probability (%)<input id="ev-probability" type="number" min="0" max="100" step="0.1" value={probabilityAssumption} onChange={(event) => setProbabilityAssumption(event.target.value)} placeholder="Required" className={inputClass}/></label><label htmlFor="ev-stake" className="text-xs font-medium text-[#8495ac]">Simulated stake<input id="ev-stake" type="number" min="0" step="10" value={evStake} onChange={(event) => setEvStake(Number(event.target.value))} className={inputClass}/></label></div><div className="mt-5 rounded-xl border border-[#243954] bg-[#0d192a] p-4"><p className="text-[10px] uppercase tracking-[.1em] text-[#687b94]">Expected value</p><p className={cn("mt-2 text-xl font-bold", research.ev === null ? "text-[#8091a8]" : research.ev >= 0 ? "text-[#22c991]" : "text-[#ff6478]")}>{research.ev === null ? "Insufficient data" : `${research.ev >= 0 ? "+" : ""}${money(research.ev)}`}</p></div></Panel>
    </div>
    <Panel className="p-5"><div className="flex items-start gap-3"><Info aria-hidden="true" className="mt-0.5 size-5 shrink-0 text-[#6ba5ff]"/><div><p className="text-sm font-semibold text-[#e2eaf5]">Forecast calibration and closing-line value</p><p className="mt-1 text-xs leading-5 text-[#75879f]">Insufficient data. EDGEOS will not manufacture model probabilities, calibration scores, or closing-line performance without legitimate timestamped outcomes and a chronological holdout set.</p></div></div></Panel>
    <Panel className="p-5"><div className="flex items-start gap-3"><span className="grid size-9 shrink-0 place-items-center rounded-xl bg-[#f5b942]/10 text-[#e8b94e]"><TriangleAlert aria-hidden="true" className="size-[18px]" /></span><div><p className="text-sm font-semibold text-[#e2eaf5]">Execution risk remains</p><p className="mt-1 text-xs leading-5 text-[#75879f]">A positive server result is a research scenario—not guaranteed profit. These manual values have no provider timestamp, observed depth, official fee provenance, or verified settlement match, and the public simulation route rejects them for execution.</p></div></div></Panel>
  </div>;
}
