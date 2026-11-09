// Alışverişi tamamla ve geçmiş alışverişler (yetenek shopping_trips). Tamamla: işaretliler geçmişe yazılır
// (POST /api/shopping-list/complete {carry_over}); alınmayanlar istenirse listede kalır. Geçmiş: #/alisveris/gecmis
// (GET /api/shopping-trips) ve #/alisveris/gecmis/<id> (GET /api/shopping-trips/{id}): plan ve ödenen, fişle
// bağlandıysa piyasa ortancasına göre dürüst tasarruf; "Tekrar kullan" (POST /{id}/reuse) ürünleri listeye ekler.
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowLeft, ChevronRight, History, RotateCcw, Trash2 } from "lucide-react";
import { useState } from "react";
import { cx, maskText, tl, todayISO } from "../format";
import { send } from "../lib/apiError";
import { ask } from "../lib/ask";
import { dayWord } from "../lib/priceView";
import { amountText, chainName, savingCopy, tripChains, tripTotals, type TripSummary } from "../lib/shopList";
import { useUI } from "../ui";
import { isViewer, useBook } from "./hooks";
import Modal from "./Modal";
import SorguDurumu, { KartIskelet, SatirIskelet } from "./SorguDurumu";
import { SAVINGS, TRIPS } from "./ShopReceiptLink";

const money = (n: number) => maskText(tl(n).replace(/,00(?=\s|$)/, ""));
export const goList = () => { window.location.hash = "/alisveris"; window.scrollTo(0, 0); };
export const goTrips = (id?: number) => { window.location.hash = id ? `/alisveris/gecmis/${id}` : "/alisveris/gecmis"; window.scrollTo(0, 0); };

interface TripItem {
  text: string; qty: number; unit: string; amount_label?: string | null; group_key?: string | null; label?: string | null; done: boolean;
  planned_chain?: string | null; planned_unit?: number | null; planned_line?: number | null; actual_unit?: number | null; actual_line?: number | null;
  entry_id?: number | null; market_unit?: number | null; market_line?: number | null;
}
type Trip = TripSummary & { items: TripItem[]; carried?: number };

/** "Alışverişi tamamla" penceresi. */
export function CompleteDialog({ done, total, busy, onClose, onConfirm }: {
  done: number; total: number; busy?: boolean; onClose: () => void; onConfirm: (carry: boolean) => void;
}) {
  const [carry, setCarry] = useState(true);
  const left = total - done;
  return (
    <Modal title="Alışverişi tamamla" onClose={onClose} footer={<>
      <div className="flex-1" />
      <button type="button" className="btn-ghost" onClick={onClose}>Vazgeç</button>
      <button type="button" className="btn-primary" disabled={busy} onClick={() => onConfirm(carry)} data-testid="tamamla-onay">Tamamla</button>
    </>}>
      <div className="space-y-3 text-sm">
        <p className="text-stone-700 dark:text-stone-300">
          Aldığın {done} ürün geçmiş alışverişlerine kaydedilir ve listeden çıkar.{" "}
          {left > 0 ? `${left} ürün alınmadı.` : "Listedeki her şeyi aldın."}
        </p>
        {left > 0 && (
          <label className="flex cursor-pointer items-start gap-3 rounded-lg border border-stone-200 px-3 py-2.5 dark:border-stone-700">
            <input type="checkbox" className="mt-0.5 size-5 shrink-0" checked={carry} onChange={(e) => setCarry(e.target.checked)} data-testid="tasi" />
            <span className="font-medium">Alınmayanları yeni listeye taşı
              <span className="block text-xs font-normal text-stone-500 dark:text-stone-400">{carry ? "Listende kalırlar; bir dahaki alışverişte çıkar." : "Bu alışverişe “alınmadı” diye yazılır, liste boşalır."}</span></span>
          </label>
        )}
        <p className="text-xs text-stone-500 dark:text-stone-400">Fişini okutursan gerçek fiyatlar bu alışverişe bağlanır.</p>
      </div>
    </Modal>
  );
}

/** POST /complete: başarıda bildirim ("Geçmişe bak"). */
export function useComplete(onDone: () => void) {
  const qc = useQueryClient();
  const ui = useUI();
  return useMutation({
    mutationFn: (carry: boolean) => send<Trip>("/api/shopping-list/complete", { method: "POST", json: { carry_over: carry } }),
    onSuccess: (t) => {
      onDone();
      ui.toast(`Alışveriş kaydedildi · ${t.bought_count} ürün${t.carried ? ` · ${t.carried} ürün listede kaldı` : ""}`,
        { action: { label: "Gör", run: () => goTrips(t.id) } }); // mobil Tamamla ile aynı
    },
    onError: (e: Error) => ui.error(e.message),
    onSettled: () => { for (const k of [["shopping-list"], ["shopping-plan"], ["shopping-optimize"], TRIPS, SAVINGS, ["shopping-frequent"], ["shopping-due"]]) void qc.invalidateQueries({ queryKey: k }); },
  });
}

function Back({ to, label }: { to: () => void; label: string }) {
  return <button type="button" className="btn-ghost -ml-2 px-2 text-sm" onClick={to}><ArrowLeft size={16} aria-hidden /> {label}</button>;
}

export function TripList() {
  const q = useQuery({ queryKey: TRIPS, queryFn: () => send<{ trips: TripSummary[] }>("/api/shopping-trips").then((d) => d.trips ?? []) });
  const today = todayISO();
  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <Back to={goList} label="Alışveriş listesi" />
      </div>
      <h2 className="text-lg font-semibold">Geçmiş alışverişler</h2>
      <SorguDurumu q={q} etiket="Geçmiş alışverişler yükleniyor"><SatirIskelet n={4} ikon={false} /></SorguDurumu>
      {q.data && !q.data.length && (
        <section className="card space-y-1 p-6 text-center">
          <History size={28} className="mx-auto text-stone-400" aria-hidden />
          <h3 className="font-semibold">Henüz tamamlanan alışveriş yok</h3>
          <p className="text-sm text-stone-500 dark:text-stone-400">Listede aldıklarını işaretleyip “Alışverişi tamamla”ya basınca burada görünür.</p>
        </section>
      )}
      {!!q.data?.length && (
        <ul className="card divide-y divide-stone-100 dark:divide-stone-800" data-testid="gecmis-liste">
          {q.data.map((t) => (
            <li key={t.id}>
              <button type="button" className="flex w-full items-center gap-3 px-4 py-3 text-left hover:bg-stone-50 dark:hover:bg-stone-800/50" onClick={() => goTrips(t.id)}>
                <span className="min-w-0 flex-1">
                  <span className="flex flex-wrap items-baseline gap-x-2">
                    <span className="font-medium">{tripChains(t.chains)}</span>
                    <span className="text-xs text-stone-500 dark:text-stone-400">{dayWord(t.day, today)} · {t.bought_count} ürün</span>
                  </span>
                  {tripTotals(t, money) && <span className="block text-sm text-stone-600 tabular-nums dark:text-stone-300">
                    {tripTotals(t, money).split(" · ").map((x, i, all) => <span key={x}><span className="whitespace-nowrap">{x}{i < all.length - 1 && " ·"}</span>{" "}</span>)}</span>}
                </span>
                {t.linked && t.saving != null && t.saving > 0 && (
                  <span className="shrink-0 rounded-full bg-green-100 px-2 py-0.5 text-xs font-semibold text-green-800 tabular-nums dark:bg-green-950 dark:text-green-300">{money(t.saving)} tasarruf</span>
                )}
                <ChevronRight size={18} className="shrink-0 text-stone-400" aria-hidden />
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

export function TripDetail({ id }: { id: number }) {
  const ui = useUI();
  const qc = useQueryClient();
  const book = useBook();
  const ro = isViewer(book.data);
  const owner = !book.data?.member || book.data.book === "kendi";
  const today = todayISO();
  const q = useQuery({ queryKey: [...TRIPS, id], retry: false, queryFn: () => send<Trip>(`/api/shopping-trips/${id}`) });
  const reuse = useMutation({
    mutationFn: () => send<{ added: number; skipped: number }>(`/api/shopping-trips/${id}/reuse`, { method: "POST", json: {} }),
    onSuccess: (r) => {
      void qc.invalidateQueries({ queryKey: ["shopping-list"] });
      // metinler mobil ile aynı (alisveris/Gecmis)
      ui.toast(r.added ? `${r.added} ürün listeye eklendi${r.skipped ? ` · ${r.skipped} ürün zaten listede` : ""}` : "Hepsi zaten listede");
      goList();
    },
    onError: (e: Error) => ui.error(e.message),
  });
  const del = useMutation({
    mutationFn: () => send(`/api/shopping-trips/${id}`, { method: "DELETE" }),
    onSuccess: () => { void qc.invalidateQueries({ queryKey: TRIPS }); void qc.invalidateQueries({ queryKey: SAVINGS }); ui.toast("Alışveriş silindi"); goTrips(); },
    onError: (e: Error) => ui.error(e.message),
  });
  const t = q.data;
  const copy = t ? savingCopy(t, money) : null;
  const bought = t?.items.filter((i) => i.done) ?? [];
  const missed = t?.items.filter((i) => !i.done) ?? [];
  return (
    <div className="space-y-3">
      <Back to={() => goTrips()} label="Geçmiş alışverişler" />
      <SorguDurumu q={q} etiket="Alışveriş yükleniyor"><KartIskelet satir={2} /><SatirIskelet n={3} ikon={false} /></SorguDurumu>
      {t && copy && (
        <>
          <section className="card space-y-2 p-4" data-testid="alisveris-ozet">
            <div>
              <h2 className="text-lg font-semibold">{tripChains(t.chains)}</h2>
              <p className="text-sm text-stone-500 dark:text-stone-400">{dayWord(t.day, today)} · {t.bought_count} ürün alındı{t.created_by_name ? ` · ${t.created_by_name}` : ""}</p>
            </div>
            <dl className="flex flex-wrap gap-x-6 gap-y-1 text-sm tabular-nums">
              {t.planned_total != null && <div><dt className="text-xs text-stone-500 dark:text-stone-400">Plan</dt><dd className="font-semibold">{money(t.planned_total)}</dd></div>}
              {t.actual_total != null && <div><dt className="text-xs text-stone-500 dark:text-stone-400">Ödenen</dt><dd className="font-semibold">{money(t.actual_total)}</dd></div>}
              {t.market_total != null && <div><dt className="text-xs text-stone-500 dark:text-stone-400">Piyasa ortancası</dt><dd className="font-semibold">{money(t.market_total)}</dd></div>}
            </dl>
            <p className={cx("rounded-lg px-3 py-2 text-sm font-medium", copy.tone === "good" ? "bg-green-50 text-green-800 dark:bg-green-950/50 dark:text-green-300"
              : copy.tone === "over" ? "bg-amber-50 text-amber-900 dark:bg-amber-950/40 dark:text-amber-200" : "bg-stone-100 text-stone-700 dark:bg-stone-800 dark:text-stone-300")}
              data-testid="tasarruf-yazi">{copy.text}</p>
            {copy.tone !== "info" && <p className="text-xs text-stone-500 dark:text-stone-400">Fişindeki fiyatlar, o günün piyasa ortancasıyla karşılaştırıldı.</p>}
          </section>

          <section className="card" aria-label="Ürünler">
            <ul className="divide-y divide-stone-100 dark:divide-stone-800" data-testid="alisveris-urunler">
              {[...bought, ...missed].map((i, n) => {
                const name = i.unit && i.unit !== "adet" ? `${i.amount_label ?? amountText(i.qty, i.unit)} ${i.text}` : i.qty && i.qty !== 1 ? `${i.qty}× ${i.text}` : i.text;
                const plan = i.planned_chain && i.planned_line != null ? `Plan: ${chainName(i.planned_chain)} ${money(i.planned_line)}` : i.planned_line != null ? `Plan: ${money(i.planned_line)}` : null;
                return (
                  <li key={n} className="flex items-center gap-3 px-4 py-2.5">
                    <span className="min-w-0 flex-1">
                      <span className={cx("block break-words", !i.done && "text-stone-400 dark:text-stone-500")}>{name}</span>
                      <span className="block text-xs text-stone-500 tabular-nums dark:text-stone-400">
                        {!i.done ? "alınmadı" : [plan, i.market_line != null ? `piyasa ${money(i.market_line)}` : null].filter(Boolean).join(" · ") || " "}</span>
                    </span>
                    {i.done && (
                      <span className="shrink-0 text-right text-sm tabular-nums">
                        {i.actual_line != null ? <span className="font-medium">{money(i.actual_line)}</span> : <span className="text-stone-400">—</span>}
                      </span>
                    )}
                  </li>
                );
              })}
            </ul>
          </section>

          {!ro && (
            <div className="flex flex-wrap items-center gap-2">
              <button type="button" className="btn-primary flex-[1_1_12rem] justify-center sm:flex-none" disabled={reuse.isPending} onClick={() => reuse.mutate()} data-testid="tekrar-kullan">
                <RotateCcw size={16} aria-hidden /> Tekrar kullan</button>
              {owner && (
                <button type="button" className="btn-danger" disabled={del.isPending}
                  onClick={() => void ask("Bu alışveriş geçmişten silinsin mi?", { irreversible: true }).then((ok) => { if (ok) del.mutate(); })}>
                  <Trash2 size={16} aria-hidden /> Sil</button>
              )}
            </div>
          )}
        </>
      )}
    </div>
  );
}
