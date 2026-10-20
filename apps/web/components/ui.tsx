import type { ButtonHTMLAttributes, HTMLAttributes } from "react";
import { cn } from "@/lib/utils";

export function Panel({ className, ...props }: HTMLAttributes<HTMLDivElement>) {
  return <div className={cn("panel rounded-2xl", className)} {...props} />;
}

export function Badge({ className, ...props }: HTMLAttributes<HTMLSpanElement>) {
  return <span className={cn("inline-flex items-center gap-1.5 rounded-full border border-[#30465c] bg-[#172536] px-2.5 py-1 text-[10px] font-semibold uppercase tracking-[.1em] text-[#b7c4d3]", className)} {...props} />;
}

export function Button({ className, type = "button", ...props }: ButtonHTMLAttributes<HTMLButtonElement>) {
  return <button type={type} className={cn("focus-ring inline-flex h-10 items-center justify-center gap-2 rounded-xl border border-[#31495f] bg-[#1a2a3c] px-4 text-sm font-semibold text-[#edf4fa] transition duration-200 hover:border-[#4b6a82] hover:bg-[#203449] disabled:pointer-events-none disabled:opacity-50", className)} {...props} />;
}

export function StatusDot({ tone = "blue" }: { tone?: "blue" | "green" | "red" | "yellow" | "gray" }) {
  const colors = { blue: "bg-[#57b9f5]", green: "bg-[#40d6a4]", red: "bg-[#f17e90]", yellow: "bg-[#f5bc69]", gray: "bg-[#708198]" };
  return <span className={cn("inline-block size-1.5 rounded-full shadow-[0_0_8px_currentColor]", colors[tone])} />;
}
