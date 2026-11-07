// Alışveriş listesi (yetenek shopping_list; paket satırı alisveris_listesi): serbest yazılan ürünler, adet, alındı
// işareti, silme. Ortak defterde ortaktır: ekleyenin baş harfi görünür, "Sadece görür" yetkisinde liste salt okunur.
// Fiyat: GET /api/shopping-list/plan → ürün başına en ucuz market ve "Hepsini tek yerden" özeti. Değişiklikler önce
// ekranda olur (iyimser), sunucu reddederse geri alınır. Üstte "Nereye gideyim?" (yetenek shopping_optimize,
// components/ShopOptimize.tsx): en iyi 1 / 2 / 3 duraklı plan. Mobil karşılığı: hesapkitap-mobile src/screens/Alisveris.tsx.
// Fiyat UX v2 (yetenek shopping_assumed_size / shopping_headline): satırın altında "En ucuz: BİM ₺32,50 · 1 L", boysuz
// satırda "1 L kabul edildi" (dokun → boy seç), fiyatsız satırda ne yapmalı; altta sabit "Nereye gideyim?" çubuğu planı açar.
// Liste v3 (her biri ayrı yetenek; eski sunucuda eski davranış):
// - shopping_units: satırda miktar ("2,5 kg elma", "6× süt"), dokununca düzenle / düzelt penceresi (components/
//   ShopItemDialog.tsx: miktar adımları, birim, ad önerileri, boy, "Listeden çıkar"); sağda satır tutarı (fiyat × miktar).
// - shopping_autofill: yazarken öneriler, "Sık aldıkların", "Listeyi doldur" (components/ShopAutofill.tsx).
// - shopping_trips: "Bu planla git" → liste zincir zincir ("BİM'den al"), "Planda olmayanlar"; işaretli varsa birincil
//   eylem "Alışverişi tamamla"; geçmiş #/alisveris/gecmis[/<id>] (components/ShopTrips.tsx); başlıkta aylık tasarruf.
// - shopping_receipt_link: son fişin listeye ait olup olmadığını soran şerit (components/ShopReceiptLink.tsx).
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Check, CheckCheck, ChevronRight, History, Info, MapPinned, Minus, Plus, Route as RouteIcon, ShoppingCart, Trash2 } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import Modal from "../components/Modal";
import { assumedText, barMain, bestInline, sizeOf, type Assumed, type Headline, type ShopBest, type UnpricedInfo } from "../lib/priceAdvice";
import type { Optimize } from "../lib/optimize";
import { cx, maskText, tl, todayISO } from "../format";
import { ApiError, bodyOf, failureText, send } from "../lib/apiError";
import { agoWord, ayrilma, bestChain, type PlanChain } from "../lib/priceView";
import { bestParts, boughtText, chainName, groupByPlan, planSummary, rowTitle, type SavedPlan, type Unit } from "../lib/shopList";
import { useUI } from "../ui";
import { isViewer, useBook, useMeta, usePeople } from "../components/hooks";
import { PlanGate } from "../components/Plan";
import ShopOptimize, { OPTIMIZE } from "../components/ShopOptimize";
import ShopItemDialog, { type ItemPatch } from "../components/ShopItemDialog";
import { AddForm, DUE, FillDialog, FREQUENT, QuickAdd, type Due } from "../components/ShopAutofill";
import { CompleteDialog, goTrips, TripDetail, TripList, useComplete } from "../components/ShopTrips";
import { ReceiptAskBanner, SAVINGS } from "../components/ShopReceiptLink";
import { AddedBy } from "../components/TxnList";

export interface ShopItem {
  id: number; text: string; group_key: string | null; qty: number | null; done: boolean; added_by: number | null; created_at: string;
  group?: { key: string; label: string; pending?: boolean } | null;
  /** yetenek shopping_assumed_size: boysuz satırın varsayılan boyu; satırın en ucuz teklifi */
  assumed?: Assumed | null; best?: (ShopBest & { line_total?: number | null }) | null;
  /** yetenek shopping_units / shopping_trips */
  unit?: string | null; amount_label?: string | null; mult?: number | null; unit_mismatch?: boolean;
  planned?: { chain: string; unit_price: number; line_total: number } | null;
  actual?: { unit_price: number | null; line_total: number | null; entry_id: number } | null;
}
interface SizeOpt { key: string; label: string; size_label: string; chain: string | null; price: number | null; unit_price_per?: number | null; per_unit?: string | null }
type OptimizeV2 = Optimize & { headline?: Headline | null; unpriced_info?: UnpricedInfo[]; empty?: { title: string; text: string } | null };
interface Best { chain: string; price: number; brand?: string | null; source: "own" | "community" | "resmi"; date?: string | null }
interface ShopPlan { items: { id: number; best: Best | null }[]; chains: PlanChain[] }
interface ListData { items: ShopItem[]; saved_plan: SavedPlan | null }

const LIST = ["shopping-list"] as const;
const PLAN = ["shopping-plan"] as const;
const BASE = "/api/shopping-list";
const UNIT_HINT = "Bu ürün bu birimle satılmıyor; miktarı adet olarak seç.";

const fetchList = () => send<ShopItem[] | { items: ShopItem[]; saved_plan?: SavedPlan | null }>(BASE)
  .then((d): ListData => (Array.isArray(d) ? { items: d, saved_plan: null } : { items: d.items ?? [], saved_plan: d.saved_plan ?? null }));
async function remove(id: number) {
  let r: Response;
  try { r = await fetch(`${BASE}/${id}`, { method: "DELETE" }); } catch { throw new ApiError("Bağlantı yok; tekrar dene.", 0); }
  if (!r.ok) { const b = await bodyOf(r); throw new ApiError(failureText(r.status, b), r.status, b); }
}
/** "~₺412" (kuruşsuz) */
const approx = (n: number) => `~${tl(Math.round(n)).replace(/,00(?=\s|$)/, "")}`;

/** #/alisveris/gecmis[/<id>] */
function readSub(): { view: "list" | "trips" | "trip"; id?: number } {
  const p = window.location.hash.replace(/^#\/?/, "").split("?")[0]!.split("/");
  if (p[1] !== "gecmis") return { view: "list" };
  const id = Number(p[2]);
  return Number.isInteger(id) && id > 0 ? { view: "trip", id } : { view: "trips" };
}

export default function Shopping() {
  const [sub, setSub] = useState(readSub);
  useEffect(() => {
    const on = () => setSub(readSub());
    window.addEventListener("hashchange", on);
    return () => window.removeEventListener("hashchange", on);
  }, []);
  const tripsOn = !!useMeta().data?.capabilities?.includes("shopping_trips");
  const view = tripsOn ? sub.view : "list";
  return (
    <div className="space-y-4">
      {view === "list" && (
        <div>
          <h1 className="text-xl font-semibold">Alışveriş listesi</h1>
          <p className="text-sm text-stone-500">Alacaklarını yaz; fişlerinden ve topluluk fiyatlarından en ucuz nerede olduğunu gösteririz.</p>
        </div>
      )}
      {view !== "list" && <h1 className="sr-only">Alışveriş listesi</h1>}
      <PlanGate feature="alisveris_listesi" title="Alışveriş listesi">
        {view === "trip" && sub.id ? <TripDetail id={sub.id} /> : view === "trips" ? <TripList /> : <ShoppingList />}
      </PlanGate>
    </div>
  );
}

/** Üst satır: bu ayki tasarruf (yalnız > 0) ve geçmiş alışverişler bağlantısı. */
function ListToolbar({ money }: { money: (n: number) => string }) {
  const s = useQuery({ queryKey: SAVINGS, retry: false, staleTime: 5 * 60_000,
    queryFn: () => send<{ month: number; year: number; month_trips: number }>("/api/shopping-trips/savings") });
  const m = s.data?.month ?? 0;
  return (
    <div className="flex flex-wrap items-center gap-2">
      {m > 0 && (
        <span className="rounded-full bg-green-100 px-2.5 py-1 text-sm font-semibold text-green-800 tabular-nums dark:bg-green-950 dark:text-green-300" data-testid="ay-tasarruf"
          title="Fişle bağlanan alışverişlerde, o günün piyasa ortancasına göre">Bu ay {money(m)} tasarruf</span>
      )}
      <button type="button" className="btn-ghost ml-auto -mr-2 text-sm" onClick={() => goTrips()} data-testid="gecmis-baglanti">
        <History size={16} aria-hidden /> Geçmiş alışverişler</button>
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
  const unitsOn = !!caps?.includes("shopping_units");
  const autofillOn = !!caps?.includes("shopping_autofill");
  const tripsOn = !!caps?.includes("shopping_trips");
  const linkOn = !!caps?.includes("shopping_receipt_link");
  const today = todayISO();
  const list = useQuery({ queryKey: LIST, queryFn: fetchList });
  const items = list.data?.items ?? [];
  const saved = tripsOn ? list.data?.saved_plan ?? null : null;
  const plan = useQuery({ queryKey: PLAN, queryFn: () => send<ShopPlan>(`${BASE}/plan`), retry: false, enabled: items.length > 0 && !unitsOn });
  const [sizeFor, setSizeFor] = useState<ShopItem | null>(null);
  const [edit, setEdit] = useState<ShopItem | null>(null);
  const [fill, setFill] = useState<Due[] | null>(null);
  const [completing, setCompleting] = useState(false);
  const [planOpen, setPlanOpen] = useState(false);
  const planRef = useRef<HTMLDivElement>(null);
  // çubuk: liste değişince (ekle / sil / adet / boy) yeniden hesaplanır; işaretlemek (alındı) planı değiştirmez
  const sig = items.filter((i) => i.id > 0).map((i) => `${i.id}:${i.qty ?? 1}:${i.unit ?? ""}:${i.group_key ?? ""}:${i.text}`).join(",");
  const opt = useQuery({
    queryKey: [...OPTIMIZE, sig], enabled: barOn && items.some((i) => !i.done && i.id > 0), retry: false, staleTime: Infinity, gcTime: 30 * 60_000,
    queryFn: () => send<OptimizeV2>("/api/shopping-list/optimize"),
  });

  const refresh = () => {
    for (const k of [LIST, PLAN, FREQUENT, DUE]) void qc.invalidateQueries({ queryKey: k });
  };
  // iyimser değişiklik: önceki listeyi sakla, hata olursa ona dön; sonunda sorgular sunucudan tazelenir
  const optimistic = <A,>(fn: (a: A) => Promise<unknown>, apply: (items: ShopItem[], a: A) => ShopItem[]) => ({
    mutationFn: fn,
    onMutate: async (a: A) => {
      await qc.cancelQueries({ queryKey: LIST });
      const prev = qc.getQueryData<ListData>(LIST);
      qc.setQueryData<ListData>(LIST, (cur) => ({ saved_plan: cur?.saved_plan ?? null, items: apply(cur?.items ?? [], a) }));
      return { prev };
    },
    onError: (e: Error, _a: A, ctx?: { prev?: ListData }) => { if (ctx?.prev) qc.setQueryData(LIST, ctx.prev); ui.error(e.message); },
    onSettled: refresh,
  });
  const add = useMutation(optimistic(
    (a: { text: string; qty?: number; group_key?: string | null; tmp: number; at: string }) =>
      send<ShopItem>(BASE, { method: "POST", json: { text: a.text, ...(a.qty != null && { qty: a.qty }), ...(a.group_key && { group_key: a.group_key }) } }),
    (xs, a) => [...xs, { id: a.tmp, text: a.text, qty: a.qty ?? 1, unit: "adet", group_key: a.group_key ?? null, done: false, added_by: people.meId ?? null, created_at: a.at }],
  ));
  const patch = useMutation(optimistic(
    (a: { id: number; done?: boolean; qty?: number; unit?: Unit; text?: string; group_key?: string | null }) => { const { id, ...body } = a; return send<ShopItem>(`${BASE}/${id}`, { method: "PATCH", json: body }); },
    (xs, a) => xs.map((it) => (it.id === a.id ? { ...it, ...(a.done !== undefined && { done: a.done }), ...(a.qty !== undefined && { qty: a.qty }),
      ...(a.unit !== undefined && { unit: a.unit }), ...(a.text !== undefined && { text: a.text }), ...(a.group_key !== undefined && { group_key: a.group_key }) } : it)),
  ));
  // "Alınanları temizle": tek tek, sırayla (biri düşerse kalanlar listede kalır, liste sunucudan tazelenir)
  const del = useMutation(optimistic(async (ids: number[]) => { for (const id of ids) await remove(id); },
    (xs, ids) => xs.filter((it) => !ids.includes(it.id))));
  const bulk = useMutation({
    mutationFn: (sel: Due[]) => send<{ added: number; skipped: number }>(`${BASE}/bulk`, { method: "POST",
      json: { items: sel.map((x) => ({ text: x.text, qty: x.qty, unit: x.unit, ...(x.group_key && { group_key: x.group_key }) })) } }),
    onSuccess: (r) => { setFill(null); ui.toast(`${r.added} ürün listeye eklendi`); },
    onError: (e: Error) => ui.error(e.message),
    onSettled: refresh,
  });
  const savePlan = useMutation({
    mutationFn: (k: number) => send<SavedPlan>(`${BASE}/saved-plan`, { method: "PUT", json: { k } }),
    onSuccess: () => { setPlanOpen(false); ui.toast("Plan kaydedildi · liste markete göre sıralandı"); window.scrollTo({ top: 0, behavior: "smooth" }); },
    onError: (e: Error) => ui.error(e.message),
    onSettled: () => void qc.invalidateQueries({ queryKey: LIST }),
  });
  const clearPlan = useMutation({
    mutationFn: () => send(`${BASE}/saved-plan`, { method: "DELETE" }),
    onSuccess: () => { setPlanOpen(false); ui.toast("Plan kaldırıldı"); },
    onError: (e: Error) => ui.error(e.message),
    onSettled: () => void qc.invalidateQueries({ queryKey: LIST }),
  });
  const complete = useComplete(() => setCompleting(false));

  const now = () => { const d = new Date(); return { tmp: -d.getTime(), at: d.toISOString() }; };
  const addOne = (a: { text: string; qty?: number; group_key?: string | null }) => add.mutate({ ...a, ...now() });

  const open = items.filter((i) => !i.done), done = items.filter((i) => i.done);
  const bestOf = new Map((plan.data?.items ?? []).map((x) => [x.id, x.best]));
  const top = barOn || unitsOn ? null : bestChain(plan.data?.chains);
  const r = opt.data;
  const hints = new Map((r?.unpriced_info ?? []).map((u) => [u.id, u]));
  const hintOf = (it: ShopItem): UnpricedInfo | undefined => hints.get(it.id)
    ?? (it.unit_mismatch ? { id: it.id, text: it.text, reason: "unit_mismatch", hint: UNIT_HINT } : undefined);
  const money = (n: number) => maskText(tl(n).replace(/,00(?=\s|$)/, ""));
  const openPlan = () => { setPlanOpen(true); requestAnimationFrame(() => planRef.current?.scrollIntoView?.({ block: "start", behavior: "smooth" })); };

  // fiyatlı ürün varsa görünür; alışverişte hepsi işaretlenince de kaybolmasın (sonuç ekranda kalsın)
  const priced = (plan.data?.items ?? []).some((x) => x.best && open.some((o) => o.id === x.id)) || !!r?.priced;
  const [optShown, setOptShown] = useState(false);
  if (priced && !optShown) setOptShown(true);

  // kaydedilen plan: zincir zincir (işaretliler yerinde kalır), sonra planda olmayanlar
  const grouped = saved ? groupByPlan(items, saved) : null;
  const completeBar = tripsOn && !ro && done.length > 0;

  const rowOf = (it: ShopItem, inPlan = false) => unitsOn ? (
    <UnitRow key={it.id} it={it} inPlan={inPlan} hint={!it.done ? hintOf(it) : undefined} ro={ro} money={money} people={people}
      onTick={(d) => patch.mutate({ id: it.id, done: d })} onEdit={() => setEdit(it)} />
  ) : (
    <LegacyRow key={it.id} it={it} best={bestOf.get(it.id)} hint={hints.get(it.id)} ro={ro} money={money} people={people} today={today} sizesOn={sizesOn}
      onTick={(d) => patch.mutate({ id: it.id, done: d })} onQty={(q) => patch.mutate({ id: it.id, qty: q })} onDel={() => del.mutate([it.id])} onSize={() => setSizeFor(it)} />
  );

  return (
    <div className="space-y-3">
      {tripsOn && <ListToolbar money={money} />}
      {linkOn && !ro && <ReceiptAskBanner />}
      {saved && !planOpen && (
        <section className="card flex flex-wrap items-center gap-3 border-green-200 bg-green-50/60 p-4 dark:border-green-900 dark:bg-green-950/30" data-testid="kayitli-plan">
          <RouteIcon size={22} className="shrink-0 text-green-700 dark:text-green-400" aria-hidden />
          <div className="min-w-0 flex-[1_1_12rem]">
            <h2 className="font-semibold break-words tabular-nums">{maskText(planSummary(saved, money))}</h2>
            <p className="text-sm text-stone-600 dark:text-stone-300">Liste markete göre sıralandı; aldıkça işaretle.</p>
          </div>
          {!ro && optimizeOn && <button type="button" className="btn-ghost ml-auto text-sm" onClick={openPlan}>Planı değiştir</button>}
        </section>
      )}
      {optimizeOn && (barOn ? planOpen : optShown) && items.length > 0 && (
        <div ref={planRef} className="scroll-mt-16">
          <ShopOptimize items={items} ro={ro} onTick={(id, d) => patch.mutate({ id, done: d })} open={barOn ? true : undefined} data={barOn ? r : undefined}
            onClose={barOn ? () => setPlanOpen(false) : undefined} onRefetch={barOn ? () => void opt.refetch() : undefined} fetching={barOn ? opt.isFetching : undefined}
            onSavePlan={tripsOn ? (k) => savePlan.mutate(k) : undefined} savingPlan={savePlan.isPending}
            onClearPlan={saved ? () => clearPlan.mutate() : undefined} initialK={saved?.k}
            onFix={unitsOn ? (id) => { const it = items.find((x) => x.id === id); if (it) setEdit(it); } : undefined} />
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
        <>
          <AddForm autofill={autofillOn} units={unitsOn} onAdd={addOne} />
          {autofillOn && <QuickAdd have={items} onAdd={(f) => addOne({ text: f.text, group_key: f.group_key })} onFill={setFill} />}
        </>
      )}

      {list.isLoading && <p className="text-sm text-stone-500">Yükleniyor…</p>}
      {list.error && <p className="text-sm text-red-600 dark:text-red-400" role="alert">{(list.error as Error).message}</p>}
      {list.data && !items.length && (
        <section className="card space-y-1 p-6 text-center">
          <ShoppingCart size={28} className="mx-auto text-stone-400" aria-hidden />
          <h2 className="font-semibold">Liste boş</h2>
          <p className="text-sm text-stone-500">{ro ? "Defterde henüz alınacak bir şey yok." : unitsOn ? "Yukarıya alacaklarını yaz: “süt”, “2 kg elma”, “6 yumurta”…" : "Yukarıya alacaklarını yaz: “süt”, “yumurta 30'lu”…"}</p>
        </section>
      )}

      {grouped && items.length > 0 ? (
        <div className="space-y-3" data-testid="plan-gruplari">
          {grouped.groups.map((g) => (
            <section key={g.chain} className="card" aria-label={`${ayrilma(g.chain)} al`}>
              <h2 className="flex flex-wrap items-baseline gap-x-2 border-b border-stone-100 px-4 py-2.5 dark:border-stone-800">
                <span className="font-semibold">{ayrilma(g.chain)} al</span>
                <span className="text-sm text-stone-500 tabular-nums dark:text-stone-400">{g.items.length} ürün · {money(g.total)}</span>
              </h2>
              <ul className="divide-y divide-stone-100 dark:divide-stone-800">{g.items.map((it) => rowOf(it, true))}</ul>
            </section>
          ))}
          {grouped.rest.length > 0 && (
            <section className="card" aria-label="Planda olmayanlar">
              <h2 className="border-b border-stone-100 px-4 py-2.5 font-semibold dark:border-stone-800">Planda olmayanlar
                <span className="ml-2 text-sm font-normal text-stone-500 dark:text-stone-400">{grouped.rest.length} ürün</span></h2>
              <ul className="divide-y divide-stone-100 dark:divide-stone-800">{[...grouped.rest.filter((i) => !i.done), ...grouped.rest.filter((i) => i.done)].map((it) => rowOf(it))}</ul>
            </section>
          )}
        </div>
      ) : items.length > 0 && (
        <ul className="card divide-y divide-stone-100 dark:divide-stone-800" aria-label="Alınacaklar">
          {[...open, ...done].map((it) => rowOf(it))}
        </ul>
      )}

      {completeBar ? (
        <div className="sticky bottom-[calc(4.75rem+var(--sab))] z-30 md:bottom-4">
          <button type="button" onClick={() => setCompleting(true)} data-testid="tamamla-cubuk"
            className="flex w-full items-center gap-3 rounded-2xl bg-green-700 px-4 py-3 text-left text-white shadow-lg shadow-green-900/20 transition active:scale-[0.99] dark:bg-green-600">
            <CheckCheck size={22} className="shrink-0" aria-hidden />
            <span className="min-w-0 flex-1">
              <span className="block font-semibold">Alışverişi tamamla</span>
              <span className="block text-xs font-medium text-green-100 tabular-nums">{boughtText(done.length, items.length)}</span>
            </span>
            <ChevronRight size={20} className="shrink-0" aria-hidden />
          </button>
        </div>
      ) : barOn && !saved && open.length > 0 && r && !planOpen && (r.headline ? (
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
      {edit && <ShopItemDialog it={edit} hint={!edit.done ? hintOf(edit) : undefined} autofill={autofillOn} money={money} onClose={() => setEdit(null)}
        onSave={(p: ItemPatch) => { patch.mutate({ id: edit.id, ...p }); setEdit(null); }}
        onRemove={() => { del.mutate([edit.id]); setEdit(null); }} />}
      {fill && <FillDialog items={fill} busy={bulk.isPending} onClose={() => setFill(null)} onConfirm={(sel) => bulk.mutate(sel)} />}
      {completing && <CompleteDialog done={done.length} total={items.length} busy={complete.isPending} onClose={() => setCompleting(false)} onConfirm={(c) => complete.mutate(c)} />}
      {!tripsOn && !ro && done.length > 0 && (
        <button type="button" className="btn-ghost text-sm" disabled={del.isPending} onClick={() => del.mutate(done.map((d) => d.id))}>
          <Trash2 size={14} aria-hidden /> Alınanları temizle ({done.length})</button>
      )}
    </div>
  );
}

type People = ReturnType<typeof usePeople>;

/** v3 satırı: işaret, miktarlı ad (dokun → düzenle / düzelt), alt yazı (en ucuz / plan / fiş / ipucu), sağda tutar. */
function UnitRow({ it, inPlan, hint, ro, money, people, onTick, onEdit }: {
  it: ShopItem; inPlan: boolean; hint?: UnpricedInfo; ro: boolean; money: (n: number) => string; people: People;
  onTick: (done: boolean) => void; onEdit: () => void;
}) {
  const who = people.multi ? people.person(it.added_by) : undefined;
  // varsayılan boy varsa boy bir kez yazılır ("1 L kabul edildi")
  const size = it.assumed && !hint && !it.done ? assumedText(it.assumed) : it.assumed?.size_label ?? sizeOf(it.group?.label);
  let sub: string | null = null, right: string | null = null;
  if (it.actual?.line_total != null) { right = money(it.actual.line_total); sub = "Fişten"; }
  else if (inPlan && it.planned) {
    right = money(it.planned.line_total);
    // zincir başlıkta; tek paketse alt yazı yalnız varsayılan boy (boy adda zaten yazılı)
    sub = Math.abs(it.planned.line_total - it.planned.unit_price) >= 0.005 ? `Birim ${money(it.planned.unit_price)}${size ? ` · ${size}` : ""}`
      : it.assumed ? size : null;
  } else if (it.best && !it.done) { const p = bestParts(it.best, size, money); sub = p.sub; right = p.right; }
  
  const title = rowTitle(it);
  const body = (
    <>
      <span className="flex items-center gap-1.5">
        <span className={cx("min-w-0 break-words", it.done && "text-stone-400 line-through dark:text-stone-500")}>{title}</span>
        {who && <AddedBy name={people.isMe(it.added_by) ? null : who.name} initial={who.name} />}
      </span>
      {hint ? (
        <span className="block text-xs text-amber-800 dark:text-amber-300" data-testid="satir-ipucu">{hint.hint}{!ro && hint.reason !== "pending" && <> <span className="font-medium underline underline-offset-2">Düzelt</span></>}</span>
      ) : sub && !it.done ? (
        <span className="block text-xs text-stone-500 tabular-nums dark:text-stone-400" data-testid="satir-en-ucuz">{maskText(sub ?? "")}</span>
      ) : null}
    </>
  );
  return (
    <li className="flex items-center gap-3 px-4 py-2.5" data-testid="satir">
      <input type="checkbox" className="size-5 shrink-0" checked={it.done} disabled={ro || it.id < 0}
        aria-label={`${it.text}: ${it.done ? "alınmadı say" : "alındı"}`} onChange={(e) => onTick(e.target.checked)} />
      {ro || it.id < 0 ? <div className="min-w-0 flex-1">{body}</div> : (
        <button type="button" className="-my-1 min-w-0 flex-1 rounded-md py-1 text-left hover:bg-stone-50 dark:hover:bg-stone-800/50" onClick={onEdit}
          aria-label={`${title}: ${hint && hint.reason !== "pending" ? "düzelt" : "düzenle"}`}>{body}</button>
      )}
      {right && <span className={cx("shrink-0 text-sm tabular-nums", it.done ? "text-stone-400 dark:text-stone-500" : "font-medium")}>{right}</span>}
    </li>
  );
}

/** Eski sunucu (shopping_units yok): adet düğmeleri ve silme satırda. */
function LegacyRow({ it, best, hint, ro, money, people, today, sizesOn, onTick, onQty, onDel, onSize }: {
  it: ShopItem; best?: Best | null; hint?: UnpricedInfo; ro: boolean; money: (n: number) => string; people: People; today: string; sizesOn: boolean;
  onTick: (done: boolean) => void; onQty: (q: number) => void; onDel: () => void; onSize: () => void;
}) {
  const who = people.multi ? people.person(it.added_by) : undefined;
  const q = it.qty ?? 1;
  return (
    <li className="flex items-center gap-3 px-4 py-2.5">
      <input type="checkbox" className="size-5 shrink-0" checked={it.done} disabled={ro || it.id < 0}
        aria-label={`${it.text}: ${it.done ? "alınmadı say" : "alındı"}`} onChange={(e) => onTick(e.target.checked)} />
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-1.5">
          <span className={cx("min-w-0 break-words", it.done && "text-stone-400 line-through")}>{it.text}</span>
          {who && <AddedBy name={people.isMe(it.added_by) ? null : who.name} initial={who.name} />}
        </div>
        {!it.done && sizesOn && it.best !== undefined ? (
          <ItemPrice it={it} hint={hint} ro={ro} money={money} onSize={onSize} />
        ) : best && !it.done && (
          <div className="text-xs text-stone-500 tabular-nums">en ucuz: {maskText(chainName(best.chain))} {tl(best.price)}{best.source === "community" ? " · topluluk" : ""}{best.source === "resmi" && best.date && best.date < today ? ` · ${agoWord(best.date, today)}` : ""}</div>
        )}
      </div>
      {ro ? (q > 1 && <span className="shrink-0 text-sm text-stone-500 tabular-nums">×{q}</span>) : (
        <div className="flex shrink-0 items-center" role="group" aria-label={`${it.text} adedi`}>
          <button type="button" className="btn-ghost p-1.5" disabled={q <= 1 || it.id < 0} aria-label="Bir azalt" onClick={() => onQty(q - 1)}><Minus size={14} /></button>
          <span className="w-6 text-center text-sm tabular-nums" aria-live="polite">{q}</span>
          <button type="button" className="btn-ghost p-1.5" disabled={it.id < 0} aria-label="Bir artır" onClick={() => onQty(q + 1)}><Plus size={14} /></button>
        </div>
      )}
      {!ro && (
        <button type="button" className="btn-ghost shrink-0 p-1.5 text-stone-500" disabled={it.id < 0} aria-label={`${it.text}: sil`}
          onClick={onDel}><Trash2 size={16} /></button>
      )}
    </li>
  );
}

/** Satır altı: en ucuz teklif ve varsayılan boy ("1 L kabul edildi" — dokununca boy seçilir) ya da fiyatsızsa ne yapmalı. */
function ItemPrice({ it, hint, ro, money, onSize }: { it: ShopItem; hint?: UnpricedInfo; ro: boolean; money: (n: number) => string; onSize: () => void }) {
  const size = it.assumed?.size_label ?? sizeOf(it.group?.label);
  return (
    <div className="flex flex-wrap items-center gap-x-2 gap-y-0.5 text-xs">
      {it.best ? (
        <span className="font-medium text-green-700 tabular-nums dark:text-green-400" data-testid="satir-en-ucuz">{maskText(bestInline({ ...it.best, chain: chainName(it.best.chain) }, size, money))}</span>
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
                <span className="min-w-0 flex-1 font-medium">{o.size_label}{o.key === cur && <Check size={15} className="ml-1.5 inline text-green-700 dark:text-green-400" aria-label="seçili" />}</span>
                <span className="shrink-0 text-right text-sm text-stone-600 tabular-nums dark:text-stone-300">
                  {o.price != null ? <>{chainName(o.chain)} {money(o.price)}{o.unit_price_per != null && o.per_unit ? <span className="block text-xs text-stone-500">{money(o.unit_price_per)}/{o.per_unit}</span> : null}</> : <span className="text-stone-400">fiyat yok</span>}
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
