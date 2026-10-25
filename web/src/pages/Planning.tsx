import { useQuery } from "@tanstack/react-query";
import { CalendarClock, Calculator, ChevronDown, PiggyBank, Plus, Repeat } from "lucide-react";
import Simulator from "../components/Simulator";
import RecurringSuggestions from "../components/RecurringSuggestions";
import { useState } from "react";
import { Bar, CartesianGrid, ComposedChart, Legend, Line, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { api, type PlanLineType, type PlanMonth } from "../api";
import Segmented from "../components/Segmented";
import { addMonths, cx, dateTR, maskText, monthLabel, PLAN_LABEL, tl, tlCompact } from "../format";
import { useUI } from "../ui";

// Yığılmış gider serileri: doğrulanmış kategorik sıra (komşu çiftler CVD-güvenli)
const SERIES: { key: PlanLineType; color: string }[] = [
  { key: "kart_gercek", color: "var(--series-1)" },
  { key: "taksit", color: "var(--series-2)" },
  { key: "abonelik", color: "var(--series-3)" },
  { key: "degisken", color: "var(--series-4)" },
  { key: "sabit", color: "var(--series-5)" },
  { key: "tek_seferlik", color: "var(--series-6)" },
  { key: "fatura", color: "var(--series-7)" },
];

function MonthCard({ m, open, onToggle }: { m: PlanMonth; open: boolean; onToggle: () => void }) {
  const ui = useUI();
  return (
    <li className="card overflow-hidden">
      <button className="flex w-full items-center gap-3 px-3 py-3 text-left sm:px-4" onClick={onToggle} aria-expanded={open}>
        <div className="flex-1">
          <div className="font-semibold">{monthLabel(m.month)}</div>
          <div className="text-xs text-stone-500">
            Gelir {tlCompact(m.income)} · Kesin gider {tlCompact(m.expense)}
            {m.estimated > 0 && <> · <span className="italic">tahmini {tlCompact(m.estimated)}</span></>}
          </div>
        </div>
        <div className="text-right">
          <div className={cx("font-semibold tabular-nums", m.net < 0 ? "text-red-600 dark:text-red-400" : "text-green-700 dark:text-green-400")}>
            {m.net >= 0 ? "+" : ""}{tl(m.net)}
          </div>
          <div className="text-xs text-stone-500">
            {m.estimated > 0 ? <>tahminle {m.net_with_estimate >= 0 ? "+" : ""}{tlCompact(m.net_with_estimate)}</> : <>kümülatif {tlCompact(m.cumulative)}</>}
          </div>
        </div>
        <ChevronDown size={18} className={cx("text-stone-400 transition-transform", open && "rotate-180")} />
      </button>
      {open && (
        <ul className="divide-y divide-stone-100 border-t border-stone-100 text-sm dark:divide-stone-800 dark:border-stone-800">
          {m.lines.map((l, i) => (
            <li key={i}>
              <button className="flex w-full items-center gap-2 px-3 py-2 text-left hover:bg-stone-50 sm:px-4 dark:hover:bg-stone-800/50"
                onClick={() => {
                  if (!l.recurring_id) return;
                  if (l.confirmed) return ui.openRecurring(l.recurring_id);
                  ui.openRecurringMonth({ rid: l.recurring_id, month: m.month, description: l.description, amount: l.amount,
                    template: l.template_amount ?? l.amount, kind: l.kind, mode: "plan" });
                }}
                disabled={!l.recurring_id}>
                <span className="w-24 shrink-0 text-xs text-stone-500 sm:w-32">{PLAN_LABEL[l.type]}</span>
                <span className="min-w-0 flex-1 truncate">
                  {maskText(l.description)}
                  {l.type === "sabit" && !l.confirmed && <span className="ml-1 text-xs text-stone-400">{l.custom ? "(bu aya özel)" : "(plan · dokun, tutarı değiştir)"}</span>}
                  {l.type === "sabit" && l.confirmed && <span className="ml-1 text-xs text-green-600">✓</span>}
                </span>
                <span className={cx("shrink-0 whitespace-nowrap tabular-nums", l.kind !== "gider" && "text-green-700 dark:text-green-400")}>
                  {l.kind === "gider" ? "−" : l.kind === "gelir" ? "+" : "↓"}{tl(l.amount)}
                </span>
              </button>
            </li>
          ))}
          {!m.lines.length && <li className="px-4 py-3 text-stone-500">Bu ay için kalem yok</li>}
        </ul>
      )}
    </li>
  );
}

export default function Planning() {
  const ui = useUI();
  const [horizon, setHorizon] = useState<"3" | "6" | "12">("6");
  const plan = useQuery({ queryKey: ["plan", horizon], queryFn: () => api.plan(Number(horizon)) });
  const [open, setOpen] = useState<string | null>(null);
  const [simOpen, setSimOpen] = useState(false);
  const p = plan.data;

  const chart = p?.months.map((m) => ({ month: m.month, Gelir: m.income, ...m.groups })) ?? [];
  const usedSeries = SERIES.filter((s) => p?.months.some((m) => m.groups[s.key]))
    .sort((a, b) => Number(a.key === "degisken") - Number(b.key === "degisken"));
  const nextMonth = addMonths(ui.month, 1);

  return (
    <div className="space-y-4">
      <header className="flex flex-wrap items-center justify-between gap-2">
        <h1 className="text-xl font-semibold">Planlama</h1>
        <div className="w-48"><Segmented value={horizon} onChange={setHorizon}
          options={[{ value: "3", label: "3 ay" }, { value: "6", label: "6 ay" }, { value: "12", label: "12 ay" }]} /></div>
      </header>

      {p && p.dues.length > 0 && (
        <section className="card p-3 sm:p-4">
          <h2 className="mb-2 flex items-center gap-2 font-semibold"><CalendarClock size={18} /> Son ödeme takvimi</h2>
          <ul className="divide-y divide-stone-100 text-sm dark:divide-stone-800">
            {p.dues.map((d, i) => (
              <li key={i} className="flex items-center justify-between gap-2 py-2">
                <span>
                  <b>{dateTR(d.date)}</b> <span className="text-stone-500">· {d.bank} {d.account}</span>
                  {d.estimated && <span className="ml-1 rounded bg-stone-100 px-1.5 py-0.5 text-xs dark:bg-stone-800">tahmini</span>}
                </span>
                <span className="text-right tabular-nums">
                  {d.amount != null ? tl(d.amount) : "—"}
                  {d.min_payment != null && <span className="block text-xs text-stone-500">asgari {tl(d.min_payment)}</span>}
                </span>
              </li>
            ))}
          </ul>
        </section>
      )}

      {p && (
        <section className="card p-3 sm:p-4">
          <h2 className="mb-1 font-semibold">Gelir ve planlanan giderler</h2>
          <p className="mb-2 text-xs text-stone-500">
            Dolu çubuklar <b>kesin</b> giderler (taksit, abonelik, sabit, fatura, gelen ekstre). Soluk kesikli kısım
            <b> tahmini kart harcaması</b>: son dönemlerin medyanı, nete katılmaz — "harcamalar böyle giderse" göstergesi.
          </p>
          <div className="h-72">
            <ResponsiveContainer width="100%" height="100%">
              <ComposedChart data={chart} onClick={(e) => {
                const m = (e as { activeLabel?: string } | null)?.activeLabel;
                if (m) setOpen(String(m));
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
                {usedSeries.map((s, i) => {
                  const est = s.key === "degisken";
                  return (
                    <Bar key={s.key} dataKey={s.key} name={est ? `${PLAN_LABEL[s.key]} (tahmini)` : PLAN_LABEL[s.key]}
                      stackId="gider" fill={s.color} fillOpacity={est ? 0.28 : 1}
                      stroke={est ? s.color : "var(--chart-surface)"} strokeWidth={est ? 1.5 : 2} strokeDasharray={est ? "4 3" : undefined}
                      maxBarSize={44} radius={i === usedSeries.length - 1 ? [4, 4, 0, 0] : undefined} />
                  );
                })}
                <Line dataKey="Gelir" name="Gelir" stroke="var(--chart-ink)" strokeWidth={2}
                  dot={{ r: 4, fill: "var(--chart-ink)", stroke: "var(--chart-surface)", strokeWidth: 2 }} />
              </ComposedChart>
            </ResponsiveContainer>
          </div>
        </section>
      )}

      {simOpen && <Simulator onClose={() => setSimOpen(false)} />}
      <div className="flex flex-wrap gap-2">
        <button className="btn-primary" onClick={() => setSimOpen(true)}>
          <Calculator size={16} /> Alırsam ne olur?
        </button>
        <button className="btn-ghost border border-stone-300 dark:border-stone-700" onClick={() => ui.openEntry(undefined, { kind: "gider", recurring: true })}>
          <Repeat size={16} /> Sabit kalem ekle
        </button>
        <button className="btn-ghost border border-stone-300 dark:border-stone-700"
          onClick={() => ui.openEntry(undefined, { kind: "gider", entry_date: `${nextMonth}-01` })}>
          <Plus size={16} /> İleri tarihli gider
        </button>
        <button className="btn-ghost border border-stone-300 dark:border-stone-700" onClick={() => ui.go("tasarruf")}>
          <PiggyBank size={16} /> Kısma hedefi / abonelik iptali
        </button>
      </div>

      {plan.isLoading && <p className="text-sm text-stone-500">Yükleniyor…</p>}
      <ul className="space-y-2">
        {p?.months.map((m) => (
          <MonthCard key={m.month} m={m} open={open === m.month} onToggle={() => setOpen(open === m.month ? null : m.month)} />
        ))}
      </ul>

      <RecurringSuggestions />

      {p && (
        <section className="card p-3 sm:p-4">
          <h2 className="mb-2 font-semibold">Sabit kalemler</h2>
          {!p.recurring.length && (
            <p className="text-sm text-stone-500">Henüz yok. Maaşını ve kiranı ekle; her ay planlamaya otomatik girer.</p>
          )}
          <ul className="divide-y divide-stone-100 text-sm dark:divide-stone-800">
            {p.recurring.map((r) => (
              <li key={r.id}>
                <button className="flex w-full items-center justify-between gap-2 py-2 text-left" onClick={() => ui.openRecurring(r.id)}>
                  <span>
                    <span className="font-medium">{r.description}</span>
                    <span className="block text-xs text-stone-500">
                      Her ay {r.day_of_month}. gün · {r.category ?? "—"} · {monthLabel(r.start_month, true)}
                      {r.end_month ? ` → ${monthLabel(r.end_month, true)}` : " → süresiz"}
                    </span>
                  </span>
                  <span className={cx("shrink-0 whitespace-nowrap tabular-nums", r.kind === "gelir" && "text-green-700 dark:text-green-400")}>
                    {r.kind === "gelir" ? "+" : "−"}{tl(r.amount_now ?? r.amount)}
                  </span>
                </button>
              </li>
            ))}
          </ul>
        </section>
      )}
    </div>
  );
}
