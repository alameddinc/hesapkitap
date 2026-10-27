import { useQuery } from "@tanstack/react-query";
import { BarChart3, Percent } from "lucide-react";
import { Bar, BarChart, CartesianGrid, Legend, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { api, type Insight } from "../api";
import BarList from "../components/BarList";
import Compare from "../components/Compare";
import MonthPicker from "../components/MonthPicker";
import YearSummary from "../components/YearSummary";
import { monthLabel, tl, tlCompact } from "../format";
import { useUI } from "../ui";

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="card p-3 sm:p-4">
      <h2 className="mb-2 font-semibold">{title}</h2>
      {children}
    </section>
  );
}

/** Faiz alarmı geçmişi: hangi dönemde ne kadar faiz / vade farkı, nedeniyle. */
export function InterestHistory() {
  const q = useQuery({ queryKey: ["interest-history"], queryFn: () => fetch("/api/interest-history").then((r) => r.json() as Promise<{
    months: { month: string; total: number; alarms: Insight[] }[]; total: number; checked: number }>) });
  const d = q.data;
  if (!d) return null;
  return (
    <section className="card p-3 sm:p-4">
      <h2 className="mb-1 flex items-center gap-2 font-semibold"><Percent size={18} /> Faiz alarmı</h2>
      {!d.months.length ? (
        <p className="text-sm text-green-700 dark:text-green-400">Son {d.checked} dönemin ekstrelerinde faiz ya da vade farkı yok. 👍</p>
      ) : (
        <>
          <p className="text-sm text-stone-500">Son {d.checked} dönemde toplam <b className="text-red-600">{tl(d.total)}</b> faiz, vade farkı ve faize bağlı vergi ödendi.</p>
          <ul className="mt-2 space-y-2">
            {d.months.map((m) => (
              <li key={m.month} className="rounded-lg border border-red-200 bg-red-50/50 p-2 text-sm dark:border-red-900 dark:bg-red-950/20">
                <div className="flex justify-between font-medium"><span>{monthLabel(m.month)}</span><span className="tabular-nums text-red-600">{tl(m.total)}</span></div>
                {m.alarms.map((a, i) => (
                  <p key={i} className="mt-1 text-xs text-stone-600 dark:text-stone-400"><b>{a.title.replace("Faiz alarmı: ", "")}</b> — {a.detail}</p>
                ))}
              </li>
            ))}
          </ul>
        </>
      )}
    </section>
  );
}

/** Aylara göre gelir ve gider (İstatistik ve, eklenirse, Özet). */
export function TrendChart({ trend, onPick }: { trend: { month: string; income: number; expense: number }[]; onPick: (m: string) => void }) {
  return (
    <>
    <div className="h-64">
      <ResponsiveContainer width="100%" height="100%">
        <BarChart data={trend} barGap={2} onClick={(e) => {
          const m = (e as { activeLabel?: string } | null)?.activeLabel;
          if (m) onPick(String(m));
        }}>
          <CartesianGrid vertical={false} stroke="var(--chart-grid)" />
          <XAxis dataKey="month" tickFormatter={(m: string) => monthLabel(m, true)} tick={{ fill: "var(--chart-text)", fontSize: 12 }}
            axisLine={false} tickLine={false} />
          <YAxis tickFormatter={(v: number) => tlCompact(v)} tick={{ fill: "var(--chart-text)", fontSize: 11 }}
            axisLine={false} tickLine={false} width={64} />
          <Tooltip formatter={(v) => tl(Number(v))} labelFormatter={(m) => monthLabel(String(m))}
            cursor={{ fill: "var(--chart-grid)", opacity: 0.5 }}
            contentStyle={{ background: "var(--chart-surface)", border: "1px solid var(--chart-grid)", borderRadius: 8 }} />
          <Legend iconType="circle" wrapperStyle={{ fontSize: 12 }}
            formatter={(v) => <span style={{ color: "var(--chart-text)" }}>{v}</span>} />
          <Bar dataKey="income" name="Gelir" fill="var(--series-1)" radius={[4, 4, 0, 0]} maxBarSize={28} />
          <Bar dataKey="expense" name="Gider" fill="var(--series-2)" radius={[4, 4, 0, 0]} maxBarSize={28} />
        </BarChart>
      </ResponsiveContainer>
    </div>
    <p className="mt-1 text-xs text-stone-500">Bir aya tıklayınca o ay seçilir. Bir ayda gelir görünmüyorsa maaşın yattığı hesabın o aylara ait dökümü eksik olabilir.</p>
    </>
  );
}

/** İstatistik: karşılaştırmalar ve uzun dönem grafikleri (Özet sade kalsın diye burada). */
export default function Stats() {
  const ui = useUI();
  const { month, setMonth } = ui;
  const summary = useQuery({ queryKey: ["summary", month], queryFn: () => api.summary(month) });
  const s = summary.data;
  return (
    <div className="space-y-4">
      <header className="flex flex-wrap items-center justify-between gap-2">
        <h1 className="flex items-center gap-2 text-xl font-semibold"><BarChart3 size={20} /> İstatistik</h1>
        <MonthPicker value={month} onChange={setMonth} />
      </header>

      <Compare month={month} />

      {s && s.trend.length > 0 && (
        <Section title="Aylara göre gelir ve gider">
          <TrendChart trend={s.trend} onPick={setMonth} />
        </Section>
      )}

      <YearSummary />

      {s && s.by_card.length > 0 && (
        <Section title="Kart bazında (bu dönem)">
          <BarList items={s.by_card} selected={null} onSelect={() => ui.go("islemler")} />
        </Section>
      )}

      <InterestHistory />
    </div>
  );
}
