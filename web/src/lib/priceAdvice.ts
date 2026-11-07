// Fiyat UX v2: "ne yapmalıyım?" yazıları (saf). Ürün detayı "Önerimiz" kartı (yetenek price_advice), topluluk teklif
// satırı (community_quality), alışveriş listesinde satır altı en ucuz, varsayılan boy ve sabit "Nereye gideyim?" çubuğu
// (shopping_assumed_size, shopping_headline). Mobil (hesapkitap-mobile) ile aynı metinler.
import { ayrilma, bulunma, lik } from "./priceView.ts";

type Money = (n: number) => string;

export type Advice =
  | { kind: "cheaper_at"; chain: string; price: number; paid: number; diff: number; qty: number; saving: number;
      unit_price_per?: number | null; per_unit?: string | null; source: string; n?: number | null; date?: string | null }
  | { kind: "good_price"; paid: number; median: number; pct: number }
  | { kind: "store_compare"; stores: { store: string; price: number; date?: string | null }[]; cheapest: string | { store: string; price?: number }; diff: number };

export interface MarketRef { median: number; min: number; cheapest_chain: string; day: string; source: "resmi" | "community" | string }

export interface AdviceText { tone: "save" | "good" | "info"; title: string; detail: string }

/** Önerimiz kartının başlığı ve ayrıntısı; tanınmayan tür null. */
export function adviceText(a: Advice | null | undefined, m: Money): AdviceText | null {
  if (!a) return null;
  if (a.kind === "cheaper_at") {
    const parts = [`Sen ${m(a.paid)} ödedin`];
    if (a.qty > 1 && a.saving > 0) parts.push(`${lik(a.qty)} alımda ${m(a.saving)} tasarruf`);
    else if (a.diff > 0) parts.push(`${m(a.diff)} daha ucuz`);
    return { tone: "save", title: `Bir dahaki sefere ${ayrilma(a.chain)} al: ${m(a.price)}`, detail: `${parts.join(" · ")}.` };
  }
  if (a.kind === "good_price") {
    const below = a.pct < 0 ? ` (%${Math.round(Math.abs(a.pct))} altında)` : "";
    return { tone: "good", title: "İyi fiyata alıyorsun", detail: `Son alımın ${m(a.paid)}; piyasa ortancası ${m(a.median)}${below}.` };
  }
  if (a.kind === "store_compare" && a.stores.length >= 2) {
    const cheap = typeof a.cheapest === "string" ? a.cheapest : a.cheapest.store;
    return { tone: "info", title: a.diff >= 0.01 ? `${cheap} ${m(a.diff)} ucuz` : "Gittiğin yerlerde fiyat aynı",
      detail: a.stores.slice(0, 3).map((s) => `${bulunma(s.store)} ${m(s.price)}`).join(", ") };
  }
  return null;
}

/** "Piyasa ortancası ₺35,95" */
export const marketLineLabel = (r: Pick<MarketRef, "median">, m: Money) => `Piyasa ortancası ${m(r.median)}`;

export interface CommunityOffer {
  source: string; price: number; n?: number | null; discounted_price?: number | null;
  official?: { price: number; date?: string | null; region?: string | null } | null; differs?: boolean;
}
/** Topluluk teklifi: "Kullanıcılar ₺38,50 ödüyor (5 kişi, bu hafta)" + "Market Fiyatı ₺35,00" (fark >%10 ise açıkça
 *  "Market Fiyatı farklı: ₺35,00") + "indirimli ₺33,90". Topluluk değilse null. */
export function communityParts(o: CommunityOffer, m: Money): { main: string; extra: string[] } | null {
  if (o.source !== "community") return null;
  const main = `Kullanıcılar ${m(o.price)} ödüyor${o.n ? ` (${o.n} kişi, bu hafta)` : " (bu hafta)"}`;
  const extra: string[] = [];
  if (o.official) extra.push(o.differs ? `Market Fiyatı farklı: ${m(o.official.price)}` : `Market Fiyatı ${m(o.official.price)}`);
  if (o.discounted_price != null) extra.push(`indirimli ${m(o.discounted_price)}`);
  return { main, extra };
}

// ---------------------------------------------------------------- alışveriş listesi

export interface Assumed { key: string; label: string; size_label: string }
export interface ShopBest { chain: string; price: number; unit_price_per?: number | null; per_unit?: string | null; source: string }

/** Grup adından boy: "Süt · 1 L" → "1 L"; boysuz null. */
export const sizeOf = (label?: string | null) => (label && label.includes(" · ") ? label.split(" · ").slice(1).join(" · ") : null);

/** Satır altı: "En ucuz: BİM ₺32,50 · 1 L" (boy varsayılan boydan ya da grup adından). */
export function bestInline(b: ShopBest, size: string | null, m: Money): string {
  return `En ucuz: ${b.chain} ${m(b.price)}${size ? ` · ${size}` : ""}`;
}
/** "1 L kabul edildi" */
export const assumedText = (a: Assumed) => `${a.size_label} kabul edildi`;

export interface Headline { title: string; subtitle: string; cta: string; chains: string[]; total: number; saving: number; count: number }
/** Sabit çubuk: "BİM'e git · ₺195 · 6 ürün" */
export const barMain = (h: Headline, m: Money) => `${h.cta} · ${m(h.total)} · ${h.count} ürün`;

export interface UnpricedInfo { id: number; text: string; reason: "no_size" | "no_group" | "no_price" | "pending" | string; hint: string }
