// Alışveriş listesi v4 (yetenekler shop_store_filter / shop_store_pin / shop_brand_size / shop_best_value): saf yazılar.
// Mağaza süzgeci (kapatılan zincirler + yerel marketler), satırı mağazaya bağlama, marka + boy, "Mağazalara göre"
// listesi, plan kârlılığı ve boy önerisi. Metinler mobil (hesapkitap-mobile) ile birebir aynı. Tutar biçimi parametre:
// gizli tutar maskesi çağıranda.
import { dayWord } from "./priceView.ts";
import { chainName } from "./shopList.ts";

type Money = (n: number) => string;

// ---------------------------------------------------------------- mağaza süzgeci

export interface ShopHidden { chains: string[]; local: boolean }
/** Gizli ipucu kaynağı (liste, plan, teklifler): {count, chains, local} */
export interface HiddenOut { count: number; chains?: string[]; local?: boolean }
export interface StoresData {
  hidden: ShopHidden; chains: { chain: string; hidden: boolean }[];
  local: { hidden: boolean; names: string[]; count: number };
}

/** Kapalı sayısı: zincirler + (yerel kapalıysa) 1. */
export const closedCount = (h?: Partial<ShopHidden> | null) => (h?.chains?.length ?? 0) + (h?.local ? 1 : 0);
/** Liste düğmesi: "Mağazalar" / "Mağazalar · 2 kapalı" */
export const storesButtonText = (h?: Partial<ShopHidden> | null) => {
  const n = closedCount(h);
  return n > 0 ? `Mağazalar · ${n} kapalı` : "Mağazalar";
};
/** Ayarlar satırının değeri: "Hepsi açık" / "2 kapalı" */
export const storesSettingValue = (h?: Partial<ShopHidden> | null) => {
  const n = closedCount(h);
  return n > 0 ? `${n} kapalı` : "Hepsi açık";
};
/** "3 mağaza gizli" (ardından "· Değiştir" düğmesi); 0 ise null */
export const hiddenText = (n?: number | null) => (n && n > 0 ? `${n} mağaza gizli` : null);
/** Yerel marketler alt yazısı: ilk 3 ad + " ve N yer daha"; ad yoksa genel açıklama. */
export function localSub(names: string[] | undefined, count: number): string {
  const n = (names ?? []).filter(Boolean);
  if (!n.length) return "Zincir olmayan mahalle marketleri, manavlar";
  const more = Math.max(count, n.length) - 3;
  return `${n.slice(0, 3).join(", ")}${more > 0 ? ` ve ${more} yer daha` : ""}`;
}
/** Zincirin anahtarı değişince kaydedilecek tam liste (sunucu tam liste bekler); büyük / küçük harf fark etmez. */
export function toggleHidden(chains: string[], chain: string, hide: boolean): string[] {
  const k = chain.toLocaleLowerCase("tr");
  const rest = chains.filter((c) => c.toLocaleLowerCase("tr") !== k);
  return hide ? [...rest, chain] : rest;
}
/** Sheet'i yanıt beklemeden güncelle (iyimser). */
export function applyHidden(d: StoresData, h: ShopHidden): StoresData {
  const ks = new Set(h.chains.map((c) => c.toLocaleLowerCase("tr")));
  return { hidden: h, chains: d.chains.map((c) => ({ ...c, hidden: ks.has(c.chain.toLocaleLowerCase("tr")) })), local: { ...d.local, hidden: h.local } };
}

// ---------------------------------------------------------------- birim fiyat, boy listesi

/** "₺199,00/L" (birim fiyat yoksa null) */
export const unitPriceText = (v: number | null | undefined, per: string | null | undefined, m: Money) =>
  v != null && per ? `${m(v)}/${per}` : null;

export interface SizeRow {
  key: string; label: string; size_label: string; size_amount?: number | null; chain: string | null; price: number | null;
  unit_price_per?: number | null; per_unit?: string | null; best_value?: boolean; hidden_count?: number;
  /** yetenek shop_unit_sizes: boyun türü (fiyatsız boyda da) */
  size_unit?: "L" | "kg" | "adet" | null;
}

/** Miktar biriminin türü: kg / g → kg, L / ml → L; adette null (her boy paket olarak alınır). */
export const unitFamily = (u: string | null | undefined): "kg" | "L" | null =>
  u === "kg" || u === "g" ? "kg" : u === "L" || u === "ml" ? "L" : null;

/** Seçili birimde gösterilecek boylar (shop_unit_sizes): kg / L satırında yalnız o türün boyları; türü bilinmeyen boy
 *  (eski sunucu, fiyatsız) kalır. Adette hepsi. */
export function sizesForUnit<T extends Pick<SizeRow, "size_unit" | "per_unit">>(rows: T[] | undefined, unit: string | null | undefined): T[] {
  const fam = unitFamily(unit);
  if (!rows || !fam) return rows ?? [];
  return rows.filter((r) => { const f = r.size_unit ?? r.per_unit; return !f || f === fam; });
}

/** kg / L satırında boyun kaç paket ettiği: 1 kg için 250 g → "4 paket"; tam bölünmüyorsa yukarı ("≈ 3 paket"). */
export function packsText(qty: number, unit: string | null | undefined, sizeAmount: number | null | undefined): string | null {
  const fam = unitFamily(unit);
  if (!fam || !sizeAmount || !(qty > 0)) return null;
  const need = unit === "g" || unit === "ml" ? qty / 1000 : qty;
  const n = need / sizeAmount;
  const whole = Math.abs(n - Math.round(n)) < 1e-6;
  const c = whole ? Math.round(n) : Math.ceil(n);
  return c >= 1 ? `${whole ? "" : "≈ "}${c} paket` : null;
}
/** Boy satırının alt yazısı: "En ucuz: BİM ₺32,50 · ₺32,50/L" / "En ucuz: BİM ₺32,50" / "Fiyat yok" */
export function sizeSub(o: Pick<SizeRow, "chain" | "price" | "unit_price_per" | "per_unit">, m: Money): string {
  if (o.price == null) return "Fiyat yok";
  const head = `En ucuz: ${o.chain ? `${chainName(o.chain)} ` : ""}${m(o.price)}`;
  const pu = unitPriceText(o.unit_price_per, o.per_unit, m);
  return pu ? `${head} · ${pu}` : head;
}
/** Boy listesinin gizli sayısı: seçili boyun hidden_count'u, yoksa (ya da 0 ise) en büyüğü. */
export function sizesHidden(rows: Pick<SizeRow, "key" | "hidden_count">[] | undefined, selected: string | null | undefined): number {
  if (!rows?.length) return 0;
  const sel = rows.find((r) => r.key === selected)?.hidden_count ?? 0;
  return sel > 0 ? sel : Math.max(0, ...rows.map((r) => r.hidden_count ?? 0));
}

// ---------------------------------------------------------------- mağazalara göre

export interface Offer {
  chain: string; price: number; unit_price_per?: number | null; per_unit?: string | null; brand?: string | null;
  source: "own" | "community" | "resmi" | string; date?: string | null; n?: number | null; region?: string | null;
  discount?: boolean; official?: { price: number; date?: string | null } | null; differs?: boolean;
}
export interface OffersData {
  group: string; label: string; size_label: string | null; brand: string | null; offers: Offer[];
  hidden?: HiddenOut | null; attribution?: string[];
}
/** Başlık: "Sütaş · 1 L" / "Her marka · 1 L" */
export const offersTitle = (brand: string | null | undefined, sizeLabel: string | null | undefined) =>
  [brand || "Her marka", sizeLabel].filter(Boolean).join(" · ");
/** Teklifin kaynak yazısı: resmi "dün" / "12 Eki", own "Senin fişin · 3 Kas", community "4 kişi". */
export function offerSource(o: Pick<Offer, "source" | "date" | "n">, today: string): string {
  if (o.source === "own") return o.date ? `Senin fişin · ${dayWord(o.date, today)}` : "Senin fişin";
  if (o.source === "community") return o.n ? `${o.n} kişi` : "topluluk";
  return o.date ? dayWord(o.date, today) : "";
}
/** Satırın ikinci satırı: "₺32,50/L · dün" */
export const offerSub = (o: Offer, today: string, m: Money) =>
  [unitPriceText(o.unit_price_per, o.per_unit, m), offerSource(o, today)].filter(Boolean).join(" · ");
/** Mağaza seçeneklerinin zincirleri (tekrarsız; teklif sırasıyla, seçince yer değiştirmez); satırın kendi mağazası
 *  tekliflerde yoksa (o boyda fiyatı yok ya da kapatılmış) sonda. */
export function storeChoices(offers: Pick<Offer, "chain">[] | undefined, current?: string | null): string[] {
  const seen = new Set<string>();
  const out: string[] = [];
  for (const c of [...(offers ?? []).map((o) => chainName(o.chain)), ...(current ? [current] : [])]) {
    const k = c.toLocaleLowerCase("tr");
    if (c && !seen.has(k)) { seen.add(k); out.push(c); }
  }
  return out;
}
/** "Bu ürünü Migros mağazasından alacağım" */
export const pinnedChoiceText = (chain: string) => `Bu ürünü ${chain} mağazasından alacağım`;

// ---------------------------------------------------------------- liste satırı

/** Satırın mağaza yazısı: "Migros · sen seçtin" / "Migros: fiyat yok" (bağlı değilse null). */
export function storeLine(it: { store?: string | null; store_missing?: boolean }): string | null {
  if (!it.store) return null;
  const ch = chainName(it.store);
  return it.store_missing ? `${ch}: fiyat yok` : `${ch} · sen seçtin`;
}
/** "Süt · Sütaş" (marka yoksa ya da adda zaten geçiyorsa yalnız ad) */
export function withBrand(title: string, brand?: string | null): string {
  if (!brand) return title;
  return title.toLocaleLowerCase("tr").includes(brand.toLocaleLowerCase("tr")) ? title : `${title} · ${brand}`;
}

// ---------------------------------------------------------------- Nereye gideyim?

export interface BetterSize {
  key: string; label: string; size_label: string; chain: string; packs: number; price: number; total: number; saving: number;
  unit_price_per?: number | null; per_unit?: string | null;
}
/** "En kârlı boy: 2 × 1 L · BİM ₺65 (₺12 daha az)" */
export const betterSizeText = (b: BetterSize, m: Money) =>
  `En kârlı boy: ${b.packs} × ${b.size_label} · ${chainName(b.chain)} ${m(b.total)} (${m(b.saving)} daha az)`;
/** "Fiyatı yok: süt, ekmek" */
export const pinnedUnpricedText = (xs: { text: string }[] | undefined) =>
  xs?.length ? `Fiyatı yok: ${xs.map((x) => x.text).join(", ")}` : null;
/** Seçtiğin marketlerde yok satırı: "En ucuz: BİM ₺32,50" */
export const missingCheapestText = (c: { chain: string; price: number }, m: Money) => `En ucuz: ${chainName(c.chain)} ${m(c.price)}`;

export const MAX_PICK = 3;
/** "Marketleri ben seçeyim" çipi: seçiliyse çıkar; değilse (3'ten azsa) ekle. */
export function togglePick(sel: string[], chain: string, max = MAX_PICK): string[] {
  if (sel.includes(chain)) return sel.filter((c) => c !== chain);
  return sel.length >= max ? sel : [...sel, chain];
}
/** optimize sorgusunun yolu: "/api/shopping-list/optimize" ya da "?chains=Migros,BİM" */
export const optimizePath = (chains?: string[] | null) =>
  `/api/shopping-list/optimize${chains?.length ? `?chains=${chains.map(encodeURIComponent).join(",")}` : ""}`;

// ---------------------------------------------------------------- grup anahtarının boyu (Fiyatlar grup ayrıntısı)

const KEY_SIZE = /^(?:(\d+)x)?(\d+(?:\.\d+)?)(l|ml|kg|g|ad)$/;
const KEY_UNIT = { l: "L", ml: "ml", kg: "kg", g: "g", ad: "adet" } as const;
/** Sunucu product_groups.size_label ile aynı: "sut|1l" → "1 L", "su|6x500ml" → "6×500 ml", "elma|kg" → "kg",
 *  "yumurta|30ad" → "30 adet"; boysuz null. */
export function keySizeLabel(key: string | null | undefined): string | null {
  if (!key || !key.includes("|")) return null;
  const sz = key.split("|", 2)[1]!;
  if (sz === "kg") return "kg";
  const m = KEY_SIZE.exec(sz);
  if (!m) return null;
  const unit = KEY_UNIT[m[3] as keyof typeof KEY_UNIT];
  const v = m[2]!.replace(".", ",");
  return m[1] ? `${m[1]}×${v} ${unit}` : `${v} ${unit}`;
}
/** Boy sırası (sunucu _size_sort ile aynı): küçükten büyüğe, türler L → kg → adet, boysuz en son. */
function sizeRank(key: string): [number, number] {
  const sz = key.includes("|") ? key.split("|", 2)[1]! : "";
  if (sz === "kg") return [1, 1];
  const m = KEY_SIZE.exec(sz);
  if (!m) return [9, 0];
  const n = (m[1] ? Number(m[1]) : 1) * Number(m[2]);
  const u = m[3]!;
  return u === "l" ? [0, n] : u === "ml" ? [0, n / 1000] : u === "kg" ? [1, n] : u === "g" ? [1, n / 1000] : [2, n];
}
/** Grup ayrıntısının boy seçenekleri: grubun kendi boyu + (seçili markanın ya da bütün markaların) boyları, aynı
 *  üründe (boysuz anahtar aynı), küçükten büyüğe. */
export function sizeOptions(groupKey: string, brands: { brand: string; sizes: string[] }[] | undefined, brand: string | null): string[] {
  const base = groupKey.split("|", 1)[0];
  const from = (brands ?? []).filter((b) => !brand || b.brand === brand).flatMap((b) => b.sizes);
  const keys = [...new Set([...(groupKey.includes("|") ? [groupKey] : []), ...from])]
    .filter((k) => k.includes("|") && k.split("|", 1)[0] === base);
  return keys.sort((a, b) => { const x = sizeRank(a), y = sizeRank(b); return x[0] - y[0] || x[1] - y[1] || a.localeCompare(b); });
}
