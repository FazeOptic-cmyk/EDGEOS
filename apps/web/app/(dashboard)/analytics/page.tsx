import type { Metadata } from "next";
import { AnalyticsClient } from "@/components/analytics/analytics-client";
export const metadata: Metadata = { title: "Analytics" };
export default function AnalyticsPage() { return <AnalyticsClient />; }
