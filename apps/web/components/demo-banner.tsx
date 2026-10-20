import { FlaskConical } from "lucide-react";
import { appMode } from "@/lib/api";

export function DemoBanner() {
  if (appMode !== "demo") return null;
  return (
    <div className="flex min-h-9 items-center justify-center gap-2 border-b border-[#31475d]/75 bg-[#132131]/92 px-4 text-center text-[11px] font-medium text-[#a6b5c9]">
      <FlaskConical className="size-3.5 text-[#57b9f5]" aria-hidden />
      <span><strong className="text-[#edf5fb]">Demonstration mode</strong> · Illustrative records only — never live or executable prices.</span>
    </div>
  );
}
