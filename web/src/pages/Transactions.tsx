import { useQuery } from "@tanstack/react-query";
import { rowTotals } from "../lib/counts";
import { usePagedTxns } from "../lib/pagedTxns";
import LoadMore from "../components/LoadMore";
import { ChevronRight, ListFilter, Plus, Search, Tags, X } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { api, type Row } from "../api";
import MonthPicker from "../components/MonthPicker";
import TxnList from "../components/TxnList";
import { EstimateSection, splitEstimates } from "../components/Estimates";
import { cx, fold, maskText, tl } from "../format";
import { useMeta } from "../components/hooks";
import { useSpenders } from "../components/Spender";
import { hasTxnFilter, matchTxnFilter, parseTxnFilter, txnFilterLabel } from "../lib/txnFilter";
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

/** #/islemler?kategori=…&yil=… süzgeci; aynı sayfadayken adres değişirse (yıl özetinden başka satır) yeniden okunur */
function useHashFilter() {
  const [hash, setHash] = useState(window.location.hash);
  useEffect(() => {
    const on = () => setHash(window.location.hash);
    window.addEventListener("hashchange", on);
    return () => window.removeEventListener("hashchange", on);
  }, []);
  return useMemo(() => parseTxnFilter(hash), [hash]);
}

export default function Transactions() {
  const ui = useUI();
  const meta = useMeta().data;
  const sf = useHashFilter();
  const sfOn = hasTxnFilter(sf);
  const spenders = useSpenders(); // süzgeç başlığında "Kim harcadı: Ayşe"
  const [q, setQ] = useState("");
  const [tab, setTab] = useState<(typeof TABS)[number]["key"]>("all");
  const summary = useQuery({ queryKey: ["summary", ui.month], queryFn: () => api.summary(ui.month) });
  const needle = fold(q.trim());
  // sayfa sayfa (200): kaydırdıkça sıradaki; sekme ya da arama tüm dönemi gerektirir → kalanlar kendiliğinden gelir
  // yıl süzgecinde tüm geçmiş istenir (sunucu ay ya da hepsi verir), yıl istemcide süzülür; süzgeçte kalanlar kendiliğinden
  const txns = usePagedTxns({ month: sf.yil ? undefined : ui.month, includeDeleted: true, includeEstimates: true, pageSize: 200,
    auto: tab !== "all" || !!needle || sfOn });
  // tahmini satırlar ana listede ve sekme sayılarında yok; başta ayrı, katlanır bölümde
  const scoped = useMemo(() => (sfOn ? txns.rows.filter((r) => matchTxnFilter(r, sf, meta?.current_month)) : txns.rows),
    [txns.rows, sf, sfOn, meta?.current_month]);
  const [real, estimates] = useMemo(() => splitEstimates(scoped), [scoped]);
  const match = (r: Row) => !needle || fold(`${r.description} ${r.category} ${r.subcategory ?? ""} ${r.note ?? ""}`).includes(needle);
  const active = TABS.find((x) => x.key === tab)!;
  const rows = real.filter((r) => active.test(r) && match(r));
  const uncat = real.filter(TABS.find((x) => x.key === "uncat")!.test).length;
  const estShown = tab === "all" || tab === "awaiting" ? estimates.filter(match) : [];

  // toplam: süzgeç yokken sunucunun dönem toplamı (sayfanın değil); süzgeçte tüm satırlar gelince counts_as_spend bayrağı
  const filtered = tab !== "all" || !!needle || sfOn;
  const spend = !filtered && txns.totals ? txns.totals.expense : txns.complete ? rowTotals(rows).spend : null;

  return (
    <div className="space-y-3">
      <header className="flex flex-wrap items-center justify-between gap-2">
        <h1 className="text-xl font-semibold">İşlemler</h1>
        <div className="flex items-center gap-2">
          {sf.yil ? <span className="text-sm font-medium text-stone-600 dark:text-stone-300">{sf.yil} yılı · tüm dönemler</span>
            : <MonthPicker value={ui.month} onChange={ui.setMonth} />}
          <button className="btn-primary hidden md:inline-flex" onClick={ui.openAdd}><Plus size={16} /> Ekle</button>
        </div>
      </header>

      {sfOn && (
        <div className="card flex items-center gap-2 p-2.5 pl-3" role="status" data-testid="islem-suzgeci">
          <ListFilter size={16} aria-hidden className="shrink-0 text-blue-600 dark:text-blue-400" />
          <span className="min-w-0 flex-1 text-sm break-words"><span className="text-stone-500 dark:text-stone-400">Süzgeç: </span>
            <b className="font-medium">{txnFilterLabel(sf, maskText, spenders.adOf)}</b></span>
          <button type="button" className="btn-ghost shrink-0 px-2 py-1 text-xs" onClick={() => { window.location.hash = "/islemler"; }}>
            <X size={14} aria-hidden /> Süzgeci kaldır</button>
        </div>
      )}

      <div className="relative">
        <Search size={16} className="pointer-events-none absolute top-1/2 left-3 -translate-y-1/2 text-stone-400" />
        <input className="input pl-9" placeholder="Ara: uber, kira, psikolog…" value={q} onChange={(e) => setQ(e.target.value)} />
      </div>

      <div className="-mx-4 flex gap-1.5 overflow-x-auto px-4 pb-1 [scrollbar-width:none] sm:mx-0 sm:flex-wrap sm:px-0">
        {TABS.map((t) => {
          const n = txns.complete ? real.filter(t.test).length : null; // sayılar dönemin tamamı gelince
          return (
            <button key={t.key} onClick={() => setTab(t.key)}
              className={cx("shrink-0 rounded-full border px-3 py-1 text-sm",
                tab === t.key ? "border-blue-600 bg-blue-600 text-white" : "border-stone-300 dark:border-stone-700")}>
              {t.label}{n != null && <span className="opacity-70"> {n}</span>}
            </button>
          );
        })}
      </div>

      {/* Kategorisizleri düzenle: aynı işyerindekileri tek seferde kategorile (sunucu /api/uncategorized) */}
      {(tab === "all" || tab === "uncat") && !needle && !sfOn && uncat > 0 && (
        <button className="card flex w-full items-center gap-3 p-3 text-left hover:bg-stone-50 dark:hover:bg-stone-800/50" onClick={() => ui.go("kategorisiz")}>
          <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-blue-50 text-blue-700 dark:bg-blue-950 dark:text-blue-300"><Tags size={18} aria-hidden /></span>
          <span className="min-w-0 flex-1">
            <span className="block text-sm font-medium">Kategorisizleri düzenle</span>
            <span className="block text-xs text-stone-500 dark:text-stone-400">{uncat} işlem kategorisiz · aynı yerdekiler tek seferde</span>
          </span>
          <ChevronRight size={16} aria-hidden className="text-stone-400" />
        </button>
      )}

      <EstimateSection rows={estShown} summary={!needle ? summary.data : undefined} />

      <div className="card overflow-hidden">
        <div className="border-b border-stone-100 px-3 py-2 text-xs text-stone-500 dark:border-stone-800">
          {txns.complete ? `${rows.length} kayıt` : `İlk ${rows.length} kayıt`} · gider toplamı {spend == null ? "…" : tl(spend)}
          {!filtered && !txns.complete && " (dönemin tamamı)"} · düzenlemek için dokun
        </div>
        {txns.isLoading ? <p className="p-4 text-sm text-stone-500">Yükleniyor…</p> : <TxnList rows={rows} />}
        {txns.error && <p className="p-3 text-sm text-red-600 dark:text-red-400" role="alert">{(txns.error as Error).message}</p>}
        {!txns.isLoading && !txns.complete && !txns.error && <LoadMore onMore={txns.loadMore} busy={txns.loadingMore} label="Daha eski işlemler" />}
      </div>
    </div>
  );
}
