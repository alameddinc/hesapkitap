import { useQuery } from "@tanstack/react-query";
import { send } from "../lib/apiError";
import { ChevronDown, Search, X } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { cx, dateTR, fold, maskText, pctSigned, tl } from "../format";
import { useMeta } from "./hooks";
import Segmented from "./Segmented";
import PriceDetail, { attributionOf, perUnitText, type PriceGroup, type PriceGroupRef, type PriceHistory } from "./PriceDetail";

interface StorePrice {
  store: string; branch: string | null; price: number; date: string; estimated: boolean; cheapest?: boolean;
  unit_price_per?: number | null; per_unit?: string | null;
}
interface Product {
  key: string; product: string; brand: string | null; unit: "adet" | "kg" | "lt"; count: number; last_date: string;
  last_price: number | null; last_estimated: boolean; first_price: number | null; change_pct: number | null; stores: StorePrice[];
  history: PriceHistory[];
  /** yetenek price_groups: markası fark etmeyen ürün grubu (Süt · 1 L) */
  group?: PriceGroupRef | null;
  /** yetenek price_categories / price_unit */
  category?: string | null; unit_price_per?: number | null; per_unit?: string | null;
}
interface Data {
  products: Product[];
  stores: { store: string; visits: number; branches: string[]; last_date: string; products: Product[] }[];
  brands: { brand: string; products: Product[]; avg_change: number | null }[];
  all_stores: string[]; all_brands: string[];
}

const pct = pctSigned;
const per = (u: Product["unit"]) => (u === "kg" ? "/kg" : u === "lt" ? "/lt" : "");
const pctClass = (x: number) => (x > 0 ? "text-red-600" : x < 0 ? "text-green-700" : "text-stone-500");
const SINCE: Record<string, () => string | null> = {
  all: () => null,
  d30: () => new Date(Date.now() - 30 * 864e5).toISOString().slice(0, 10),
  d90: () => new Date(Date.now() - 90 * 864e5).toISOString().slice(0, 10),
  year: () => `${new Date().getFullYear()}-01-01`,
};

function useDebounced<T>(v: T, ms = 300) {
  const [d, setD] = useState(v);
  useEffect(() => { const t = setTimeout(() => setD(v), ms); return () => clearTimeout(t); }, [v, ms]);
  return d;
}

interface RowProps {
  p: Product; showStores?: boolean;
  /** ürün grubu çipi (yetenek price_groups): dokununca listeyi o gruba süzer */
  onGroup?: (g: PriceGroupRef) => void;
  /** "Nereden alınır" satırından gelindi: aç ve görünür kıl */
  focus?: boolean;
}

function ProductRow({ p, showStores = true, onGroup, focus }: RowProps) {
  const [open, setOpen] = useState(!!focus); // focus değişince satır yeniden kurulur (anahtar), açık başlar
  const el = useRef<HTMLLIElement>(null);
  useEffect(() => { if (focus) el.current?.scrollIntoView?.({ block: "start", behavior: "smooth" }); }, [focus]);
  return (
    <li ref={el} data-product={p.key}>
      <button className="flex w-full items-start gap-3 px-4 py-2.5 text-left" aria-expanded={open} onClick={() => setOpen(!open)}>
        <div className="min-w-0 flex-1">
          <div className="font-medium">{p.product}{p.brand && !fold(p.product).includes(fold(p.brand)) && <span className="text-xs font-normal text-stone-500"> · {p.brand}</span>}</div>
          {showStores && (
            <div className="mt-1 flex flex-wrap gap-1 text-xs">
              {p.stores.map((s) => (
                <span key={s.store} className={cx("rounded-full px-2 py-0.5 tabular-nums",
                  s.cheapest ? "bg-green-100 text-green-800 dark:bg-green-950 dark:text-green-300" : "bg-stone-100 text-stone-600 dark:bg-stone-800 dark:text-stone-300")}>
                  {maskText(s.store)} {s.estimated ? "≈" : ""}{tl(s.price)}{per(p.unit)}{s.cheapest ? " · en ucuz" : ""}
                </span>
              ))}
              {!p.stores.length && <span className="rounded-full bg-stone-100 px-2 py-0.5 text-stone-500 dark:bg-stone-800">fiyat okunamadı</span>}
            </div>
          )}
          <div className="mt-1 text-xs text-stone-500">{p.count} alım · son {dateTR(p.last_date)}</div>
        </div>
        <div className="shrink-0 text-right text-sm tabular-nums">
          {p.last_price != null && <div className="font-semibold">{p.last_estimated ? "≈" : ""}{tl(p.last_price)}<span className="text-xs font-normal text-stone-500">{per(p.unit)}</span></div>}
          {p.unit_price_per != null && p.per_unit && <div className="text-xs text-stone-500" data-testid="birim-fiyat">{perUnitText(p.unit_price_per, p.per_unit)}</div>}
          {p.change_pct != null && <div className={cx("text-xs", pctClass(p.change_pct))}>{pct(p.change_pct)}</div>}
        </div>
        <ChevronDown size={14} className={cx("mt-1 shrink-0 text-stone-400 transition-transform", open && "rotate-180")} />
      </button>
      {onGroup && p.group && (
        <div className="-mt-1.5 px-4 pb-2.5">
          <button type="button" className="rounded-full border border-stone-200 px-2 py-0.5 text-xs text-stone-600 hover:bg-stone-100 dark:border-stone-700 dark:text-stone-300 dark:hover:bg-stone-800"
            onClick={() => onGroup(p.group!)} aria-label={`Yalnız ${p.group.label} grubunu göster`} data-testid="grup-cip">{p.group.label}</button>
        </div>
      )}
      {open && <PriceDetail p={p} />}
    </li>
  );
}

function Group({ title, sub, right, children }: { title: string; sub: string; right?: React.ReactNode; children: React.ReactNode }) {
  const [open, setOpen] = useState(false);
  return (
    <li>
      <button className="flex w-full items-center gap-3 px-4 py-3 text-left" onClick={() => setOpen(!open)}>
        <div className="min-w-0 flex-1"><div className="font-medium">{title}</div><div className="text-xs text-stone-500">{sub}</div></div>
        {right}
        <ChevronDown size={14} className={cx("shrink-0 text-stone-400 transition-transform", open && "rotate-180")} />
      </button>
      {open && <ul className="divide-y divide-stone-100 border-t border-stone-100 bg-stone-50/50 dark:divide-stone-800 dark:border-stone-800 dark:bg-stone-900/40">{children}</ul>}
    </li>
  );
}

/** Fişlerden fiyat takibi: ürün / mağaza / marka kırılımı, tarih ve mağaza/marka filtresi. */
export default function PriceWatch() {
  const [view, setView] = useState<"urun" | "magaza" | "marka">("urun");
  const [since, setSince] = useState("all");
  const [store, setStore] = useState("");
  const [brand, setBrand] = useState("");
  const [term, setTerm] = useState("");
  const [groupF, setGroupF] = useState<PriceGroupRef | null>(null);
  const [focus, setFocus] = useState<string | null>(null);
  const [cat, setCat] = useState<string | null>(null);
  const caps = useMeta().data?.capabilities;
  const dterm = useDebounced(term.trim());
  const params = new URLSearchParams();
  const s = SINCE[since]();
  if (s) params.set("since", s);
  if (store) params.set("store", store);
  if (brand) params.set("brand", brand);
  const q = useQuery({ queryKey: ["prices", params.toString()], queryFn: () => send<Data>(`/api/prices?${params}`) });
  const d = q.data;
  const searching = !groupF && !!caps?.includes("where_to_buy") && dterm.length >= 2;
  // aramanın grupları ("1 l süt" → Süt · 1 L): adı metinle eşleşmese de o gruptaki ürünler listede (WhereToBuy ile aynı sorgu)
  const ara = useQuery({
    queryKey: ["price-groups-search", dterm], retry: false, staleTime: 60_000, enabled: searching,
    queryFn: () => send<PriceGroup[] | { groups: PriceGroup[] }>(`/api/prices/groups?q=${encodeURIComponent(dterm)}&limit=3`),
    select: (x) => (Array.isArray(x) ? x : x.groups ?? []).filter((g) => g.best.length).slice(0, 3),
  });
  const araKeys = new Set(searching ? (ara.data ?? []).map((g) => g.key) : []);
  const catOf = (p: Product) => p.category ?? p.group?.category ?? null;
  const cats = [...new Set((d?.products ?? []).map(catOf).filter((c): c is string => !!c))].sort((a, b) => a.localeCompare(b, "tr"));
  const match = (p: Product) => (!cat || catOf(p) === cat) && (groupF ? p.group?.key === groupF.key
    : !term.trim() || (!!p.group && araKeys.has(p.group.key)) || fold(`${p.product} ${p.brand ?? ""} ${p.group?.label ?? ""}`).includes(fold(term.trim())));
  const onGroup = caps?.includes("price_groups") ? (g: PriceGroupRef) => { setGroupF(g); setFocus(null); setView("urun"); } : undefined;
  // "Nereden alınır" satırı: kendi aldığın ürünse (aynı grup, o zincirden) ürünün ayrıntısını aç
  const ownProduct = (g: PriceGroup, chain: string) => {
    const same = (d?.products ?? []).filter((p) => p.group?.key === g.key);
    return same.find((p) => p.stores.some((st) => fold(st.store).includes(fold(chain)) || fold(chain).includes(fold(st.store)))) ?? same[0];
  };
  const openOwn = (p: Product) => { setGroupF(null); setTerm(""); setView("urun"); setFocus(p.key); };
  const prods = (d?.products ?? []).filter(match);
  const changed = prods.filter((p) => p.change_pct != null);
  const avg = changed.length ? changed.reduce((a, p) => a + (p.change_pct ?? 0), 0) / changed.length : null;

  return (
    <section className="card">
      <div className="space-y-2 p-4">
        <Segmented value={view} onChange={setView} options={[
          { value: "urun", label: "Ürün" }, { value: "magaza", label: "Mağaza" }, { value: "marka", label: "Marka" }]} />
        <div className="grid grid-cols-3 gap-2">
          <select className="input" value={since} onChange={(e) => setSince(e.target.value)} aria-label="Dönem">
            <option value="all">Her zaman</option><option value="d30">30 gün</option>
            <option value="d90">90 gün</option><option value="year">Bu yıl</option>
          </select>
          <select className="input" value={store} onChange={(e) => setStore(e.target.value)} aria-label="Mağaza">
            <option value="">Mağaza</option>
            {d?.all_stores.map((x) => <option key={x} value={x}>{x}</option>)}
          </select>
          <select className="input" value={brand} onChange={(e) => setBrand(e.target.value)} aria-label="Marka">
            <option value="">Marka</option>
            {d?.all_brands.map((x) => <option key={x} value={x}>{x}</option>)}
          </select>
        </div>
        <div className="relative">
          <Search size={16} className="pointer-events-none absolute top-1/2 left-3 -translate-y-1/2 text-stone-400" />
          <input className="input pl-9" placeholder="Ürün ara: latte, süt, 1 L süt…" value={term} aria-label="Ürün ara"
            onChange={(e) => { setTerm(e.target.value); setGroupF(null); setFocus(null); }} />
        </div>
        {cats.length > 1 && (
          <div className="-mx-4 flex gap-1.5 overflow-x-auto px-4 pb-0.5" role="group" aria-label="Kategori">
            {[null, ...cats].map((c) => (
              <button key={c ?? "_"} type="button" aria-pressed={cat === c} onClick={() => setCat(c)}
                className={cx("shrink-0 rounded-full border px-2.5 py-1 text-xs whitespace-nowrap",
                  cat === c ? "border-blue-600 bg-blue-600 text-white dark:border-blue-400 dark:bg-blue-400 dark:text-stone-900"
                    : "border-stone-200 text-stone-600 hover:bg-stone-100 dark:border-stone-700 dark:text-stone-300 dark:hover:bg-stone-800")}>
                {c ?? "Tümü"}</button>
            ))}
          </div>
        )}
        {groupF && (
          <div className="flex items-center gap-2 text-xs text-stone-500">
            Grup:
            <button type="button" className="inline-flex items-center gap-1 rounded-full bg-blue-50 px-2 py-0.5 font-medium text-blue-800 dark:bg-blue-950 dark:text-blue-200"
              onClick={() => setGroupF(null)} aria-label={`${groupF.label} süzgecini kaldır`}>{groupF.label} <X size={12} aria-hidden /></button>
          </div>
        )}
        {searching && (
          <WhereToBuy q={dterm} onOpen={(g, chain) => { const p = ownProduct(g, chain); if (p) openOwn(p); }}
            canOpen={(g, b) => b.source === "own" && !!ownProduct(g, b.chain)} />
        )}
        {avg != null && <p className="text-xs text-stone-500">Seçimdeki ürünlerin ortalama fiyat değişimi: <b className={pctClass(avg)}>{pct(avg)}</b></p>}
      </div>

      <ul className="divide-y divide-stone-100 border-t border-stone-100 dark:divide-stone-800 dark:border-stone-800">
        {view === "urun" && prods.map((p) => <ProductRow key={focus === p.key ? `${p.key}:odak` : p.key} p={p} onGroup={onGroup} focus={focus === p.key} />)}
        {view === "magaza" && d?.stores.map((st) => {
          const ps = st.products.filter(match);
          if (!ps.length) return null;
          return (
            <Group key={st.store} title={maskText(st.store)}
              sub={`${st.visits} fiş · ${ps.length} ürün · son ${dateTR(st.last_date)}${st.branches.length ? ` · ${st.branches.join(", ")}` : ""}`}>
              {ps.map((p) => <ProductRow key={p.key} p={p} showStores={false} onGroup={onGroup} />)}
            </Group>
          );
        })}
        {view === "marka" && d?.brands.map((b) => {
          const ps = b.products.filter(match);
          if (!ps.length) return null;
          return (
            <Group key={b.brand} title={b.brand} sub={`${ps.length} ürün`}
              right={b.avg_change != null ? <span className={cx("text-sm tabular-nums", pctClass(b.avg_change))}>{pct(b.avg_change)}</span> : undefined}>
              {ps.map((p) => <ProductRow key={p.key} p={p} onGroup={onGroup} />)}
            </Group>
          );
        })}
        {d && d.products.length > 0 && !prods.length && view === "urun" && (
          <li className="px-4 py-4 text-sm text-stone-500">Bu aramada kendi aldığın ürün yok.</li>
        )}
        {d && !d.products.length && (
          <li className="px-4 py-4 text-sm text-stone-500">
            {s || store || brand ? "Bu filtreye uyan ürün yok." : "Henüz fişten ürün yok. Fiş okuttukça burada birikir."}
          </li>
        )}
      </ul>
    </section>
  );
}

/** Aramada "Nereden alınır": GET /api/prices/groups?q=<metin>&limit=3 (yetenek where_to_buy). Sunucu boyu ("1 L süt")
 *  ve adı tam aranan ürünü öne alır. Grup başına zincir zincir en ucuz teklifler; en ucuzu yeşil. */
function WhereToBuy({ q, onOpen, canOpen }: {
  q: string; onOpen: (g: PriceGroup, chain: string) => void; canOpen: (g: PriceGroup, b: PriceGroup["best"][number]) => boolean;
}) {
  const r = useQuery({
    queryKey: ["price-groups-search", q], retry: false, staleTime: 60_000,
    queryFn: () => send<PriceGroup[] | { groups: PriceGroup[] }>(`/api/prices/groups?q=${encodeURIComponent(q)}&limit=3`),
    select: (d) => (Array.isArray(d) ? d : d.groups ?? []).filter((g) => g.best.length).slice(0, 3),
  });
  if (r.isLoading) return <p className="text-xs text-stone-500" aria-busy="true">Nereden alınır aranıyor…</p>;
  if (r.error || !r.data?.length) return null;
  return (
    <section className="space-y-2 pt-1" data-testid="arama-nereden" aria-label="Nereden alınır">
      <h3 className="text-xs font-semibold tracking-wide text-stone-500 uppercase">Nereden alınır</h3>
      <div className="grid gap-2 lg:grid-cols-3">
        {r.data.map((g) => {
          const best = [...g.best].sort((a, b) => a.price - b.price);
          return (
            <div key={g.key} className="rounded-lg border border-stone-200 dark:border-stone-700" data-testid="nereden-grup">
              <div className="border-b border-stone-100 px-3 py-2 text-sm font-semibold dark:border-stone-800">{g.label}</div>
              <ul className="divide-y divide-stone-100 text-sm dark:divide-stone-800">
                {best.map((b, i) => {
                  const cheap = i === 0 && best.length > 1;
                  const tail = b.source === "own" ? "senin alımın" : b.source === "resmi" ? "Market Fiyatı" : `topluluk${b.n ? `, ${b.n} kişi` : ""}`;
                  const pu = perUnitText(b.unit_price_per, b.per_unit);
                  const body = (
                    <>
                      <span className={cx("min-w-0 flex-1 break-words", cheap && "font-semibold text-green-700 dark:text-green-400")}>
                        {maskText(b.chain)} <span className="tabular-nums">{tl(b.price)}</span>{b.brand ? <span className="font-normal text-stone-600 dark:text-stone-300"> · {b.brand}</span> : ""}
                        <span className="block text-xs font-normal text-stone-500 tabular-nums">{[pu, tail].filter(Boolean).join(" · ")}</span></span>
                      {cheap && <span className="shrink-0 rounded bg-green-100 px-1.5 py-0.5 text-[11px] font-semibold text-green-800 dark:bg-green-950 dark:text-green-300">en ucuz</span>}
                    </>
                  );
                  return (
                    <li key={`${b.chain}-${i}`}>
                      {canOpen(g, b) ? (
                        <button type="button" className={cx("flex w-full items-center gap-2 px-3 py-1.5 text-left hover:bg-stone-50 dark:hover:bg-stone-800/60", cheap && "bg-green-50/60 dark:bg-green-950/30")}
                          onClick={() => onOpen(g, b.chain)}>{body}</button>
                      ) : <div className={cx("flex items-center gap-2 px-3 py-1.5", cheap && "bg-green-50/60 dark:bg-green-950/30")}>{body}</div>}
                    </li>
                  );
                })}
              </ul>
              {attributionOf(best) && <p className="border-t border-stone-100 px-3 py-1.5 text-[11px] text-stone-500 dark:border-stone-800" data-testid="resmi-kaynak">{attributionOf(best)}</p>}
            </div>
          );
        })}
      </div>
    </section>
  );
}
