import { useQuery } from "@tanstack/react-query";
import { Camera, List, Minimize2 } from "lucide-react";
import { api } from "../api";
import { addMonths, dateTR, monthLabel, tl } from "../format";
import { useUI } from "../ui";
import { EstimateNote } from "./Estimates";
import { isViewer, useBook, useUiPrefs } from "./hooks";

/** Sade görünüm: büyük yazı, tek bakışta "bu ay ne kadar harcadım" (kuruşu kuruşuna), yaklaşan ödemeler, iki düğme. */
export default function SimpleDashboard() {
  const ui = useUI();
  const { save } = useUiPrefs();
  const viewer = isViewer(useBook().data); // ortak defterde sadece görür
  const summary = useQuery({ queryKey: ["summary", ui.month], queryFn: () => api.summary(ui.month) });
  const prevMonth = addMonths(ui.month, -1);
  const prev = useQuery({ queryKey: ["summary", prevMonth], queryFn: () => api.summary(prevMonth) });
  const payables = useQuery({ queryKey: ["payables"], queryFn: api.payables });
  const t = summary.data?.totals;
  const due = (payables.data ?? []).filter((p) => p.status !== "odendi").slice(0, 4);
  return (
    <div className="space-y-5">
      <section className="card p-6 text-center">
        <div className="text-lg text-stone-500">{monthLabel(ui.month)} · bu ay harcadığın</div>
        <div className="mt-1 text-5xl font-bold tabular-nums tracking-tight">{t ? tl(t.expense) : "…"}</div>
        {prev.data && <div className="mt-1 text-base text-stone-500">Geçen ay ({monthLabel(prevMonth)}): {tl(prev.data.totals.expense)}</div>}
        {summary.data && <div className="text-left"><EstimateNote summary={summary.data} /></div>}
        {t && (
          <div className="mt-4 grid grid-cols-2 gap-3 text-left">
            <div className="rounded-xl bg-green-50 p-3 dark:bg-green-950/30">
              <div className="text-base text-stone-500">Gelen</div>
              <div className="text-2xl font-semibold tabular-nums text-green-700 dark:text-green-400">{tl(t.income)}</div>
            </div>
            <div className="rounded-xl bg-stone-50 p-3 dark:bg-stone-800/50">
              <div className="text-base text-stone-500">Kalan</div>
              <div className={`text-2xl font-semibold tabular-nums ${t.net - t.savings < 0 ? "text-red-600" : ""}`}>{tl(t.net - t.savings)}</div>
            </div>
          </div>
        )}
        {!!t?.savings && <p className="mt-3 text-lg text-amber-700 dark:text-amber-400">Birikime giden (altın/döviz): {tl(t.savings)}</p>}
        {!!t?.fees && <p className="mt-3 text-lg text-red-600">Bu ay {tl(t.fees)} faiz / ücret ödendi</p>}
      </section>

      <div className="grid grid-cols-2 gap-3">
        <button className="btn-primary flex-col justify-center gap-1 py-4 text-lg" disabled={viewer} onClick={ui.openAdd}><Camera size={26} /> Ekle</button>
        <button className="btn-ghost flex-col justify-center gap-1 whitespace-nowrap border border-stone-300 py-4 text-lg dark:border-stone-700"
          onClick={() => ui.go("islemler")}><List size={26} /> Harcamalar</button>
      </div>

      {due.length > 0 && (
        <section className="card p-4">
          <h2 className="mb-2 text-xl font-semibold">Yaklaşan ödemeler</h2>
          <ul className="divide-y divide-stone-100 dark:divide-stone-800">
            {due.map((p) => (
              <li key={`${p.type}-${p.id ?? p.title}`} className="flex items-center justify-between gap-3 py-3 text-lg">
                <span className="min-w-0">
                  <span className="block truncate font-medium">{p.title}</span>
                  <span className={`text-base ${p.status === "gecikti" ? "text-red-600" : "text-stone-500"}`}>
                    {p.status === "gecikti" ? "gecikti · " : ""}son gün {dateTR(p.due_date)}{p.status === "tahmini" ? " · tahmini" : ""}
                  </span>
                </span>
                <span className="shrink-0 font-semibold tabular-nums">{tl(p.amount - (p.paid_amount ?? 0))}</span>
              </li>
            ))}
          </ul>
        </section>
      )}

      <button className="btn-ghost mx-auto flex text-base text-stone-500" onClick={() => save.mutate({ simple: false })}>
        <Minimize2 size={18} /> Tam görünüme geç
      </button>
    </div>
  );
}
