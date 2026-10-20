import type { Metadata } from "next";
import { EdgeLabClient } from "@/components/edge-lab/edge-lab-client";
export const metadata: Metadata = { title: "Edge Lab" };
export default function EdgeLabPage() { return <EdgeLabClient />; }
