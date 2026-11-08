// Alışveriş listesi v5 (yetenek shop_combo_picker): "Zincir ve marka seç" — zincir × marka × boy satırları. Saf yazı,
// süzgeç, sıralama ve seçim hesapları. Metinler mobil (hesapkitap-mobile) ile birebir aynı. Tutar biçimi parametre.
import { chainName } from "./shopList.ts";
import { keySizeLabel, type HiddenOut } from "./shopStores.ts";

type Money = (n: number) => string;

export interface ComboRow {
  chain: string; brand: string | null; key: string; size_label?: string | null; price: number;
  unit_price_per?: number | null; per_unit?: string | null; source: "own" | "community" | "resmi" | string;
  date?: string | null; n?: number | null; region?: string | null; discount?: boolean; product?: string | null;
}
export interface ComboSel { store: string | null; brand: string | null; key: string | null }
export interface CombosData {
  keys: string[]; rows: ComboRow[]; chains: string[]; brands: string[]; sizes: { key: string; size_label: string | null }[];
  hidden?: HiddenOut | null; attribution?: string[]; selected?: ComboSel | null;
}
export type ComboSort = "price" | "unit";
export interface ComboFilter { chain: string | null; brand: string | null; size: string | null }

const same = (a?: string | null, b?: string | null) => (a ?? "").toLocaleLowerCase("tr") === (b ?? "").toLocaleLowerCase("tr");
const sizeOf = (r: Pick<ComboRow, "key" | "size_label">) => r.size_label ?? keySizeLabel(r.key);

/** "BİM · Örneksüt 1 L" / "BİM · Markasız 1 L" */
export const comboTitle = (r: Pick<ComboRow, "chain" | "brand" | "key" | "size_label">) =>
  `${chainName(r.chain)} · ${[r.brand || "Markasız", sizeOf(r)].filter(Boolean).join(" ")}`;
/** Bildirim: "BİM · Örneksüt 1 L seçildi" */
export const comboToast = (r: Pick<ComboRow, "chain" | "brand" | "key" | "size_label">) => `${comboTitle(r)} seçildi`;
export const ANY_TOAST = "Marka fark etmez seçildi";

/** Süzgeç (null = hepsi). */
export const filterCombos = (rows: ComboRow[], f: ComboFilter) => rows.filter((r) =>
  (!f.chain || same(chainName(r.chain), f.chain)) && (!f.brand || same(r.brand, f.brand)) && (!f.size || r.key === f.size));

/** "Fiyat": ucuzdan; "Birim fiyat": birim fiyatı olanlar ucuzdan, olmayanlar sonda (fiyata göre). Kararlı. */
export function sortCombos(rows: ComboRow[], by: ComboSort): ComboRow[] {
  const xs = rows.map((r, i) => ({ r, i }));
  xs.sort((a, b) => {
    if (by === "unit") {
      const ua = a.r.unit_price_per, ub = b.r.unit_price_per;
      if ((ua == null) !== (ub == null)) return ua == null ? 1 : -1;
      if (ua != null && ub != null && ua !== ub) return ua - ub;
    }
    return a.r.price - b.r.price || a.i - b.i;
  });
  return xs.map((x) => x.r);
}

/** Görünen listenin en ucuzu ve birim fiyatı en düşüğü (aynı satır olabilir); tek satırda rozet yok. */
export function comboBadges(rows: ComboRow[]): { cheapest: ComboRow | null; bestValue: ComboRow | null } {
  if (rows.length < 2) return { cheapest: null, bestValue: null };
  // toplama yok, yalnız en küçüğü seçmek (ilk gelen kazanır)
  let cheapest = rows[0]!;
  let bestValue: ComboRow | null = null;
  let units = 0;
  for (const r of rows) {
    if (r.price < cheapest.price) cheapest = r;
    if (r.unit_price_per != null) {
      units++;
      if (!bestValue || r.unit_price_per < bestValue.unit_price_per!) bestValue = r;
    }
  }
  return { cheapest, bestValue: units > 1 ? bestValue : null };
}

/** "Marka fark etmez" seçili mi (mağaza da marka da otomatik). */
export const isAnySel = (s?: ComboSel | null) => !s?.store && !s?.brand;
/** Satır seçili kombinasyon mu. */
export const isSelected = (s: ComboSel | null | undefined, r: ComboRow) =>
  !!s?.store && same(chainName(s.store), chainName(r.chain)) && same(s.brand, r.brand) && s.key === r.key;

/** Satıra dokununca tek PATCH: {store, brand, group_key}. */
export const comboPatch = (r: ComboRow) => ({ store: chainName(r.chain), brand: r.brand ?? null, group_key: r.key });
/** "Marka fark etmez": {brand: null, store: null} (boy kalır). */
export const ANY_PATCH = { brand: null, store: null } as const;

/** Düzenle penceresindeki özet: "BİM · Örneksüt 1 L · ₺36,50" / "Marka fark etmez". */
export function comboSummary(s: ComboSel | null | undefined, rows: ComboRow[] | undefined, m: Money): string {
  if (isAnySel(s)) return "Marka fark etmez";
  const r = (rows ?? []).find((x) => isSelected(s, x));
  if (r) return `${comboTitle(r)} · ${m(r.price)}`;
  const parts = [s!.brand || "Markasız", keySizeLabel(s!.key)].filter(Boolean).join(" ");
  return s!.store ? `${chainName(s!.store)} · ${parts}` : parts;
}
