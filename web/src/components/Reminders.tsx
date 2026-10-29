import { useQuery } from "@tanstack/react-query";
import { BellOff, Clock, CreditCard, FileText, Landmark, MoreHorizontal } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { api } from "../api";
import { cx } from "../format";
import { useUI, type Route } from "../ui";
import { useSave } from "./hooks";

/** /api/reminders: kararlı id ("ekstre:<kart>:<YYYY-MM>"), aynı veri ileride push bildirimlerinde de kullanılır. */
export interface Reminder {
  id: string; kind: "ekstre" | "dokum" | "odeme"; title: string; text: string; cta: string; route: Route;
  action?: "upload" | "pay"; due: string; severity: "info" | "warn" | "urgent"; tag?: string; bank?: string; last4?: string;
  statement_id?: number;
}

const post = (id: string, what: "snooze" | "dismiss", body?: object) =>
  fetch(`/api/reminders/${encodeURIComponent(id)}/${what}`, {
    method: "POST", headers: { "Content-Type": "application/json" }, body: body ? JSON.stringify(body) : undefined,
  }).then((r) => { if (!r.ok) throw new Error("Kaydedilemedi"); return r.json(); });

// banka rozeti: logo uydurmadan, adın baş harfi + addan türeyen sabit bir ton
const TONES = [
  "bg-blue-100 text-blue-800 dark:bg-blue-950 dark:text-blue-300",
  "bg-emerald-100 text-emerald-800 dark:bg-emerald-950 dark:text-emerald-300",
  "bg-amber-100 text-amber-800 dark:bg-amber-950 dark:text-amber-300",
  "bg-rose-100 text-rose-800 dark:bg-rose-950 dark:text-rose-300",
  "bg-violet-100 text-violet-800 dark:bg-violet-950 dark:text-violet-300",
  "bg-teal-100 text-teal-800 dark:bg-teal-950 dark:text-teal-300",
];
const tone = (s: string) => TONES[[...s].reduce((h, c) => (h * 31 + c.charCodeAt(0)) >>> 0, 7) % TONES.length];
const TAG = {
  info: "bg-stone-100 text-stone-600 dark:bg-stone-800 dark:text-stone-300",
  warn: "bg-amber-100 text-amber-800 dark:bg-amber-950 dark:text-amber-300",
  urgent: "bg-red-100 text-red-800 dark:bg-red-950 dark:text-red-300",
};

function Badge({ r }: { r: Reminder }) {
  const bank = r.bank ?? "";
  const Ic = r.kind === "dokum" ? Landmark : r.kind === "odeme" ? Clock : CreditCard;
  return (
    <span className={cx("relative flex h-11 w-[52px] shrink-0 flex-col justify-between rounded-lg px-1.5 py-1 shadow-[inset_0_0_0_1px_rgba(0,0,0,0.04)]", tone(bank))} aria-hidden="true">
      <span className="flex items-center justify-between">
        <span className="font-display text-sm leading-none font-bold">{bank.slice(0, 1).toLocaleUpperCase("tr") || "?"}</span>
        <Ic size={12} className="opacity-70" />
      </span>
      <span className="text-[10px] leading-none font-semibold tracking-wider tabular-nums opacity-80">{r.last4 ? `••${r.last4}` : "Hesap"}</span>
    </span>
  );
}

function Menu({ r, onDone }: { r: Reminder; onDone: (msg: string) => void }) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  const snooze = useSave(() => post(r.id, "snooze", { days: 1 }), () => onDone("Yarın yeniden hatırlatırız"));
  const dismiss = useSave(() => post(r.id, "dismiss"), () => onDone("Bu dönem için gizlendi"));
  useEffect(() => {
    if (!open) return;
    const close = (e: MouseEvent) => { if (!ref.current?.contains(e.target as Node)) setOpen(false); };
    const esc = (e: KeyboardEvent) => { if (e.key === "Escape") setOpen(false); };
    document.addEventListener("mousedown", close);
    document.addEventListener("keydown", esc);
    return () => { document.removeEventListener("mousedown", close); document.removeEventListener("keydown", esc); };
  }, [open]);
  const item = "flex w-full items-center gap-2 rounded-md px-2.5 py-2 text-left text-sm hover:bg-stone-100 dark:hover:bg-stone-800";
  return (
    <div ref={ref} className="relative">
      <button className="btn-ghost p-1.5 text-stone-400" aria-label="Diğer seçenekler" aria-haspopup="menu" aria-expanded={open}
        onClick={() => setOpen(!open)}><MoreHorizontal size={16} /></button>
      {open && (
        <div role="menu" className="absolute right-0 z-20 mt-1 w-44 rounded-lg border border-stone-200 bg-white p-1 shadow-lg dark:border-stone-700 dark:bg-stone-900">
          <button role="menuitem" className={item} onClick={() => { setOpen(false); snooze.mutate(undefined); }}>
            <Clock size={14} className="text-stone-400" />Yarın hatırlat</button>
          <button role="menuitem" className={item} onClick={() => { setOpen(false); dismiss.mutate(undefined); }}>
            <BellOff size={14} className="text-stone-400" />Bu ay gösterme</button>
        </div>
      )}
    </div>
  );
}

/** Özet'te "Yapılacaklar": kesilen ekstre, ayı kapanan hesap dökümü, yaklaşan son ödeme. Boşsa hiç görünmez. */
export default function Reminders() {
  const ui = useUI();
  const q = useQuery({
    queryKey: ["reminders"], staleTime: 60_000,
    queryFn: () => fetch("/api/reminders").then((r) => (r.ok ? r.json() : []) as Promise<Reminder[]>),
  });
  const pay = useQuery({ queryKey: ["payables"], queryFn: api.payables });
  const [all, setAll] = useState(false);
  const items = q.data ?? [];
  if (!items.length) return null;
  const shown = all ? items : items.slice(0, 3);
  const run = (r: Reminder) => {
    const p = r.action === "pay" ? pay.data?.find((x) => x.type === "statement" && x.id === r.statement_id) : undefined;
    if (p) return ui.openPay(p);
    ui.go(r.action === "upload" ? "ekstreler" : r.route);
  };
  return (
    <section className="card overflow-visible">
      <div className="flex items-baseline justify-between gap-2 px-3 pt-3 sm:px-4">
        <h2 className="font-display text-lg font-semibold tracking-tight">Yapılacaklar
          <span className="ml-1.5 align-middle text-xs font-medium text-stone-400 tabular-nums">{items.length}</span></h2>
        {items.length > 3 && (
          <button className="text-sm text-blue-600 hover:underline dark:text-blue-400" onClick={() => setAll(!all)}>
            {all ? "Daha az" : "Tümü"}</button>
        )}
      </div>
      <ul className="divide-y divide-stone-100 px-1 pb-1 dark:divide-stone-800">
        {shown.map((r) => (
          <li key={r.id} className="flex items-center gap-3 px-2 py-2.5 sm:px-3">
            <Badge r={r} />
            <div className="min-w-0 flex-1">
              <div className="flex items-center gap-2">
                <span className="truncate text-sm font-semibold">{r.title}</span>
                {r.tag && <span className={cx("shrink-0 rounded px-1.5 py-0.5 text-[11px] font-medium", TAG[r.severity])}>{r.tag}</span>}
              </div>
              <p className="truncate text-xs text-stone-500 dark:text-stone-400" title={r.text}>{r.text}</p>
            </div>
            <button className="btn-primary shrink-0 px-3" onClick={() => run(r)}>
              {r.action === "upload" ? <><FileText size={14} />Yükle</> : "Bak"}</button>
            <Menu r={r} onDone={ui.toast} />
          </li>
        ))}
      </ul>
    </section>
  );
}
