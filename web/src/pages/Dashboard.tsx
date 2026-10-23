import { useQuery } from "@tanstack/react-query";
import { useMemo, useState } from "react";
import { Bar, BarChart, CartesianGrid, Legend, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { api, type Insight, type Row } from "../api";
import BarList from "../components/BarList";
import { BudgetForm, BudgetList } from "../components/Budgets";
import Insights from "../components/Insights";
import Kpi from "../components/Kpi";
import NetWorth, { ZakatDue } from "../components/NetWorth";
import Onboarding from "../components/Onboarding";
import Compare from "../components/Compare";
import MonthPicker from "../components/MonthPicker";
import Payables from "../components/Payables";
import TxnList from "../components/TxnList";
import { monthLabel, tl, tlCompact } from "../format";
import { useUI, type Route } from "../ui";

type Sel = { by: "category" | "merchant" | "card"; name: string } | null;

function Section({ title, children, right }: { title: string; children: React.ReactNode; right?: React.ReactNode }) {
  return (
    <section className="card p-3 sm:p-4">
      <div className="mb-2 flex items-center justify-between">
        <h2 className="font-semibold">{title}</h2>
        {right}
      </div>
      {children}
    </section>
  );
}

export default function Dashboard() {
  const ui = useUI();
  const { month, setMonth } = ui;
  const summary = useQuery({ queryKey: ["summary", month], queryFn: () => api.summary(month) });
  const txns = useQuery({ queryKey: ["txns", month], queryFn: () => api.transactions(month) });
  const [sel, setSel] = useState<Sel>(null);
  const [insightFilter, setInsightFilter] = useState<Insight | null>(null);
  const [editBudgets, setEditBudgets] = useState(false);

  const detail = useMemo(() => {
    const rows = (txns.data ?? []).filter((r) => !r.pending && !r.skipped && !r.linked && r.kind !== "odeme");
    if (sel) {
      const pick = (r: Row) => (sel.by === "category" ? r.category : sel.by === "merchant" ? r.merchant : r.card);
      return rows.filter((r) => pick(r) === sel.name && r.kind !== "gelir");
    }
    const f = insightFilter?.filter;
    if (f) {
      return (txns.data ?? []).filter((r) =>
        (f.kind ? r.kind === f.kind : true) && (f.category ? r.category === f.category : true) &&
        (f.fx ? !!r.fx : true) && (f.pending ? !!r.pending : true) && (f.awaiting ? !!r.awaiting_statement : true));
    }
    return null;
  }, [sel, insightFilter, txns.data]);

  const s = summary.data;
  const select = (by: NonNullable<Sel>["by"]) => (name: string | null) => {
    setInsightFilter(null);
    setSel(name ? { by, name } : null);
  };
  const detailTitle = sel ? sel.name : insightFilter?.title;

  return (
    <div className="space-y-4">
      <header className="flex flex-wrap items-center justify-between gap-2">
        <h1 className="text-xl font-semibold">Özet</h1>
        <MonthPicker value={month} onChange={(m) => { setMonth(m); setSel(null); setInsightFilter(null); }} />
      </header>

      {summary.isLoading && <p className="text-sm text-stone-500">Yükleniyor…</p>}
      {summary.error && <p className="text-sm text-red-600">{(summary.error as Error).message}</p>}

      {s && (
        <>
          <div className="grid grid-cols-2 gap-2 sm:gap-3 lg:grid-cols-4">
            <Kpi label="Gelir" value={s.totals.income} tone="green"
              sub={s.pending.income ? `+${tl(s.pending.income)} bekliyor` : undefined} />
            <Kpi label="Gider" value={s.totals.expense}
              sub={`Kart ${tlCompact(s.totals.card_spend)} · Elle ${tlCompact(s.totals.manual_expense)}`} />
            <Kpi label="Net (bu dönem)" value={s.totals.net} tone={s.totals.net < 0 ? "red" : "green"}
              sub={s.totals.debt_flow ? `borç/alacak hareketleri ayrı: ${s.totals.debt_flow > 0 ? "+" : ""}${tl(s.totals.debt_flow)}` : "gelir − gider"} />
            <Kpi label="Faiz & ücret" value={s.totals.fees} tone={s.totals.fees > 0 ? "red" : undefined}
              sub={s.totals.refunds ? `İade ${tl(s.totals.refunds)}` : undefined} />
          </div>

          <Onboarding />

          <NetWorth compact />
          <ZakatDue />

          <Compare month={month} />

          <Payables />

          <section>
            <h2 className="mb-2 font-semibold">Dikkat etmen gerekenler</h2>
            <Insights items={s.insights} onClick={(i) => {
              if (i.link) return ui.go(i.link as Route);
              setSel(null);
              setInsightFilter(insightFilter === i ? null : i);
            }} />
          </section>

          {detail && (
            <section className="card overflow-hidden" id="detay">
              <div className="flex items-center justify-between border-b border-stone-100 px-3 py-2 dark:border-stone-800">
                <div>
                  <h2 className="font-semibold">{detailTitle}</h2>
                  <p className="text-xs text-stone-500">{detail.length} işlem · {tl(detail.reduce((a, r) => a + r.amount, 0))}</p>
                </div>
                <button className="btn-ghost" onClick={() => { setSel(null); setInsightFilter(null); }}>Kapat</button>
              </div>
              <TxnList rows={[...detail].sort((a, b) => b.amount - a.amount)} />
            </section>
          )}

          <BudgetList items={s.budgets} onEdit={() => setEditBudgets(true)}
            onSelect={(c) => { setInsightFilter(null); setSel({ by: "category", name: c }); }} />
          {editBudgets && <BudgetForm month={month} onClose={() => setEditBudgets(false)} onSaved={ui.toast} />}

          <div className="grid gap-4 lg:grid-cols-2">
            <Section title="Kategoriye göre">
              <BarList items={s.by_category} selected={sel?.by === "category" ? sel.name : null} onSelect={select("category")} />
            </Section>
            <Section title="En çok harcanan yerler">
              <BarList items={s.by_merchant} selected={sel?.by === "merchant" ? sel.name : null} onSelect={select("merchant")} limit={10} />
            </Section>
          </div>

          {s.by_card.length > 0 && (
            <Section title="Kart bazında">
              <BarList items={s.by_card} selected={sel?.by === "card" ? sel.name : null} onSelect={select("card")} />
            </Section>
          )}

          {s.trend.length > 0 && (
            <Section title="Aylara göre gelir ve gider">
              <div className="h-64">
                <ResponsiveContainer width="100%" height="100%">
                  <BarChart data={s.trend} barGap={2} onClick={(e) => {
                    const m = (e as { activeLabel?: string } | null)?.activeLabel;
                    if (m) setMonth(String(m));
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
              <p className="mt-1 text-xs text-stone-500">Bir aya tıklayınca o ayın özetine geçer.</p>
            </Section>
          )}
        </>
      )}
    </div>
  );
}
