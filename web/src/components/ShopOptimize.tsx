// Alışveriş listesi → "Nereye gideyim?" (yetenek shopping_optimize): GET /api/shopping-list/optimize ile en iyi 1 / 2 / 3
// duraklı plan. Sonuç bir kez hesaplanır, alışveriş sırasında işaretlemek (done) planı yeniden hesaplatmaz; işaret
// listenin önbelleğinden okunur. "Yeniden hesapla" elle. Yazılar mobil (hesapkitap-mobile AlisverisListesi) ile aynı.
// Liste v3 (yetenek shopping_trips): birincil eylem "Bu planla git" (PUT /api/shopping-list/saved-plan {k}); satırlarda
// yalnız zincir adı (marka, il, şube yok); altta tek dipnot: sunucunun kaynak yazısı (demoda "Örnek veri: …"), yoksa "Fiyatlar: Market Fiyatı · dün".
// Liste v4 (her biri ayrı yetenek): headline.profit ("Bu planla ₺120 tasarruf", shop_best_value), satırda birim fiyat ve
// "En kârlı boy" önerisi (yalnız öneri; dokununca düzenle), bağlı durak rozeti "Senin seçimin" ve pinned_unpriced,
// "Marketleri ben seçeyim" (shop_store_pin; chain_options, en çok 3, ?chains=…, manual.missing), gizli mağaza ipucu.
import { useQuery } from "@tanstack/react-query";
import { Check, ChevronDown, ChevronRight, MapPinned, RefreshCw, Store, TrendingDown, X } from "lucide-react";
import { useState } from "react";
import { cx, maskText, tl, todayISO } from "../format";
import { send } from "../lib/apiError";
import type { Headline, UnpricedInfo } from "../lib/priceAdvice";
import { compareRows, headline, lineSub, lineTitle, namesLine, sourceLine, stopTitle, subline, type Optimize } from "../lib/optimize";
import { chainName, cheaperElsewhereText, ownPlaceText } from "../lib/shopList";
import { betterSizeText, MAX_PICK, missingCheapestText, optimizePath, pinnedUnpricedText, togglePick, unitPriceText, type BetterSize, type HiddenOut } from "../lib/shopStores";
import { useCap } from "./hooks";
import Segmented from "./Segmented";
import { HiddenHint } from "./ShopStores";

export const OPTIMIZE = ["shopping-optimize"] as const;
/** "₺1.200" (tam lirada kuruşsuz), "₺59,90" */
const money = (n: number) => maskText(tl(n).replace(/,00(?=\s|$)/, ""));

/** Liste v4 alanları (eski sunucuda yok) */
export interface Profit { saving: number; text: string | null; basis: string | null; size_saving: number; size_text: string | null }
type OptimizeV2 = Optimize & {
  headline?: (Headline & { profit?: Profit | null }) | null; unpriced_info?: UnpricedInfo[]; empty?: { title: string; text: string } | null;
  hidden?: HiddenOut | null; manual?: { chains: string[]; missing: { id: number; text: string; cheapest: { chain: string; price: number } }[] } | null;
  pinned?: { id: number; text: string; chain: string }[]; chain_options?: { chain: string; covered: number }[];
};
type StopV4 = { pinned?: boolean; pinned_unpriced?: { id: number; text: string }[] };
type LineV4 = { better_size?: BetterSize | null; pinned?: boolean };

/** data verilirse (yetenek shopping_headline: listenin sabit çubuğu planı zaten aldı) kendi isteğini atmaz; başlık
 *  sunucunun "Önerimiz" cümlesi, durak başına "Buradan al:" listesi, fiyatsız satırlar için ne yapmalı. */
export default function ShopOptimize({ items, ro, onTick, open: openP, data, onClose, onRefetch, fetching, onSavePlan, savingPlan, onClearPlan, onFix, initialK }: {
  items: { id: number; text: string; done: boolean }[]; ro: boolean; onTick: (id: number, done: boolean) => void;
  open?: boolean; data?: OptimizeV2 | null; onClose?: () => void; onRefetch?: () => void; fetching?: boolean;
  /** yetenek shopping_trips: "Bu planla git" (seçili durak sayısı; manuel planda seçilen marketler); kayıtlı plan varsa "Planı kaldır" */
  onSavePlan?: (k: number, chains?: string[]) => void; savingPlan?: boolean; onClearPlan?: () => void;
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
  const pinOn = useCap("shop_store_pin");
  const bestOn = useCap("shop_best_value");
  // "Marketleri ben seçeyim": seçici açık mı, seçilenler, gösterilen manuel plan (null: önerilen)
  const [picking, setPicking] = useState(false);
  const [pick, setPick] = useState<string[]>([]);
  const [manual, setManual] = useState<string[] | null>(null);
  const mq = useQuery({
    queryKey: [...OPTIMIZE, "manual", manual?.join(",") ?? ""], enabled: open && !!manual?.length, retry: false, staleTime: 60_000,
    queryFn: () => send<OptimizeV2>(optimizePath(manual)),
  });
  const base = ext ? { data: data ?? undefined, isLoading: !data, isFetching: !!fetching, error: null as Error | null, refetch: () => onRefetch?.() } : own;
  const q = manual ? { data: mq.data, isLoading: mq.isLoading, isFetching: mq.isFetching, error: mq.error as Error | null, refetch: () => void mq.refetch() } : base;
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
  const rec = base.data; // seçici her zaman önerilen planın chain_options'ı ile
  const options = pinOn ? rec?.chain_options ?? [] : [];
  const showManual = (chains: string[]) => { setManual(chains); setPicking(false); setK(null); };
  const backToRec = () => { setManual(null); setK(null); };
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
                {bestOn && r.headline.profit?.text && (
                  <div className="pt-1" data-testid="plan-kar">
                    <p className="flex items-center gap-1.5 font-semibold text-green-700 tabular-nums dark:text-green-400"><TrendingDown size={16} className="shrink-0" aria-hidden />{maskText(r.headline.profit.text)}</p>
                    {r.headline.profit.basis && <p className="text-xs text-stone-500 dark:text-stone-400">{r.headline.profit.basis}</p>}
                    {r.headline.profit.size_text && <p className="text-sm font-medium text-green-700 tabular-nums dark:text-green-400">{maskText(r.headline.profit.size_text)}</p>}
                  </div>
                )}
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
                  <span className="min-w-0 flex-1 break-words tabular-nums">{r.headline ? <><span className="block text-xs font-normal text-stone-500">Buradan al:</span>{maskText(`${chainName(s.chain)} · ${s.items.length} ürün · ${money(s.total)}`)}</> : maskText(stopTitle({ ...s, chain: chainName(s.chain) }, money))}
                    {(s as StopV4).pinned && <span className="ml-1.5 inline-block rounded bg-blue-100 px-1.5 py-0.5 align-middle text-[11px] font-semibold text-blue-800 dark:bg-blue-950 dark:text-blue-200" data-testid="senin-secimin">Senin seçimin</span>}</span>
                  <ChevronDown size={16} className="shrink-0 text-stone-500 transition-transform group-open:rotate-180" aria-hidden />
                </summary>
                {pinnedUnpricedText((s as StopV4).pinned_unpriced) && (
                  <p className="border-t border-stone-100 px-3 py-2 text-sm break-words text-amber-800 dark:border-stone-800 dark:text-amber-300" data-testid="bagli-fiyatsiz">{pinnedUnpricedText((s as StopV4).pinned_unpriced)}</p>
                )}
                <ul className="divide-y divide-stone-100 border-t border-stone-100 empty:hidden dark:divide-stone-800 dark:border-stone-800">
                  {s.items.map((l) => {
                    const d = !!done.get(l.id);
                    // birim fiyat (shop_best_value) tutarın yanında; alt yazıda yinelenmez
                    const pu = bestOn ? unitPriceText(l.unit_price_per, l.per_unit, money) : null;
                    const sub = lineSub(pu ? { ...l, unit_price_per: null } : l, today, money, r.source_info?.resmi_date ?? today);
                    const bs = bestOn ? (l as LineV4).better_size : null;
                    return (
                      <li key={l.id} className="flex items-center gap-3 px-3 py-2">
                        <input type="checkbox" className="size-5 shrink-0" checked={d} disabled={ro}
                          aria-label={`${l.text}: ${d ? "alınmadı say" : "alındı"}`} onChange={(e) => onTick(l.id, e.target.checked)} />
                        <div className="min-w-0 flex-1">
                          <div className={cx("break-words", d && "text-stone-400 line-through")}>{lineTitle(l)}</div>
                          {sub && <div className="text-xs break-words text-stone-500 tabular-nums">{maskText(sub)}</div>}
                          {l.own_place && <div className="text-xs break-words text-stone-500 tabular-nums">{maskText(ownPlaceText(l.own_place, money))}</div>}
                          {l.cheaper_elsewhere && <div className="text-xs break-words text-stone-400 tabular-nums dark:text-stone-500" data-testid="plan-disi">{maskText(cheaperElsewhereText(l.cheaper_elsewhere, money))}</div>}
                          {bs && (onFix && !ro ? (
                            <button type="button" className="mt-0.5 block text-left text-xs font-medium break-words text-green-700 tabular-nums underline-offset-2 hover:underline dark:text-green-400"
                              onClick={() => onFix(l.id)} data-testid="karli-boy">{maskText(betterSizeText(bs, money))}</button>
                          ) : <div className="mt-0.5 text-xs font-medium break-words text-green-700 tabular-nums dark:text-green-400" data-testid="karli-boy">{maskText(betterSizeText(bs, money))}</div>)}
                        </div>
                        <span className={cx("shrink-0 text-right text-sm tabular-nums", d && "text-stone-400")}>{money(l.line_total)}
                          {pu && <span className="block text-xs text-stone-500 dark:text-stone-400" data-testid="satir-birim">{pu}</span>}</span>
                      </li>
                    );
                  })}
                </ul>
              </details>
            ))}
          </div>
          {manual && !!r.manual?.missing.length && (
            <div className="rounded-lg bg-amber-50 px-3 py-2 dark:bg-amber-950/30" data-testid="secilende-yok">
              <p className="text-xs font-semibold text-amber-900 dark:text-amber-200">Seçtiğin marketlerde yok</p>
              <ul className="mt-0.5 space-y-0.5 text-sm">
                {r.manual.missing.map((x) => (
                  <li key={x.id} className="flex flex-wrap justify-between gap-x-3 break-words">
                    <span className="min-w-0 break-words">{x.text}</span>
                    <span className="text-stone-600 tabular-nums dark:text-stone-300">{maskText(missingCheapestText(x.cheapest, money))}</span>
                  </li>
                ))}
              </ul>
            </div>
          )}
          {pinOn && (manual || options.length > 1) && (
            <div className="space-y-2" data-testid="ben-secerim">
              {manual ? (
                <button type="button" className="btn-ghost border border-stone-300 text-sm dark:border-stone-700" onClick={backToRec} data-testid="onerilene-don">Önerilen plana dön</button>
              ) : !picking ? (
                <button type="button" className="btn-ghost border border-stone-300 text-sm dark:border-stone-700" onClick={() => { setPick([]); setPicking(true); }} data-testid="ben-secerim-ac">
                  <Store size={15} aria-hidden /> Marketleri ben seçeyim</button>
              ) : (
                <div className="space-y-2 rounded-lg border border-stone-200 p-3 dark:border-stone-700">
                  <div className="flex items-start gap-2">
                    <p className="min-w-0 flex-1 text-sm font-medium">Marketleri ben seçeyim <span className="font-normal text-stone-500 tabular-nums dark:text-stone-400">({pick.length}/{MAX_PICK})</span></p>
                    <button type="button" className="btn-ghost -mt-1 -mr-2 shrink-0 p-1.5" onClick={() => setPicking(false)} aria-label="Seçimi kapat"><X size={16} /></button>
                  </div>
                  <div className="flex flex-wrap gap-1.5" role="group" aria-label="Marketler (en çok 3)">
                    {options.map((o) => {
                      const on = pick.includes(o.chain);
                      const off = !on && pick.length >= MAX_PICK;
                      return (
                        <button key={o.chain} type="button" aria-pressed={on} disabled={off} onClick={() => setPick((p) => togglePick(p, o.chain))}
                          className={cx("inline-flex max-w-full items-center gap-1 rounded-full border px-3 py-1 text-left text-sm disabled:opacity-40",
                            on ? "border-green-700 bg-green-700 text-white dark:border-green-500 dark:bg-green-600"
                              : "border-stone-300 text-stone-700 hover:bg-stone-100 dark:border-stone-600 dark:text-stone-200 dark:hover:bg-stone-800")}>
                          {on && <Check size={14} className="shrink-0" aria-hidden />}<span className="min-w-0 break-words">{chainName(o.chain)}</span>
                          <span className={cx("shrink-0 text-xs tabular-nums", on ? "text-green-100" : "text-stone-500 dark:text-stone-400")} aria-label={`${o.covered} ürün`}>{o.covered}</span></button>
                      );
                    })}
                  </div>
                  <button type="button" className="btn-primary justify-center" disabled={!pick.length} onClick={() => showManual(pick)} data-testid="plani-goster">Planı göster</button>
                </div>
              )}
            </div>
          )}
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
                onClick={() => onSavePlan(plan.k, manual ?? undefined)}>Bu planla git</button>
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
          <HiddenHint count={r.hidden?.count} />
          {[namesLine("Bu planda yok", manual && r.manual ? [] : plan.missing, names), r.unpriced_info ? "" : namesLine("Fiyatı bilinmeyen", r.unpriced, names)].filter(Boolean).map((t) => (
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
