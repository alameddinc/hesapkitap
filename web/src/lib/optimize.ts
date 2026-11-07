// Alışveriş listesi "Nereye gideyim?" (yetenek shopping_optimize; GET /api/shopping-list/optimize) ve ürün grafiğindeki
// piyasa bandı (yetenek price_market; GET /api/prices/market, history[].vs_market): saf yazı hesapları.
// Yazılar mobil (hesapkitap-mobile) ile birebir aynı. Tutar biçimi parametre: gizli tutar maskesi çağıranda.
import { agoWord, ayrilma, bulunma } from "./priceView.ts";
import { officialName } from "./shopList.ts";

export interface OptLine {
  id: number; text: string; qty: number; group_label: string | null; price: number; line_total: number;
  /** yetenek shopping_units: "adet" | "kg" | "g" | "L" | "ml" ve "2,5 kg" */
  unit?: string | null; amount_label?: string | null;
  unit_price_per?: number | null; per_unit?: string | null; brand?: string | null; source: string; date?: string | null; region?: string | null;
  /** kişinin kendi ürününün son alımı (ad) / kafede son alım yeri / satırın en ucuz zinciri planda yok */
  product?: string | null; own_place?: { place: string; price: number } | null; cheaper_elsewhere?: { chain: string; price: number } | null;
}
export interface OptStop { chain: string; total: number; items: OptLine[] }
export interface OptPlan {
  k: number; stops: OptStop[]; total: number; covered: number; missing: number[];
  saving_vs_single: number; saving_vs_prev: number; extra_vs_best: number; recommended: boolean;
}
export interface OptSingle { chain: string; total: number; covered: number; missing: number[]; vs_recommended: number | null }
export interface Optimize {
  region: string; date: string; stop_cost: number; max_stops: number; plans: OptPlan[]; single: OptSingle[];
  recommended_k: number | null; one_store: OptSingle | null; priced: number; unpriced: number[]; sources: string[];
  source_info: { kinds: string[]; resmi_date: string | null; national: boolean };
}

type Money = (n: number) => string;

/** "2× Zeytinyağı 1 L" (adet 1 ise yalnız ad); birimliyse "2,5 kg elma" */
export const lineTitle = (l: Pick<OptLine, "qty" | "text" | "unit">) =>
  l.unit && l.unit !== "adet" ? `${(l.qty ?? 1).toLocaleString("tr-TR", { maximumFractionDigits: 3, useGrouping: false })} ${l.unit} ${l.text}`
    : l.qty && l.qty !== 1 ? `${l.qty.toLocaleString("tr-TR", { maximumFractionDigits: 3 })}× ${l.text}` : l.text;

/** "₺59,90/L · dün" (resmî teklif ref gününden — varsayılan bugün; dipnot varken dipnotun günü — eskiyse yaşı). Marka
 *  ve il / şube satırda yazılmaz (sade: yalnız zincir). */
export function lineSub(l: OptLine, today: string, m: Money, ref: string = today): string {
  return [l.product ?? null, l.unit_price_per != null && l.per_unit ? `${m(l.unit_price_per)}/${l.per_unit}` : null,
    l.source === "resmi" && l.date && l.date < ref ? agoWord(l.date, today) : null].filter(Boolean).join(" · ");
}

/** "En iyi plan: BİM + Migros · ₺1.200" (önerilen) / "3 duraklı plan: …" */
export function headline(p: OptPlan, rec: number | null, m: Money): string {
  const chains = p.stops.map((s) => s.chain).join(" + ");
  return `${p.k === rec ? "En iyi plan" : `${p.k} duraklı plan`}: ${chains} · ${m(p.total)}`;
}

/** "Tek duraktan ₺340 ucuz" / "Tek durak yeter" */
export function subline(p: OptPlan, m: Money): string {
  if (p.saving_vs_single > 0) return `Tek duraktan ${m(p.saving_vs_single)} ucuz`;
  return p.k === 1 ? "Tek durak yeter" : "";
}

/** "BİM'den al (8 ürün) · ₺1.020" */
export const stopTitle = (s: OptStop, m: Money) => `${ayrilma(s.chain)} al (${s.items.length} ürün) · ${m(s.total)}`;

/** Karşılaştırma satırları: "Tamamı BİM'den: ₺1.240 (2 ürün BİM'de yok)", "Tamamı Migros'tan: ₺1.540 (+₺340)",
 *  "3 durak: sadece ₺40 daha ucuz — değmez". Önerilen tek duraklı planla aynı satır atlanır; en çok 3 market. */
export function compareRows(r: Optimize, m: Money): string[] {
  const rec = r.recommended_k;
  const same = rec === 1 ? r.plans[0]?.stops[0]?.chain : undefined;
  const out = r.single.filter((s) => s.chain !== same).slice(0, 3).map((s) => {
    const note = s.missing.length ? `${s.missing.length} ürün ${bulunma(s.chain)} yok`
      : s.vs_recommended != null && s.vs_recommended > 0 ? `+${m(s.vs_recommended)}` : "";
    return `Tamamı ${ayrilma(s.chain)}: ${m(s.total)}${note ? ` (${note})` : ""}`;
  });
  if (rec != null) {
    for (const p of r.plans) {
      const prev = r.plans.find((x) => x.k === p.k - 1);
      if (p.k > rec && prev && p.covered === prev.covered && p.saving_vs_prev > 0 && p.saving_vs_prev < r.stop_cost)
        out.push(`${p.k} durak: sadece ${m(p.saving_vs_prev)} daha ucuz — değmez`);
    }
  }
  return out;
}

/** Tek dipnot: "Fiyatlar: Market Fiyatı · bugün · fişlerin · topluluk" (il ve "ulusal" notu yazılmaz). Sunucu kaynak
 *  yazısı verdiyse (sources[0]; demoda "Örnek veri: …") resmî kısım onunla, "Fiyatlar:" öneki olmadan. */
export function sourceLine(r: Optimize, today: string): string {
  const k = r.source_info?.kinds ?? [];
  const o = k.includes("resmi") ? officialName(r.sources?.find(Boolean)) : null;
  const parts: string[] = [];
  if (o) parts.push(o.sample ? o.name : [o.name, r.source_info.resmi_date ? agoWord(r.source_info.resmi_date, today) : null].filter(Boolean).join(" · "));
  if (k.includes("own")) parts.push("fişlerin");
  if (k.includes("community")) parts.push("topluluk");
  return parts.length ? `${o?.sample ? "" : "Fiyatlar: "}${parts.join(" · ")}` : "";
}

/** "Fiyatı bilinmeyen: …" / "Bu planda yok: …" (adlar listeden) */
export function namesLine(label: string, ids: number[], names: Map<number, string>): string {
  const n = ids.map((i) => names.get(i)).filter(Boolean);
  return n.length ? `${label}: ${n.join(", ")}` : "";
}

// ---------------------------------------------------------------- piyasa bandı

export interface MarketDay { day: string; min: number; median: number; max: number; cheapest_chain: string; chains: number; source: "resmi" | "community" }
export interface Market { group: string; region: string; days: MarketDay[]; attribution: string | null }
export interface VsMarket { median: number; min: number; max: number; pct: number; verdict: "ucuz" | "normal" | "pahali"; cheapest_chain: string; day: string; source: string }

/** Grafik altı: son alımın (vs_market'li) piyasaya göre yeri; veri yoksa null. */
export function marketCaption(history: { date: string; price: number | null; vs_market?: VsMarket | null }[], m: Money): string | null {
  const h = history.filter((x) => x.price != null && x.vs_market).sort((a, b) => a.date.localeCompare(b.date)).pop();
  const v = h?.vs_market;
  if (!h || !v) return null;
  const pct = Math.round(Math.abs(v.pct));
  if (v.verdict === "ucuz") return `Son alımın piyasanın %${pct} altında — iyi aldın`;
  if (v.verdict === "normal") return `Son alımın piyasa fiyatında (ortanca ${m(v.median)})`;
  return `Son alımın piyasanın %${pct} üstünde; ${v.day === h.date.slice(0, 10) ? "aynı gün" : "o günlerde"} ${bulunma(v.cheapest_chain)} ${m(v.min)}`;
}

/** "Piyasa: Market Fiyatı · İstanbul" / "Piyasa: topluluk"; sunucu kaynak yazısı verdiyse (demoda "Örnek veri: …") o. */
export const marketSource = (mk: Pick<Market, "days" | "region"> & { attribution?: string | null }) =>
  !mk.days.some((d) => d.source === "resmi") ? "Piyasa: topluluk"
    : officialName(mk.attribution).sample ? `${mk.attribution} · ${mk.region}` : `Piyasa: Market Fiyatı · ${mk.region}`;
