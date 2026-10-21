import { useQuery } from "@tanstack/react-query";
import { ChevronDown, Search, Tag } from "lucide-react";
import { useState } from "react";
import { cx, dateTR, fold, maskText, tl } from "../format";

interface Store { store: string; merchant: string | null; price: number; date: string; estimated: boolean; cheapest?: boolean }
interface Product {
  key: string; product: string; unit: "adet" | "kg" | "lt"; count: number; last_date: string;
  last_price: number | null; last_estimated: boolean; first_price: number | null; change_pct: number | null; stores: Store[];
  history: { date: string; price: number | null; merchant: string | null; qty: number; name: string }[];
}

const pct = (x: number) => `${x > 0 ? "+" : x < 0 ? "−" : ""}%${Math.abs(x).toLocaleString("tr-TR", { maximumFractionDigits: 1 })}`;
const per = (u: Product["unit"]) => (u === "kg" ? "/kg" : u === "lt" ? "/lt" : "");

/** Fişlerden ürünler: son fiyat, mağaza mağaza karşılaştırma (en ucuz işaretli), zamanla değişim. */
export default function PriceWatch() {
  const q = useQuery({ queryKey: ["prices"], queryFn: () => fetch("/api/prices").then((r) => r.json() as Promise<Product[]>) });
  const [open, setOpen] = useState<string | null>(null);
  const [term, setTerm] = useState("");
  const all = q.data ?? [];
  const items = term.trim() ? all.filter((p) => fold(p.product).includes(fold(term.trim()))) : all;
  const changed = all.filter((p) => p.change_pct != null);
  const avg = changed.length ? changed.reduce((a, p) => a + (p.change_pct ?? 0), 0) / changed.length : null;
  return (
    <section className="card">
      <div className="space-y-2 p-4 pb-2">
        <h2 className="flex items-center gap-2 font-semibold"><Tag size={18} /> Ürünler ({all.length})</h2>
        <p className="text-sm text-stone-500">
          Okuttuğun fişlerdeki ürünler. Aynı ürünü farklı marketten aldıysan en ucuzu işaretlenir; tekrar aldıkça fiyat değişimi görünür.
          {avg != null && <> Ortalama değişim: <b className={avg > 0 ? "text-red-600" : avg < 0 ? "text-green-700" : ""}>{pct(avg)}</b></>}
        </p>
        {all.length > 6 && (
          <div className="relative">
            <Search size={16} className="pointer-events-none absolute top-1/2 left-3 -translate-y-1/2 text-stone-400" />
            <input className="input pl-9" placeholder="Ürün ara: süt, gofret…" value={term} onChange={(e) => setTerm(e.target.value)} />
          </div>
        )}
      </div>
      <ul className="divide-y divide-stone-100 dark:divide-stone-800">
        {items.map((p) => (
          <li key={p.key}>
            <button className="flex w-full items-start gap-3 px-4 py-2.5 text-left" onClick={() => setOpen(open === p.key ? null : p.key)}>
              <div className="min-w-0 flex-1">
                <div className="font-medium">{p.product}</div>
                <div className="mt-1 flex flex-wrap gap-1 text-xs">
                  {p.stores.map((s) => (
                    <span key={s.store} className={cx("rounded-full px-2 py-0.5 tabular-nums",
                      s.cheapest ? "bg-green-100 text-green-800 dark:bg-green-950 dark:text-green-300" : "bg-stone-100 text-stone-600 dark:bg-stone-800 dark:text-stone-300")}>
                      {maskText(s.store)} {s.estimated ? "≈" : ""}{tl(s.price)}{per(p.unit)}{s.cheapest ? " · en ucuz" : ""}
                    </span>
                  ))}
                  {!p.stores.length && <span className="rounded-full bg-stone-100 px-2 py-0.5 text-stone-500 dark:bg-stone-800">fiyat okunamadı</span>}
                </div>
                <div className="mt-1 text-xs text-stone-500">{p.count} alım · son {dateTR(p.last_date)}</div>
              </div>
              <div className="shrink-0 text-right text-sm tabular-nums">
                {p.last_price != null && <div className="font-semibold">{p.last_estimated ? "≈" : ""}{tl(p.last_price)}<span className="text-xs font-normal text-stone-500">{per(p.unit)}</span></div>}
                {p.change_pct != null && (
                  <div className={cx("text-xs", p.change_pct > 0 ? "text-red-600" : p.change_pct < 0 ? "text-green-700" : "text-stone-500")}>
                    {pct(p.change_pct)}
                  </div>
                )}
              </div>
              <ChevronDown size={14} className={cx("mt-1 shrink-0 text-stone-400 transition-transform", open === p.key && "rotate-180")} />
            </button>
            {open === p.key && (
              <ul className="space-y-0.5 px-4 pb-3 text-xs text-stone-500">
                {p.history.map((h, i) => (
                  <li key={i} className="flex justify-between gap-2">
                    <span className="truncate">{dateTR(h.date)}{h.merchant && ` · ${maskText(h.merchant)}`} · <i>{h.name}</i>{h.qty !== 1 && ` · ${h.qty}${p.unit === "adet" ? " adet" : ` ${p.unit}`}`}</span>
                    <span className="shrink-0 tabular-nums">{h.price != null ? `${tl(h.price)}${per(p.unit)}` : "—"}</span>
                  </li>
                ))}
              </ul>
            )}
          </li>
        ))}
        {!all.length && !q.isLoading && (
          <li className="px-4 py-4 text-sm text-stone-500">Henüz fişten ürün yok. Fiş okuttukça burada birikir (fiş satırları bugünden itibaren saklanıyor).</li>
        )}
        {!!all.length && !items.length && <li className="px-4 py-4 text-sm text-stone-500">Aramaya uyan ürün yok.</li>}
      </ul>
    </section>
  );
}
