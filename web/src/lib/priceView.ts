// Fiyat ayrıntısı ve alışveriş listesinin saf hesapları (grafik ölçeği, özet cümlesi, ek, tek yerden toplam).
// Mobil karşılığı: hesapkitap-mobile src/screens/FiyatTakibi.tsx / lib/endpoints/alisveris.ts.

export interface PricePoint { date: string; price: number; store: string; estimated: boolean; i: number }

/** Fiyatı bilinen alımlar, tarihe göre (aynı günde fiş sırası korunur). */
export function pricedPoints(history: { date: string; price: number | null; store: string; estimated?: boolean }[]): PricePoint[] {
  return history
    .map((h, i) => ({ h, i }))
    .filter(({ h }) => h.price != null)
    .sort((a, b) => a.h.date.localeCompare(b.h.date) || a.i - b.i)
    .map(({ h, i }) => ({ date: h.date.slice(0, 10), price: h.price as number, store: h.store, estimated: !!h.estimated, i }));
}

/** Yuvarlak eksen adımları: en küçük ve en büyük fiyatı içine alan 1/2/2,5/5 × 10ⁿ adımlı 3–5 çizgi. */
export function niceTicks(min: number, max: number, target = 4): { lo: number; hi: number; step: number; ticks: number[] } {
  if (!Number.isFinite(min) || !Number.isFinite(max)) return { lo: 0, hi: 1, step: 1, ticks: [0, 1] };
  if (max < min) [min, max] = [max, min];
  let span = max - min;
  if (span < 1e-9) span = Math.max(Math.abs(max) * 0.1, 1); // tek fiyat: çevresinde küçük bir aralık
  const raw = span / Math.max(1, target - 1);
  const p = 10 ** Math.floor(Math.log10(raw));
  const step = [1, 2, 2.5, 5, 10].map((m) => m * p).find((s) => s >= raw - 1e-9) ?? 10 * p;
  let lo = Math.floor(min / step + 1e-9) * step;
  let hi = Math.ceil(max / step - 1e-9) * step;
  if (hi - lo < 1e-9) { lo -= step; hi += step; }
  if (lo < 0 && min >= 0) lo = 0;
  const ticks: number[] = [];
  for (let v = lo; v <= hi + step / 2; v += step) ticks.push(Math.round(v * 100) / 100);
  return { lo, hi: ticks[ticks.length - 1]!, step, ticks };
}

/** İki tarih arasındaki gün (b − a), yerel takvim günü. */
export function daysBetween(a: string, b: string): number {
  const d = (s: string) => { const [y, m, dd] = s.slice(0, 10).split("-").map(Number); return Date.UTC(y!, m! - 1, dd!); };
  return Math.round((d(b) - d(a)) / 864e5);
}

const MON = ["Oca", "Şub", "Mar", "Nis", "May", "Haz", "Tem", "Ağu", "Eyl", "Eki", "Kas", "Ara"];
/** "bugün", "dün", "12 Eki" (yıl farklıysa "12 Eki 2025") */
export function dayWord(iso: string, today: string): string {
  const n = daysBetween(iso, today);
  if (n === 0) return "bugün";
  if (n === 1) return "dün";
  const [y, m, d] = iso.slice(0, 10).split("-").map(Number);
  return `${d} ${MON[m! - 1]}${String(y) !== today.slice(0, 4) ? ` ${y}` : ""}`;
}
export const monthShort = (ym: string) => MON[Number(ym.slice(5, 7)) - 1] ?? ym;

/** Değişim yüzdesi, tam sayıya yuvarlanmış (−6,5 → −7). */
export const changePct = (from: number, to: number) => (from ? Math.round(((to - from) / from) * 100) : 0);
/** "+%17" / "−%7" / "%0" */
export const pctWord = (p: number) => `${p > 0 ? "+" : p < 0 ? "−" : ""}%${Math.abs(p)}`;

const UNLU = "aeıioöuüâîû";
const SERT = "fstkçşhp";
/** Rakamla biten adın okunuşu (son kısmı yeter): A101 → "bir", 5M → "beş" */
function sayiOkunusu(n: number): string {
  if (n === 0) return "sıfır";
  if (n % 1000 === 0) return "bin";
  if (n % 100 === 0) return "yüz";
  const u = n % 10;
  if (u) return ["", "bir", "iki", "üç", "dört", "beş", "altı", "yedi", "sekiz", "dokuz"][u]!;
  return ["", "on", "yirmi", "otuz", "kırk", "elli", "altmış", "yetmiş", "seksen", "doksan"][(n % 100) / 10]!;
}
/** Ek ünlüsü ve ünsüzü: son ünlü ince mi, son ses sert mi (rakamla biten ad okunuşuna göre). */
function ekSesi(ad: string): { s: string; ince: boolean; sert: boolean } {
  const s = ad.trim();
  const dig = /(\d+)$/.exec(s);
  const h = dig ? sayiOkunusu(Number(dig[1])) : s.toLocaleLowerCase("tr").replace(/[^a-zçğıöşüâîû]/g, "");
  const son = [...h].reverse().find((c) => UNLU.includes(c));
  return { s, ince: !son || "eiöüî".includes(son), sert: !!h && SERT.includes(h[h.length - 1]!) };
}
/** Ayrılma eki: File'den, BİM'den, A101'den, Şok'tan, Migros'tan, Marmara'dan (son ünlü ve sert ünsüz). */
export function ayrilma(ad: string): string {
  const { s, ince, sert } = ekSesi(ad);
  return `${s}'${sert ? "t" : "d"}${ince ? "e" : "a"}n`;
}
/** Bulunma eki: BİM'de, A101'de, ŞOK'ta, Migros'ta, CarrefourSA'da. */
export function bulunma(ad: string): string {
  const { s, ince, sert } = ekSesi(ad);
  return `${s}'${sert ? "t" : "d"}${ince ? "e" : "a"}`;
}

/** Resmî fiyatın yaşı: "bugün", "dün", "3 gün önce". */
export function agoWord(iso: string, today: string): string {
  const n = daysBetween(iso, today);
  return n <= 0 ? "bugün" : n === 1 ? "dün" : `${n} gün önce`;
}

// ---------------------------------------------------------------- alışveriş listesi

export interface PlanChain { chain: string; total: number; covered: number; missing: number }
/** Hepsini tek yerden: en çok ürünü bulunan, eşitse toplamı en düşük market. */
export function bestChain(chains: PlanChain[] | null | undefined): PlanChain | null {
  const list = (chains ?? []).filter((c) => c.covered > 0);
  if (!list.length) return null;
  return [...list].sort((a, b) => b.covered - a.covered || a.total - b.total)[0]!;
}

/** Resmî teklifin kaynak yazısı: "Market Fiyatı, bugün, İzmir" ("dün", "3 gün önce"); BİM / A101 / ŞOK "Market
 *  Fiyatı, ulusal" (eskiyse ", dün"); bölgesiz
 *  (eski dosya içe aktarımı) "Market Fiyatı, bugün". */
export function officialSrc(b: { date?: string | null; region?: string | null }, today: string): string {
  if (b.region === "ulusal") return ["Market Fiyatı, ulusal", b.date && b.date < today ? agoWord(b.date, today) : null].filter(Boolean).join(", ");
  return ["Market Fiyatı", b.date ? agoWord(b.date, today) : null, b.region || null].filter(Boolean).join(", ");
}

/** Haftalık değişim çipi: |değişim| ≥ %3 ise "7 günde +%8" / "7 günde −%4", değilse null. */
export function change7Text(change?: number | null): string | null {
  if (change == null || !Number.isFinite(change) || Math.abs(change) < 3) return null;
  return `7 günde ${pctWord(Math.round(change))}`;
}
