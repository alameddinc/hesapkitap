import { useQuery } from "@tanstack/react-query";
import { BarChart3, Percent } from "lucide-react";
import { Bar, BarChart, CartesianGrid, Legend, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { useId } from "react";
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

type TrendPoint = { month: string; income: number; expense: number; estimated_expense?: number; estimated_income?: number };

/** Tahmini katman: gerçek çubuğun üstünde 2px boşlukla, taralı ve kesikli kenarlı ayrı parça (gerçekle karışmasın). */
function hatchShape(pid: string, color: string) {
  return (p: unknown) => {
    const { x, y, width, height } = p as { x: number; y: number; width: number; height: number };
    if (!height || height < 3) return <g />;
    return <rect x={x + 0.75} y={y + 0.75} width={Math.max(0, width - 1.5)} height={height - 2.75} rx={3}
      fill={`url(#${pid})`} stroke={color} strokeWidth={1.25} strokeDasharray="3 2" />;
  };
}

/** Aylara göre gelir ve gider (İstatistik ve, eklenirse, Özet). Tahmini tutarlar (ekstre/döküm bekleniyor) ayrı katman. */
export function TrendChart({ trend, onPick }: { trend: TrendPoint[]; onPick: (m: string) => void }) {
  const pid = "hk-hatch-" + useId().replace(/[^a-zA-Z0-9]/g, "");
  // 0 yerine null: ipucunda tahmini olmayan ayda "Tahmini" satırı çıkmasın (filterNull)
  const data = trend.map((t) => ({ ...t, estimated_expense: t.estimated_expense || null, estimated_income: t.estimated_income || null }));
  const estExp = data.some((t) => !!t.estimated_expense);
  const estInc = data.some((t) => !!t.estimated_income);
  return (
    <>
    <svg width="0" height="0" className="absolute" aria-hidden focusable="false">
      <defs>
        {(["exp", "inc"] as const).map((k) => (
          <pattern key={k} id={`${pid}-${k}`} patternUnits="userSpaceOnUse" width="6" height="6" patternTransform="rotate(45)">
            <rect width="6" height="6" fill={k === "exp" ? "var(--series-2)" : "var(--series-1)"} fillOpacity="0.14" />
            <line x1="0" y1="0" x2="0" y2="6" stroke={k === "exp" ? "var(--series-2)" : "var(--series-1)"} strokeWidth="2" strokeOpacity="0.55" />
          </pattern>
        ))}
      </defs>
    </svg>
    <div className="h-64">
      <ResponsiveContainer width="100%" height="100%">
        <BarChart data={data} barGap={2} onClick={(e) => {
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
            wrapperStyle={{ maxWidth: "75%", zIndex: 5 }}
            contentStyle={{ background: "var(--chart-surface)", border: "1px solid var(--chart-grid)", borderRadius: 8, fontSize: 12,
              whiteSpace: "normal", padding: "6px 10px" }}
            itemStyle={{ padding: "1px 0", color: "var(--chart-ink)" }} labelStyle={{ color: "var(--chart-ink)", fontWeight: 600 }} />
          <Legend iconType="circle" wrapperStyle={{ fontSize: 12 }}
            formatter={(v) => <span style={{ color: "var(--chart-text)" }}>{v}</span>} />
          <Bar dataKey="income" name="Gelir" stackId="i" fill="var(--series-1)" radius={[4, 4, 0, 0]} maxBarSize={28} />
          {estInc && <Bar dataKey="estimated_income" name="Tahmini gelir (döküm bekleniyor)" stackId="i" fill={`url(#${pid}-inc)`}
            stroke="var(--series-1)" shape={hatchShape(`${pid}-inc`, "var(--series-1)")} maxBarSize={28} />}
          <Bar dataKey="expense" name="Gider" stackId="g" fill="var(--series-2)" radius={[4, 4, 0, 0]} maxBarSize={28} />
          {estExp && <Bar dataKey="estimated_expense" name="Tahmini (ekstre bekleniyor)" stackId="g" fill={`url(#${pid}-exp)`}
            stroke="var(--series-2)" shape={hatchShape(`${pid}-exp`, "var(--series-2)")} maxBarSize={28} />}
        </BarChart>
      </ResponsiveContainer>
    </div>
    {(estExp || estInc) && (
      <p className="mt-1 text-xs text-stone-600 dark:text-stone-400">
        <svg aria-hidden width="12" height="12" className="mr-1 inline align-[-1px]"><rect x="0.75" y="0.75" width="10.5" height="10.5" rx="2"
          fill={`url(#${pid}-exp)`} stroke="var(--series-2)" strokeWidth="1.25" strokeDasharray="3 2" /></svg>
        Taralı parça <b>tahmini</b>: ekstresi yüklenmemiş kart ödemeleri{estInc ? " ve dökümü olmayan maaş" : ""}. Gerçek gidere ve gelire katılmaz; belge yüklenince yerini gerçek işlemler alır.
      </p>
    )}
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
