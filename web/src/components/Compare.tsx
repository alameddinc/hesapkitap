import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { Bar, BarChart, CartesianGrid, Legend, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { cx, tl, tlCompact } from "../format";
import Segmented from "./Segmented";

interface Cmp {
  month: string; start: string; end: string; length: number; day: number; variable_only: boolean;
  current: number[]; prev: number[]; avg3: number[];
  at_day: { current: number; prev: number; avg3: number; vs_prev: number | null; vs_avg3: number | null };
  weekdays: { day: string; current: number; avg3: number; pct: number | null }[];
  weeks: { week: number; current: number; avg3: number; done: boolean; pct: number | null }[];
  notes: string[];
}

const tip = { background: "var(--chart-surface)", border: "1px solid var(--chart-grid)", borderRadius: 8 };
const axis = { fill: "var(--chart-text)", fontSize: 11 };
const Pct = ({ v }: { v: number | null }) => v == null ? <span className="text-stone-500">—</span>
  : <b className={v < 0 ? "text-green-700 dark:text-green-400" : v > 0 ? "text-red-600" : ""}>%{Math.abs(Math.round(v))} {v < 0 ? "az" : v > 0 ? "fazla" : ""}</b>;

/** Bu dönem aynı güne kadar: geçen dönem ve son 3 dönem ortalamasıyla kıyas; hafta günleri ve haftalar. */
export default function Compare({ month }: { month: string }) {
  const [all, setAll] = useState(false);
  const [tab, setTab] = useState<"gun" | "haftagunu" | "hafta">("gun");
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
      <Segmented value={tab} onChange={setTab} options={[{ value: "gun", label: "Gün gün" }, { value: "haftagunu", label: "Hafta günü" }, { value: "hafta", label: "Haftalar" }]} />
      <div className="h-56">
        <ResponsiveContainer width="100%" height="100%">
          {tab === "gun" ? (
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
      <p className={cx("text-xs text-stone-500")}>
        {tab === "haftagunu" ? "O gün başına ortalama harcama (dönemin aynı gün aralığıyla kıyaslanır)." :
          tab === "hafta" ? "Dönemin 1–7. günü 1. hafta, 8–14. günü 2. hafta…" :
          "Birikimli harcama; kart harcaması ekstre dönemine göre değil harcandığı güne göre."}
        {!all && " Kira, fatura, taksit ve sabit kalemler hariç."}
      </p>
    </section>
  );
}
