// Alışveriş listesi → "Nereye gideyim?" (yetenek shopping_optimize): GET /api/shopping-list/optimize ile en iyi 1 / 2 / 3
// duraklı plan. Sonuç bir kez hesaplanır, alışveriş sırasında işaretlemek (done) planı yeniden hesaplatmaz; işaret
// listenin önbelleğinden okunur. "Yeniden hesapla" elle. Yazılar mobil (hesapkitap-mobile AlisverisListesi) ile aynı.
import { useQuery } from "@tanstack/react-query";
import { ChevronDown, MapPinned, RefreshCw } from "lucide-react";
import { useState } from "react";
import { cx, maskText, tl, todayISO } from "../format";
import { send } from "../lib/apiError";
import { compareRows, headline, lineSub, lineTitle, namesLine, sourceLine, stopTitle, subline, type Optimize } from "../lib/optimize";
import Segmented from "./Segmented";

export const OPTIMIZE = ["shopping-optimize"] as const;
/** "₺1.200" (tam lirada kuruşsuz), "₺59,90" */
const money = (n: number) => maskText(tl(n).replace(/,00(?=\s|$)/, ""));

export default function ShopOptimize({ items, ro, onTick }: {
  items: { id: number; text: string; done: boolean }[]; ro: boolean; onTick: (id: number, done: boolean) => void;
}) {
  const [open, setOpen] = useState(false);
  const [k, setK] = useState<number | null>(null);
  const q = useQuery({
    queryKey: OPTIMIZE, enabled: open, retry: false, staleTime: Infinity, gcTime: 30 * 60_000,
    queryFn: () => send<Optimize>("/api/shopping-list/optimize"),
  });
  const today = todayISO();

  if (!open) {
    return (
      <section className="card flex flex-wrap items-center gap-3 border-green-200 bg-green-50/60 p-4 dark:border-green-900 dark:bg-green-950/30" data-testid="nereye-gideyim">
        <MapPinned size={24} className="shrink-0 text-green-700 dark:text-green-400" aria-hidden />
        <div className="min-w-0 flex-[1_1_12rem]">
          <h2 className="font-semibold">Nereye gideyim?</h2>
          <p className="text-sm text-stone-600 dark:text-stone-300">Listeni en ucuza hangi marketlerden alırsın, hesaplayalım</p>
        </div>
        <button type="button" className="btn-primary justify-center" onClick={() => setOpen(true)}>Hesapla</button>
      </section>
    );
  }

  const r = q.data;
  const plan = r?.plans.find((p) => p.k === (k ?? r.recommended_k)) ?? r?.plans[0];
  const done = new Map(items.map((i) => [i.id, i.done]));
  const names = new Map(items.map((i) => [i.id, i.text]));
  const src = r ? sourceLine(r, today) : "";

  return (
    <section className="card space-y-3 p-4" data-testid="optimum" aria-busy={q.isFetching}>
      {q.isLoading && <p className="text-sm text-stone-500">Hesaplanıyor…</p>}
      {q.error && <p className="text-sm text-red-600 dark:text-red-400" role="alert">{(q.error as Error).message}</p>}
      {r && !plan && <p className="text-sm text-stone-500">Listede fiyatını bildiğimiz ürün yok.</p>}
      {r && plan && (
        <>
          <div className="space-y-0.5">
            <h2 className="text-lg font-semibold break-words tabular-nums" data-testid="optimum-baslik">{maskText(headline(plan, r.recommended_k, money))}</h2>
            {subline(plan, money) && <p className="text-sm font-medium text-green-700 dark:text-green-400">{subline(plan, money)}</p>}
          </div>
          {r.plans.length > 1 && (
            <Segmented label="Durak sayısı" value={String(plan.k)} onChange={(v) => setK(Number(v))}
              options={r.plans.map((p) => ({ value: String(p.k), label: `${p.k} durak` }))} />
          )}
          <div className="space-y-2">
            {plan.stops.map((s) => (
              <details key={s.chain} open className="group rounded-lg border border-stone-200 dark:border-stone-700" data-testid="durak">
                <summary className="flex cursor-pointer list-none items-center gap-2 px-3 py-2.5 font-medium [&::-webkit-details-marker]:hidden">
                  <span className="min-w-0 flex-1 break-words tabular-nums">{maskText(stopTitle(s, money))}</span>
                  <ChevronDown size={16} className="shrink-0 text-stone-500 transition-transform group-open:rotate-180" aria-hidden />
                </summary>
                <ul className="divide-y divide-stone-100 border-t border-stone-100 dark:divide-stone-800 dark:border-stone-800">
                  {s.items.map((l) => {
                    const d = !!done.get(l.id);
                    const sub = lineSub(l, today, money);
                    return (
                      <li key={l.id} className="flex items-center gap-3 px-3 py-2">
                        <input type="checkbox" className="size-5 shrink-0" checked={d} disabled={ro}
                          aria-label={`${l.text}: ${d ? "alınmadı say" : "alındı"}`} onChange={(e) => onTick(l.id, e.target.checked)} />
                        <div className="min-w-0 flex-1">
                          <div className={cx("break-words", d && "text-stone-400 line-through")}>{lineTitle(l)}</div>
                          {sub && <div className="text-xs break-words text-stone-500 tabular-nums">{maskText(sub)}</div>}
                        </div>
                        <span className={cx("shrink-0 text-sm tabular-nums", d && "text-stone-400")}>{money(l.line_total)}</span>
                      </li>
                    );
                  })}
                </ul>
              </details>
            ))}
          </div>
          {compareRows(r, money).length > 0 && (
            <div>
              <h3 className="mb-1 text-xs font-semibold tracking-wide text-stone-500 uppercase">Karşılaştır</h3>
              <ul className="space-y-1 text-sm text-stone-700 tabular-nums dark:text-stone-300" data-testid="karsilastir">
                {compareRows(r, money).map((t) => <li key={t} className="break-words">{maskText(t)}</li>)}
              </ul>
            </div>
          )}
          {[namesLine("Bu planda yok", plan.missing, names), namesLine("Fiyatı bilinmeyen", r.unpriced, names)].filter(Boolean).map((t) => (
            <p key={t} className="text-sm break-words text-stone-600 dark:text-stone-300">{t}</p>
          ))}
          <div className="space-y-0.5 border-t border-stone-100 pt-2 dark:border-stone-800">
            {src && <p className="text-xs text-stone-500" data-testid="optimum-kaynak">{src}</p>}
            {r.sources.map((a) => <p key={a} className="text-[11px] text-stone-400 dark:text-stone-500">{a}</p>)}
          </div>
        </>
      )}
      {!q.isLoading && (
        <button type="button" className="btn-ghost px-2 py-1 text-sm" disabled={q.isFetching} onClick={() => { setK(null); void q.refetch(); }}>
          <RefreshCw size={14} className={cx(q.isFetching && "animate-spin")} aria-hidden /> Yeniden hesapla</button>
      )}
    </section>
  );
}
