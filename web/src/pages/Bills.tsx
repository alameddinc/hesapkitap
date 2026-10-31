import { useQuery } from "@tanstack/react-query";
import { send } from "../lib/apiError";
import { Check, ChevronDown, ChevronRight, Plus, Zap } from "lucide-react";
import { useState } from "react";
import { api } from "../api";
import { isViewer, useBook, useOpenRow } from "../components/hooks";
import Modal from "../components/Modal";
import MonthBars from "../components/MonthBars";
import { addMonths, cx, dateTR, maskText, monthLabel, tl, todayISO } from "../format";
import { useUI } from "../ui";

interface BillItem { id: number; source: "entry" | "card"; date: string; amount: number; description: string; invoice: string | null; due: string | null }
interface BillMonth { month: string; amount: number; count: number; items: BillItem[] }
interface Change { month: string; amount: number; diff: number; pct: number }
export interface BillSeries {
  name: string;
  category?: string;
  months: BillMonth[];
  total: number;
  average: number;
  paid_months: number;
  last: { month: string; amount: number } | null;
  max: { month: string; amount: number } | null;
  vs_prev_month: Change | null;
  vs_last_year: Change | null;
}
interface BillSummary { providers: BillSeries[]; categories: BillSeries[]; window: { start: string; end: string } }

const fetchSummary = (until: string) => send<BillSummary>(`/api/bills/summary?months=24&until=${until}`);

const monthYear = (ym: string) => `${monthLabel(ym)}`;
const pctTR = (p: number) => `%${Math.abs(p).toLocaleString("tr-TR", { maximumFractionDigits: 1 })}`;

/** Değişim rozeti: ok + metin (renk tek başına anlam taşımaz). Artış uyarı rengi (gider arttı), azalış yeşil. */
export function ChangeTag({ c, what = "önceki aya göre", compact }: { c: Change | null; what?: string; compact?: boolean }) {
  if (!c) return <span className="text-xs text-stone-500 dark:text-stone-400">{compact ? "önceki ay yok" : "karşılaştırılacak ay yok"}</span>;
  if (Math.abs(c.diff) < 0.01) return <span className="text-xs text-stone-500 dark:text-stone-400">= aynı{compact ? "" : ` (${what})`}</span>;
  const up = c.diff > 0;
  return (
    <span className={cx("inline-flex items-center gap-1 text-xs font-semibold tabular-nums",
      up ? "text-amber-700 dark:text-amber-400" : "text-green-700 dark:text-green-400")}>
      <span aria-hidden>{up ? "↑" : "↓"}</span>
      <span>{pctTR(c.pct)} {up ? "arttı" : "azaldı"}</span>
      {!compact && <span className="font-normal text-stone-500 dark:text-stone-400">({up ? "+" : "−"}{tl(Math.abs(c.diff))}, {what})</span>}
    </span>
  );
}

/** Faturalar: her ay gelen ama tutarı değişen giderler (elektrik, doğalgaz, su, telefon, internet) kurum kurum. */
export default function Bills() {
  const ui = useUI();
  const viewer = isViewer(useBook().data);
  const until = todayISO().slice(0, 7);
  const q = useQuery({ queryKey: ["bills-summary", until], queryFn: () => fetchSummary(until) });
  const pay = useQuery({ queryKey: ["payables"], queryFn: api.payables });
  const [sel, setSel] = useState<{ kind: "provider" | "category"; name: string } | null>(null);
  const d = q.data;
  const unpaid = (pay.data ?? []).filter((p) => p.type === "bill" && p.status !== "odendi");

  // toplam: kategorilerin aylık toplamı (her ödeme bir kez)
  const totalOf = (ym: string) => (d?.categories ?? []).reduce((a, c) => a + (c.months.find((m) => m.month === ym)?.amount ?? 0), 0);
  const lastMonth = d ? [...new Set(d.categories.flatMap((c) => c.months.filter((m) => m.count).map((m) => m.month)))].sort().pop() ?? null : null;
  const lastTotal = lastMonth ? totalOf(lastMonth) : 0;
  const prevTotal = lastMonth ? totalOf(addMonths(lastMonth, -1)) : 0;
  const yearAgo = lastMonth ? totalOf(addMonths(lastMonth, -12)) : 0;
  const change = (now: number, before: number, month: string): Change | null =>
    before > 0 ? { month, amount: before, diff: now - before, pct: Math.round(((now - before) / before) * 1000) / 10 } : null;
  const last12 = lastMonth ? Array.from({ length: 12 }, (_, i) => addMonths(lastMonth, -i)) : [];
  const paid12 = last12.filter((m) => totalOf(m) > 0);
  const avg12 = paid12.length ? paid12.reduce((a, m) => a + totalOf(m), 0) / paid12.length : 0;
  const detail = sel && d ? (sel.kind === "provider" ? d.providers : d.categories).find((x) => x.name === sel.name) : null;

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <h1 className="text-xl font-semibold">Faturalar</h1>
          <p className="text-sm text-stone-500 dark:text-stone-400">
            Her ay gelen ama tutarı değişen giderler: elektrik, doğalgaz, su, telefon, internet. Hesap dökümü, kart ekstresi
            ve elle girdiğin kayıtlardan kurum kurum toplanır.
          </p>
        </div>
        {!viewer && <button className="btn-ghost border border-stone-300 dark:border-stone-700" onClick={() => ui.openBill()}><Plus size={15} aria-hidden /> Fatura ekle</button>}
      </div>

      {q.isLoading && <p className="text-sm text-stone-500">Yükleniyor…</p>}
      {q.error && <p className="text-sm text-red-600 dark:text-red-400" role="alert">{(q.error as Error).message}</p>}

      {d && lastMonth && (
        <div className="grid gap-3 sm:grid-cols-3">
          <div className="card p-3 sm:p-4">
            <div className="text-xs text-stone-500 dark:text-stone-400">{monthYear(lastMonth)} faturaları</div>
            <div className="mt-1 text-lg font-semibold tabular-nums sm:text-xl">{tl(lastTotal)}</div>
            <div className="mt-0.5"><ChangeTag c={change(lastTotal, prevTotal, addMonths(lastMonth, -1))} /></div>
          </div>
          <div className="card p-3 sm:p-4">
            <div className="text-xs text-stone-500 dark:text-stone-400">Geçen yıl {monthYear(addMonths(lastMonth, -12))}</div>
            <div className="mt-1 text-lg font-semibold tabular-nums sm:text-xl">{yearAgo > 0 ? tl(yearAgo) : "—"}</div>
            <div className="mt-0.5"><ChangeTag c={change(lastTotal, yearAgo, addMonths(lastMonth, -12))} what="geçen yıla göre" /></div>
          </div>
          <div className="card p-3 sm:p-4">
            <div className="text-xs text-stone-500 dark:text-stone-400">Aylık ortalama</div>
            <div className="mt-1 text-lg font-semibold tabular-nums sm:text-xl">{tl(avg12)}</div>
            <div className="mt-0.5 text-xs text-stone-500 dark:text-stone-400">son 12 ayda ödeme olan {paid12.length} ay</div>
          </div>
        </div>
      )}

      {unpaid.length > 0 && (
        <section className="card overflow-hidden">
          <h2 className="border-b border-stone-100 px-4 py-2.5 font-semibold dark:border-stone-800">Ödenmemiş</h2>
          <ul className="divide-y divide-stone-100 dark:divide-stone-800">
            {unpaid.map((p) => (
              <li key={p.id} className="flex items-center gap-3 px-4 py-2.5">
                <div className="min-w-0 flex-1">
                  <div className="font-medium break-words">{p.title}</div>
                  <div className={cx("text-xs", p.status === "gecikti" ? "text-red-700 dark:text-red-400" : "text-stone-500 dark:text-stone-400")}>
                    son ödeme {dateTR(p.due_date)}{p.status === "gecikti" ? ` · ${Math.abs(p.days_left)} gün geçti` : p.days_left >= 0 ? ` · ${p.days_left} gün kaldı` : ""}
                    {p.status === "kismi" && ` · ${tl(p.paid_amount ?? 0)} ödendi`}</div>
                </div>
                <span className="shrink-0 font-semibold tabular-nums">{tl(p.amount)}</span>
                {!viewer && <button className="btn-primary shrink-0 px-2.5 py-1 text-xs" onClick={() => ui.openPay(p)}><Check size={14} aria-hidden /> Ödedim</button>}
              </li>
            ))}
          </ul>
        </section>
      )}

      {d && !d.providers.length && (
        <div className="card space-y-2 p-6 text-center">
          <Zap className="mx-auto text-stone-400" aria-hidden />
          <p className="font-medium">Henüz fatura yok</p>
          <p className="text-sm text-stone-500 dark:text-stone-400">Elektrik, doğalgaz, su, telefon ve internet ödemelerin hesap dökümünden ya da ekstreden gelince burada kurum kurum toplanır.</p>
        </div>
      )}

      {d && d.providers.length > 0 && (
        <SeriesList title="Kurumlar" items={d.providers} onPick={(name) => setSel({ kind: "provider", name })} />
      )}
      {d && d.categories.length > 0 && (
        <SeriesList title="Türe göre" items={d.categories} onPick={(name) => setSel({ kind: "category", name })} />
      )}
      <p className="text-xs text-stone-500 dark:text-stone-400">Kira, aidat ve abonelikler gibi tutarı her ay aynı olanlar Planlama'daki sabit kalemlerde.</p>

      {detail && lastMonth && <SeriesDetail s={detail} end={until} onClose={() => setSel(null)} />}
    </div>
  );
}

function SeriesList({ title, items, onPick }: { title: string; items: BillSeries[]; onPick: (name: string) => void }) {
  return (
    <section className="card overflow-hidden">
      <h2 className="border-b border-stone-100 px-4 py-2.5 font-semibold dark:border-stone-800">{title}</h2>
      <ul className="divide-y divide-stone-100 dark:divide-stone-800">
        {items.map((p) => (
          <li key={p.name}>
            <button className="flex w-full items-center gap-3 px-4 py-3 text-left hover:bg-stone-50 dark:hover:bg-stone-800/50" onClick={() => onPick(p.name)}
              aria-label={`${p.name}: ${p.last ? `${monthYear(p.last.month)} ${tl(p.last.amount)}` : "ödeme yok"}. Aylık geçmiş`}>
              <span className="flex size-9 shrink-0 items-center justify-center rounded-lg bg-stone-100 text-stone-500 dark:bg-stone-800 dark:text-stone-400"><Zap size={17} aria-hidden /></span>
              <span className="min-w-0 flex-1">
                <span className="block font-medium break-words">{maskText(p.name)}</span>
                <span className="block text-xs text-stone-500 dark:text-stone-400">
                  {[p.category, p.last ? `son ${monthYear(p.last.month)}` : null, `${p.paid_months} ay`].filter(Boolean).join(" · ")}</span>
              </span>
              <span className="flex shrink-0 flex-col items-end gap-0.5">
                <span className="font-semibold tabular-nums">{p.last ? tl(p.last.amount) : "—"}</span>
                <ChangeTag c={p.vs_prev_month} compact />
              </span>
              <ChevronRight size={16} className="shrink-0 text-stone-400" aria-hidden />
            </button>
          </li>
        ))}
      </ul>
    </section>
  );
}

/** Kurum / tür detayı: son 12 ay grafik, önceki ay ve geçen yılın aynı ayına göre değişim, ortalama, en yüksek ay,
 *  ay ay liste (kayıtlara dokununca düzenleme). */
function SeriesDetail({ s, end, onClose }: { s: BillSeries; end: string; onClose: () => void }) {
  const ui = useUI();
  const openRow = useOpenRow();
  const [openM, setOpenM] = useState<string | null>(null);
  const byMonth = new Map(s.months.map((m) => [m.month, m]));
  const months12 = Array.from({ length: 12 }, (_, i) => addMonths(end, i - 11));
  const bars = months12.map((m) => ({ month: m, amount: byMonth.get(m)?.amount ?? 0 }));
  const list = [...s.months].filter((m) => m.count).sort((a, b) => b.month.localeCompare(a.month));
  const edit = (it: BillItem) => {
    onClose();
    void openRow(it.source === "card" ? `c${it.id}` : `m${it.id}`, it.date, (row) => (row.source === "card" ? ui.openCardTxn(row) : ui.openEntry(row)))
      .then((ok) => { if (!ok) ui.toast("Kayıt bulunamadı"); });
  };
  const change = (ym: string, back: number): Change | null => {
    const now = byMonth.get(ym)?.amount ?? 0, before = byMonth.get(addMonths(ym, -back))?.amount ?? 0;
    return before > 0 ? { month: addMonths(ym, -back), amount: before, diff: now - before, pct: Math.round(((now - before) / before) * 1000) / 10 } : null;
  };
  return (
    <Modal title={s.name} onClose={onClose}>
      <div className="space-y-4">
        <p className="-mt-1 text-sm text-stone-500 dark:text-stone-400">{s.category ? `${s.category} · ` : ""}aylık ödenen tutar</p>
        <div className="card p-3"><MonthBars data={bars} average={s.average} label={`${s.name} son 12 ay`} /></div>
        <dl className="grid grid-cols-2 gap-2 text-sm">
          <div className="rounded-lg bg-stone-50 p-2.5 dark:bg-stone-800/60">
            <dt className="text-xs text-stone-500 dark:text-stone-400">{s.last ? `Son: ${monthYear(s.last.month)}` : "Son ay"}</dt>
            <dd className="font-semibold tabular-nums">{s.last ? tl(s.last.amount) : "—"}</dd>
          </div>
          <div className="rounded-lg bg-stone-50 p-2.5 dark:bg-stone-800/60">
            <dt className="text-xs text-stone-500 dark:text-stone-400">Önceki aya göre</dt>
            <dd><ChangeTag c={s.vs_prev_month} compact /></dd>
            {s.vs_prev_month && <dd className="text-xs text-stone-500 tabular-nums dark:text-stone-400">{monthYear(s.vs_prev_month.month)}: {tl(s.vs_prev_month.amount)}</dd>}
          </div>
          <div className="rounded-lg bg-stone-50 p-2.5 dark:bg-stone-800/60">
            <dt className="text-xs text-stone-500 dark:text-stone-400">Geçen yılın aynı ayına göre</dt>
            <dd><ChangeTag c={s.vs_last_year} compact /></dd>
            {s.vs_last_year && <dd className="text-xs text-stone-500 tabular-nums dark:text-stone-400">{monthYear(s.vs_last_year.month)}: {tl(s.vs_last_year.amount)}</dd>}
          </div>
          <div className="rounded-lg bg-stone-50 p-2.5 dark:bg-stone-800/60">
            <dt className="text-xs text-stone-500 dark:text-stone-400">Ortalama · en yüksek</dt>
            <dd className="font-semibold tabular-nums">{tl(s.average)}</dd>
            {s.max && <dd className="text-xs text-stone-500 tabular-nums dark:text-stone-400">en yüksek {monthYear(s.max.month)}: {tl(s.max.amount)}</dd>}
          </div>
        </dl>
        <section aria-label="Aylar">
          <h3 className="mb-1 text-xs font-semibold tracking-wide text-stone-500 uppercase dark:text-stone-400">Aylar</h3>
          <ul className="divide-y divide-stone-100 rounded-lg border border-stone-200 text-sm dark:divide-stone-800 dark:border-stone-700">
            {list.map((m) => {
              const one = m.items.length === 1;
              const open = openM === m.month;
              return (
                <li key={m.month}>
                  <button className="flex w-full items-start gap-2 px-3 py-2 text-left hover:bg-stone-50 dark:hover:bg-stone-800/50"
                    aria-expanded={one ? undefined : open}
                    onClick={() => (one ? edit(m.items[0]) : setOpenM(open ? null : m.month))}>
                    <span className="min-w-0 flex-1">
                      <span className="block font-medium">{monthYear(m.month)}{!one && <span className="font-normal text-stone-500 dark:text-stone-400"> · {m.count} ödeme</span>}</span>
                      <span className="mt-0.5 flex flex-wrap gap-x-3 gap-y-0.5">
                        <ChangeTag c={change(m.month, 1)} compact />
                        {change(m.month, 12) && <span className="text-xs text-stone-500 dark:text-stone-400">geçen yıl: <ChangeTag c={change(m.month, 12)} compact /></span>}
                      </span>
                    </span>
                    <span className="shrink-0 font-semibold tabular-nums">{tl(m.amount)}</span>
                    {one ? <ChevronRight size={16} className="mt-0.5 shrink-0 text-stone-400" aria-hidden />
                      : <ChevronDown size={16} className={cx("mt-0.5 shrink-0 text-stone-400 transition-transform", open && "rotate-180")} aria-hidden />}
                  </button>
                  {!one && open && (
                    <ul className="bg-stone-50 dark:bg-stone-800/40">
                      {m.items.map((it) => (
                        <li key={`${it.source}${it.id}`}>
                          <button className="flex w-full items-start gap-2 py-2 pr-3 pl-6 text-left hover:bg-stone-100 dark:hover:bg-stone-800" onClick={() => edit(it)}>
                            <span className="min-w-0 flex-1">
                              <span className="block break-words">{maskText(it.description)}</span>
                              <span className="block text-xs text-stone-500 dark:text-stone-400">{dateTR(it.date)}{it.source === "card" ? " · kart" : ""}{it.invoice ? ` · fatura no ${it.invoice}` : ""}</span>
                            </span>
                            <span className="shrink-0 tabular-nums">{tl(it.amount)}</span>
                          </button>
                        </li>
                      ))}
                    </ul>
                  )}
                </li>
              );
            })}
          </ul>
          <p className="mt-1 text-xs text-stone-500 dark:text-stone-400">Bir aya dokununca o ödemenin kaydı açılır; tutarı, tarihi ve kategorisi oradan düzeltilir.</p>
        </section>
      </div>
    </Modal>
  );
}
