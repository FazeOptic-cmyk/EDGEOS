import { Badge } from "./ui";

export function PageIntro({ badge, title, description, actions }: { badge: string; title: string; description: string; actions?: React.ReactNode }) {
  return <section className="flex flex-col gap-4 lg:flex-row lg:items-end lg:justify-between"><div><Badge className="border-[#38537a] bg-[#14243c] text-[#7eaef7]">{badge}</Badge><h2 className="font-display mt-3 text-2xl font-bold tracking-tight text-[#f2f6ff]">{title}</h2><p className="mt-1 max-w-3xl text-sm leading-6 text-[#7c8ea8]">{description}</p></div>{actions}</section>;
}
