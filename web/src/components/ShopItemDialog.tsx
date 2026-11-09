// Alışveriş satırına dokununca: düzenle / düzelt penceresi (yetenek shopping_units). Ad (yazarken öneriler, yetenek
// shopping_autofill: GET /api/shopping-list/suggest — seçilen öneri boyu sabitler), miktar (küçük adım düğmeleri; adet 1,
// kg 0,5, g 100, L 0,5, ml 250; ondalık yazılabilir) ve birim (adet kg g L ml), boy (GET /{id}/sizes; kg / L satırında shop_unit_sizes ile). Satır
// tanınmadıysa / boyu yoksa / birimi uymuyorsa sunucunun ipucu en üstte. "Listeden çıkar" de burada.
// Liste v4 (her biri ayrı yetenek; eski sunucuda görünmez): "Marka" çipleri (shop_brand_size; GET /{id}/brands, boş ise
// bölüm yok), boy listesi küçükten büyüğe "En ucuz: … · ₺/L" + "En kârlı" rozeti (shop_best_value), seçili boyun
// "Mağazalara göre" listesi (GET /api/prices/offers), "Mağaza" seçimi (shop_store_pin; store null = otomatik).
// Liste v5 (shop_combo_picker): bu üç bölümün yerine tek "Zincir ve marka" bölümü — seçim özeti + "Değiştir" → seçici
// (components/ShopCombos.tsx; dokununca tek PATCH). Boy listesi aynen kalır.
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Check, ChevronRight, Info, Minus, Plus, Trash2 } from "lucide-react";
import { useId, useState } from "react";
import { cx } from "../format";
import { send } from "../lib/apiError";
import type { UnpricedInfo } from "../lib/priceAdvice";
import { chainName, convertQty, fmtQty, isUnit, parseQty, stepQty, UNITS, validQty, type Unit } from "../lib/shopList";
import { packsText, pinnedChoiceText, sizesForUnit, sizesHidden, sizeSub, storeChoices, type SizeRow } from "../lib/shopStores";
import { useCap } from "./hooks";
import Modal, { kirliyseSor } from "./Modal";
import { SuggestList, useSuggest, type Suggestion } from "./ShopAutofill";
import { BrandChips, OffersList, useOffers, type BrandOpt } from "./ShopOffers";
import { HiddenHint } from "./ShopStores";
import { ComboSheet, PriceStatus, retry, useItemCombos } from "./ShopCombos";
import { bekliyor } from "./SorguDurumu";
import { comboSummary } from "../lib/combos";
import { DURUM } from "../lib/durum";

export interface EditableItem {
  id: number; text: string; qty: number | null; unit?: string | null; group_key: string | null;
  group?: { key: string; label: string; pending?: boolean } | null;
  /** yetenek shopping_assumed_size: boysuz satırın kabul edilen boyu (group_key bundan farklıysa boy elle sabitlenmiş) */
  assumed?: { key: string; label?: string; size_label?: string } | null;
  /** yetenek shop_store_pin / shop_brand_size: bağlı mağaza ve marka (null: otomatik / marka fark etmez) */
  store?: string | null; brand?: string | null;
  /** shop_brand_auto: addan okunan marka + seri ("Çaykur Turist") */
  brand_label?: string | null;
}
type SizeOpt = SizeRow;
export interface ItemPatch { text?: string; qty?: number; unit?: Unit; group_key?: string | null; brand?: string | null; store?: string | null }

/** seçim özetinde addan okunan seri de: "Çaykur Turist 1 kg" */
function withLine<T extends { brand: string | null } | null | undefined>(sel: T, it: EditableItem): T {
  return sel && sel.brand && it.brand_label && sel.brand === it.brand ? { ...sel, brand: it.brand_label } : sel;
}

export default function ShopItemDialog({ it, hint, autofill, money, busy, onClose, onSave, onRemove }: {
  it: EditableItem; hint?: UnpricedInfo; autofill: boolean; money: (n: number) => string; busy?: boolean;
  onClose: () => void; onSave: (p: ItemPatch) => void; onRemove: () => void;
}) {
  const qc = useQueryClient();
  const unit0: Unit = isUnit(it.unit) ? it.unit : "adet";
  const [text, setText] = useState(it.text);
  const [unit, setUnit] = useState<Unit>(unit0);
  const [qtyS, setQtyS] = useState(fmtQty(it.qty ?? 1));
  const [picked, setPicked] = useState<Suggestion | null>(null);
  const cur = it.group?.key ?? it.group_key;
  const [size, setSize] = useState<string | null>(cur);
  const [sugOpen, setSugOpen] = useState(hint?.reason === "no_group");
  const [active, setActive] = useState(-1);
  const lid = useId().replace(/:/g, "");
  const renamed = text.trim() !== it.text;
  const qty = parseQty(qtyS);
  const okQty = validQty(qty, unit);
  const sug = useSuggest(text, autofill && sugOpen);
  const sugItems = (sug.data ?? []).filter((s) => !(s.text === it.text && (s.group_key ?? null) === (cur ?? null)));
  // v5 seçicisi varsa v4'ün ayrı marka / mağazalara göre / mağaza bölümleri yok
  const comboOn = useCap("shop_combo_picker");
  const brandOn = useCap("shop_brand_size") && !comboOn;
  const pinOn = useCap("shop_store_pin") && !comboOn;
  const [comboOpen, setComboOpen] = useState(false);
  const bestOn = useCap("shop_best_value");
  const known = !!it.group && !it.group.pending;
  const brand0 = it.brand ?? null;
  const [brand, setBrand] = useState<string | null>(brand0);
  const [store, setStore] = useState<string | null>(it.store ?? null);
  const brands = useQuery({
    queryKey: ["shopping-brands", it.id], retry: false, enabled: brandOn && known, staleTime: 5 * 60_000,
    queryFn: () => send<{ group: string | null; brands: BrandOpt[]; selected: string | null }>(`/api/shopping-list/${it.id}/brands`),
  });
  // marka seçimi boy listesini o markanın fiyatlarıyla getirir ("" marka fark etmez); eski sunucuda parametresiz
  const sizes = useQuery({
    queryKey: brandOn ? ["shopping-sizes", it.id, brand ?? ""] : ["shopping-sizes", it.id], retry: false, enabled: known,
    placeholderData: (prev) => prev,
    queryFn: () => send<SizeOpt[]>(`/api/shopping-list/${it.id}/sizes${brandOn ? `?brand=${encodeURIComponent(brand ?? "")}` : ""}`),
  });
  // kg / L satırında da boy listesi (shop_unit_sizes): yalnız o türün boyları, kilo / litre fiyatıyla ve kaç paket
  // ettiğiyle ("1 kg için 4 paket"); eski sunucuda boy listesi yalnız adette
  const unitSizesOn = useCap("shop_unit_sizes");
  const sizeRows = unitSizesOn ? sizesForUnit(sizes.data, unit) : sizes.data;
  const sizeUnitOk = unit === "adet" || unitSizesOn;
  const showSizes = !renamed && sizeUnitOk && (sizeRows?.length ?? 0) > 0;
  const sizeBox = !renamed && sizeUnitOk && !!sizes.data; // boş listede "başka boyunu bilmiyoruz"
  const packs = (o: SizeOpt) => (unit !== "adet" && okQty ? packsText(qty, unit, o.size_amount) : null);
  const pinned = !!it.group_key && it.group_key !== it.assumed?.key; // boy elle seçilmiş: "Otomatik seç" geri alır
  const showBrand = brandOn && !renamed && (brands.data?.brands.length ?? 0) > 0;
  // "Mağazalara göre" ve mağaza seçenekleri: seçili (boylu) boy, yoksa kabul edilen boy
  const offKey = !renamed && known ? (size && size.includes("|") ? size : it.assumed?.key ?? (cur && cur.includes("|") ? cur : null)) : null;
  const offSize = sizes.data?.find((o) => o.key === offKey)?.size_label ?? it.assumed?.size_label ?? null;
  const offers = useOffers(offKey, brand, brandOn || pinOn);
  const choices = storeChoices(offers.data?.offers, store);
  const combos = useItemCombos(it.id, comboOn && known && !renamed);

  const title = hint && hint.reason !== "pending" ? "Ürünü düzelt" : "Ürünü düzenle";
  const sizesKey = brandOn ? ["shopping-sizes", it.id, brand ?? ""] : ["shopping-sizes", it.id];
  const step = (dir: 1 | -1) => setQtyS(fmtQty(stepQty(qty, unit, dir)));
  const pickUnit = (u: Unit) => { setQtyS(fmtQty(convertQty(okQty ? qty : Number.NaN, unit, u))); setUnit(u); };
  const pickSug = (s: Suggestion) => { setText(s.text); setPicked(s); setSugOpen(false); setActive(-1); };

  /** yalnız değişen alanlar */
  const patchOf = (): ItemPatch => {
    const p: ItemPatch = {};
    const t = text.trim();
    if (t && renamed) {
      p.text = t;
      if (picked && picked.text === t && picked.group_key) p.group_key = picked.group_key;
    } else if (showSizes && size !== cur && sizeRows!.some((o) => o.key === size)) p.group_key = size;
    if (!renamed && brandOn && brand !== brand0) p.brand = brand;
    if (!renamed && pinOn && store !== (it.store ?? null)) p.store = store;
    if (okQty && (qty !== (it.qty ?? 1) || unit !== unit0)) { p.qty = qty; p.unit = unit; }
    return p;
  };
  const save = () => {
    if (!text.trim() || !okQty) return; // Kaydet düğmesi de kapalı; "Değişiklikler kaydedilmedi" sorusundan gelince pencere açık kalır
    const p = patchOf();
    if (!Object.keys(p).length) return onClose();
    onSave(p);
  };
  // kaydedilmemiş değişiklik (Bulgu 5; mobil alisveris/Duzenle ile aynı): ad, miktar, birim, boy, marka, mağaza. "Zincir ve
  // marka seç" seçimi orada hemen kaydedilir (kirli sayılmaz). Kapatınca "Değişiklikler kaydedilmedi · Kaydet / Kaydetmeden çık / Vazgeç".
  const dirty = renamed || !okQty || Object.keys(patchOf()).length > 0;
  const onKey = (e: React.KeyboardEvent) => {
    if (!sugOpen || !sugItems.length) return;
    if (e.key === "ArrowDown") { e.preventDefault(); setActive((a) => (a + 1) % sugItems.length); }
    else if (e.key === "ArrowUp") { e.preventDefault(); setActive((a) => (a <= 0 ? sugItems.length - 1 : a - 1)); }
    else if (e.key === "Enter" && active >= 0) { e.preventDefault(); pickSug(sugItems[active]!); }
  };

  return (
    <>
    <Modal title={title} onClose={onClose} hidden={comboOpen} dirty={dirty && !busy} onSave={save} footer={<>
      <button type="button" className="btn-danger" onClick={onRemove} disabled={busy} data-testid="listeden-cikar"><Trash2 size={16} aria-hidden /> Listeden çıkar</button>
      <div className="flex-1" />
      <button type="button" className="btn-ghost hidden sm:inline-flex" onClick={() => void kirliyseSor(dirty && !busy, onClose, save)}>Vazgeç</button>
      <button type="button" className="btn-primary" disabled={busy || !text.trim() || !okQty} onClick={save}>Kaydet</button>
    </>}>
      <div className="space-y-4">
        {hint && (
          <p className="flex gap-2 rounded-lg bg-amber-50 px-3 py-2 text-sm text-amber-900 dark:bg-amber-950/40 dark:text-amber-200" data-testid="duzelt-ipucu">
            <Info size={16} className="mt-0.5 shrink-0" aria-hidden />{hint.hint}</p>
        )}
        <div className="relative">
          <label className="label" htmlFor="sd-text">Ürün</label>
          <input id="sd-text" className="input" value={text} maxLength={120} autoComplete="off"
            onChange={(e) => { setText(e.target.value); setPicked(null); setSugOpen(true); setActive(-1); }}
            onFocus={() => setSugOpen(true)} onKeyDown={onKey}
            {...(autofill ? { role: "combobox", "aria-expanded": sugOpen && sugItems.length > 0, "aria-controls": lid, "aria-autocomplete": "list" as const,
              "aria-activedescendant": active >= 0 ? `${lid}-${active}` : undefined } : {})} />
          {autofill && sugOpen && sugItems.length > 0 && text.trim().length >= 2 && (
            <div className="mt-2">
              <p className="mb-1 text-xs text-stone-500 dark:text-stone-400">Bunu mu demek istedin?</p>
              <SuggestList id={lid} items={sugItems} active={active} onPick={pickSug} className="max-h-56 shadow-none" />
            </div>
          )}
        </div>

        <div>
          <span className="label" id="sd-qty-l">Miktar</span>
          <div className="flex flex-wrap items-center gap-2">
            <div className="flex items-center rounded-lg border border-stone-300 dark:border-stone-700" role="group" aria-labelledby="sd-qty-l">
              <button type="button" className="btn-ghost rounded-r-none px-2.5 py-2" aria-label="Azalt" onClick={() => step(-1)}><Minus size={16} /></button>
              <input className="w-16 border-x border-stone-200 bg-transparent py-1.5 text-center text-base tabular-nums outline-none focus:bg-blue-50/50 sm:text-sm dark:border-stone-700 dark:focus:bg-blue-950/30"
                inputMode="decimal" value={qtyS} onChange={(e) => setQtyS(e.target.value)} aria-label={`Miktar (${unit})`} aria-invalid={!okQty} />
              <button type="button" className="btn-ghost rounded-l-none px-2.5 py-2" aria-label="Artır" onClick={() => step(1)}><Plus size={16} /></button>
            </div>
            <div className="flex rounded-lg bg-stone-100 p-1 dark:bg-stone-800" role="radiogroup" aria-label="Birim">
              {UNITS.map((u) => (
                <button key={u} type="button" role="radio" aria-checked={unit === u} onClick={() => pickUnit(u)}
                  className={cx("min-w-10 rounded-md px-2 py-1 text-sm font-medium", unit === u ? "bg-white text-stone-900 shadow-sm dark:bg-stone-600 dark:text-white" : "text-stone-600 dark:text-stone-300")}>{u}</button>
              ))}
            </div>
          </div>
          {!okQty && <p className="mt-1 text-xs text-red-600 dark:text-red-400" role="alert">Geçerli bir miktar yaz (ör. 2,5).</p>}
        </div>

        {showBrand && (
          <div>
            <span className="label" id="sd-marka-l">Marka</span>
            <BrandChips id="sd-marka" brands={brands.data!.brands} value={brand} onChange={setBrand} />
          </div>
        )}

        {!renamed && sizeUnitOk && known && !sizes.data && (bekliyor(sizes) || sizes.error) && (
          <div data-testid="boy-durum">
            <span className="label">Boy</span>
            <PriceStatus q={sizes} rows={3} testId="boy-yukleniyor" onRetry={() => retry(qc, sizesKey)} />
          </div>
        )}
        {sizeBox && (
          <div>
            <span className="label">Boy</span>
            {!showSizes && <p className="rounded-lg border border-stone-200 px-3 py-2 text-sm text-stone-500 dark:border-stone-700">Bu ürünün başka boyunu bilmiyoruz.</p>}
            {showSizes && (
            <ul className="divide-y divide-stone-100 rounded-lg border border-stone-200 dark:divide-stone-800 dark:border-stone-700" data-testid="boy-listesi">
              {sizeRows!.map((o) => bestOn ? (
                <li key={o.key}>
                  <button type="button" onClick={() => setSize(o.key)} aria-pressed={o.key === size} data-testid="boy-satir"
                    className={cx("flex w-full items-start gap-3 px-3 py-2 text-left hover:bg-stone-50 dark:hover:bg-stone-800/60", o.key === size && "bg-green-50/70 dark:bg-green-950/30")}>
                    <span className="min-w-0 flex-1">
                      <span className="flex flex-wrap items-center gap-1.5 font-medium">
                        {o.size_label}
                        {o.best_value && <span className="rounded bg-green-100 px-1.5 py-0.5 text-[11px] font-semibold text-green-800 dark:bg-green-950 dark:text-green-300" data-testid="en-karli">En kârlı</span>}
                        {o.key === size && <Check size={15} className="text-green-700 dark:text-green-400" aria-label="seçili" />}
                      </span>
                      <span className={cx("block text-xs break-words tabular-nums", o.price == null ? "text-stone-400 dark:text-stone-500" : "text-stone-500 dark:text-stone-400")}>{sizeSub(o, money)}</span>
                      {packs(o) && <span className="block text-xs text-stone-500 tabular-nums dark:text-stone-400" data-testid="boy-paket">{fmtQty(qty)} {unit} için {packs(o)}</span>}
                    </span>
                  </button>
                </li>
              ) : (
                <li key={o.key}>
                  <button type="button" onClick={() => setSize(o.key)} aria-pressed={o.key === size}
                    className={cx("flex w-full items-center gap-3 px-3 py-2 text-left hover:bg-stone-50 dark:hover:bg-stone-800/60", o.key === size && "bg-green-50/70 dark:bg-green-950/30")}>
                    <span className="min-w-0 flex-1 font-medium">{o.size_label}{o.key === size && <Check size={15} className="ml-1.5 inline text-green-700 dark:text-green-400" aria-label="seçili" />}</span>
                    <span className="shrink-0 text-right text-sm text-stone-600 tabular-nums dark:text-stone-300">
                      {o.price != null ? <>{o.chain ? `${chainName(o.chain)} ` : ""}{money(o.price)}{o.unit_price_per != null && o.per_unit ? <span className="block text-xs text-stone-500 dark:text-stone-400">{money(o.unit_price_per)}/{o.per_unit}</span> : null}</> : <span className="text-stone-400">fiyat yok</span>}
                    </span>
                  </button>
                </li>
              ))}
            </ul>)}
            {/* "Mağazalara göre" açıksa ipucu onun altında (tek yerde) */}
            {showSizes && !(brandOn && offKey) && !comboOn && <HiddenHint count={sizesHidden(sizeRows, size)} className="mt-1.5" />}
            {pinned && <button type="button" className="btn-ghost mt-2 border border-stone-300 text-sm dark:border-stone-700" disabled={busy}
              onClick={() => onSave({ group_key: null })}>Otomatik seç</button>}
          </div>
        )}

        {comboOn && known && !renamed && (
          <div data-testid="zincir-marka">
            <span className="label">Zincir ve marka</span>
            <button type="button" onClick={() => setComboOpen(true)} disabled={busy}
              className="flex w-full items-center gap-3 rounded-lg border border-stone-200 px-3 py-2.5 text-left hover:bg-stone-50 dark:border-stone-700 dark:hover:bg-stone-800/60">
              <span className="min-w-0 flex-1 font-medium break-words tabular-nums" data-testid="zincir-marka-ozet">
                {combos.data ? comboSummary(withLine(combos.data.selected, it), combos.data.rows, money) : combos.isLoading ? DURUM.fiyatYukleniyor : comboSummary({ store: it.store ?? null, brand: it.brand ?? null, key: cur }, [], money)}</span>
              <span className="inline-flex shrink-0 items-center gap-0.5 text-sm font-medium text-blue-700 dark:text-blue-300">Değiştir<ChevronRight size={16} aria-hidden /></span>
            </button>
            <HiddenHint count={combos.data?.hidden?.count} className="mt-1.5" />
          </div>
        )}

        {brandOn && offKey && <OffersList group={offKey} brand={brand} sizeLabel={offSize} money={money} />}

        {pinOn && !renamed && known && (
          <div>
            <span className="label" id="sd-magaza-l">Mağaza</span>
            <p className="mb-1.5 text-sm text-stone-600 dark:text-stone-300">Nereden alacağım?</p>
            <div className="flex flex-wrap gap-2" role="radiogroup" aria-labelledby="sd-magaza-l" data-testid="magaza-secimi">
              {[null, ...choices].map((c) => {
                const on = store === c;
                return (
                  <button key={c ?? "_"} type="button" role="radio" aria-checked={on} onClick={() => setStore(c)}
                    className={cx("hit inline-flex min-h-9 max-w-full items-center rounded-full border px-3 py-1 text-left text-sm break-words",
                      on ? "border-green-700 bg-green-700 text-white dark:border-green-500 dark:bg-green-600"
                        : "border-stone-300 text-stone-700 hover:bg-stone-100 dark:border-stone-600 dark:text-stone-200 dark:hover:bg-stone-800")}>
                    {c ?? "En uygun yer (otomatik)"}</button>
                );
              })}
            </div>
            {store && <p className="mt-1.5 text-sm font-medium break-words text-green-800 dark:text-green-300" data-testid="magaza-secildi">{pinnedChoiceText(store)}</p>}
          </div>
        )}
      </div>
    </Modal>
    {/* seçici bu pencerenin ADIMI: düzenle gizlenir (yazılanlar kalır), "← Ürünü düzenle" geri getirir */}
    {comboOpen && <ComboSheet itemId={it.id} back={title} onClose={() => setComboOpen(false)} onDone={(p) => { if (p.group_key) setSize(p.group_key); }} />}
    </>
  );
}
