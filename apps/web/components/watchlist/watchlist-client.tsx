"use client";

import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Heart, LoaderCircle, Plus, Trash2 } from "lucide-react";
import { PageIntro } from "@/components/page-intro";
import { WorkflowDialog } from "@/components/workflows/workflow-dialog";
import { Badge, Button, Panel } from "@/components/ui";
import { createWatchlistItem, deleteWatchlistItem, getWatchlist, type WatchlistItem } from "@/lib/workflow-api";

const kinds: WatchlistItem["kind"][] = ["team","sport","league","event","market","provider","strategy"];

export function WatchlistClient() {
  const queryClient = useQueryClient();
  const desktopBuild=process.env.NEXT_PUBLIC_DESKTOP_BUILD==="true";
  const [adding,setAdding]=useState(false);
  const [kind,setKind]=useState<WatchlistItem["kind"]>("team");
  const [label,setLabel]=useState("");
  const [value,setValue]=useState("");
  const watchlist = useQuery({
    queryKey:["watchlist"],
    queryFn:getWatchlist,
  });
  const addMutation=useMutation({
    mutationFn:()=>createWatchlistItem({kind,value:value.trim(),label:label.trim(),idempotency_key:crypto.randomUUID()}),
    onSuccess:(item)=>{queryClient.setQueryData<Awaited<ReturnType<typeof getWatchlist>>>(["watchlist"],(old)=>old?{...old,items:[...old.items.filter((entry)=>!(entry.kind===item.kind&&entry.value===item.value)),item]}:old);void queryClient.invalidateQueries({queryKey:["watchlist"]});setLabel("");setValue("");setAdding(false);},
  });
  const deleteMutation=useMutation({
    mutationFn:async(item:WatchlistItem)=>{await deleteWatchlistItem(item.id);return item.id;},
    onSuccess:(id)=>queryClient.setQueryData<Awaited<ReturnType<typeof getWatchlist>>>(["watchlist"],(old)=>old?{...old,items:old.items.filter((item)=>item.id!==id)}:old),
  });
  const items=watchlist.data?.items??[];
  return <div className="space-y-5">
    <PageIntro badge="saved research" title="Watchlist" description="Follow typed research entities. Records are owned by the current workspace and served through its private-data API." actions={<Button onClick={()=>setAdding(true)} className="border-[#4f8cff] bg-[#4f8cff] text-white"><Plus className="size-4"/> Add watched item</Button>}/>
    {watchlist.isPending&&<Panel className="flex items-center gap-3 p-5 text-sm text-[#8294aa]"><LoaderCircle className="size-4 animate-spin"/> Loading watchlist…</Panel>}
    {watchlist.error&&<Panel role="alert" className="flex flex-col gap-3 border-[#603545] p-5 text-sm text-[#f17e90] sm:flex-row sm:items-center sm:justify-between"><span>Watchlist unavailable: {watchlist.error.message}. No browser fixtures were substituted.</span><Button onClick={()=>void watchlist.refetch()} className="shrink-0">Retry</Button></Panel>}
    {deleteMutation.error&&<Panel role="alert" className="border-[#603545] p-4 text-xs text-[#ff91a0]">{deleteMutation.error.message}</Panel>}
    {!watchlist.isError&&<Panel className="overflow-hidden"><div className="flex items-center justify-between border-b border-[#203149] px-5 py-4"><div><p className="text-sm font-semibold text-[#e2eaf5]">Watched items</p><p className="mt-1 text-xs text-[#6d8099]">{items.length} active · {watchlist.data?.mode === "demo" ? desktopBuild ? "DEMO · DEVICE LOCAL" : "DEMO WORKSPACE" : "PRIVATE PERSISTED"}</p></div><Heart className="size-5 fill-[#ff6478]/20 text-[#ff7183]"/></div>
      {items.length?<div className="grid gap-px bg-[#203149] sm:grid-cols-2">{items.map((item)=><div key={item.id} className="group flex items-center gap-3 bg-[#101b2d] p-5 transition hover:bg-[#142239]"><span className="grid size-10 shrink-0 place-items-center rounded-xl border border-[#2b405e] bg-[#17263d] text-xs font-bold text-[#78aaf6]">{item.label.slice(0,2).toUpperCase()}</span><div className="min-w-0 flex-1"><div className="flex items-center gap-2"><p className="truncate text-sm font-semibold text-[#dfe8f4]">{item.label}</p><Badge className="px-1.5 py-0.5 text-[9px]">{item.kind}</Badge>{item.is_demo&&<Badge className="border-[#665329] bg-[#302713] text-[#f0c76d]">DEMO</Badge>}</div><p className="mt-1 text-xs text-[#6a7d96]">{item.detail??item.value}</p></div><button type="button" aria-label={`Remove ${item.label}`} onClick={()=>deleteMutation.mutate(item)} disabled={deleteMutation.isPending} className="focus-ring rounded-lg p-2 text-[#647891] transition hover:bg-[#2a1b25] hover:text-[#ff7183]"><Trash2 className="size-4"/></button></div>)}</div>:!watchlist.isPending&&<div className="flex flex-col items-center py-16 text-center"><Heart className="size-8 text-[#4c6079]"/><p className="mt-4 text-sm font-semibold text-[#dce6f2]">Your watchlist is empty</p><p className="mt-1 text-xs text-[#71839b]">Add a team, event, market, provider, or strategy.</p></div>}
    </Panel>}
    <WorkflowDialog open={adding} onClose={()=>setAdding(false)} title="Add watched item" description="Choose a typed entity so duplicate prevention and future alert evaluation remain deterministic.">
      <form className="mt-5 space-y-4" onSubmit={(event)=>{event.preventDefault();addMutation.mutate();}}>
        <label className="block text-xs text-[#8192a9]">Entity type<select autoFocus value={kind} onChange={(event)=>setKind(event.target.value as WatchlistItem["kind"])} className="focus-ring mt-2 h-11 w-full rounded-xl border border-[#293e5b] bg-[#0d1829] px-3 text-[#e5edf8]">{kinds.map((item)=><option key={item} value={item}>{item.replace(/^./,(letter)=>letter.toUpperCase())}</option>)}</select></label>
        <label className="block text-xs text-[#8192a9]">Display label<input required value={label} onChange={(event)=>setLabel(event.target.value)} placeholder="e.g. Buffalo Bills" className="focus-ring mt-2 h-11 w-full rounded-xl border border-[#293e5b] bg-[#0d1829] px-3 text-[#e5edf8]"/></label>
        <label className="block text-xs text-[#8192a9]">Canonical ID or slug<input required value={value} onChange={(event)=>setValue(event.target.value)} placeholder="e.g. buffalo-bills" className="focus-ring mt-2 h-11 w-full rounded-xl border border-[#293e5b] bg-[#0d1829] px-3 text-[#e5edf8]"/></label>
        {addMutation.error&&<p role="alert" className="text-xs text-[#ff91a0]">{addMutation.error.message}</p>}
        <Button type="submit" disabled={!label.trim()||!value.trim()||addMutation.isPending} className="h-11 w-full border-[#4f8cff] bg-[#4f8cff] text-white"><Plus className="size-4"/> {addMutation.isPending?"Saving…":"Save watched item"}</Button>
      </form>
    </WorkflowDialog>
  </div>;
}
