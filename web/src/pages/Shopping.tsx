// Alışveriş listesi (yetenek shopping_list; paket satırı alisveris_listesi): serbest yazılan ürünler, adet, alındı
// işareti, silme. Ortak defterde ortaktır: ekleyenin baş harfi görünür, "Sadece görür" yetkisinde liste salt okunur.
// Fiyat: GET /api/shopping-list/plan → ürün başına en ucuz market ve "Hepsini tek yerden" özeti. Değişiklikler önce
// ekranda olur (iyimser), sunucu reddederse geri alınır. Mobil karşılığı: hesapkitap-mobile src/screens/Alisveris.tsx.
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Minus, Plus, ShoppingCart, Trash2 } from "lucide-react";
import { useState } from "react";
import { cx, maskText, tl } from "../format";
import { ApiError, bodyOf, failureText, send } from "../lib/apiError";
import { bestChain, type PlanChain } from "../lib/priceView";
import { useUI } from "../ui";
import { isViewer, useBook, usePeople } from "../components/hooks";
import { PlanGate } from "../components/Plan";
import { AddedBy } from "../components/TxnList";

export interface ShopItem { id: number; text: string; group_key: string | null; qty: number | null; done: boolean; added_by: number | null; created_at: string }
interface Best { chain: string; price: number; brand?: string | null; source: "own" | "community" }
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
  const list = useQuery({ queryKey: LIST, queryFn: fetchList });
  const plan = useQuery({ queryKey: PLAN, queryFn: () => send<ShopPlan>(`${BASE}/plan`), retry: false, enabled: !!list.data?.length });
  const [text, setText] = useState("");
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
    (a: { id: number; done?: boolean; qty?: number }) => { const { id, ...body } = a; return send<ShopItem>(`${BASE}/${id}`, { method: "PATCH", json: body }); },
    (items, a) => items.map((it) => (it.id === a.id ? { ...it, ...(a.done !== undefined && { done: a.done }), ...(a.qty !== undefined && { qty: a.qty }) } : it)),
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
  const top = bestChain(plan.data?.chains);

  return (
    <div className="space-y-3">
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
                  {best && !it.done && (
                    <div className="text-xs text-stone-500 tabular-nums">en ucuz: {maskText(best.chain)} {tl(best.price)}{best.brand ? ` (${best.brand})` : ""}{best.source === "community" ? " · topluluk" : ""}</div>
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
      {!ro && done.length > 0 && (
        <button type="button" className="btn-ghost text-sm" disabled={del.isPending} onClick={() => del.mutate(done.map((d) => d.id))}>
          <Trash2 size={14} aria-hidden /> Alınanları temizle ({done.length})</button>
      )}
    </div>
  );
}
