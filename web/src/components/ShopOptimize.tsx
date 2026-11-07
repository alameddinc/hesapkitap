// Alışveriş listesi → "Nereye gideyim?" (yetenek shopping_optimize): GET /api/shopping-list/optimize ile en iyi 1 / 2 / 3
// duraklı plan. Sonuç bir kez hesaplanır, alışveriş sırasında işaretlemek (done) planı yeniden hesaplatmaz; işaret
// listenin önbelleğinden okunur. "Yeniden hesapla" elle. Yazılar mobil (hesapkitap-mobile AlisverisListesi) ile aynı.
// Liste v3 (yetenek shopping_trips): birincil eylem "Bu planla git" (PUT /api/shopping-list/saved-plan {k}); satırlarda
// yalnız zincir adı (marka, il, şube yok); altta tek dipnot: sunucunun kaynak yazısı (demoda "Örnek veri: …"), yoksa "Fiyatlar: Market Fiyatı · dün".
import { useQuery } from "@tanstack/react-query";
import { ChevronDown, ChevronRight, MapPinned, RefreshCw, X } from "lucide-react";
import { useState } from "react";
import { cx, maskText, tl, todayISO } from "../format";
import { send } from "../lib/apiError";
import type { Headline, UnpricedInfo } from "../lib/priceAdvice";
import { compareRows, headline, lineSub, lineTitle, namesLine, sourceLine, stopTitle, subline, type Optimize } from "../lib/optimize";
import { chainName } from "../lib/shopList";
import Segmented from "./Segmented";

export const OPTIMIZE = ["shopping-optimize"] as const;
/** "₺1.200" (tam lirada kuruşsuz), "₺59,90" */
const money = (n: number) => maskText(tl(n).replace(/,00(?=\s|$)/, ""));

type OptimizeV2 = Optimize & { headline?: Headline | null; unpriced_info?: UnpricedInfo[]; empty?: { title: string; text: string } | null };

/** data verilirse (yetenek shopping_headline: listenin sabit çubuğu planı zaten aldı) kendi isteğini atmaz; başlık
 *  sunucunun "Önerimiz" cümlesi, durak başına "Buradan al:" listesi, fiyatsız satırlar için ne yapmalı. */
export default function ShopOptimize({ items, ro, onTick, open: openP, data, onClose, onRefetch, fetching, onSavePlan, savingPlan, onClearPlan, onFix, initialK }: {
  items: { id: number; text: string; done: boolean }[]; ro: boolean; onTick: (id: number, done: boolean) => void;
  open?: boolean; data?: OptimizeV2 | null; onClose?: () => void; onRefetch?: () => void; fetching?: boolean;
  /** yetenek shopping_trips: "Bu planla git" (seçili durak sayısı); kayıtlı plan varsa "Planı kaldır" */
  onSavePlan?: (k: number) => void; savingPlan?: boolean; onClearPlan?: () => void;
  /** fiyatsız satıra dokununca düzelt penceresi */
  onFix?: (id: number) => void;
  /** kayıtlı planın durak sayısı: açılınca o seçili */
  initialK?: number | null;
}) {
  const [openS, setOpen] = useState(false);
  const open = openP ?? openS;
  const [k, setK] = useState<number | null>(initialK ?? null);
  const ext = openP !== undefined;
  const own = useQuery({
    queryKey: OPTIMIZE, enabled: open && !ext, retry: false, staleTime: Infinity, gcTime: 30 * 60_000,
    queryFn: () => send<OptimizeV2>("/api/shopping-list/optimize"),
  });
  const q = ext ? { data: data ?? undefined, isLoading: !data, isFetching: !!fetching, error: null as Error | null, refetch: () => onRefetch?.() } : own;
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
  // sunucunun kaynak yazısı dipnotta yazıyorsa ipucunda yinelenmez (yalnız ötekiler)
  const srcExtra = (r?.sources ?? []).filter((x) => !src.startsWith(x)).join(" ");
  // sunucunun başlığı önerilen plan içindir; başka durak sayısı seçilince istemci başlığı
  const useHead = !!r?.headline && (k == null || k === r.recommended_k);

  return (
    <section className="card space-y-3 p-4" data-testid="optimum" aria-busy={q.isFetching}>
      {q.isLoading && <p className="text-sm text-stone-500">Hesaplanıyor…</p>}
      {q.error && <p className="text-sm text-red-600 dark:text-red-400" role="alert">{(q.error as Error).message}</p>}
      {r && !plan && (r.empty ? <div><p className="font-semibold">{r.empty.title}</p><p className="text-sm text-stone-500">{r.empty.text}</p></div>
        : <p className="text-sm text-stone-500">Listede fiyatını bildiğimiz ürün yok.</p>)}
      {r && plan && (
        <>
          <div className="flex items-start gap-2">
            <div className="min-w-0 flex-1 space-y-0.5">
              {useHead && r.headline ? <>
                <h2 className="text-lg font-semibold break-words tabular-nums" data-testid="optimum-baslik">{maskText(r.headline.title)}</h2>
                {r.headline.subtitle && <p className="text-sm font-medium text-green-700 dark:text-green-400">{maskText(r.headline.subtitle)}</p>}
              </> : <>
                <h2 className="text-lg font-semibold break-words tabular-nums" data-testid="optimum-baslik">{maskText(headline(plan, r.recommended_k, money))}</h2>
                {subline(plan, money) && <p className="text-sm font-medium text-green-700 dark:text-green-400">{subline(plan, money)}</p>}
              </>}
            </div>
            {onClose && <button type="button" className="btn-ghost -mt-1 -mr-2 shrink-0 p-2" onClick={onClose} aria-label="Planı kapat"><X size={18} /></button>}
          </div>
          {r.plans.length > 1 && (
            <Segmented label="Durak sayısı" value={String(plan.k)} onChange={(v) => setK(Number(v))}
              options={r.plans.map((p) => ({ value: String(p.k), label: `${p.k} durak` }))} />
          )}
          <div className="space-y-2">
            {plan.stops.map((s) => (
              <details key={s.chain} open className="group rounded-lg border border-stone-200 dark:border-stone-700" data-testid="durak">
                <summary className="flex cursor-pointer list-none items-center gap-2 px-3 py-2.5 font-medium [&::-webkit-details-marker]:hidden">
                  <span className="min-w-0 flex-1 break-words tabular-nums">{r.headline ? <><span className="block text-xs font-normal text-stone-500">Buradan al:</span>{maskText(`${chainName(s.chain)} · ${s.items.length} ürün · ${money(s.total)}`)}</> : maskText(stopTitle({ ...s, chain: chainName(s.chain) }, money))}</span>
                  <ChevronDown size={16} className="shrink-0 text-stone-500 transition-transform group-open:rotate-180" aria-hidden />
                </summary>
                <ul className="divide-y divide-stone-100 border-t border-stone-100 dark:divide-stone-800 dark:border-stone-800">
                  {s.items.map((l) => {
                    const d = !!done.get(l.id);
                    const sub = lineSub(l, today, money, r.source_info?.resmi_date ?? today);
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
          {onSavePlan && !ro && (
            <div className="flex flex-wrap items-center gap-2 pt-1">
              <button type="button" className="btn-primary flex-[1_1_12rem] justify-center" data-testid="planla-git" disabled={savingPlan || q.isFetching}
                onClick={() => onSavePlan(plan.k)}>Bu planla git</button>
              {onClearPlan && <button type="button" className="btn-ghost text-sm" onClick={onClearPlan}>Planı kaldır</button>}
            </div>
          )}
          {!!r.unpriced_info?.length && (
            <div className="rounded-lg bg-stone-50 px-3 py-2 dark:bg-stone-800/60" data-testid="fiyatsiz">
              <p className="text-xs font-semibold text-stone-600 dark:text-stone-300">Fiyatını bulamadıklarımız</p>
              <ul className="mt-0.5 space-y-0.5 text-sm">
                {r.unpriced_info.map((u) => (
                  <li key={u.id} className="break-words">
                    {onFix && !ro ? (
                      <button type="button" className="-mx-1 flex w-full items-start gap-1 rounded px-1 py-0.5 text-left hover:bg-stone-100 dark:hover:bg-stone-700/60"
                        onClick={() => onFix(u.id)} aria-label={`${u.text}: düzelt`}>
                        <span className="min-w-0 flex-1"><b className="font-medium">{u.text}</b> <span className="text-stone-500 dark:text-stone-400">— {u.hint}</span></span>
                        <ChevronRight size={16} className="mt-0.5 shrink-0 text-stone-400" aria-hidden />
                      </button>
                    ) : <><b className="font-medium">{u.text}</b> <span className="text-stone-500">— {u.hint}</span></>}
                  </li>
                ))}
              </ul>
            </div>
          )}
          {[namesLine("Bu planda yok", plan.missing, names), r.unpriced_info ? "" : namesLine("Fiyatı bilinmeyen", r.unpriced, names)].filter(Boolean).map((t) => (
            <p key={t} className="text-sm break-words text-stone-600 dark:text-stone-300">{t}</p>
          ))}
        </>
      )}
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1 border-t border-stone-100 pt-2 dark:border-stone-800">
        {!q.isLoading && (
          <button type="button" className="btn-ghost -ml-2 px-2 py-1 text-sm" disabled={q.isFetching} onClick={() => { setK(null); void q.refetch(); }}>
            <RefreshCw size={14} className={cx(q.isFetching && "animate-spin")} aria-hidden /> Yeniden hesapla</button>
        )}
        {/* tek dipnot; yasal kaynak yazısı ipucunda (ekran okuyucu için de sr-only) */}
        {r && plan && src && (
          <p className="ml-auto text-xs text-stone-500 dark:text-stone-400" data-testid="optimum-kaynak" title={srcExtra || undefined}>
            {src}{srcExtra && <span className="sr-only"> ({srcExtra})</span>}</p>
        )}
      </div>
    </section>
  );
}
