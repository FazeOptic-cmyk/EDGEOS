import type { Metadata } from "next";
import { ScannerClient } from "@/components/scanner/scanner-client";

export const metadata: Metadata = { title: "Arbitrage Scanner" };

export default async function ScannerPage({ searchParams }: { searchParams: Promise<{ q?: string }> }) {
  const { q } = await searchParams;
  return <ScannerClient key={q ?? ""} initialSearch={q ?? ""} />;
}
