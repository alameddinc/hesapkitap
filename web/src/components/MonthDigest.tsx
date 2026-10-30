import { useQuery } from "@tanstack/react-query";
import { CalendarCheck, ChevronDown, X } from "lucide-react";
import { useState } from "react";
import { cx, dateTR, maskText, tl } from "../format";
import { useUI } from "../ui";
import { useSave } from "./hooks";

interface Digest {
  month: string; label: string; income: number; expense: number; net: number; expense_change: number | null;
  savings_rate: number | null; fees: number; top: { name: string; amount: number; change: number | null }[]; rising: string[];
  budgets_over: { category: string; spent: number; limit: number }[];
  upcoming: { count: number; total: number; items: { title: string; amount: number; due_date: string; estimated: boolean }[] };
  zakat: { name: string; kind: string; date: string; days_left: number; amount: number }[];
  suggestions: number;
  estimated_expense?: number; estimated_count?: number; // ekstresi beklenen kart ödemeleri: giderde değil
}

const pct = (p: number | null) => (p == null ? "" : p === 0 ? " · aynı" : ` · ${p > 0 ? "+" : ""}${p}%`);

/** Yeni dönemin ilk haftası: biten dönemin özeti (Telegram'daki raporla aynı rakamlar). */
export default function MonthDigest() {
  const ui = useUI();
  const q = useQuery({ queryKey: ["digest"], queryFn: () => fetch("/api/digest").then((r) => r.json() as Promise<Digest | null>) });
  const dismiss = useSave(() => fetch("/api/digest/dismiss", { method: "POST" }));
  const [open, setOpen] = useState(false); // ayrıntılar dokununca: Özet'te yazı kalabalığı olmasın
  const d = q.data;
  if (!d) return null;
  return (
    <section className="card relative space-y-3 border-blue-200 p-4 pb-2 dark:border-blue-900">
      <button className="btn-ghost absolute right-2 top-2 p-1.5" aria-label="Kapat" onClick={() => dismiss.mutate(undefined)}><X size={16} /></button>
      <h2 className="flex items-center gap-2 font-semibold"><CalendarCheck size={18} className="text-blue-600" /> {d.label} özeti</h2>
      <div className="grid grid-cols-3 gap-2 text-sm">
        <div><div className="text-xs text-stone-500">Gelir</div><div className="font-semibold tabular-nums text-green-700 dark:text-green-400">{tl(d.income)}</div></div>
        <div><div className="text-xs text-stone-500">Gider{pct(d.expense_change)}</div><div className="font-semibold tabular-nums">{tl(d.expense)}</div></div>
        <div><div className="text-xs text-stone-500">Kalan</div>
          <div className={cx("font-semibold tabular-nums", d.net < 0 && "text-red-600")}>{tl(d.net)}{d.savings_rate != null && d.savings_rate > 0 && <span className="text-xs font-normal text-stone-500"> · %{d.savings_rate}</span>}</div></div>
      </div>
      {!!d.estimated_expense && (
        <p className="rounded-lg border border-dashed border-stone-300 px-2.5 py-1.5 text-xs text-stone-600 dark:border-stone-700 dark:text-stone-400">
          {d.estimated_count ? `${d.estimated_count} kart ödemesinin` : "Bazı kart ödemelerinin"} ekstresi yok · tahmini ~{tl(d.estimated_expense)} (gidere katılmadı){" "}
          <button className="font-medium text-blue-700 underline dark:text-blue-400" onClick={() => ui.go("ekstreler")}>Ekstreyi yükle</button>
        </p>
      )}
      <button className="flex w-full items-center justify-center gap-1 rounded-lg py-1 text-xs text-stone-500 active:bg-stone-100 dark:active:bg-stone-800"
        onClick={() => setOpen(!open)} aria-expanded={open}>
        {open ? "Daha az" : "Ayrıntılar"} <ChevronDown size={14} className={cx("transition-transform", open && "rotate-180")} />
      </button>
      {open && <>
      <ul className="space-y-1 text-sm">
        <li>{d.fees ? <span className="text-red-600">⚠️ Faiz ve ücret: <b>{tl(d.fees)}</b></span> : <span className="text-green-700 dark:text-green-400">✅ Faiz ödemedin</span>}</li>
        {d.top.slice(0, 3).map((c) => (
          <li key={c.name} className="flex justify-between gap-2"><span>{c.name}<span className="text-xs text-stone-500">{pct(c.change)}</span></span>
            <span className="tabular-nums">{tl(c.amount)}</span></li>
        ))}
        {d.rising.length > 0 && <li className="text-amber-700 dark:text-amber-400">📈 Artanlar: {d.rising.join(", ")}</li>}
        {d.budgets_over.map((b) => <li key={b.category} className="text-red-600">Bütçe aşıldı: {b.category} ({tl(b.spent)} / {tl(b.limit)})</li>)}
      </ul>
      {d.upcoming.count > 0 && (
        <div className="rounded-lg bg-stone-50 p-2 text-sm dark:bg-stone-800/50">
          <div className="font-medium">Önümüzdeki 30 gün: {d.upcoming.count} ödeme · {tl(d.upcoming.total)}</div>
          <ul className="text-xs text-stone-600 dark:text-stone-400">
            {d.upcoming.items.map((i) => <li key={i.title + i.due_date}>{maskText(i.title)} · {i.estimated ? "~" : ""}{tl(i.amount)} · {dateTR(i.due_date).slice(0, 5)}</li>)}
          </ul>
        </div>
      )}
      {d.zakat.map((z) => (
        <p key={z.name + z.kind} className="text-sm">🌙 {maskText(z.name)}: {z.kind === "zekat" ? "zekât günü" : "ek kontrol"} {dateTR(z.date)} ({z.days_left} gün)</p>
      ))}
      {d.suggestions > 0 && (
        <button className="text-sm text-blue-700 underline dark:text-blue-400" onClick={() => ui.go("plan")}>
          🔁 Planlama'da {d.suggestions} sabit kalem önerisi bekliyor
        </button>
      )}
      </>}
    </section>
  );
}
