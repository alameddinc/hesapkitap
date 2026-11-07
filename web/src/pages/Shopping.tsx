// Alışveriş listesi (yetenek shopping_list; paket satırı alisveris_listesi): serbest yazılan ürünler, adet, alındı
// işareti, silme. Ortak defterde ortaktır: ekleyenin baş harfi görünür, "Sadece görür" yetkisinde liste salt okunur.
// Fiyat: GET /api/shopping-list/plan → ürün başına en ucuz market ve "Hepsini tek yerden" özeti. Değişiklikler önce
// ekranda olur (iyimser), sunucu reddederse geri alınır. Üstte "Nereye gideyim?" (yetenek shopping_optimize,
// components/ShopOptimize.tsx): en iyi 1 / 2 / 3 duraklı plan. Mobil karşılığı: hesapkitap-mobile src/screens/Alisveris.tsx.
// Fiyat UX v2 (yetenek shopping_assumed_size / shopping_headline): satırın altında "En ucuz: BİM ₺32,50 · 1 L", boysuz
// satırda "1 L kabul edildi" (dokun → boy seç), fiyatsız satırda ne yapmalı; altta sabit "Nereye gideyim?" çubuğu planı açar.
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ChevronRight, Info, MapPinned, Minus, Plus, ShoppingCart, Trash2 } from "lucide-react";
import { useRef, useState } from "react";
import Modal from "../components/Modal";
import { assumedText, barMain, bestInline, sizeOf, type Assumed, type Headline, type ShopBest, type UnpricedInfo } from "../lib/priceAdvice";
import type { Optimize } from "../lib/optimize";
import { cx, maskText, tl, todayISO } from "../format";
import { ApiError, bodyOf, failureText, send } from "../lib/apiError";
import { agoWord, bestChain, type PlanChain } from "../lib/priceView";
import { useUI } from "../ui";
import { isViewer, useBook, useMeta, usePeople } from "../components/hooks";
import { PlanGate } from "../components/Plan";
import ShopOptimize, { OPTIMIZE } from "../components/ShopOptimize";
import { AddedBy } from "../components/TxnList";

export interface ShopItem {
  id: number; text: string; group_key: string | null; qty: number | null; done: boolean; added_by: number | null; created_at: string;
  group?: { key: string; label: string; pending?: boolean } | null;
  /** yetenek shopping_assumed_size: boysuz satırın varsayılan boyu; satırın en ucuz teklifi */
  assumed?: Assumed | null; best?: ShopBest | null;
}
interface SizeOpt { key: string; label: string; size_label: string; chain: string | null; price: number | null; unit_price_per?: number | null; per_unit?: string | null }
type OptimizeV2 = Optimize & { headline?: Headline | null; unpriced_info?: UnpricedInfo[]; empty?: { title: string; text: string } | null };
interface Best { chain: string; price: number; brand?: string | null; source: "own" | "community" | "resmi"; date?: string | null }
interface ShopPlan { items: { id: number; best: Best | null }[]; chains: PlanChain[] }

const LIST = ["shopping-list"] as const;
const PLAN = ["shopping-plan"] as const;
const BASE = "/api/shopping-list";

const fetchList = () => send<ShopItem[] | { items: ShopItem[] }>(BASE).then((d) => (Array.isArray(d) ? d : d.items ?? []));
async function remove(id: number) {
  let r: Response;
  try { r = await fetch(`${BASE}/${id}`, { method: "DELETE" }); } catch { throw new ApiError("Bağlantı yok; tekrar dene.", 0); }
  if (!r.ok) { const b = await bodyOf(r); throw new ApiError(failureText(r.status, b), r.status, b); }
}
/** "~₺412" (kuruşsuz) */
const approx = (n: number) => `~${tl(Math.round(n)).replace(/,00(?=\s|$)/, "")}`;

export default function Shopping() {
  return (
    <div className="space-y-4">
      <div>
        <h1 className="text-xl font-semibold">Alışveriş listesi</h1>
        <p className="text-sm text-stone-500">Alacaklarını yaz; fişlerinden ve topluluk fiyatlarından en ucuz nerede olduğunu gösteririz.</p>
      </div>
      <PlanGate feature="alisveris_listesi" title="Alışveriş listesi"><ShoppingList /></PlanGate>
    </div>
  );
}

function ShoppingList() {
  const ui = useUI();
  const qc = useQueryClient();
  const book = useBook();
  const people = usePeople();
  const ro = isViewer(book.data);
  const caps = useMeta().data?.capabilities;
  const optimizeOn = !!caps?.includes("shopping_optimize");
  const sizesOn = !!caps?.includes("shopping_assumed_size");
  const barOn = optimizeOn && !!caps?.includes("shopping_headline");
  const today = todayISO();
  const list = useQuery({ queryKey: LIST, queryFn: fetchList });
  const plan = useQuery({ queryKey: PLAN, queryFn: () => send<ShopPlan>(`${BASE}/plan`), retry: false, enabled: !!list.data?.length });
  const [text, setText] = useState("");
  const [sizeFor, setSizeFor] = useState<ShopItem | null>(null);
  const [planOpen, setPlanOpen] = useState(false);
  const planRef = useRef<HTMLDivElement>(null);
  // çubuk: liste değişince (ekle / sil / adet / boy) yeniden hesaplanır; işaretlemek (alındı) planı değiştirmez
  const sig = (list.data ?? []).filter((i) => i.id > 0).map((i) => `${i.id}:${i.qty ?? 1}:${i.group_key ?? ""}`).join(",");
  const opt = useQuery({
    queryKey: [...OPTIMIZE, sig], enabled: barOn && !!list.data?.some((i) => !i.done && i.id > 0), retry: false, staleTime: Infinity, gcTime: 30 * 60_000,
    queryFn: () => send<OptimizeV2>("/api/shopping-list/optimize"),
  });
  const [qty, setQty] = useState("1");

  // iyimser değişiklik: önceki listeyi sakla, hata olursa ona dön; sonunda iki sorgu da sunucudan tazelenir
  const optimistic = <A,>(fn: (a: A) => Promise<unknown>, apply: (items: ShopItem[], a: A) => ShopItem[]) => ({
    mutationFn: fn,
    onMutate: async (a: A) => {
      await qc.cancelQueries({ queryKey: LIST });
      const prev = qc.getQueryData<ShopItem[]>(LIST);
      qc.setQueryData<ShopItem[]>(LIST, (cur) => apply(cur ?? [], a));
      return { prev };
    },
    onError: (e: Error, _a: A, ctx?: { prev?: ShopItem[] }) => { if (ctx?.prev) qc.setQueryData(LIST, ctx.prev); ui.error(e.message); },
    onSettled: () => { void qc.invalidateQueries({ queryKey: LIST }); void qc.invalidateQueries({ queryKey: PLAN }); },
  });
  const add = useMutation(optimistic(
    (a: { text: string; qty: number; tmp: number; at: string }) => send<ShopItem>(BASE, { method: "POST", json: { text: a.text, qty: a.qty } }),
    (items, a) => [...items, { id: a.tmp, text: a.text, qty: a.qty, group_key: null, done: false, added_by: people.meId ?? null, created_at: a.at }],
  ));
  const patch = useMutation(optimistic(
    (a: { id: number; done?: boolean; qty?: number; group_key?: string | null }) => { const { id, ...body } = a; return send<ShopItem>(`${BASE}/${id}`, { method: "PATCH", json: body }); },
    (items, a) => items.map((it) => (it.id === a.id ? { ...it, ...(a.done !== undefined && { done: a.done }), ...(a.qty !== undefined && { qty: a.qty }), ...(a.group_key !== undefined && { group_key: a.group_key }) } : it)),
  ));
  // "Alınanları temizle": tek tek, sırayla (biri düşerse kalanlar listede kalır, liste sunucudan tazelenir)
  const del = useMutation(optimistic(async (ids: number[]) => { for (const id of ids) await remove(id); },
    (items, ids) => items.filter((it) => !ids.includes(it.id))));

  const submit = (e: React.FormEvent) => {
    e.preventDefault();
    const t = text.trim();
    if (!t) return;
    const n = Math.max(1, Math.round(Number(qty.replace(",", ".")) || 1));
    const now = new Date();
    add.mutate({ text: t, qty: n, tmp: -now.getTime(), at: now.toISOString() });
    setText("");
    setQty("1");
  };

  const items = list.data ?? [];
  const open = items.filter((i) => !i.done), done = items.filter((i) => i.done);
  const bestOf = new Map((plan.data?.items ?? []).map((x) => [x.id, x.best]));
  const top = barOn ? null : bestChain(plan.data?.chains);
  const r = opt.data;
  const hints = new Map((r?.unpriced_info ?? []).map((u) => [u.id, u]));
  const money = (n: number) => maskText(tl(n).replace(/,00(?=\s|$)/, ""));
  const openPlan = () => { setPlanOpen(true); requestAnimationFrame(() => planRef.current?.scrollIntoView?.({ block: "start", behavior: "smooth" })); };

  // fiyatlı ürün varsa görünür; alışverişte hepsi işaretlenince de kaybolmasın (sonuç ekranda kalsın)
  const priced = (plan.data?.items ?? []).some((x) => x.best && open.some((o) => o.id === x.id)) || !!r?.priced;
  const [optShown, setOptShown] = useState(false);
  if (priced && !optShown) setOptShown(true);

  return (
    <div className="space-y-3">
      {optimizeOn && (barOn ? planOpen : optShown) && items.length > 0 && (
        <div ref={planRef} className="scroll-mt-16">
          <ShopOptimize items={items} ro={ro} onTick={(id, d) => patch.mutate({ id, done: d })} open={barOn ? true : undefined} data={barOn ? r : undefined}
            onClose={barOn ? () => setPlanOpen(false) : undefined} onRefetch={barOn ? () => void opt.refetch() : undefined} fetching={barOn ? opt.isFetching : undefined} />
        </div>
      )}
      {top && open.length > 0 && (
        <section className="card flex items-center gap-3 p-4" data-testid="tek-yerden">
          <ShoppingCart size={20} className="shrink-0 text-green-700 dark:text-green-400" aria-hidden />
          <p className="min-w-0 text-sm">
            Hepsini tek yerden: <b className="tabular-nums">{maskText(top.chain)} {approx(top.total)}</b>
            {top.missing > 0 && <span className="text-stone-500"> ({top.missing} ürün fiyatsız)</span>}
          </p>
        </section>
      )}

      {ro ? (
        <p className="rounded-lg bg-stone-100 px-3 py-2 text-xs text-stone-600 dark:bg-stone-800 dark:text-stone-300">Bu defterde sadece görüntüleme yetkin var; listeyi değiştiremezsin.</p>
      ) : (
        <form className="card flex flex-wrap gap-2 p-3" onSubmit={submit}>
          <label className="sr-only" htmlFor="al-text">Ürün</label>
          <input id="al-text" className="input min-w-0 flex-[1_1_12rem]" placeholder="Süt, yumurta 30'lu, deterjan…" value={text}
            onChange={(e) => setText(e.target.value)} maxLength={120} autoComplete="off" />
          <label className="sr-only" htmlFor="al-qty">Adet</label>
          <input id="al-qty" className="input w-16 text-center tabular-nums" inputMode="numeric" value={qty} onChange={(e) => setQty(e.target.value)} aria-label="Adet" />
          <button className="btn-primary justify-center" disabled={!text.trim()}><Plus size={16} aria-hidden /> Ekle</button>
        </form>
      )}

      {list.isLoading && <p className="text-sm text-stone-500">Yükleniyor…</p>}
      {list.error && <p className="text-sm text-red-600 dark:text-red-400" role="alert">{(list.error as Error).message}</p>}
      {list.data && !items.length && (
        <section className="card space-y-1 p-6 text-center">
          <ShoppingCart size={28} className="mx-auto text-stone-400" aria-hidden />
          <h2 className="font-semibold">Liste boş</h2>
          <p className="text-sm text-stone-500">{ro ? "Defterde henüz alınacak bir şey yok." : "Yukarıya alacaklarını yaz: “süt”, “yumurta 30'lu”…"}</p>
        </section>
      )}

      {items.length > 0 && (
        <ul className="card divide-y divide-stone-100 dark:divide-stone-800" aria-label="Alınacaklar">
          {[...open, ...done].map((it) => {
            const best = bestOf.get(it.id);
            const who = people.multi ? people.person(it.added_by) : undefined;
            const q = it.qty ?? 1;
            return (
              <li key={it.id} className="flex items-center gap-3 px-4 py-2.5">
                <input type="checkbox" className="size-5 shrink-0" checked={it.done} disabled={ro || it.id < 0}
                  aria-label={`${it.text}: ${it.done ? "alınmadı say" : "alındı"}`} onChange={(e) => patch.mutate({ id: it.id, done: e.target.checked })} />
                <div className="min-w-0 flex-1">
                  <div className="flex items-center gap-1.5">
                    <span className={cx("min-w-0 break-words", it.done && "text-stone-400 line-through")}>{it.text}</span>
                    {who && <AddedBy name={people.isMe(it.added_by) ? null : who.name} initial={who.name} />}
                  </div>
                  {!it.done && sizesOn && it.best !== undefined ? (
                    <ItemPrice it={it} hint={hints.get(it.id)} ro={ro} money={money} onSize={() => setSizeFor(it)} />
                  ) : best && !it.done && (
                    <div className="text-xs text-stone-500 tabular-nums">en ucuz: {maskText(best.chain)} {tl(best.price)}{best.brand ? ` (${best.brand})` : ""}{best.source === "community" ? " · topluluk" : ""}{best.source === "resmi" && best.date && best.date < today ? ` · ${agoWord(best.date, today)}` : ""}</div>
                  )}
                </div>
                {ro ? (q > 1 && <span className="shrink-0 text-sm text-stone-500 tabular-nums">×{q}</span>) : (
                  <div className="flex shrink-0 items-center" role="group" aria-label={`${it.text} adedi`}>
                    <button type="button" className="btn-ghost p-1.5" disabled={q <= 1 || it.id < 0} aria-label="Bir azalt" onClick={() => patch.mutate({ id: it.id, qty: q - 1 })}><Minus size={14} /></button>
                    <span className="w-6 text-center text-sm tabular-nums" aria-live="polite">{q}</span>
                    <button type="button" className="btn-ghost p-1.5" disabled={it.id < 0} aria-label="Bir artır" onClick={() => patch.mutate({ id: it.id, qty: q + 1 })}><Plus size={14} /></button>
                  </div>
                )}
                {!ro && (
                  <button type="button" className="btn-ghost shrink-0 p-1.5 text-stone-500" disabled={it.id < 0} aria-label={`${it.text}: sil`}
                    onClick={() => del.mutate([it.id])}><Trash2 size={16} /></button>
                )}
              </li>
            );
          })}
        </ul>
      )}
      {barOn && open.length > 0 && r && !planOpen && (r.headline ? (
        <div className="sticky bottom-[calc(4.75rem+var(--sab))] z-30 md:bottom-4">
          <button type="button" onClick={openPlan} data-testid="nereye-cubuk"
            className="flex w-full items-center gap-3 rounded-2xl bg-green-700 px-4 py-3 text-left text-white shadow-lg shadow-green-900/20 transition active:scale-[0.99] dark:bg-green-600 dark:text-white">
            <MapPinned size={22} className="shrink-0" aria-hidden />
            <span className="min-w-0 flex-1">
              <span className="block text-xs font-medium text-green-100">Nereye gideyim?</span>
              <span className="block font-semibold break-words tabular-nums">{maskText(barMain(r.headline, money))}</span>
            </span>
            <ChevronRight size={20} className="shrink-0" aria-hidden />
          </button>
        </div>
      ) : r.empty && (
        <section className="card flex gap-3 p-4" data-testid="fiyat-yok">
          <Info size={20} className="mt-0.5 shrink-0 text-stone-400" aria-hidden />
          <div className="min-w-0"><h2 className="font-semibold">{r.empty.title}</h2><p className="text-sm text-stone-600 dark:text-stone-300">{r.empty.text}</p></div>
        </section>
      ))}
      {sizeFor && <SizePicker it={sizeFor} money={money} onClose={() => setSizeFor(null)}
        onPick={(key) => { patch.mutate({ id: sizeFor.id, group_key: key }); setSizeFor(null); }} />}
      {!ro && done.length > 0 && (
        <button type="button" className="btn-ghost text-sm" disabled={del.isPending} onClick={() => del.mutate(done.map((d) => d.id))}>
          <Trash2 size={14} aria-hidden /> Alınanları temizle ({done.length})</button>
      )}
    </div>
  );
}

/** Satır altı: en ucuz teklif ve varsayılan boy ("1 L kabul edildi" — dokununca boy seçilir) ya da fiyatsızsa ne yapmalı. */
function ItemPrice({ it, hint, ro, money, onSize }: { it: ShopItem; hint?: UnpricedInfo; ro: boolean; money: (n: number) => string; onSize: () => void }) {
  const size = it.assumed?.size_label ?? sizeOf(it.group?.label);
  return (
    <div className="flex flex-wrap items-center gap-x-2 gap-y-0.5 text-xs">
      {it.best ? (
        <span className="font-medium text-green-700 tabular-nums dark:text-green-400" data-testid="satir-en-ucuz">{maskText(bestInline(it.best, size, money))}</span>
      ) : hint ? <span className="text-stone-500" data-testid="satir-ipucu">{hint.hint}</span> : null}
      {it.assumed && (ro ? <span className="text-stone-500">{assumedText(it.assumed)}</span> : (
        <button type="button" onClick={onSize} data-testid="boy-sec"
          className="rounded-full border border-stone-200 px-2 py-0.5 text-stone-600 hover:bg-stone-100 dark:border-stone-700 dark:text-stone-300 dark:hover:bg-stone-800"
          aria-label={`${it.text}: ${assumedText(it.assumed)}, boyu değiştir`}>{assumedText(it.assumed)} · değiştir</button>
      ))}
    </div>
  );
}

/** Boy seç: GET /api/shopping-list/{id}/sizes; seçilince PATCH group_key ("Varsayılana dön": null). */
function SizePicker({ it, money, onClose, onPick }: { it: ShopItem; money: (n: number) => string; onClose: () => void; onPick: (key: string | null) => void }) {
  const q = useQuery({ queryKey: ["shopping-sizes", it.id], retry: false, queryFn: () => send<SizeOpt[]>(`${BASE}/${it.id}/sizes`) });
  const cur = it.group?.key ?? it.group_key;
  return (
    <Modal title={`${it.text}: boy seç`} onClose={onClose}>
      <div className="space-y-2">
        <p className="text-sm text-stone-500">Fiyatı bu boya göre karşılaştırırız.</p>
        {q.isLoading && <p className="text-sm text-stone-500">Yükleniyor…</p>}
        {q.error && <p className="text-sm text-red-600 dark:text-red-400" role="alert">{(q.error as Error).message}</p>}
        {q.data && !q.data.length && <p className="text-sm text-stone-500">Bu ürünün başka boyu yok.</p>}
        <ul className="divide-y divide-stone-100 rounded-lg border border-stone-200 dark:divide-stone-800 dark:border-stone-700">
          {q.data?.map((o) => (
            <li key={o.key}>
              <button type="button" onClick={() => onPick(o.key)} aria-pressed={o.key === cur}
                className={cx("flex w-full items-center gap-3 px-3 py-2.5 text-left hover:bg-stone-50 dark:hover:bg-stone-800/60", o.key === cur && "bg-green-50/70 dark:bg-green-950/30")}>
                <span className="min-w-0 flex-1 font-medium">{o.size_label}</span>
                <span className="shrink-0 text-right text-sm text-stone-600 tabular-nums dark:text-stone-300">
                  {o.price != null ? <>{o.chain} {money(o.price)}{o.unit_price_per != null && o.per_unit ? <span className="block text-xs text-stone-500">{money(o.unit_price_per)}/{o.per_unit}</span> : null}</> : <span className="text-stone-400">fiyat yok</span>}
                </span>
              </button>
            </li>
          ))}
        </ul>
        {<button type="button" className="btn-ghost text-sm" onClick={() => onPick(null)}>Varsayılan boya dön</button>}
      </div>
    </Modal>
  );
}
