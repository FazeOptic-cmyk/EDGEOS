import type { Metadata } from "next";
import { WatchlistClient } from "@/components/watchlist/watchlist-client";
export const metadata: Metadata = { title: "Watchlist" };
export default function WatchlistPage(){return <WatchlistClient/>}
