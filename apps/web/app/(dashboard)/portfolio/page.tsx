import type { Metadata } from "next";
import { PortfolioClient } from "@/components/portfolio/portfolio-client";
export const metadata: Metadata = { title: "Paper Portfolio" };
export default function PortfolioPage() { return <PortfolioClient />; }
