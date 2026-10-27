import { useQuery } from "@tanstack/react-query";
import { ArrowDown, ArrowUp, Plus, Settings2, X } from "lucide-react";
import { useMemo, useState } from "react";
import { api, type Insight, type Row } from "../api";
import BarList from "../components/BarList";
import { BudgetForm, BudgetList } from "../components/Budgets";
import Insights from "../components/Insights";
import Kpi from "../components/Kpi";
import NetWorth, { ZakatDue } from "../components/NetWorth";
import Onboarding from "../components/Onboarding";
import SharedInbox from "../components/SharedInbox";
import MonthPicker from "../components/MonthPicker";
import MonthDigest from "../components/MonthDigest";
import Features from "../components/Features";
import SimpleDashboard from "../components/SimpleDashboard";
import Compare from "../components/Compare";
import YearSummary from "../components/YearSummary";
import Jars from "../components/Jars";
import Holdings from "../components/Holdings";
import { InterestHistory, TrendChart } from "./Stats";
import { routeVisible, useBook, useDemo, useMeta, useUiPrefs } from "../components/hooks";
import Payables from "../components/Payables";
import TxnList from "../components/TxnList";
import { tl, tlCompact } from "../format";
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

// Özet bileşenleri: sırası ve hangilerinin görüneceği kişiye özel (Düzenle). module: ait olduğu özellik/sayfa.
const WIDGETS: Record<string, { title: string; module?: string }> = {
  kartlar: { title: "Gelir · gider · net · faiz" },
  gelenler: { title: "Telefondan gelenler" },
  baslarken: { title: "Başlarken" },
  uyarilar: { title: "Dikkat etmen gerekenler" },
  net: { title: "Net durum ve hesaplar", module: "hesaplar" },
  zekat: { title: "Zekât borcu", module: "zekat" },
  butceler: { title: "Bütçeler" },
  kategoriler: { title: "Kategoriye göre" },
  isyerleri: { title: "En çok harcanan yerler" },
  "istatistik-link": { title: "İstatistik bağlantısı", module: "istatistik" },
  odemeler: { title: "Ödemeler" },
  trend: { title: "Aylara göre gelir ve gider", module: "istatistik" },
  karsilastirma: { title: "Geçen dönemlere göre", module: "istatistik" },
  yillik: { title: "Yıllık özet", module: "istatistik" },
  faiz: { title: "Faiz alarmı geçmişi", module: "istatistik" },
  kumbaralar: { title: "Kumbaralar", module: "hesaplar" },
  altin: { title: "Altın ve döviz", module: "hesaplar" },
  "son-islemler": { title: "Son işlemler" },
};
const DEFAULT_LAYOUT = ["kartlar", "gelenler", "baslarken", "uyarilar", "net", "zekat", "butceler", "kategoriler", "isyerleri",
  "istatistik-link", "odemeler"];

export default function Dashboard() {
  const ui = useUI();
  const book = useBook().data; // ortak defter üyesi: kapalı bölümler gösterilmez
  const meta = useMeta().data; // kapatılan özellikler
  const demo = useDemo().data;
  const { ui: prefs, save: savePrefs } = useUiPrefs();
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

  const [editing, setEditing] = useState(false);
  const layout = (prefs.layout ?? DEFAULT_LAYOUT).filter((id) => WIDGETS[id]);
  const setLayout = (next: string[]) => savePrefs.mutate({ layout: next });
  const move = (id: string, d: number) => {
    const next = [...layout];
    const i = next.indexOf(id);
    [next[i], next[i + d]] = [next[i + d], next[i]];
    setLayout(next);
  };
  // bileşen, kapatılan özelliğe aitse ya da ortak defter üyesine kapalıysa gösterilmez
  const shown = (id: string) => { const m = WIDGETS[id]?.module; return !m || routeVisible(book, m, meta?.modules); };

  const detailBlock = detail && (
    <section className="card mt-4 overflow-hidden" id="detay">
      <div className="flex items-center justify-between border-b border-stone-100 px-3 py-2 dark:border-stone-800">
        <div>
          <h2 className="font-semibold">{detailTitle}</h2>
          <p className="text-xs text-stone-500">{detail.length} işlem · {tl(detail.reduce((a, r) => a + r.amount, 0))}</p>
        </div>
        <button className="btn-ghost" onClick={() => { setSel(null); setInsightFilter(null); }}>Kapat</button>
      </div>
      <TxnList rows={[...detail].sort((a, b) => b.amount - a.amount)} />
    </section>
  );

  const render = (id: string): React.ReactNode => {
    if (!s) return null;
    switch (id) {
      case "kartlar": return (
        <div className="grid grid-cols-2 gap-2 sm:gap-3 lg:grid-cols-4">
          <Kpi label="Gelir" value={s.totals.income} tone="green"
            sub={s.pending.income ? `+${tl(s.pending.income)} bekliyor` : undefined} />
          <Kpi label="Gider" value={s.totals.expense}
            sub={`Kart ${tlCompact(s.totals.card_spend)} · Elle ${tlCompact(s.totals.manual_expense)}`} />
          <Kpi label="Net (bu dönem)" value={s.totals.net} tone={s.totals.net < 0 ? "red" : "green"}
            sub={s.totals.debt_flow ? `borç/alacak hareketleri ayrı: ${s.totals.debt_flow > 0 ? "+" : ""}${tl(s.totals.debt_flow)}` : "gelir − gider"} />
          {book?.hide_fees ? <Kpi label="İade" value={s.totals.refunds} /> : (
            <Kpi label="Faiz & ücret" value={s.totals.fees} tone={s.totals.fees > 0 ? "red" : undefined}
              sub={s.totals.refunds ? `İade ${tl(s.totals.refunds)}` : undefined} />)}
        </div>);
      case "gelenler": return <SharedInbox />;
      case "baslarken": return book?.member ? null : <Onboarding />;
      case "uyarilar": return (
        <section>
          <h2 className="mb-2 font-semibold">Dikkat etmen gerekenler</h2>
          <Insights items={s.insights} onClick={(i) => {
            if (i.link) return ui.go(i.link as Route);
            setSel(null);
            setInsightFilter(insightFilter === i ? null : i);
          }} />
        </section>);
      case "net": return <NetWorth compact month={month} />;
      case "zekat": return <ZakatDue />;
      case "butceler": return (<>
        <BudgetList items={s.budgets} onEdit={() => setEditBudgets(true)}
          onSelect={(c) => { setInsightFilter(null); setSel({ by: "category", name: c }); }} />
        {editBudgets && <BudgetForm month={month} onClose={() => setEditBudgets(false)} onSaved={ui.toast} />}
      </>);
      case "kategoriler": return (
        <Section title="Kategoriye göre">
          <BarList items={s.by_category} selected={sel?.by === "category" ? sel.name : null} onSelect={select("category")} />
        </Section>);
      case "isyerleri": return (
        <Section title="En çok harcanan yerler">
          <BarList items={s.by_merchant} selected={sel?.by === "merchant" ? sel.name : null} onSelect={select("merchant")} limit={10} />
        </Section>);
      case "istatistik-link": return (
        <button className="card flex w-full items-center justify-between p-3 text-left text-sm" onClick={() => ui.go("istatistik")}>
          <span>📊 <b>İstatistik</b> · geçen dönemlerle karşılaştırma, aylara göre gelir/gider, kartlar, faiz</span>
          <span className="text-stone-400">›</span>
        </button>);
      case "odemeler": return <Payables />;
      case "trend": return s.trend.length ? <Section title="Aylara göre gelir ve gider"><TrendChart trend={s.trend} onPick={setMonth} /></Section> : null;
      case "karsilastirma": return <Compare month={month} />;
      case "yillik": return <YearSummary />;
      case "faiz": return book?.hide_fees ? null : <InterestHistory />;
      case "kumbaralar": return <Jars />;
      case "altin": return <Holdings />;
      case "son-islemler": {
        const recent = (txns.data ?? []).filter((r) => !r.pending && !r.skipped && !r.linked && r.kind !== "odeme")
          .sort((a, b) => b.date.localeCompare(a.date)).slice(0, 8);
        return recent.length ? <Section title="Son işlemler"><TxnList rows={recent} /></Section> : null;
      }
      default: return null;
    }
  };

  if (prefs.simple) return <SimpleDashboard />;
  return (
    <div className="space-y-4">
      <header className="flex flex-wrap items-center justify-between gap-2">
        <h1 className="flex items-center gap-3 text-xl font-semibold">Özet
          <button className="btn-ghost border border-stone-300 px-2 py-0.5 text-xs font-normal dark:border-stone-700"
            title="Büyük yazı, sadece önemli rakamlar" onClick={() => savePrefs.mutate({ simple: true })}>Sade görünüm</button>
          <button className={editing ? "btn-primary px-2 py-0.5 text-xs font-normal" : "btn-ghost border border-stone-300 px-2 py-0.5 text-xs font-normal dark:border-stone-700"}
            onClick={() => setEditing(!editing)}><Settings2 size={12} /> {editing ? "Bitti" : "Düzenle"}</button>
        </h1>
        <MonthPicker value={month} onChange={(m) => { setMonth(m); setSel(null); setInsightFilter(null); }} />
      </header>

      {meta && meta.modules_chosen === false && !book?.member && !demo && <Features intro />}

      <MonthDigest />

      {summary.isLoading && <p className="text-sm text-stone-500">Yükleniyor…</p>}
      {summary.error && <p className="text-sm text-red-600">{(summary.error as Error).message}</p>}

      {s && (
        <>
          {layout.map((id) => {
            const w = WIDGETS[id];
            if (!w || !shown(id)) return null;
            const body = render(id);
            if (!body) return null;
            const i = layout.indexOf(id);
            return (
              <div key={id} className={editing ? "relative rounded-xl ring-2 ring-blue-300 ring-offset-2 dark:ring-blue-800 dark:ring-offset-stone-950" : undefined}>
                {editing && (
                  <div className="mb-1 flex items-center gap-1 text-xs">
                    <span className="mr-auto font-medium text-blue-700 dark:text-blue-400">{w.title}</span>
                    <button className="btn-ghost p-1" aria-label="Yukarı" disabled={i === 0} onClick={() => move(id, -1)}><ArrowUp size={14} /></button>
                    <button className="btn-ghost p-1" aria-label="Aşağı" disabled={i === layout.length - 1} onClick={() => move(id, 1)}><ArrowDown size={14} /></button>
                    <button className="btn-ghost p-1 text-red-600" aria-label="Kaldır" onClick={() => setLayout(layout.filter((x) => x !== id))}><X size={14} /></button>
                  </div>
                )}
                {body}
                {id === "uyarilar" && detailBlock}
              </div>
            );
          })}
          {!layout.includes("uyarilar") && detailBlock}
          {editing && (
            <section className="card space-y-2 p-3">
              <div className="text-sm font-medium">Bileşen ekle</div>
              <div className="flex flex-wrap gap-2">
                {Object.entries(WIDGETS).filter(([id]) => !layout.includes(id) && shown(id)).map(([id, w]) => (
                  <button key={id} className="btn-ghost border border-stone-300 px-2 py-1 text-xs dark:border-stone-700"
                    onClick={() => setLayout([...layout, id])}><Plus size={13} /> {w.title}</button>
                ))}
                {Object.keys(WIDGETS).filter((id) => !layout.includes(id) && shown(id)).length === 0 && <span className="text-xs text-stone-500">Hepsi ekli.</span>}
              </div>
              <div className="flex gap-2">
                <button className="btn-primary" onClick={() => setEditing(false)}>Bitti</button>
                <button className="btn-ghost" onClick={() => { savePrefs.mutate({ reset_layout: true }); setEditing(false); }}>Varsayılana dön</button>
              </div>
            </section>
          )}
        </>
      )}
    </div>
  );
}
