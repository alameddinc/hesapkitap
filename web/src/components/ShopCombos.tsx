// Alışveriş listesi v5 (yetenek shop_combo_picker): TEK seçici "Zincir ve marka seç" — satırlar zincir × marka × boy
// ("BİM · Örneksüt 1 L"), süzgeç çipleri (zincir / marka / boy), "Fiyat" | "Birim fiyat" sıralaması, en üstte "Marka fark
// etmez". v4'teki Marka çipleri + Mağazalara göre + Mağaza bölümünün yerine geçer.
// - GET /api/shopping-list/{id}/combos?all_sizes=true (satır; selected {store, brand, key}) — dokununca tek PATCH
//   {store, brand, group_key}; "Marka fark etmez" {brand: null, store: null}.
// - GET /api/prices/combos?group=… (Fiyatlar grup ayrıntısı; salt görüntü).
// Plan durağından açılınca zincir süzgeci o zincirle önceden seçili (değiştirilebilir: liste bütün zincirlerle gelir).
// Mobil karşılığı: hesapkitap-mobile.
import { useQuery, useQueryClient, type QueryClient } from "@tanstack/react-query";
import { Check } from "lucide-react";
import { useState } from "react";
import { cx, maskText, tl, todayISO } from "../format";
import { send } from "../lib/apiError";
import { ANY_PATCH, ANY_TOAST, comboBadges, comboPatch, comboTitle, comboToast, filterCombos, isAnySel, isSelected, sortCombos,
  type ComboRow, type ComboSel, type ComboSort, type CombosData } from "../lib/combos";
import { chainName } from "../lib/shopList";
import { offerSource, unitPriceText } from "../lib/shopStores";
import { useUI } from "../ui";
import Modal from "./Modal";
import { OfferBadges, PriceFootnote, type GroupBest } from "./PriceDetail";
import Segmented from "./Segmented";
import { HiddenHint } from "./ShopStores";
import { bekliyor, Cevrimdisi, Donen, HataNotu, type SorguBenzeri } from "./SorguDurumu";
import { cevrimdisiHata, DURUM, useBekleme, useCevrimici } from "../lib/durum";

export const COMBOS = "shop-combos";
export const itemCombosKey = (id: number) => [COMBOS, "item", id] as const;
export const useItemCombos = (id: number, enabled = true) => useQuery({
  queryKey: itemCombosKey(id), enabled, retry: false, staleTime: 60_000,
  queryFn: () => send<CombosData>(`/api/shopping-list/${id}/combos?all_sizes=true`),
});
export const useGroupCombos = (group: string, enabled = true) => useQuery({
  queryKey: [COMBOS, "group", group], enabled, retry: false, staleTime: 5 * 60_000,
  queryFn: () => send<CombosData>(`/api/prices/combos?group=${encodeURIComponent(group)}`),
});

const money = (n: number) => maskText(tl(n));

/** "Tekrar dene": bekleyen isteği bırak, yenisini at. */
export const retry = (qc: QueryClient, queryKey: readonly unknown[]) =>
  void qc.cancelQueries({ queryKey, exact: true }).then(() => qc.refetchQueries({ queryKey, exact: true }));

function Chips<T extends string | null>({ label, all, items, value, onChange, text, bleed }: {
  label: string; all: string; items: T[]; value: T | null; onChange: (v: T | null) => void; text?: (v: T) => string; bleed: string;
}) {
  return (
    <div className={cx("flex gap-1.5 overflow-x-auto py-1.5", bleed)} role="radiogroup" aria-label={label}>
      {[null, ...items].map((v) => {
        const on = (value ?? null) === v;
        return (
          <button key={v ?? "_"} type="button" role="radio" aria-checked={on} onClick={() => onChange(v as T | null)}
            className={cx("hit inline-flex min-h-8 shrink-0 items-center rounded-full border px-3 text-xs whitespace-nowrap",
              on ? "border-green-700 bg-green-700 text-white dark:border-green-500 dark:bg-green-600"
                : "border-stone-200 text-stone-600 hover:bg-stone-100 dark:border-stone-700 dark:text-stone-300 dark:hover:bg-stone-800")}>
            {v === null ? all : text ? text(v as T) : v}</button>
        );
      })}
    </div>
  );
}

const Badge = ({ children, tone }: { children: string; tone: "green" | "blue" }) => (
  <span className={cx("rounded px-1.5 py-0.5 text-[11px] font-semibold",
    tone === "green" ? "bg-green-100 text-green-800 dark:bg-green-950 dark:text-green-300" : "bg-blue-100 text-blue-800 dark:bg-blue-950 dark:text-blue-200")}>{children}</span>
);

/** Fiyat listelerinin durumu (Zincir ve marka seç, Boy, Mağazalara göre, Fiyatlar grup ayrıntısı). SorguDurumu ile aynı kurallar
 *  ve metinler (lib/durum.ts; mobil alisveris/ZincirMarka FiyatDurumu ile aynı): yükleniyor → satır iskeleti + "Fiyatlar
 *  getiriliyor…"; 5 sn sonra "Bağlantı yavaş · Bekliyoruz · Tekrar dene"; bağlantı yok → çevrimdışı kutusu; hata → sunucunun
 *  metni + "Tekrar dene" (bölüm sessizce gizlenmez). Veri geldiyse hiçbir şey çizmez. */
export function PriceStatus({ q, rows = 4, onRetry, testId = "fiyat-yukleniyor", className }: {
  q: SorguBenzeri; rows?: number; onRetry?: () => void; testId?: string; className?: string;
}) {
  const online = useCevrimici();
  const again = onRetry ?? (() => void q.refetch());
  if (q.error && q.data === undefined) return cevrimdisiHata(q.error) ? <Cevrimdisi retry={again} className={className} /> : <HataNotu error={q.error} retry={again} className={className} />;
  if (!bekliyor(q)) return null;
  if (!online) return <Cevrimdisi retry={again} className={className} />;
  return <PriceLoading rows={rows} onRetry={again} testId={testId} className={className} />;
}

/** Yükleniyor: liste satırına benzeyen iskeletler + "Fiyatlar getiriliyor…"; 5 sn sonra "Bağlantı yavaş · Bekliyoruz · Tekrar dene"
 *  (dokununca istek yeniden atılır, süre baştan). */
export function PriceLoading({ rows = 4, onRetry, testId = "fiyat-yukleniyor", className }: { rows?: number; onRetry?: () => void; testId?: string; className?: string }) {
  const [round, setRound] = useState(0);
  return <PriceWaiting key={round} rows={rows} testId={testId} className={className} onRetry={onRetry && (() => { setRound((n) => n + 1); onRetry(); })} />;
}

function PriceWaiting({ rows, onRetry, testId, className }: { rows: number; onRetry?: () => void; testId: string; className?: string }) {
  const evre = useBekleme(true);
  return (
    <div className={cx("space-y-2", className)} data-testid={testId} aria-busy="true">
      <ul className="divide-y divide-stone-100 rounded-lg border border-stone-200 dark:divide-stone-800 dark:border-stone-700" aria-hidden="true">
        {Array.from({ length: rows }, (_, i) => (
          <li key={i} className="flex items-center gap-3 px-3 py-3">
            <span className="min-w-0 flex-1 space-y-1.5">
              <span className="block h-3.5 animate-pulse rounded bg-stone-200 motion-reduce:animate-none dark:bg-stone-700" style={{ width: `${70 - (i % 3) * 12}%` }} />
              <span className="block h-2.5 animate-pulse rounded bg-stone-100 motion-reduce:animate-none dark:bg-stone-800" style={{ width: `${42 - (i % 2) * 10}%` }} />
            </span>
            <span className="h-3.5 w-14 shrink-0 animate-pulse rounded bg-stone-200 motion-reduce:animate-none dark:bg-stone-700" />
          </li>
        ))}
      </ul>
      <p className="flex min-h-5 flex-wrap items-center gap-x-1.5 text-sm text-stone-500 dark:text-stone-400" role="status" aria-live="polite" data-evre={evre}>
        <Donen />
        {evre === 2 ? <><span>{DURUM.yavas}{onRetry ? " ·" : ""}</span>{onRetry && (
          <button type="button" className="inline-flex min-h-11 items-center font-semibold text-blue-700 hover:underline dark:text-blue-300"
            onClick={onRetry} data-testid="fiyat-tekrar">{DURUM.tekrar}</button>)}</> : <span>{DURUM.fiyatYukleniyor}</span>}
      </p>
    </div>
  );
}

/** Seçici listesi (süzgeç, sıralama, satırlar, ipucu, dipnot). onPick verilmezse salt görüntü. */
export function ComboList({ q, selected, onPick, onAny, busy, initialChain, bleed = "-mx-4 px-4", onRetry }: {
  /** seçici sorgusu (useItemCombos / useGroupCombos): veri, yükleniyor, hata */
  q: SorguBenzeri & { data?: CombosData }; selected?: ComboSel | null; onRetry?: () => void;
  onPick?: (r: ComboRow) => void; onAny?: () => void; busy?: boolean; initialChain?: string | null;
  /** çip satırının kaydırma payı: kabın iç boşluğu kadar (pencerede px-4, kartta px-3) */
  bleed?: string;
}) {
  const data = q.data;
  const [chain, setChain] = useState<string | null>(initialChain ? chainName(initialChain) : null);
  const [brand, setBrand] = useState<string | null>(null);
  const [size, setSize] = useState<string | null>(null);
  const [by, setBy] = useState<ComboSort>("price");
  const today = todayISO();
  const all = data?.rows ?? [];
  const chains = data?.chains.map(chainName) ?? [];
  // önceden seçili zincir listede yoksa süzgeç boş kalmasın diye yine çip olarak görünür
  const chainItems = chain && !chains.some((c) => c.toLocaleLowerCase("tr") === chain.toLocaleLowerCase("tr")) ? [chain, ...chains] : chains;
  const rows = sortCombos(filterCombos(all, { chain, brand, size }), by);
  const { cheapest, bestValue } = comboBadges(rows);
  const sel = !!onPick;
  const foot: GroupBest[] = all.map((r) => ({ chain: r.chain, price: r.price, source: r.source as GroupBest["source"], date: r.date,
    attribution: r.source === "resmi" ? data?.attribution?.[0] ?? null : null }));
  return (
    <div className="space-y-3" data-testid="secici">
      {sel && onAny && (
        <button type="button" onClick={onAny} disabled={busy} aria-pressed={!!data && isAnySel(selected)} data-testid="marka-fark-etmez"
          className={cx("flex w-full items-center gap-3 rounded-lg border px-3 py-2.5 text-left hover:bg-stone-50 dark:hover:bg-stone-800/60",
            data && isAnySel(selected) ? "border-green-300 bg-green-50/70 dark:border-green-800 dark:bg-green-950/30" : "border-stone-200 dark:border-stone-700")}>
          <span className="min-w-0 flex-1"><span className="block font-medium">Marka fark etmez</span>
            <span className="block text-xs text-stone-500 dark:text-stone-400">En uygun yeri plan seçer</span></span>
          {data && isAnySel(selected) && <Check size={18} className="shrink-0 text-green-700 dark:text-green-400" aria-label="seçili" />}
        </button>
      )}
      <PriceStatus q={q} onRetry={onRetry} />
      {data && all.length > 0 && (
        <div className="space-y-1.5">
          <Chips bleed={bleed} label="Zincir" all="Tüm zincirler" items={chainItems} value={chain} onChange={setChain} />
          {data.brands.length > 0 && <Chips bleed={bleed} label="Marka" all="Tüm markalar" items={data.brands} value={brand} onChange={setBrand} />}
          {data.sizes.length > 1 && <Chips bleed={bleed} label="Boy" all="Tüm boylar" items={data.sizes.map((s) => s.key)} value={size} onChange={setSize}
            text={(k) => data.sizes.find((s) => s.key === k)?.size_label ?? k} />}
          <Segmented label="Sıralama" value={by} onChange={setBy} options={[{ value: "price", label: "Fiyat" }, { value: "unit", label: "Birim fiyat" }]} />
        </div>
      )}
      {data && !all.length && <p className="rounded-lg border border-stone-200 px-3 py-2 text-sm text-stone-500 dark:border-stone-700" data-testid="secici-bos">
        Bu ürün için zincir/marka fiyatı yok{sel && onAny && <> · <button type="button" className="font-medium text-blue-700 hover:underline dark:text-blue-300" onClick={onAny} disabled={busy}>Marka fark etmez</button></>}</p>}
      {data && all.length > 0 && !rows.length && <p className="rounded-lg border border-stone-200 px-3 py-2 text-sm text-stone-500 dark:border-stone-700">Bu süzgeçle sonuç yok.</p>}
      {rows.length > 0 && (
        <ul className="divide-y divide-stone-100 rounded-lg border border-stone-200 dark:divide-stone-800 dark:border-stone-700" aria-label="Zincir ve marka">
          {rows.map((r, i) => {
            const on = isSelected(selected, r);
            const pu = unitPriceText(r.unit_price_per, r.per_unit, money);
            const sub = [pu, offerSource(r, today)].filter(Boolean).join(" · ");
            const body = (
              <>
                <span className="min-w-0 flex-1">
                  <span className="block font-medium break-words">{maskText(comboTitle(r))}</span>
                  {(sub || r.discount || r === cheapest || r === bestValue) && (
                    <span className="mt-0.5 flex flex-wrap items-center gap-1.5 text-xs text-stone-500 tabular-nums dark:text-stone-400">
                      {sub && <span className="break-words">{maskText(sub)}</span>}
                      {r === cheapest && <Badge tone="green">En ucuz</Badge>}
                      {r === bestValue && <Badge tone="blue">En kârlı</Badge>}
                      {r.discount && <OfferBadges b={{ chain: r.chain, price: r.price, source: "resmi", discount: true }} />}
                    </span>
                  )}
                </span>
                <span className="shrink-0 text-right font-medium tabular-nums">{money(r.price)}</span>
                {sel && <span className="w-5 shrink-0">{on && <Check size={18} className="text-green-700 dark:text-green-400" aria-label="seçili" />}</span>}
              </>
            );
            return (
              <li key={`${r.chain}|${r.brand ?? ""}|${r.key}|${i}`} data-testid="secici-satir">
                {sel ? (
                  <button type="button" onClick={() => onPick!(r)} disabled={busy} aria-pressed={on}
                    className={cx("flex w-full items-start gap-3 px-3 py-2.5 text-left hover:bg-stone-50 dark:hover:bg-stone-800/60", on && "bg-green-50/70 dark:bg-green-950/30")}>{body}</button>
                ) : <div className="flex items-start gap-3 px-3 py-2.5">{body}</div>}
              </li>
            );
          })}
        </ul>
      )}
      <HiddenHint count={data?.hidden?.count} />
      <PriceFootnote best={foot} today={today} />
    </div>
  );
}

/** "Zincir ve marka seç" penceresi (satır bağlamı): dokununca tek PATCH, bildirim, kapanır. */
export function ComboSheet({ itemId, chain, onClose, onDone, back }: {
  itemId: number; chain?: string | null; onClose: () => void;
  /** açıldığı pencerenin adımı: "← Ürünü düzenle" (o pencere bu sırada gizli) */
  back?: string;
  /** PATCH'ten sonra (düzenle penceresi seçili boyu günceller) */
  onDone?: (p: { store: string | null; brand: string | null; group_key?: string }) => void;
}) {
  const ui = useUI();
  const qc = useQueryClient();
  const q = useItemCombos(itemId);
  const [busy, setBusy] = useState(false);
  const apply = async (p: { store: string | null; brand: string | null; group_key?: string }, msg: string) => {
    setBusy(true);
    try {
      await send(`/api/shopping-list/${itemId}`, { method: "PATCH", json: p });
      ui.toast(msg);
      onDone?.(p);
      onClose();
    } catch (e) {
      ui.error(e);
    } finally {
      setBusy(false);
      for (const k of ["shopping-list", "shopping-optimize", "shopping-plan", "shopping-sizes", COMBOS]) void qc.invalidateQueries({ queryKey: [k] });
    }
  };
  return (
    <Modal title="Zincir ve marka seç" onClose={onClose} back={back ? { label: back, onClick: onClose } : undefined}>
      <ComboList q={q} selected={q.data?.selected} busy={busy} initialChain={chain}
        onRetry={() => retry(qc, itemCombosKey(itemId))}
        onAny={() => void apply({ ...ANY_PATCH }, ANY_TOAST)}
        onPick={(r) => { const p = comboPatch(r); void apply(p, comboToast(r)); }} />
    </Modal>
  );
}
