// Alışveriş listesi v3 (yetenekler shopping_units / shopping_autofill / shopping_trips / shopping_receipt_link): saf
// yazı ve miktar hesapları. Metinden miktar okumak sunucuda (POST /api/shopping-list "2,5 kg elma"); burada yalnız
// gösterim, düzenleme penceresinin adım düğmeleri, kaydedilen plana göre gruplama, geçmiş alışveriş ve fiş bağlama
// yazıları. Mobil (hesapkitap-mobile) ile aynı metinler. Tutar biçimi parametre: gizli tutar maskesi çağıranda.
import { agoWord } from "./priceView.ts";

type Money = (n: number) => string;

export const UNITS = ["adet", "kg", "g", "L", "ml"] as const;
export type Unit = (typeof UNITS)[number];
export const isUnit = (u: unknown): u is Unit => typeof u === "string" && (UNITS as readonly string[]).includes(u);

/** Adım düğmesinin bir basışı: adet 1, kg 0,5, g 100, L 0,5, ml 250. */
export const STEP: Record<Unit, number> = { adet: 1, kg: 0.5, g: 100, L: 0.5, ml: 250 };
/** Sunucu sınırı (app/shopping.py UNIT_MAX): adet / kg / L ≤ 999, g / ml ≤ 99999. */
export const MAX: Record<Unit, number> = { adet: 999, kg: 999, g: 99_999, L: 999, ml: 99_999 };
/** Birim değişip tür de değişince (adet ↔ ağırlık ↔ hacim) başlangıç miktarı. */
const START: Record<Unit, number> = { adet: 1, kg: 1, g: 500, L: 1, ml: 500 };
const FAMILY: Record<Unit, { fam: string; base: number }> = {
  adet: { fam: "adet", base: 1 }, kg: { fam: "w", base: 1000 }, g: { fam: "w", base: 1 }, L: { fam: "v", base: 1000 }, ml: { fam: "v", base: 1 },
};

const round3 = (x: number) => Math.round(x * 1000) / 1000;

/** "2,5" / "0,75" / "6" (en çok 3 ondalık, Türkçe virgül). */
export const fmtQty = (q: number) => q.toLocaleString("tr-TR", { maximumFractionDigits: 3, useGrouping: false });

/** "2,5 kg", "6 adet", "500 g" (sunucunun amount_label'ıyla aynı). */
export const amountText = (q: number, unit?: string | null) => `${fmtQty(q)} ${unit || "adet"}`;

/** Satır adı miktarıyla: "2,5 kg elma", "6× süt", adet 1 ise yalnız ad. */
export function rowTitle(it: { text: string; qty?: number | null; unit?: string | null }): string {
  const q = it.qty ?? 1;
  const u = it.unit || "adet";
  if (u !== "adet") return `${fmtQty(q)} ${u} ${it.text}`;
  return q !== 1 ? `${fmtQty(q)}× ${it.text}` : it.text;
}

/** Kullanıcının yazdığı miktar: "2,5" / "2.5" / " 750 " → sayı; geçersizse NaN. */
export function parseQty(s: string): number {
  const t = s.trim().replace(/\s/g, "").replace(",", ".");
  if (!/^\d+(\.\d+)?$/.test(t)) return NaN;
  return Number(t);
}

export const validQty = (q: number, unit: Unit) => Number.isFinite(q) && q > 0 && q <= MAX[unit];

/** Adım düğmesi: bir sonraki / önceki adım katına gider (2,3 kg + → 2,5; − → 2); en az bir adım, en çok sınır. */
export function stepQty(q: number, unit: Unit, dir: 1 | -1): number {
  const s = STEP[unit];
  const cur = Number.isFinite(q) && q > 0 ? q : 0;
  const k = cur / s;
  const onStep = Math.abs(k - Math.round(k)) < 1e-9;
  const n = dir > 0 ? (onStep ? Math.round(k) + 1 : Math.ceil(k)) : (onStep ? Math.round(k) - 1 : Math.floor(k));
  return round3(Math.min(MAX[unit], Math.max(s, n * s)));
}

/** Birim değişince miktar: aynı türde çevrilir (1,5 kg → 1500 g), tür değişince başlangıç miktarı. */
export function convertQty(q: number, from: Unit, to: Unit): number {
  if (from === to) return q;
  const a = FAMILY[from], b = FAMILY[to];
  if (a.fam !== b.fam || !Number.isFinite(q) || q <= 0) return START[to];
  return round3(Math.min(MAX[to], (q * a.base) / b.base));
}

/** Görünen zincir adı (sunucu price_sources.chain_name'in istemci yedeği): "BİM (Dost)" → "BİM", "Örnek Fırın ·
 *  Üsküdar" → "Örnek Fırın", "Örnek Market - Kadıköy" → "Örnek Market". Marka ve şube / semt gösterilmez. */
export function chainName(chain: string | null | undefined): string {
  const raw = (chain ?? "").trim();
  if (!raw) return raw;
  const s = raw.replace(/\s*[([].*?[)\]]/g, "").split(/\s+[·•|/–—-]\s+|\s*·\s*/)[0]!.replace(/^[\s,-]+|[\s,-]+$/g, "");
  return s || raw;
}

/** Tek dipnot: "Fiyatlar: Market Fiyatı · dün" (resmî teklif varsa; en yeni günün yaşı). İl ve "ulusal" yazılmaz;
 *  yasal kaynak yazısı dipnotun ipucunda (title). */
export function priceFootnote(offers: { source: string; date?: string | null }[], today: string): string | null {
  const r = offers.filter((o) => o.source === "resmi");
  if (!r.length) return null;
  const day = r.map((o) => o.date ?? "").filter(Boolean).sort().pop();
  return `Fiyatlar: Market Fiyatı (TÜBİTAK BİLGEM)${day ? ` · ${agoWord(day, today)}` : ""}`;
}

// ---------------------------------------------------------------- satır fiyatı

export interface LineBest { chain: string; price: number; line_total?: number | null }
/** Satırın alt yazısı ve sağdaki tutar. Satır tutarı (fiyat × miktar) birim fiyattan farklıysa alt yazı birim fiyatı
 *  söyler ("En ucuz: BİM ₺40 · kg", sağda ₺100); aynıysa "En ucuz: BİM · 1 L", sağda fiyat. */
export function bestParts(b: LineBest, size: string | null, m: Money): { sub: string; right: string | null } {
  const ch = chainName(b.chain);
  const line = b.line_total;
  const sz = size ? ` · ${size}` : "";
  // null: miktar bu ürünün satış birimine çevrilemedi (unit_mismatch) — satır tutarı yok, birim fiyat alt yazıda
  if (line === null) return { sub: `En ucuz: ${ch} ${m(b.price)}${sz}`, right: null };
  if (line !== undefined && Math.abs(line - b.price) >= 0.005) return { sub: `En ucuz: ${ch} ${m(b.price)}${sz}`, right: m(line) };
  return { sub: `En ucuz: ${ch}${sz}`, right: m(b.price) };
}

// ---------------------------------------------------------------- kaydedilen plan

export interface SavedPlan {
  k: number; total: number; chains: { chain: string; total: number; item_ids: number[] }[];
  items: { id: number; text: string; chain: string; unit_price: number; line_total: number }[];
  created_at?: string | null; created_by?: number | null; date?: string | null;
}

/** "Planın: BİM + Migros · ₺1.200" */
export const planSummary = (p: Pick<SavedPlan, "chains" | "total">, m: Money) =>
  `Planın: ${p.chains.map((c) => chainName(c.chain)).join(" + ")} · ${m(p.total)}`;

/** Liste satırlarını kaydedilen plana göre zincir zincir dizer (planın sırasıyla); planda olmayanlar ayrı. */
export function groupByPlan<T extends { id: number }>(items: T[], plan: Pick<SavedPlan, "chains"> | null | undefined):
  { groups: { chain: string; total: number; items: T[] }[]; rest: T[] } {
  if (!plan?.chains.length) return { groups: [], rest: items };
  const by = new Map(items.map((i) => [i.id, i]));
  const used = new Set<number>();
  const groups = plan.chains.map((c) => {
    const its = c.item_ids.map((id) => by.get(id)).filter((x): x is T => !!x && !used.has(x.id));
    its.forEach((x) => used.add(x.id));
    return { chain: chainName(c.chain), total: c.total, items: its };
  }).filter((g) => g.items.length);
  return { groups, rest: items.filter((i) => !used.has(i.id)) };
}

/** "3/5 alındı" */
export const boughtText = (done: number, total: number) => `${done}/${total} alındı`;

// ---------------------------------------------------------------- geçmiş alışverişler

export interface TripSummary {
  id: number; day: string; completed_at?: string | null; chains: string[]; planned_total: number | null;
  actual_total: number | null; market_total: number | null; saving: number | null; item_count: number; bought_count: number;
  linked: boolean; plan_saved?: boolean; entry_ids?: number[]; created_by?: number | null; created_by_name?: string | null;
}

/** "BİM + Migros" (zincir yoksa "Alışveriş") */
export const tripChains = (chains: string[] | null | undefined) =>
  chains?.length ? [...new Set(chains.map(chainName))].join(" + ") : "Alışveriş";

/** "Plan ₺130 · Ödenen ₺160" (bilinmeyen yarı yazılmaz) */
export function tripTotals(t: Pick<TripSummary, "planned_total" | "actual_total">, m: Money): string {
  return [t.planned_total != null ? `Plan ${m(t.planned_total)}` : null, t.actual_total != null ? `Ödenen ${m(t.actual_total)}` : null]
    .filter(Boolean).join(" · ");
}

/** Dürüst tasarruf yazısı (piyasa ortancasına göre; en pahalıya göre değil). */
export function savingCopy(t: Pick<TripSummary, "linked" | "saving">, m: Money): { tone: "good" | "over" | "info"; text: string } {
  if (!t.linked || t.saving == null) return { tone: "info", text: "Fişini okutunca gerçek fiyatlar burada görünür" };
  if (t.saving > 0) return { tone: "good", text: `Piyasa ortancasına göre ${m(t.saving)} tasarruf ettin` };
  if (Math.abs(t.saving) < 0.005) return { tone: "info", text: "Piyasa ortancasıyla aynı fiyata aldın" };
  return { tone: "over", text: `Piyasa ortancasının ${m(Math.abs(t.saving))} üstünde` };
}

// ---------------------------------------------------------------- fiş bağlama

export interface ShoppingLinked {
  status: "linked"; linked?: boolean; target: "list" | "trip"; trip_id?: number | null; count: number; chain?: string | null;
  names?: string[]; saving?: number | null; actual_total?: number | null; market_total?: number | null;
}
export interface ShoppingAsk {
  status?: "ask"; entry_id: number; target: "list" | "trip"; trip_id?: number | null; chain?: string | null; day?: string | null;
  count: number; in_plan?: boolean; confident?: boolean; names?: string[];
}
export type ShoppingLink = ShoppingLinked | ShoppingAsk;

/** "süt, elma ve 2 ürün daha" (en çok 3 ad) */
export function namesText(names: string[] | undefined, count: number): string {
  const n = (names ?? []).slice(0, 3);
  const more = Math.max(0, count - n.length);
  if (!n.length) return `${count} ürün`;
  return more ? `${n.join(", ")} ve ${more} ürün daha` : n.length > 1 ? `${n.slice(0, -1).join(", ")} ve ${n[n.length - 1]}` : n[0]!;
}

/** Kendiliğinden bağlanınca bildirim: "BİM fişi alışveriş listene bağlandı: 3 ürün işaretlendi · ₺31 tasarruf" */
export function linkedText(l: ShoppingLinked, m: Money): string {
  const who = l.chain ? `${chainName(l.chain)} fişi` : "Fiş";
  const head = l.target === "trip" ? `${who} son alışverişine bağlandı` : `${who} alışveriş listene bağlandı: ${l.count} ürün işaretlendi`;
  return l.saving != null && l.saving > 0 ? `${head} · ${m(l.saving)} tasarruf` : head;
}

/** Sorunun gövdesi: "BİM fişindeki süt, elma ve ekmek listende var." / "… son alışverişinde var." */
export function askText(a: ShoppingAsk): string {
  const who = a.chain ? `${chainName(a.chain)} fişindeki` : "Fişteki";
  return `${who} ${namesText(a.names, a.count)} ${a.target === "trip" ? "son alışverişinde" : "listende"} var.`;
}
