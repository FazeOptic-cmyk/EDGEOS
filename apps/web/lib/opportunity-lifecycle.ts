import type { OpportunityLifecycle } from "./types";

export type OpportunityDisplayState = "active" | "stale" | "withdrawn";

export function opportunityDisplayState(lifecycle: OpportunityLifecycle | null): OpportunityDisplayState {
  if (lifecycle === "stale") return "stale";
  if (lifecycle === "invalidated" || lifecycle === "expired" || lifecycle === "settled") return "withdrawn";
  return "active";
}

export function opportunityLifecycleLabel(lifecycle: OpportunityLifecycle | null): string | null {
  if (lifecycle === null) return null;
  if (lifecycle === "invalidated") return "Invalidated";
  return lifecycle[0].toUpperCase() + lifecycle.slice(1);
}

export function isOpportunityActionable(lifecycle: OpportunityLifecycle | null): boolean {
  return opportunityDisplayState(lifecycle) === "active";
}
