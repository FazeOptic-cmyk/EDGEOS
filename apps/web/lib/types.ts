export type MatchStatus = "VERIFIED MATCH" | "REVIEW REQUIRED" | "REJECTED";
export type DataMode = "live" | "delayed" | "cached" | "historical" | "demo";
export type OpportunityLifecycle = "active" | "stale" | "invalidated" | "expired" | "settled";

export interface QuoteLeg {
  platform: string;
  outcome: string;
  price: number;
  decimalOdds: number;
  size: number | null;
  fee: number | null;
  feedType: "streaming" | "polling" | "snapshot";
  timestamp: string | null;
}

export interface Opportunity {
  id: string;
  event: string;
  sport: string;
  league: string;
  market: string;
  marketType: string;
  startTime: string | null;
  live: boolean;
  currency: string;
  legs: QuoteLeg[];
  grossRoi: number;
  netRoi: number;
  potentialProfit: number;
  liquidity: number | null;
  quoteAgeSeconds: number | null;
  lifecycle: OpportunityLifecycle | null;
  expiresAt: string | null;
  withdrawnAt: string | null;
  observedAt: string;
  matchStatus: MatchStatus;
  score: number;
  dataMode: DataMode;
  totalAllocation: number;
  totalAllocationExact?: string;
  totalFees: number | null;
  worstCase: number;
  bestCase: number;
  settlementSummary: string;
  matchReasons: string[];
  riskReasons: string[];
  allocations: {
    platform: string;
    outcome: string;
    stake: number;
    cashCost: number;
    fee: number;
    quantity: number;
    priceKind: "decimal_odds" | "contract_ask";
    modeledPrice?: number;
  }[];
  payouts: { outcome: string; gross: number; net: number }[];
  sources: { label: string; url: string }[];
  history: { time: string; roi: number }[];
}

export interface ProviderHealth {
  id: string;
  name: string;
  status: "connected" | "delayed" | "disconnected" | "requires_key" | "requires_api_key" | "not_supported" | "demo_only" | "unknown";
  feed_type?: string;
  last_successful_update?: string | null;
  checked_at?: string | null;
  last_error?: string | null;
  refresh_interval_seconds?: number | null;
  measured_data_delay_seconds?: number | null;
  active_instruments?: number | null;
  quota_used?: number | null;
  quota_limit?: number | null;
  quota_remaining?: number | null;
  quota_reset_at?: string | null;
  retry_after_seconds?: number | null;
  rate_limit_status?: string | null;
  market_coverage?: string[];
  capabilities?: string[];
  message?: string | null;
}
