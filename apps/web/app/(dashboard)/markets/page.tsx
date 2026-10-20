import type { Metadata } from "next";
import { MarketsClient } from "@/components/markets/markets-client";
import { MatchReviewQueue } from "@/components/markets/match-review-queue";
export const metadata: Metadata = { title: "Markets & Providers" };
export default function MarketsPage() {
  return <div className="space-y-8"><MarketsClient /><MatchReviewQueue /></div>;
}
