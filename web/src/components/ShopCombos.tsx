// Alışveriş listesi v5 (yetenek shop_combo_picker): TEK seçici "Zincir ve marka seç" — satırlar zincir × marka × boy
// ("BİM · Örneksüt 1 L"), süzgeç çipleri (zincir / marka / boy), "Fiyat" | "Birim fiyat" sıralaması, en üstte "Marka fark
// etmez". v4'teki Marka çipleri + Mağazalara göre + Mağaza bölümünün yerine geçer.
// - GET /api/shopping-list/{id}/combos?all_sizes=true (satır; selected {store, brand, key}) — dokununca tek PATCH
//   {store, brand, group_key}; "Marka fark etmez" {brand: null, store: null}.
// - GET /api/prices/combos?group=… (Fiyatlar grup ayrıntısı; salt görüntü).
// Plan durağından açılınca zincir süzgeci o zincirle önceden seçili (değiştirilebilir: liste bütün zincirlerle gelir).
// Mobil karşılığı: hesapkitap-mobile.
import { useQuery, useQueryClient } from "@tanstack/react-query";
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

function Chips<T extends string | null>({ label, all, items, value, onChange, text, bleed }: {
  label: string; all: string; items: T[]; value: T | null; onChange: (v: T | null) => void; text?: (v: T) => string; bleed: string;
}) {
  return (
    <div className={cx("flex gap-1.5 overflow-x-auto pb-0.5", bleed)} role="radiogroup" aria-label={label}>
      {[null, ...items].map((v) => {
        const on = (value ?? null) === v;
        return (
          <button key={v ?? "_"} type="button" role="radio" aria-checked={on} onClick={() => onChange(v as T | null)}
            className={cx("shrink-0 rounded-full border px-2.5 py-1 text-xs whitespace-nowrap",
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

/** Seçici listesi (süzgeç, sıralama, satırlar, ipucu, dipnot). onPick verilmezse salt görüntü. */
export function ComboList({ data, loading, error, selected, onPick, onAny, busy, initialChain, bleed = "-mx-4 px-4" }: {
  data?: CombosData; loading?: boolean; error?: Error | null; selected?: ComboSel | null;
  onPick?: (r: ComboRow) => void; onAny?: () => void; busy?: boolean; initialChain?: string | null;
  /** çip satırının kaydırma payı: kabın iç boşluğu kadar (pencerede px-4, kartta px-3) */
  bleed?: string;
}) {
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
      {loading && <p className="text-sm text-stone-500">Yükleniyor…</p>}
      {error && <p className="text-sm text-red-600 dark:text-red-400" role="alert">{error.message}</p>}
      {sel && onAny && (
        <button type="button" onClick={onAny} disabled={busy} aria-pressed={isAnySel(selected)} data-testid="marka-fark-etmez"
          className={cx("flex w-full items-center gap-3 rounded-lg border px-3 py-2.5 text-left hover:bg-stone-50 dark:hover:bg-stone-800/60",
            isAnySel(selected) ? "border-green-300 bg-green-50/70 dark:border-green-800 dark:bg-green-950/30" : "border-stone-200 dark:border-stone-700")}>
          <span className="min-w-0 flex-1"><span className="block font-medium">Marka fark etmez</span>
            <span className="block text-xs text-stone-500 dark:text-stone-400">En uygun yeri plan seçer</span></span>
          {isAnySel(selected) && <Check size={18} className="shrink-0 text-green-700 dark:text-green-400" aria-label="seçili" />}
        </button>
      )}
      {data && all.length > 0 && (
        <div className="space-y-1.5">
          <Chips bleed={bleed} label="Zincir" all="Tüm zincirler" items={chainItems} value={chain} onChange={setChain} />
          {data.brands.length > 0 && <Chips bleed={bleed} label="Marka" all="Tüm markalar" items={data.brands} value={brand} onChange={setBrand} />}
          {data.sizes.length > 1 && <Chips bleed={bleed} label="Boy" all="Tüm boylar" items={data.sizes.map((s) => s.key)} value={size} onChange={setSize}
            text={(k) => data.sizes.find((s) => s.key === k)?.size_label ?? k} />}
          <Segmented label="Sıralama" value={by} onChange={setBy} options={[{ value: "price", label: "Fiyat" }, { value: "unit", label: "Birim fiyat" }]} />
        </div>
      )}
      {data && !all.length && <p className="rounded-lg border border-stone-200 px-3 py-2 text-sm text-stone-500 dark:border-stone-700">Bu ürün için fiyatı görülen zincir yok.</p>}
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
export function ComboSheet({ itemId, chain, onClose, onDone }: {
  itemId: number; chain?: string | null; onClose: () => void;
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
      ui.error((e as Error).message);
    } finally {
      setBusy(false);
      for (const k of ["shopping-list", "shopping-optimize", "shopping-plan", "shopping-sizes", COMBOS]) void qc.invalidateQueries({ queryKey: [k] });
    }
  };
  return (
    <Modal title="Zincir ve marka seç" onClose={onClose}>
      <ComboList data={q.data} loading={q.isLoading} error={q.error as Error | null} selected={q.data?.selected} busy={busy} initialChain={chain}
        onAny={() => void apply({ ...ANY_PATCH }, ANY_TOAST)}
        onPick={(r) => { const p = comboPatch(r); void apply(p, comboToast(r)); }} />
    </Modal>
  );
}
