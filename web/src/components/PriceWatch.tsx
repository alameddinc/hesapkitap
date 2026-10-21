import { useQuery } from "@tanstack/react-query";
import { ChevronDown, Tag } from "lucide-react";
import { useState } from "react";
import { cx, dateTR, maskText, tl } from "../format";

interface Price {
  product: string; key: string; merchant: string | null; count: number;
  first_price: number; first_date: string; last_price: number; last_date: string; change_pct: number;
  history: { date: string; price: number; merchant: string | null }[];
}

/** Fişlerden ürün fiyat takibi: aynı ürünü kaça almıştın, şimdi kaça alıyorsun (kişisel enflasyon). */
export default function PriceWatch() {
  const q = useQuery({ queryKey: ["prices"], queryFn: () => fetch("/api/prices").then((r) => r.json() as Promise<Price[]>) });
  const [open, setOpen] = useState<string | null>(null);
  const items = q.data ?? [];
  const avg = items.length ? items.reduce((a, p) => a + p.change_pct, 0) / items.length : 0;
  return (
    <section className="card">
      <div className="p-4 pb-2">
        <h2 className="flex items-center gap-2 font-semibold"><Tag size={18} /> Fiyat takibi</h2>
        <p className="text-sm text-stone-500">
          Fiş okuttukça ürünlerin birim fiyatı kaydedilir; aynı ürünü ikinci kez aldığında burada değişimi görürsün.
          {items.length > 1 && <> Ortalama değişim: <b className={avg > 0 ? "text-red-600" : "text-green-700"}>{avg > 0 ? "+" : ""}%{avg.toFixed(1)}</b></>}
        </p>
      </div>
      <ul className="divide-y divide-stone-100 dark:divide-stone-800">
        {items.map((p) => (
          <li key={p.key}>
            <button className="flex w-full items-center gap-3 px-4 py-2.5 text-left" onClick={() => setOpen(open === p.key ? null : p.key)}>
              <div className="min-w-0 flex-1">
                <div className="truncate font-medium">{p.product}</div>
                <div className="text-xs text-stone-500">{p.merchant && `${maskText(p.merchant)} · `}{p.count} alım · {dateTR(p.first_date)} → {dateTR(p.last_date)}</div>
              </div>
              <div className="text-right text-sm tabular-nums">
                <div>{tl(p.first_price)} → <b>{tl(p.last_price)}</b></div>
                <div className={cx("text-xs", p.change_pct > 0 ? "text-red-600" : p.change_pct < 0 ? "text-green-700" : "text-stone-500")}>
                  {p.change_pct > 0 ? "+" : ""}%{p.change_pct}
                </div>
              </div>
              <ChevronDown size={14} className={cx("text-stone-400 transition-transform", open === p.key && "rotate-180")} />
            </button>
            {open === p.key && (
              <ul className="px-4 pb-3 text-xs text-stone-500">
                {p.history.map((h, i) => (
                  <li key={i} className="flex justify-between"><span>{dateTR(h.date)}{h.merchant && ` · ${maskText(h.merchant)}`}</span><span className="tabular-nums">{tl(h.price)}</span></li>
                ))}
              </ul>
            )}
          </li>
        ))}
        {!items.length && !q.isLoading && (
          <li className="px-4 py-4 text-sm text-stone-500">Henüz aynı ürünü iki kez fişle almadın. Fiş okuttukça dolacak.</li>
        )}
      </ul>
    </section>
  );
}
