import { useQuery } from "@tanstack/react-query";
import { ChevronDown, Search } from "lucide-react";
import { useState } from "react";
import { cx, dateTR, fold, maskText, tl } from "../format";
import Segmented from "./Segmented";

interface StorePrice { store: string; branch: string | null; price: number; date: string; estimated: boolean; cheapest?: boolean }
interface Product {
  key: string; product: string; brand: string | null; unit: "adet" | "kg" | "lt"; count: number; last_date: string;
  last_price: number | null; last_estimated: boolean; first_price: number | null; change_pct: number | null; stores: StorePrice[];
  history: { date: string; price: number | null; store: string; branch: string | null; qty: number; name: string }[];
}
interface Data {
  products: Product[];
  stores: { store: string; visits: number; branches: string[]; last_date: string; products: Product[] }[];
  brands: { brand: string; products: Product[]; avg_change: number | null }[];
  all_stores: string[]; all_brands: string[];
}

const pct = (x: number) => `${x > 0 ? "+" : x < 0 ? "−" : ""}%${Math.abs(x).toLocaleString("tr-TR", { maximumFractionDigits: 1 })}`;
const per = (u: Product["unit"]) => (u === "kg" ? "/kg" : u === "lt" ? "/lt" : "");
const pctClass = (x: number) => (x > 0 ? "text-red-600" : x < 0 ? "text-green-700" : "text-stone-500");
const SINCE: Record<string, () => string | null> = {
  all: () => null,
  d30: () => new Date(Date.now() - 30 * 864e5).toISOString().slice(0, 10),
  d90: () => new Date(Date.now() - 90 * 864e5).toISOString().slice(0, 10),
  year: () => `${new Date().getFullYear()}-01-01`,
};

function ProductRow({ p, showStores = true }: { p: Product; showStores?: boolean }) {
  const [open, setOpen] = useState(false);
  return (
    <li>
      <button className="flex w-full items-start gap-3 px-4 py-2.5 text-left" onClick={() => setOpen(!open)}>
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
          {p.change_pct != null && <div className={cx("text-xs", pctClass(p.change_pct))}>{pct(p.change_pct)}</div>}
        </div>
        <ChevronDown size={14} className={cx("mt-1 shrink-0 text-stone-400 transition-transform", open && "rotate-180")} />
      </button>
      {open && (
        <ul className="space-y-0.5 px-4 pb-3 text-xs text-stone-500">
          {p.history.map((h, i) => (
            <li key={i} className="flex justify-between gap-2">
              <span className="min-w-0">
                {dateTR(h.date)} · {maskText(h.store)}{h.branch && ` (${h.branch})`} · <i>{h.name}</i>
                {h.qty !== 1 && ` · ${h.qty.toLocaleString("tr-TR")}${p.unit === "adet" ? " adet" : ` ${p.unit}`}`}
              </span>
              <span className="shrink-0 tabular-nums">{h.price != null ? `${tl(h.price)}${per(p.unit)}` : "—"}</span>
            </li>
          ))}
        </ul>
      )}
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
  const params = new URLSearchParams();
  const s = SINCE[since]();
  if (s) params.set("since", s);
  if (store) params.set("store", store);
  if (brand) params.set("brand", brand);
  const q = useQuery({ queryKey: ["prices", params.toString()], queryFn: () => fetch(`/api/prices?${params}`).then((r) => r.json() as Promise<Data>) });
  const d = q.data;
  const match = (p: Product) => !term.trim() || fold(`${p.product} ${p.brand ?? ""}`).includes(fold(term.trim()));
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
          <input className="input pl-9" placeholder="Ürün ara: latte, süt, gofret…" value={term} onChange={(e) => setTerm(e.target.value)} />
        </div>
        {avg != null && <p className="text-xs text-stone-500">Seçimdeki ürünlerin ortalama fiyat değişimi: <b className={pctClass(avg)}>{pct(avg)}</b></p>}
      </div>

      <ul className="divide-y divide-stone-100 border-t border-stone-100 dark:divide-stone-800 dark:border-stone-800">
        {view === "urun" && prods.map((p) => <ProductRow key={p.key} p={p} />)}
        {view === "magaza" && d?.stores.map((st) => {
          const ps = st.products.filter(match);
          if (!ps.length) return null;
          return (
            <Group key={st.store} title={maskText(st.store)}
              sub={`${st.visits} fiş · ${ps.length} ürün · son ${dateTR(st.last_date)}${st.branches.length ? ` · ${st.branches.join(", ")}` : ""}`}>
              {ps.map((p) => <ProductRow key={p.key} p={p} showStores={false} />)}
            </Group>
          );
        })}
        {view === "marka" && d?.brands.map((b) => {
          const ps = b.products.filter(match);
          if (!ps.length) return null;
          return (
            <Group key={b.brand} title={b.brand} sub={`${ps.length} ürün`}
              right={b.avg_change != null ? <span className={cx("text-sm tabular-nums", pctClass(b.avg_change))}>{pct(b.avg_change)}</span> : undefined}>
              {ps.map((p) => <ProductRow key={p.key} p={p} />)}
            </Group>
          );
        })}
        {d && !d.products.length && (
          <li className="px-4 py-4 text-sm text-stone-500">
            {s || store || brand ? "Bu filtreye uyan ürün yok." : "Henüz fişten ürün yok. Fiş okuttukça burada birikir."}
          </li>
        )}
      </ul>
    </section>
  );
}
