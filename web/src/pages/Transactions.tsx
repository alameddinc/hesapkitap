import { useQuery } from "@tanstack/react-query";
import { Plus, Search } from "lucide-react";
import { useMemo, useState } from "react";
import { api, type Row } from "../api";
import MonthPicker from "../components/MonthPicker";
import TxnList from "../components/TxnList";
import { cx, fold, tl } from "../format";
import { useUI } from "../ui";

const TABS = [
  { key: "all", label: "Tümü", test: (r: Row) => !r.deleted },
  { key: "card", label: "Kart", test: (r: Row) => r.source === "card" && !r.deleted },
  { key: "manual", label: "Elle", test: (r: Row) => r.source !== "card" },
  { key: "income", label: "Gelir", test: (r: Row) => r.kind === "gelir" },
  { key: "debt", label: "Borç", test: (r: Row) => r.kind === "borc" },
  { key: "fee", label: "Faiz", test: (r: Row) => r.kind === "faiz_ucret" && !r.deleted },
  { key: "pending", label: "Bekleyen", test: (r: Row) => !!r.pending },
  { key: "awaiting", label: "Ekstre bekleyen", test: (r: Row) => !!r.awaiting_statement },
  { key: "uncat", label: "Kategorisiz", test: (r: Row) => r.category === "Kategorisiz" && !r.deleted },
  { key: "deleted", label: "Silinen", test: (r: Row) => r.deleted },
] as const;

export default function Transactions() {
  const ui = useUI();
  const [q, setQ] = useState("");
  const [tab, setTab] = useState<(typeof TABS)[number]["key"]>("all");
  const txns = useQuery({ queryKey: ["txns", ui.month, "all"], queryFn: () => api.transactions(ui.month, true) });

  const rows = useMemo(() => {
    const t = TABS.find((x) => x.key === tab)!;
    const needle = fold(q.trim());
    return (txns.data ?? []).filter((r) => t.test(r) &&
      (!needle || fold(`${r.description} ${r.category} ${r.note ?? ""}`).includes(needle)));
  }, [txns.data, tab, q]);

  const spend = rows.filter((r) => !r.deleted && !r.pending && !r.skipped && !r.linked && r.kind !== "gelir" && r.kind !== "odeme" && r.kind !== "borc")
    .reduce((a, r) => a + r.amount, 0);

  return (
    <div className="space-y-3">
      <header className="flex flex-wrap items-center justify-between gap-2">
        <h1 className="text-xl font-semibold">İşlemler</h1>
        <div className="flex items-center gap-2">
          <MonthPicker value={ui.month} onChange={ui.setMonth} />
          <button className="btn-primary hidden md:inline-flex" onClick={ui.openAdd}><Plus size={16} /> Ekle</button>
        </div>
      </header>

      <div className="relative">
        <Search size={16} className="pointer-events-none absolute top-1/2 left-3 -translate-y-1/2 text-stone-400" />
        <input className="input pl-9" placeholder="Ara: uber, kira, psikolog…" value={q} onChange={(e) => setQ(e.target.value)} />
      </div>

      <div className="-mx-4 flex gap-1.5 overflow-x-auto px-4 pb-1 [scrollbar-width:none] sm:mx-0 sm:flex-wrap sm:px-0">
        {TABS.map((t) => {
          const n = (txns.data ?? []).filter(t.test).length;
          return (
            <button key={t.key} onClick={() => setTab(t.key)}
              className={cx("shrink-0 rounded-full border px-3 py-1 text-sm",
                tab === t.key ? "border-blue-600 bg-blue-600 text-white" : "border-stone-300 dark:border-stone-700")}>
              {t.label} <span className="opacity-70">{n}</span>
            </button>
          );
        })}
      </div>

      <div className="card overflow-hidden">
        <div className="border-b border-stone-100 px-3 py-2 text-xs text-stone-500 dark:border-stone-800">
          {rows.length} kayıt · gider toplamı {tl(spend)} · düzenlemek için dokun
        </div>
        {txns.isLoading ? <p className="p-4 text-sm text-stone-500">Yükleniyor…</p> : <TxnList rows={rows} />}
      </div>
    </div>
  );
}
