import { useQuery } from "@tanstack/react-query";
import { CalendarRange } from "lucide-react";
import { useState } from "react";
import type { Group } from "../api";
import { maskText, monthLabel, tl } from "../format";
import BarList from "./BarList";
import Kpi from "./Kpi";

interface MonthTotal { month: string; income: number; expense: number; net: number }
interface Year {
  year: number; years: number[]; months: number; income: number; expense: number; net: number; fees: number;
  avg_expense: number; savings_rate: number | null; highest: MonthTotal | null; lowest: MonthTotal | null;
  categories: (Group & { share: number })[]; merchants: Group[]; expense_change: number | null; income_change: number | null;
  zakat: number; sadaka: number; subscriptions_yearly: number;
  estimated_expense?: number; // tahmini (ekstre bekleniyor): giderde ve kategorilerde YOK
}

const chg = (p: number | null, label: string) => (p == null ? null : `${label} ${p > 0 ? "+" : ""}${p}%`);

/** İstatistik → yıllık özet: "bu yıl nereye gitti". */
export default function YearSummary() {
  const [year, setYear] = useState(() => new Date().getFullYear());
  const q = useQuery({ queryKey: ["year-summary", year], queryFn: () => fetch(`/api/year-summary?year=${year}`).then((r) => r.json() as Promise<Year>) });
  const y = q.data;
  if (!y || (!y.months && y.years.length === 0)) return null;
  return (
    <section className="card space-y-3 p-3 sm:p-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h2 className="flex items-center gap-2 font-semibold"><CalendarRange size={18} /> {y.year} yılı</h2>
        <div className="flex gap-1">
          {y.years.slice(0, 4).map((v) => (
            <button key={v} className={v === y.year ? "btn-primary px-2.5 py-1 text-xs" : "btn-ghost border border-stone-300 px-2.5 py-1 text-xs dark:border-stone-700"}
              onClick={() => setYear(v)}>{v}</button>
          ))}
        </div>
      </div>
      {!y.months ? <p className="text-sm text-stone-500">Bu yıl için kayıt yok.</p> : <>
        <div className="grid grid-cols-2 gap-2 lg:grid-cols-4">
          <Kpi label="Gelir" value={y.income} tone="green" sub={chg(y.income_change, "geçen yıla göre") ?? `${y.months} ay`} />
          <Kpi label="Gider" value={y.expense} sub={chg(y.expense_change, "geçen yıla göre") ?? undefined} />
          <Kpi label="Kalan" value={y.net} tone={y.net < 0 ? "red" : "green"} sub={y.savings_rate != null ? `gelirin %${y.savings_rate}'i` : undefined} />
          <Kpi label="Aylık ortalama gider" value={y.avg_expense} sub="tamamlanan dönemler" />
        </div>
        <ul className="space-y-1 text-sm">
          {y.highest && y.lowest && y.highest.month !== y.lowest.month && (
            <li>En çok harcanan ay <b>{monthLabel(y.highest.month)}</b> ({tl(y.highest.expense)}), en az <b>{monthLabel(y.lowest.month)}</b> ({tl(y.lowest.expense)})</li>
          )}
          <li>{y.fees ? <span className="text-red-600">Faiz ve ücretlere <b>{tl(y.fees)}</b> gitti</span> : <span className="text-green-700 dark:text-green-400">Bu yıl faiz ödemedin ✓</span>}</li>
          {(y.zakat > 0 || y.sadaka > 0) && <li>Zekât {tl(y.zakat)} · sadaka {tl(y.sadaka)}</li>}
          {y.subscriptions_yearly > 0 && <li>Aktif abonelikler yılda ~{tl(y.subscriptions_yearly)}</li>}
        </ul>
        <div className="grid gap-4 md:grid-cols-2">
          <div>
            <h3 className="mb-1 text-sm font-medium">Nereye gitti</h3>
            <BarList items={y.categories.map((c) => ({ ...c, name: `${c.name} · %${c.share}` }))} />
            {!!y.estimated_expense && (
              <div className="mt-2 flex items-center justify-between gap-2 rounded-lg border border-dashed border-stone-300 px-2.5 py-1.5 text-xs text-stone-600 dark:border-stone-700 dark:text-stone-400"
                data-testid="year-estimate">
                <span>Tahmini (ekstre bekleniyor) <span className="text-stone-500">· gidere ve paylara katılmadı</span></span>
                <span className="shrink-0 tabular-nums">~{tl(y.estimated_expense)}</span>
              </div>
            )}
          </div>
          <div>
            <h3 className="mb-1 text-sm font-medium">En çok harcanan yerler</h3>
            <BarList items={y.merchants.map((m) => ({ ...m, name: maskText(m.name) }))} />
          </div>
        </div>
      </>}
    </section>
  );
}
