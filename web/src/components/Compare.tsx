import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { X } from "lucide-react";
import { Bar, BarChart, CartesianGrid, Cell, Legend, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { cx, dateTR, maskText, tl, tlCompact } from "../format";
import Segmented from "./Segmented";

interface Cmp {
  month: string; start: string; end: string; length: number; day: number; variable_only: boolean;
  current: number[]; prev: number[]; avg3: number[];
  at_day: { current: number; prev: number; avg3: number; vs_prev: number | null; vs_avg3: number | null };
  weekdays: { day: string; current: number; avg3: number; pct: number | null }[];
  weeks: { week: number; current: number; avg3: number; done: boolean; pct: number | null }[];
  notes: string[];
  daily: { current: Day[]; prev: Day[]; avg_per_day: number };
  top_days: (Day & { period: "bu" | "gecen" })[];
}
interface Day { date: string; amount: number; spike: boolean }
interface DayDetail {
  date: string; total: number; by_category: { name: string; amount: number }[];
  card_payments: { title: string; amount: number; period_end: string }[];
  rows: { uid: string; description: string; category: string; amount: number; card: string | null; source: string; fixed: boolean }[];
}

const TR_MON = ["Oca", "Şub", "Mar", "Nis", "May", "Haz", "Tem", "Ağu", "Eyl", "Eki", "Kas", "Ara"];
const short = (d: string) => `${Number(d.slice(8, 10))} ${TR_MON[Number(d.slice(5, 7)) - 1]}`;

/** Bir günün harcamaları: kategori özeti + kalem kalem. */
function DayPanel({ date, all, onClose }: { date: string; all: boolean; onClose: () => void }) {
  const q = useQuery({ queryKey: ["day", date, all], queryFn: () => fetch(`/api/day/${date}?all=${all}`).then((r) => r.json() as Promise<DayDetail>) });
  const d = q.data;
  return (
    <div className="rounded-lg border border-stone-200 p-3 dark:border-stone-700">
      <div className="flex items-center justify-between gap-2">
        <b>{dateTR(date)}{d && ` · ${tl(d.total)}`}</b>
        <button className="btn-ghost p-1" aria-label="Kapat" onClick={onClose}><X size={16} /></button>
      </div>
      {d && d.by_category.length > 1 && (
        <div className="mt-1 flex flex-wrap gap-1 text-xs">
          {d.by_category.map((c) => <span key={c.name} className="rounded-full bg-stone-100 px-2 py-0.5 dark:bg-stone-800">{c.name} {tl(c.amount)}</span>)}
        </div>
      )}
      <ul className="mt-2 divide-y divide-stone-100 text-sm dark:divide-stone-800">
        {d?.rows.map((r) => (
          <li key={r.uid} className="flex items-start justify-between gap-2 py-1.5">
            <span className="min-w-0">
              <span className="block truncate">{maskText(r.description)}</span>
              <span className="block text-xs text-stone-500">{r.category}{r.card ? ` · ${r.card}` : r.source === "manual" ? " · elle" : ""}{r.fixed ? " · sabit" : ""}</span>
            </span>
            <span className={cx("shrink-0 tabular-nums", r.amount < 0 && "text-green-700")}>{tl(r.amount)}</span>
          </li>
        ))}
        {d && !d.rows.length && <li className="py-2 text-sm text-stone-500">Bu gün harcama yok.</li>}
      </ul>
      {!!d?.card_payments.length && (
        <div className="mt-2 rounded-md bg-stone-100 p-2 text-xs text-stone-600 dark:bg-stone-800 dark:text-stone-300">
          Bu gün ödenen kart ekstreleri (harcama sayılmaz; içindeki harcamalar yapıldıkları günlerde sayılır):
          {d.card_payments.map((p) => <div key={p.title} className="flex justify-between"><span>{p.title} · kesim {dateTR(p.period_end)}</span><span className="tabular-nums">{tl(p.amount)}</span></div>)}
        </div>
      )}
    </div>
  );
}

const tip = { background: "var(--chart-surface)", border: "1px solid var(--chart-grid)", borderRadius: 8 };
const axis = { fill: "var(--chart-text)", fontSize: 11 };
const Pct = ({ v }: { v: number | null }) => v == null ? <span className="text-stone-500">—</span>
  : <b className={v < 0 ? "text-green-700 dark:text-green-400" : v > 0 ? "text-red-600" : ""}>%{Math.abs(Math.round(v))} {v < 0 ? "az" : v > 0 ? "fazla" : ""}</b>;

/** Bu dönem aynı güne kadar: geçen dönem ve son 3 dönem ortalamasıyla kıyas; hafta günleri ve haftalar. */
export default function Compare({ month }: { month: string }) {
  const [all, setAll] = useState(false);
  const [tab, setTab] = useState<"gunluk" | "gun" | "haftagunu" | "hafta">("gunluk");
  const [which, setWhich] = useState<"bu" | "gecen">("bu");
  const [day, setDay] = useState<string | null>(null);
  const q = useQuery({ queryKey: ["compare", month, all], queryFn: () => fetch(`/api/compare?month=${month}${all ? "&all=1" : ""}`).then((r) => r.json() as Promise<Cmp>) });
  const c = q.data;
  if (!c || c.day === 0) return null;
  const data = Array.from({ length: c.length }, (_, i) => ({
    gun: i + 1, "Bu dönem": i < c.current.length ? c.current[i] : null, "Geçen dönem": c.prev[i] ?? null, "Son 3 ort.": c.avg3[i] ?? null,
  }));
  const SHORT: Record<string, string> = { Pazartesi: "Pzt", Salı: "Sal", Çarşamba: "Çar", Perşembe: "Per", Cuma: "Cum", Cumartesi: "Cmt", Pazar: "Paz" };
  const wdShort = (d: string) => SHORT[d] ?? d.slice(0, 3);
  return (
    <section className="card space-y-3 p-4">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div>
          <h2 className="font-semibold">Geçen dönemlere göre</h2>
          <p className="text-sm text-stone-600 dark:text-stone-300">
            Dönemin {c.day}. gününde {tl(c.at_day.current)} harcadın: geçen döneme göre <Pct v={c.at_day.vs_prev} />, son 3 dönem
            ortalamasına göre <Pct v={c.at_day.vs_avg3} />.
          </p>
          {c.notes.map((n) => <p key={n} className="text-sm text-stone-600 dark:text-stone-300">• {n}.</p>)}
        </div>
        <div className="w-48 shrink-0">
          <Segmented value={all ? "all" : "var"} onChange={(v) => setAll(v === "all")}
            options={[{ value: "var", label: "Değişken" }, { value: "all", label: "Tümü" }]} />
        </div>
      </div>
      {c.top_days.length > 0 && (
        <div className="flex flex-wrap items-center gap-1.5 text-xs">
          <span className="text-stone-500">En yoğun günler:</span>
          {c.top_days.map((t) => (
            <button key={t.date} onClick={() => setDay(t.date)}
              className={cx("rounded-full px-2 py-0.5 tabular-nums", t.spike ? "bg-amber-100 text-amber-900 dark:bg-amber-950 dark:text-amber-200" : "bg-stone-100 dark:bg-stone-800",
                day === t.date && "ring-2 ring-blue-500")}>
              {short(t.date)} · {tl(t.amount)}
            </button>
          ))}
        </div>
      )}
      <Segmented value={tab} onChange={setTab} options={[{ value: "gunluk", label: "Günlük" }, { value: "gun", label: "Birikimli" },
        { value: "haftagunu", label: "Hf. günü" }, { value: "hafta", label: "Haftalar" }]} />
      {tab === "gunluk" && (
        <div className="w-56">
          <Segmented value={which} onChange={setWhich} options={[{ value: "bu", label: "Bu dönem" }, { value: "gecen", label: "Geçen dönem" }]} />
        </div>
      )}
      <div className="h-56">
        <ResponsiveContainer width="100%" height="100%">
          {tab === "gunluk" ? (
            <BarChart data={(which === "bu" ? c.daily.current : c.daily.prev).map((x) => ({ ...x, ad: short(x.date) }))}
              onClick={(e) => { const i = (e as { activeTooltipIndex?: number } | null)?.activeTooltipIndex;
                const list = which === "bu" ? c.daily.current : c.daily.prev;
                if (i != null && list[Number(i)]) setDay(list[Number(i)].date); }}>
              <CartesianGrid vertical={false} stroke="var(--chart-grid)" />
              <XAxis dataKey="ad" tick={axis} axisLine={false} tickLine={false} interval="preserveStartEnd" />
              <YAxis tickFormatter={(v: number) => tlCompact(v)} tick={axis} axisLine={false} tickLine={false} width={56} />
              <Tooltip formatter={(v) => [tl(Number(v)), "Harcama"]} labelFormatter={(_l, p) => dateTR(String(p?.[0]?.payload?.date ?? ""))}
                cursor={{ fill: "var(--chart-grid)", opacity: 0.5 }} contentStyle={tip} />
              <Bar dataKey="amount" radius={[3, 3, 0, 0]} maxBarSize={18} cursor="pointer">
                {(which === "bu" ? c.daily.current : c.daily.prev).map((x) => (
                  <Cell key={x.date} fill={x.date === day ? "var(--chart-ink)" : x.spike ? "var(--series-4)" : "var(--series-1)"} />
                ))}
              </Bar>
            </BarChart>
          ) : tab === "gun" ? (
            <LineChart data={data}>
              <CartesianGrid vertical={false} stroke="var(--chart-grid)" />
              <XAxis dataKey="gun" tick={axis} axisLine={false} tickLine={false} interval="preserveStartEnd" />
              <YAxis tickFormatter={(v: number) => tlCompact(v)} tick={axis} axisLine={false} tickLine={false} width={56} />
              <Tooltip formatter={(v) => tl(Number(v))} labelFormatter={(g) => `Dönemin ${g}. günü`} contentStyle={tip} />
              <Legend iconType="plainline" wrapperStyle={{ fontSize: 12 }} formatter={(v) => <span style={{ color: "var(--chart-text)" }}>{v}</span>} />
              <Line dataKey="Son 3 ort." stroke="var(--chart-text)" strokeWidth={2} strokeDasharray="2 3" dot={false} />
              <Line dataKey="Geçen dönem" stroke="var(--series-2)" strokeWidth={2} strokeDasharray="5 4" dot={false} />
              <Line dataKey="Bu dönem" stroke="var(--series-1)" strokeWidth={2.5} dot={false} connectNulls={false} />
            </LineChart>
          ) : (
            <BarChart data={tab === "haftagunu"
              ? c.weekdays.map((w) => ({ ad: wdShort(w.day), tam: w.day, "Bu dönem": w.current, "Son 3 ort.": w.avg3 }))
              : c.weeks.map((w) => ({ ad: `${w.week}. hf`, tam: `${w.week}. hafta${w.done ? "" : " (sürüyor)"}`, "Bu dönem": w.current, "Son 3 ort.": w.avg3 }))}
              barGap={2}>
              <CartesianGrid vertical={false} stroke="var(--chart-grid)" />
              <XAxis dataKey="ad" tick={axis} axisLine={false} tickLine={false} />
              <YAxis tickFormatter={(v: number) => tlCompact(v)} tick={axis} axisLine={false} tickLine={false} width={56} />
              <Tooltip formatter={(v) => tl(Number(v))} labelFormatter={(_l, p) => String(p?.[0]?.payload?.tam ?? "")}
                cursor={{ fill: "var(--chart-grid)", opacity: 0.5 }} contentStyle={tip} />
              <Legend iconType="circle" wrapperStyle={{ fontSize: 12 }} formatter={(v) => <span style={{ color: "var(--chart-text)" }}>{v}</span>} />
              <Bar dataKey="Son 3 ort." fill="var(--chart-text)" fillOpacity={0.35} radius={[4, 4, 0, 0]} maxBarSize={22} />
              <Bar dataKey="Bu dönem" fill="var(--series-1)" radius={[4, 4, 0, 0]} maxBarSize={22} />
            </BarChart>
          )}
        </ResponsiveContainer>
      </div>
      {day && <DayPanel date={day} all={all} onClose={() => setDay(null)} />}
      <p className={cx("text-xs text-stone-500")}>
        {tab === "gunluk" ? `Güne dokun, o günün harcamaları açılsın. Sarı: son 3 dönemin günlük ortalamasının (${tl(c.daily.avg_per_day)}) 2,5 katından fazla.` :
          tab === "haftagunu" ? "O gün başına ortalama harcama (dönemin aynı gün aralığıyla kıyaslanır)." :
          tab === "hafta" ? "Dönemin 1–7. günü 1. hafta, 8–14. günü 2. hafta…" :
          "Birikimli harcama; kart harcaması ekstre dönemine göre değil harcandığı güne göre."}
        {!all && " Kira, fatura, taksit ve sabit kalemler hariç."}
      </p>
    </section>
  );
}
