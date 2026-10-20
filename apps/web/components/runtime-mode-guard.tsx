"use client";

import { useEffect, useState } from "react";
import { AlertTriangle, LoaderCircle } from "lucide-react";
import {
  assertEdgeosMode,
  configuredAppMode,
  EdgeosModeMismatchError,
} from "@/lib/edgeos-proxy";

type GuardState =
  | { status: "checking" }
  | { status: "ready" }
  | { status: "blocked"; message: string };

export function RuntimeModeGuard({ children }: { children: React.ReactNode }) {
  const [state, setState] = useState<GuardState>({ status: "checking" });

  useEffect(() => {
    let active = true;
    const expected = configuredAppMode();

    void fetch("/api/edgeos/api/v1/health", {
      cache: "no-store",
      headers: { Accept: "application/json" },
      // An explicitly labelled offline demo can proceed after a short probe.
      // Live mode stays fail-closed and gets the longer operational timeout.
      signal: AbortSignal.timeout(expected === "demo" ? 2_000 : 10_000),
    }).then((response) => {
      assertEdgeosMode(response.headers, expected);
      if (!response.ok) throw new Error(`Health check failed (${response.status})`);
      if (active) setState({ status: "ready" });
    }).catch((error: unknown) => {
      if (!active) return;
      if (expected === "demo" && !(error instanceof EdgeosModeMismatchError)) {
        // A credential-free demo remains usable offline and visibly labelled.
        // A positively observed live-mode API is still blocked: demo fallback
        // must never mask an actual cross-mode deployment mistake.
        setState({ status: "ready" });
        return;
      }
      setState({
        status: "blocked",
        message: error instanceof Error ? error.message : "Unable to verify the EDGEOS API data mode.",
      });
    });

    return () => { active = false; };
  }, []);

  if (state.status === "ready") return children;
  if (state.status === "checking") {
    return (
      <div className="flex min-h-[55vh] items-center justify-center" role="status">
        <div className="flex items-center gap-3 text-sm text-[var(--muted)]">
          <LoaderCircle className="h-4 w-4 animate-spin text-[var(--primary)]" aria-hidden="true" />
          Verifying API data mode…
        </div>
      </div>
    );
  }
  return (
    <section
      className="mx-auto mt-12 max-w-2xl rounded-2xl border border-[color:rgba(255,100,120,.35)] bg-[color:rgba(255,100,120,.07)] p-6"
      role="alert"
    >
      <div className="flex items-start gap-3">
        <AlertTriangle className="mt-0.5 h-5 w-5 shrink-0 text-[var(--negative)]" aria-hidden="true" />
        <div>
          <h1 className="font-display text-lg font-semibold text-[var(--text)]">Data mode configuration error</h1>
          <p className="mt-2 text-sm leading-6 text-[var(--muted)]">{state.message}</p>
          <p className="mt-3 text-xs leading-5 text-[var(--muted)]">
            EDGEOS has blocked market and performance results. Set the web and API to the same explicit demo or live mode, then restart both services.
          </p>
        </div>
      </div>
    </section>
  );
}
