"use client";

import { useEffect, useRef, useState } from "react";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useIsFetching, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Activity,
  BarChart3,
  Bell,
  BookOpenCheck,
  ChevronLeft,
  ChevronRight,
  CircleUserRound,
  FlaskConical,
  Gauge,
  Heart,
  Menu,
  RefreshCw,
  Search,
  Settings,
  TrendingUp,
  WalletCards,
  X,
} from "lucide-react";
import { appMode, getProviderHealth } from "@/lib/api";
import { foregroundLiveRefresh } from "@/lib/live-refresh";
import type { ProviderHealth } from "@/lib/types";
import { cn } from "@/lib/utils";
import { getAlerts } from "@/lib/workflow-api";
import { DemoBanner } from "./demo-banner";

const navigation = [
  {
    label: "Research",
    items: [
      { href: "/", label: "Dashboard", icon: Gauge },
      { href: "/scanner", label: "Arbitrage Scanner", icon: Activity },
      { href: "/markets", label: "Prediction Markets", icon: BookOpenCheck },
      { href: "/edge-lab", label: "+EV & Calculator", icon: FlaskConical },
    ],
  },
  {
    label: "Portfolio",
    items: [
      { href: "/portfolio", label: "Bankroll & Paper", icon: WalletCards },
      { href: "/analytics", label: "Profit Analytics", icon: BarChart3 },
    ],
  },
  {
    label: "Workspace",
    items: [
      { href: "/watchlist", label: "Saved & Watchlist", icon: Heart },
      { href: "/alerts", label: "Alerts", icon: Bell },
      { href: "/settings", label: "Settings & APIs", icon: Settings },
    ],
  },
] as const;

const pageTitles: Record<string, { eyebrow: string; title: string }> = {
  "/": { eyebrow: "Overview", title: "Research dashboard" },
  "/scanner": { eyebrow: "Market intelligence", title: "Arbitrage research" },
  "/edge-lab": { eyebrow: "Quantitative research", title: "+EV & calculator" },
  "/markets": { eyebrow: "Data operations", title: "Prediction markets" },
  "/portfolio": { eyebrow: "Simulation", title: "Bankroll & paper portfolio" },
  "/analytics": { eyebrow: "Performance", title: "Profit analytics" },
  "/watchlist": { eyebrow: "Research", title: "Saved opportunities & watchlist" },
  "/alerts": { eyebrow: "Monitoring", title: "Alerts" },
  "/settings": { eyebrow: "Workspace", title: "Settings & API integrations" },
};

type ConnectionTone = "blue" | "green" | "amber" | "red" | "muted";

export type ConnectionSummary = {
  label: string;
  detail: string;
  tone: ConnectionTone;
  latestSuccessfulUpdate: string | null;
};

function validTimestamp(value: string | null | undefined): number | null {
  if (!value) return null;
  const timestamp = Date.parse(value);
  return Number.isFinite(timestamp) ? timestamp : null;
}

export function summarizeProviderConnection(
  providers: ProviderHealth[] | undefined,
  options: { pending: boolean; error: boolean; source?: "api" | "demo" },
): ConnectionSummary {
  const latestTimestamp = Math.max(
    ...((providers ?? []).flatMap((provider) => {
      const timestamp = validTimestamp(provider.last_successful_update);
      return timestamp === null ? [] : [timestamp];
    })),
    Number.NEGATIVE_INFINITY,
  );
  const latestSuccessfulUpdate = Number.isFinite(latestTimestamp)
    ? new Date(latestTimestamp).toISOString()
    : null;

  if (options.pending && !providers) {
    return { label: "Checking data", detail: "Provider health pending", tone: "muted", latestSuccessfulUpdate };
  }
  if (options.error) {
    return {
      label: providers?.length ? "Health refresh failed" : "Data unavailable",
      detail: providers?.length ? "Showing last known status" : "Provider health request failed",
      tone: "red",
      latestSuccessfulUpdate,
    };
  }
  if (options.source === "demo") {
    return { label: "DEMO data", detail: "No live provider claim", tone: "blue", latestSuccessfulUpdate };
  }

  const connected = (providers ?? []).filter((provider) => provider.status === "connected").length;
  const delayed = (providers ?? []).filter((provider) => provider.status === "delayed").length;
  if (connected > 0) {
    return {
      label: `${connected} provider${connected === 1 ? "" : "s"} live`,
      detail: delayed > 0 ? `${delayed} delayed` : "Read-only feeds connected",
      tone: delayed > 0 ? "amber" : "green",
      latestSuccessfulUpdate,
    };
  }
  if (delayed > 0) {
    return {
      label: `${delayed} provider${delayed === 1 ? "" : "s"} delayed`,
      detail: "No current live feed",
      tone: "amber",
      latestSuccessfulUpdate,
    };
  }
  return {
    label: "No live feeds",
    detail: "Configure an integration",
    tone: "muted",
    latestSuccessfulUpdate,
  };
}

export function formatRefreshTime(value: string | null): string {
  if (!value) return "No successful refresh";
  const parsed = new Date(value);
  if (!Number.isFinite(parsed.getTime())) return "No successful refresh";
  const formatted = new Intl.DateTimeFormat("en-US", {
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
    timeZone: "UTC",
    timeZoneName: "short",
  }).format(parsed);
  return `Last refresh ${formatted}`;
}

function isEditableTarget(target: EventTarget | null): boolean {
  if (!(target instanceof HTMLElement)) return false;
  return target.isContentEditable || ["INPUT", "SELECT", "TEXTAREA"].includes(target.tagName);
}

const toneClasses: Record<ConnectionTone, string> = {
  blue: "bg-[#57b9f5] shadow-[0_0_10px_rgba(87,185,245,.58)]",
  green: "bg-[#40d6a4] shadow-[0_0_10px_rgba(64,214,164,.52)]",
  amber: "bg-[#f5bc69] shadow-[0_0_10px_rgba(245,188,105,.45)]",
  red: "bg-[#f17e90] shadow-[0_0_10px_rgba(241,126,144,.46)]",
  muted: "bg-[#708198]",
};

export function AppShell({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const router = useRouter();
  const queryClient = useQueryClient();
  const activeFetches = useIsFetching();
  const searchRef = useRef<HTMLInputElement>(null);
  const [collapsed, setCollapsed] = useState(false);
  const [mobileOpen, setMobileOpen] = useState(false);
  const [search, setSearch] = useState("");
  const [manualRefresh, setManualRefresh] = useState(false);
  const current = pageTitles[pathname] ?? { eyebrow: "EDGEOS", title: "Market intelligence" };

  const providerQuery = useQuery({
    queryKey: ["providers", "health"],
    queryFn: getProviderHealth,
    ...foregroundLiveRefresh(appMode),
  });
  const alertsQuery = useQuery({
    queryKey: ["alerts"],
    queryFn: getAlerts,
    retry: false,
    ...foregroundLiveRefresh(appMode),
  });
  const connection = summarizeProviderConnection(providerQuery.data?.data, {
    pending: providerQuery.isPending,
    error: providerQuery.isError,
    source: providerQuery.data?.source,
  });
  const unreadAlerts = alertsQuery.data?.events.filter((event) => event.read_at === null).length ?? 0;
  const notificationMode = alertsQuery.data?.mode;
  const unreadVisual = notificationMode === "demo" ? `DEMO ${unreadAlerts}` : String(unreadAlerts);
  const notificationLabel = alertsQuery.isPending
    ? "Notifications, checking unread count"
    : alertsQuery.isError
      ? "Notifications unavailable"
      : unreadAlerts > 0
        ? `${unreadAlerts} unread ${notificationMode === "demo" ? "demonstration " : ""}notification${unreadAlerts === 1 ? "" : "s"}`
        : "Notifications, no unread alerts";

  useEffect(() => {
    const handleShortcut = (event: KeyboardEvent) => {
      const searchShortcut = (event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "k";
      const slashShortcut = event.key === "/" && !event.ctrlKey && !event.metaKey && !event.altKey;
      if (searchShortcut || (slashShortcut && !isEditableTarget(event.target))) {
        event.preventDefault();
        searchRef.current?.focus();
      }
      if (event.key === "Escape" && document.activeElement === searchRef.current) {
        setSearch("");
        searchRef.current?.blur();
      }
    };
    window.addEventListener("keydown", handleShortcut);
    return () => window.removeEventListener("keydown", handleShortcut);
  }, []);

  const refreshActiveData = async () => {
    setManualRefresh(true);
    try {
      await queryClient.refetchQueries({ type: "active" });
    } finally {
      setManualRefresh(false);
    }
  };

  return (
    <div className="min-h-screen bg-[var(--background)]">
      <DemoBanner />
      <div className={cn("flex", appMode === "demo" ? "min-h-[calc(100vh-36px)]" : "min-h-screen")}>
        {mobileOpen && (
          <button
            type="button"
            aria-label="Close navigation"
            className="fixed inset-0 z-40 bg-black/65 backdrop-blur-sm lg:hidden"
            onClick={() => setMobileOpen(false)}
          />
        )}
        <aside
          className={cn(
            "fixed bottom-0 left-0 z-50 flex max-w-[calc(100vw-3rem)] flex-col border-r border-[#26384c] bg-[#101a29]/98 shadow-[18px_0_55px_rgba(0,0,0,.18)] transition-[width,transform] duration-200 lg:sticky lg:top-0 lg:max-w-none",
            appMode === "demo" ? "top-9 lg:h-[calc(100vh-36px)]" : "top-0 lg:h-screen",
            collapsed ? "w-[82px]" : "w-[264px]",
            mobileOpen ? "visible translate-x-0" : "invisible -translate-x-full lg:visible lg:translate-x-0",
          )}
        >
          <div className={cn("flex h-[76px] shrink-0 items-center border-b border-[#26384c]/80", collapsed ? "justify-center px-3" : "justify-between px-5")}>
            <Link href="/" className="focus-ring flex items-center gap-3 rounded-xl" onClick={() => setMobileOpen(false)}>
              <span className="grid size-10 place-items-center rounded-[14px] border border-[#35526b] bg-[#172536] shadow-[0_0_24px_rgba(87,185,245,.12)]">
                <TrendingUp className="size-5 text-[#57b9f5]" strokeWidth={2.3} />
              </span>
              {!collapsed && (
                <span className="font-display text-[18px] font-extrabold tracking-[.16em] text-[#f8fafc]">
                  EDGE<span className="text-[#57b9f5]">OS</span>
                </span>
              )}
            </Link>
            {!collapsed && (
              <button type="button" onClick={() => setMobileOpen(false)} className="focus-ring rounded-lg p-1 text-[#8898ad] hover:text-white lg:hidden" aria-label="Close navigation">
                <X className="size-5" />
              </button>
            )}
          </div>

          <nav className="flex-1 overflow-y-auto overflow-x-hidden px-3 py-4" aria-label="Primary navigation">
            {navigation.map((group, groupIndex) => (
              <div key={group.label} className={cn(groupIndex > 0 && "mt-5")}>
                {!collapsed ? (
                  <p className="mb-2 px-3 text-[10px] font-bold uppercase tracking-[.18em] text-[#718197]">{group.label}</p>
                ) : groupIndex > 0 ? (
                  <div className="mx-3 mb-3 border-t border-[#26384c]" aria-hidden="true" />
                ) : null}
                <div className="space-y-1">
                  {group.items.map(({ href, label, icon: Icon }) => {
                    const active = href === "/" ? pathname === href : pathname.startsWith(href);
                    const isAlerts = href === "/alerts";
                    return (
                      <Link
                        key={href}
                        href={href}
                        title={collapsed ? label : undefined}
                        aria-label={collapsed ? label : undefined}
                        aria-current={active ? "page" : undefined}
                        onClick={() => setMobileOpen(false)}
                        className={cn(
                          "focus-ring group relative flex min-h-11 items-center gap-3 rounded-2xl px-3 text-sm font-medium transition duration-200",
                          collapsed && "justify-center px-0",
                          active
                            ? "bg-[#1c3044] text-[#f8fafc] shadow-[inset_0_0_0_1px_rgba(87,185,245,.18)]"
                            : "text-[#93a3b7] hover:bg-[#172536] hover:text-[#e3ebf4]",
                        )}
                      >
                        {active && <span className="absolute -left-3 h-6 w-0.5 rounded-r-full bg-[#57b9f5] shadow-[0_0_12px_rgba(87,185,245,.8)]" />}
                        <Icon className={cn("size-[18px] shrink-0 transition", active ? "text-[#76cdff]" : "text-[#7f91a8] group-hover:text-[#b9c8d9]")} strokeWidth={1.9} />
                        {!collapsed && <span className="truncate">{label}</span>}
                        {isAlerts && unreadAlerts > 0 && !alertsQuery.isError && (
                          <span
                            className={cn(
                              collapsed ? "absolute right-2 top-1 size-2 rounded-full" : "ml-auto rounded-full px-2 py-0.5 text-[9px] font-bold",
                              notificationMode === "demo" ? "bg-[#3a3020] text-[#f5bc69]" : "bg-[#1b4660] text-[#8bd8ff]",
                            )}
                            title={notificationLabel}
                          >
                            {!collapsed && unreadVisual}
                          </span>
                        )}
                      </Link>
                    );
                  })}
                </div>
              </div>
            ))}
          </nav>

          <div className="shrink-0 border-t border-[#26384c]/80 p-3">
            {!collapsed && (
              <Link href="/markets" className="focus-ring mb-3 block rounded-2xl border border-[#293e53] bg-[#132131] p-3 transition hover:border-[#3c5971]" title={connection.detail}>
                <div className="flex items-center gap-2 text-xs font-semibold text-[#dce6f1]">
                  <span className={cn("size-2 rounded-full", toneClasses[connection.tone])} aria-hidden="true" />
                  <span className="truncate">{connection.label}</span>
                </div>
                <p className="mt-1.5 truncate text-[10px] leading-4 text-[#7d8fa5]">{formatRefreshTime(connection.latestSuccessfulUpdate)}</p>
                <p className="mt-0.5 truncate text-[9px] uppercase tracking-[.1em] text-[#61748b]">{appMode === "demo" ? "Demonstration workspace" : connection.detail}</p>
              </Link>
            )}
            <button
              type="button"
              onClick={() => setCollapsed((value) => !value)}
              aria-label={collapsed ? "Expand sidebar" : "Collapse sidebar"}
              aria-expanded={!collapsed}
              className="focus-ring hidden h-9 w-full items-center justify-center gap-2 rounded-xl text-xs font-medium text-[#7f91a7] transition hover:bg-[#172536] hover:text-white lg:flex"
            >
              {collapsed ? <ChevronRight className="size-4" /> : <><ChevronLeft className="size-4" /> Collapse</>}
            </button>
          </div>
        </aside>

        <div className="min-w-0 flex-1">
          <header className="sticky top-0 z-30 flex min-h-[76px] items-center gap-3 border-b border-[#26384c]/75 bg-[#080d16]/88 px-4 py-3 backdrop-blur-xl sm:px-6 lg:px-7">
            <button
              type="button"
              onClick={() => setMobileOpen(true)}
              className="focus-ring shrink-0 rounded-2xl border border-[#2a3d52] bg-[#142131] p-2.5 text-[#b3c1d1] lg:hidden"
              aria-label="Open navigation"
              aria-expanded={mobileOpen}
            >
              <Menu className="size-5" />
            </button>
            <div className="min-w-0 shrink sm:max-w-[17rem]">
              <p className="hidden text-[10px] font-bold uppercase tracking-[.18em] text-[#7091ae] min-[420px]:block">{current.eyebrow}</p>
              <h1 className="font-display truncate text-base font-bold text-[#f3f7fb] sm:text-lg xl:text-xl">{current.title}</h1>
            </div>

            <div className="ml-auto flex min-w-0 items-center gap-2">
              <Link
                href="/markets"
                className="focus-ring hidden min-w-0 max-w-48 items-center gap-2.5 rounded-2xl border border-[#293e53] bg-[#111d2c] px-3 py-2 min-[1180px]:flex"
                aria-label={`${connection.label}. ${connection.detail}. ${formatRefreshTime(connection.latestSuccessfulUpdate)}.`}
              >
                <span className={cn("size-2 shrink-0 rounded-full", toneClasses[connection.tone])} aria-hidden="true" />
                <span className="min-w-0">
                  <span className="block truncate text-[10px] font-semibold text-[#dce6f1]">{connection.label}</span>
                  <span className="block truncate text-[9px] text-[#71849a]">{formatRefreshTime(connection.latestSuccessfulUpdate)}</span>
                </span>
              </Link>

              <form
                role="search"
                className="relative hidden min-[980px]:block"
                onSubmit={(event) => {
                  event.preventDefault();
                  const value = search.trim();
                  router.push(value ? `/scanner?q=${encodeURIComponent(value)}` : "/scanner");
                }}
              >
                <label htmlFor="edgeos-global-search" className="sr-only">Search EDGEOS</label>
                <Search className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-[#73869c]" aria-hidden="true" />
                <input
                  id="edgeos-global-search"
                  ref={searchRef}
                  type="search"
                  value={search}
                  onChange={(event) => setSearch(event.target.value)}
                  placeholder="Search markets…"
                  className="focus-ring h-10 w-44 rounded-2xl border border-[#293e53] bg-[#111d2c] pl-9 pr-11 text-xs text-[#edf4fa] placeholder:text-[#718197] xl:w-56"
                />
                <kbd className="pointer-events-none absolute right-3 top-1/2 hidden -translate-y-1/2 rounded-md border border-[#344a60] bg-[#172536] px-1.5 py-0.5 text-[9px] text-[#8496aa] xl:block">Ctrl K</kbd>
              </form>
              <Link href="/scanner" className="focus-ring grid size-10 place-items-center rounded-2xl border border-[#293e53] bg-[#111d2c] text-[#8fa0b4] transition hover:border-[#436078] hover:text-white min-[980px]:hidden" aria-label="Search markets">
                <Search className="size-[18px]" />
              </Link>
              <button
                type="button"
                onClick={() => void refreshActiveData()}
                disabled={manualRefresh}
                className="focus-ring hidden size-10 place-items-center rounded-2xl border border-[#293e53] bg-[#111d2c] text-[#8fa0b4] transition hover:border-[#436078] hover:text-white disabled:opacity-60 sm:grid"
                aria-label={manualRefresh ? "Refreshing active data" : "Refresh active data"}
                title={activeFetches > 0 ? `${activeFetches} data request${activeFetches === 1 ? "" : "s"} active` : "Refresh active data"}
              >
                <RefreshCw className={cn("size-[18px]", manualRefresh && "animate-spin")} />
              </button>
              <Link
                href="/alerts"
                className={cn(
                  "focus-ring relative grid size-10 place-items-center rounded-2xl border bg-[#111d2c] text-[#8fa0b4] transition hover:border-[#436078] hover:text-white",
                  alertsQuery.isError ? "border-[#70404a]" : "border-[#293e53]",
                )}
                aria-label={notificationLabel}
                title={notificationLabel}
              >
                <Bell className="size-[18px]" />
                {unreadAlerts > 0 && !alertsQuery.isError && (
                  <span className={cn(
                    "absolute -right-1.5 -top-1.5 min-w-5 rounded-full px-1.5 py-0.5 text-center text-[8px] font-extrabold ring-2 ring-[#080d16]",
                    notificationMode === "demo" ? "bg-[#f5bc69] text-[#241b0e]" : "bg-[#57b9f5] text-[#07131c]",
                  )}>
                    {notificationMode === "demo" ? `D${Math.min(unreadAlerts, 9)}` : unreadAlerts > 9 ? "9+" : unreadAlerts}
                  </span>
                )}
              </Link>
              <Link href="/settings" className="focus-ring hidden size-10 place-items-center rounded-2xl border border-[#293e53] bg-[#111d2c] text-[#8fa0b4] transition hover:border-[#436078] hover:text-white sm:grid" aria-label="Settings">
                <Settings className="size-[18px]" />
              </Link>
              <Link href="/settings" className="focus-ring grid size-10 place-items-center rounded-2xl bg-[#57b9f5] text-[#08131d] shadow-[inset_0_0_0_1px_rgba(255,255,255,.24),0_8px_24px_rgba(87,185,245,.18)] transition hover:bg-[#76cdff]" aria-label="Account profile">
                <CircleUserRound className="size-5" strokeWidth={2.1} />
              </Link>
            </div>
          </header>
          <main className="mx-auto max-w-[1720px] overflow-x-clip p-4 sm:p-6 lg:p-7">{children}</main>
        </div>
      </div>
    </div>
  );
}
