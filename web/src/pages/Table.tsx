import { useQuery } from "@tanstack/react-query";
import { ArrowDown, ArrowUp, Download, X } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { api, type Row } from "../api";
import MultiSelect from "../components/MultiSelect";
import Segmented from "../components/Segmented";
import { cx, dateTR, fold, KIND_LABEL, monthLabel, tl } from "../format";
import { useUI } from "../ui";

type View = "liste" | "pivot";
type PivotBy = "category" | "merchant" | "card";
type SortKey = "date" | "month" | "description" | "category" | "card" | "kind" | "amount";

interface Filters {
  from: string;
  to: string;
  categories: string[];
  cards: string[];
  kinds: string[];
  q: string;
  min: string;
  max: string;
}

const KEY = "tablo-filtreler";
const EMPTY: Filters = { from: "", to: "", categories: [], cards: [], kinds: ["harcama", "iade", "faiz_ucret", "gider"], q: "", min: "", max: "" };

function load(): Filters {
  try {
    return { ...EMPTY, ...JSON.parse(localStorage.getItem(KEY) ?? "{}") };
  } catch {
    return EMPTY;
  }
}

/** Türkçe Excel: ; ayraç, virgüllü ondalık, UTF-8 BOM. */
function downloadCsv(name: string, rows: (string | number)[][]) {
  const cell = (v: string | number) => {
    const s = typeof v === "number" ? v.toFixed(2).replace(".", ",") : v;
    return /[;"\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
  };
  const blob = new Blob(["﻿" + rows.map((r) => r.map(cell).join(";")).join("\r\n")], { type: "text/csv;charset=utf-8" });
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = name;
  a.click();
  URL.revokeObjectURL(a.href);
}

export default function Table() {
  const ui = useUI();
  const all = useQuery({ queryKey: ["txns", "all-months"], queryFn: () => api.transactions(undefined) });
  const [f, setF] = useState<Filters>(load);
  const [view, setView] = useState<View>("pivot");
  const [pivotBy, setPivotBy] = useState<PivotBy>("category");
  const [sort, setSort] = useState<{ key: SortKey; dir: 1 | -1 }>({ key: "date", dir: -1 });
  const [drill, setDrill] = useState<{ name: string; month?: string } | null>(null);
  useEffect(() => {
    try { localStorage.setItem(KEY, JSON.stringify(f)); } catch { /* özel mod */ }
  }, [f]);
  const set = (p: Partial<Filters>) => { setF({ ...f, ...p }); setDrill(null); };

  const data = (all.data ?? []).filter((r) => !r.deleted && !r.pending && !r.skipped && !r.linked);
  const months = useMemo(() => [...new Set(data.map((r) => r.month))].sort(), [data]);
  const categories = useMemo(() => [...new Set(data.map((r) => r.category))].sort((a, b) => a.localeCompare(b, "tr")), [data]);
  const cards = useMemo(() => [...new Set(data.map((r) => r.card ?? "Elle girilen"))].sort(), [data]);

  const filtered = useMemo(() => {
    const q = fold(f.q.trim());
    const min = f.min ? Number(f.min.replace(",", ".")) : -Infinity;
    const max = f.max ? Number(f.max.replace(",", ".")) : Infinity;
    return data.filter((r) =>
      (!f.from || r.month >= f.from) && (!f.to || r.month <= f.to) &&
      (!f.categories.length || f.categories.includes(r.category)) &&
      (!f.cards.length || f.cards.includes(r.card ?? "Elle girilen")) &&
      (!f.kinds.length || f.kinds.includes(r.kind)) &&
      (!q || fold(`${r.description} ${r.merchant} ${r.note ?? ""} ${r.category}`).includes(q)) &&
      r.amount >= min && r.amount <= max);
  }, [data, f]);

  const pivotKey = (r: Row) => (pivotBy === "category" ? r.category : pivotBy === "merchant" ? r.merchant : r.card ?? "Elle girilen");
  const listRows = useMemo(() => {
    const rows = drill ? filtered.filter((r) => pivotKey(r) === drill.name && (!drill.month || r.month === drill.month)) : filtered;
    const v = (r: Row): string | number => sort.key === "amount" ? r.amount : sort.key === "card" ? r.card ?? "" : (r[sort.key] as string) ?? "";
    return [...rows].sort((a, b) => {
      const x = v(a), y = v(b);
      return (typeof x === "number" ? x - (y as number) : String(x).localeCompare(String(y), "tr")) * sort.dir;
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [filtered, sort, drill, pivotBy]);

  const pivot = useMemo(() => {
    const ms = [...new Set(filtered.map((r) => r.month))].sort();
    const map = new Map<string, Map<string, number>>();
    for (const r of filtered) {
      const k = pivotKey(r);
      if (!map.has(k)) map.set(k, new Map());
      const m = map.get(k)!;
      m.set(r.month, (m.get(r.month) ?? 0) + (r.kind === "gelir" ? -r.amount : r.amount));
    }
    const rows = [...map.entries()].map(([name, m]) => ({ name, cells: m, total: [...m.values()].reduce((a, b) => a + b, 0) }))
      .sort((a, b) => b.total - a.total);
    const colTotals = ms.map((mo) => rows.reduce((a, r) => a + (r.cells.get(mo) ?? 0), 0));
    return { months: ms, rows, colTotals, grand: colTotals.reduce((a, b) => a + b, 0) };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [filtered, pivotBy]);

  const total = listRows.reduce((a, r) => a + (r.kind === "gelir" ? -r.amount : r.amount), 0);
  const activeCount = [f.from, f.to, f.q, f.min, f.max].filter(Boolean).length + (f.categories.length ? 1 : 0) + (f.cards.length ? 1 : 0);

  const exportCsv = () => {
    if (view === "pivot") {
      downloadCsv("pivot.csv", [["", ...pivot.months.map((m) => monthLabel(m)), "Toplam"],
        ...pivot.rows.map((r) => [r.name, ...pivot.months.map((m) => r.cells.get(m) ?? 0), r.total]),
        ["Toplam", ...pivot.colTotals, pivot.grand]]);
    } else {
      downloadCsv("islemler.csv", [["Tarih", "Ekstre ayı", "Açıklama", "İşyeri", "Kategori", "Kart", "Tür", "Taksit", "Döviz", "Tutar", "Not"],
        ...listRows.map((r) => [dateTR(r.date), r.month, r.description, r.merchant, r.category, r.card ?? "Elle", KIND_LABEL[r.kind] ?? r.kind,
          r.installment ?? "", r.fx ?? "", r.amount, r.note ?? ""])]);
    }
  };

  const Th = ({ k, children, right }: { k: SortKey; children: React.ReactNode; right?: boolean }) => (
    <th className={cx("sticky top-0 cursor-pointer bg-stone-100 px-2 py-2 font-medium whitespace-nowrap select-none dark:bg-stone-800", right && "text-right")}
      onClick={() => setSort({ key: k, dir: sort.key === k ? (-sort.dir as 1 | -1) : k === "amount" || k === "date" ? -1 : 1 })}>
      <span className="inline-flex items-center gap-1">{children}
        {sort.key === k && (sort.dir === 1 ? <ArrowUp size={12} /> : <ArrowDown size={12} />)}</span>
    </th>
  );

  return (
    <div className="space-y-3">
      <header className="flex flex-wrap items-center justify-between gap-2">
        <h1 className="text-xl font-semibold">Tablo</h1>
        <div className="flex items-center gap-2">
          <div className="w-44"><Segmented value={view} onChange={(v) => { setView(v); setDrill(null); }}
            options={[{ value: "pivot", label: "Pivot" }, { value: "liste", label: "Liste" }]} /></div>
          <button className="btn-ghost border border-stone-300 dark:border-stone-700" onClick={exportCsv}><Download size={16} /> Excel</button>
        </div>
      </header>

      <div className="card flex flex-wrap items-center gap-2 p-2.5">
        <select className="input w-auto py-1.5" value={f.from} onChange={(e) => set({ from: e.target.value })} aria-label="Başlangıç ayı">
          <option value="">İlk ay</option>
          {months.map((m) => <option key={m} value={m}>{monthLabel(m, true)}</option>)}
        </select>
        <span className="text-stone-400">→</span>
        <select className="input w-auto py-1.5" value={f.to} onChange={(e) => set({ to: e.target.value })} aria-label="Bitiş ayı">
          <option value="">Son ay</option>
          {months.map((m) => <option key={m} value={m}>{monthLabel(m, true)}</option>)}
        </select>
        <MultiSelect label="Kategori" options={categories} value={f.categories} onChange={(v) => set({ categories: v })} />
        <MultiSelect label="Kart" options={cards} value={f.cards} onChange={(v) => set({ cards: v })} />
        <MultiSelect label="Tür" options={["harcama", "iade", "faiz_ucret", "gider", "gelir", "odeme"]} value={f.kinds}
          onChange={(v) => set({ kinds: v })} render={(o) => KIND_LABEL[o] ?? o} />
        <input className="input w-36 py-1.5" placeholder="Ara" value={f.q} onChange={(e) => set({ q: e.target.value })} />
        <input className="input w-20 py-1.5 text-right" inputMode="decimal" placeholder="min ₺" value={f.min} onChange={(e) => set({ min: e.target.value })} />
        <input className="input w-20 py-1.5 text-right" inputMode="decimal" placeholder="max ₺" value={f.max} onChange={(e) => set({ max: e.target.value })} />
        {activeCount > 0 && <button className="btn-ghost text-xs" onClick={() => set({ ...EMPTY })}><X size={14} /> Temizle</button>}
      </div>

      {view === "pivot" && (
        <div className="flex flex-wrap items-center gap-2 text-sm">
          <span className="text-stone-500">Satırlar:</span>
          <div className="w-64"><Segmented value={pivotBy} onChange={(v) => { setPivotBy(v); setDrill(null); }}
            options={[{ value: "category", label: "Kategori" }, { value: "merchant", label: "İşyeri" }, { value: "card", label: "Kart" }]} /></div>
          <span className="text-xs text-stone-500">Hücreye tıkla → o işlemler aşağıda listelenir</span>
        </div>
      )}

      {all.isLoading && <p className="text-sm text-stone-500">Yükleniyor…</p>}

      {view === "pivot" && (
        <div className="card overflow-auto">
          <table className="w-full text-sm tabular-nums">
            <thead>
              <tr>
                <th className="sticky top-0 left-0 z-10 bg-stone-100 px-2 py-2 text-left font-medium dark:bg-stone-800">
                  {pivotBy === "category" ? "Kategori" : pivotBy === "merchant" ? "İşyeri" : "Kart"}
                </th>
                {pivot.months.map((m) => <th key={m} className="sticky top-0 bg-stone-100 px-2 py-2 text-right font-medium whitespace-nowrap dark:bg-stone-800">{monthLabel(m, true)}</th>)}
                <th className="sticky top-0 bg-stone-200 px-2 py-2 text-right font-semibold dark:bg-stone-700">Toplam</th>
              </tr>
            </thead>
            <tbody>
              {pivot.rows.map((r) => (
                <tr key={r.name} className="border-t border-stone-100 dark:border-stone-800">
                  <td className="sticky left-0 max-w-48 truncate bg-white px-2 py-1.5 dark:bg-stone-900">
                    <button className={cx("text-left hover:underline", drill?.name === r.name && !drill.month && "font-semibold text-blue-600")}
                      onClick={() => setDrill(drill?.name === r.name && !drill.month ? null : { name: r.name })}>{r.name}</button>
                  </td>
                  {pivot.months.map((m) => {
                    const v = r.cells.get(m);
                    const on = drill?.name === r.name && drill.month === m;
                    return (
                      <td key={m} className="px-1 py-1 text-right">
                        {v ? (
                          <button onClick={() => setDrill(on ? null : { name: r.name, month: m })}
                            className={cx("w-full rounded px-1 py-0.5 text-right hover:bg-blue-50 dark:hover:bg-blue-950/40",
                              on && "bg-blue-100 font-semibold dark:bg-blue-900/50", v < 0 && "text-green-700 dark:text-green-400")}>
                            {tl(v).replace(",00", "")}
                          </button>
                        ) : <span className="px-1 text-stone-300 dark:text-stone-700">—</span>}
                      </td>
                    );
                  })}
                  <td className="bg-stone-50 px-2 py-1.5 text-right font-semibold dark:bg-stone-800/50">{tl(r.total).replace(",00", "")}</td>
                </tr>
              ))}
            </tbody>
            <tfoot>
              <tr className="border-t-2 border-stone-300 font-semibold dark:border-stone-600">
                <td className="sticky left-0 bg-stone-100 px-2 py-2 dark:bg-stone-800">Toplam</td>
                {pivot.colTotals.map((v, i) => <td key={i} className="bg-stone-100 px-2 py-2 text-right whitespace-nowrap dark:bg-stone-800">{tl(v).replace(",00", "")}</td>)}
                <td className="bg-stone-200 px-2 py-2 text-right whitespace-nowrap dark:bg-stone-700">{tl(pivot.grand).replace(",00", "")}</td>
              </tr>
            </tfoot>
          </table>
          {!pivot.rows.length && !all.isLoading && <p className="p-4 text-sm text-stone-500">Filtreye uyan işlem yok.</p>}
        </div>
      )}

      {(view === "liste" || drill) && (
        <div className="card overflow-hidden">
          <div className="flex items-center justify-between border-b border-stone-100 px-3 py-2 text-xs text-stone-500 dark:border-stone-800">
            <span>
              {drill && <b className="text-stone-800 dark:text-stone-200">{drill.name}{drill.month && ` · ${monthLabel(drill.month)}`} — </b>}
              {listRows.length} kayıt · toplam <b>{tl(total)}</b> · satıra tıkla → düzenle
            </span>
            {drill && <button className="btn-ghost px-2 py-0.5 text-xs" onClick={() => setDrill(null)}><X size={12} /> Kapat</button>}
          </div>
          <div className="max-h-[70vh] overflow-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="text-left">
                  <Th k="date">Tarih</Th><Th k="month">Ekstre ayı</Th><Th k="description">Açıklama</Th>
                  <Th k="category">Kategori</Th><Th k="card">Kart</Th><Th k="kind">Tür</Th><Th k="amount" right>Tutar</Th>
                </tr>
              </thead>
              <tbody>
                {listRows.map((r) => (
                  <tr key={r.uid} className="cursor-pointer border-t border-stone-100 hover:bg-stone-50 dark:border-stone-800 dark:hover:bg-stone-800/50"
                    onClick={() => (r.source === "card" ? ui.openCardTxn(r) : ui.openEntry(r))}>
                    <td className="px-2 py-1.5 whitespace-nowrap">{dateTR(r.date)}</td>
                    <td className="px-2 py-1.5 whitespace-nowrap text-stone-500">{monthLabel(r.month, true)}</td>
                    <td className="max-w-72 px-2 py-1.5">
                      <div className="truncate">{r.description}</div>
                      {(r.installment || r.fx || r.note) && (
                        <div className="truncate text-xs text-stone-500">{[r.installment && `${r.installment} taksit`, r.fx, r.note].filter(Boolean).join(" · ")}</div>
                      )}
                    </td>
                    <td className="px-2 py-1.5 whitespace-nowrap">{r.category}</td>
                    <td className="px-2 py-1.5 whitespace-nowrap text-stone-500">{r.card ?? "Elle"}</td>
                    <td className="px-2 py-1.5 whitespace-nowrap text-stone-500">{KIND_LABEL[r.kind] ?? r.kind}</td>
                    <td className={cx("px-2 py-1.5 text-right whitespace-nowrap tabular-nums", (r.kind === "gelir" || r.amount < 0) && "text-green-700 dark:text-green-400")}>
                      {tl(r.amount)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </div>
  );
}
