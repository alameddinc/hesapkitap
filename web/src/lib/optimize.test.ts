// "Nereye gideyim?" ve piyasa bandı yazıları (uydurma veri; mobil ile aynı metinler).
import assert from "node:assert/strict";
import { test } from "node:test";

import { compareRows, headline, lineSub, lineTitle, marketCaption, marketSource, namesLine, sourceLine, stopTitle, subline, type OptPlan, type Optimize } from "./optimize.ts";
import { ayrilma, bulunma } from "./priceView.ts";

const m = (n: number) => `₺${n.toLocaleString("tr-TR", { maximumFractionDigits: 2 })}`;
const TODAY = "2026-11-07";
const line = (id: number, chain: string, total: number) => ({ id, text: `Ürün ${id}`, qty: 1, group_label: null, price: total, line_total: total, source: "resmi", chain });
const plan = (k: number, chains: string[], total: number, extra: Partial<OptPlan> = {}): OptPlan => ({
  k, total, covered: 4, missing: [], saving_vs_single: 0, saving_vs_prev: 0, extra_vs_best: 0, recommended: false,
  stops: chains.map((c, i) => ({ chain: c, total: total / chains.length, items: [line(i + 1, c, total / chains.length)] })), ...extra,
});
const R: Optimize = {
  region: "İstanbul", date: TODAY, stop_cost: 100, max_stops: 3, recommended_k: 2, priced: 4, unpriced: [9],
  plans: [plan(1, ["BİM"], 900), plan(2, ["BİM", "Migros"], 560, { saving_vs_single: 340, saving_vs_prev: 340, recommended: true }),
    plan(3, ["BİM", "Migros", "A101"], 520, { saving_vs_single: 380, saving_vs_prev: 40 })],
  single: [{ chain: "BİM", total: 1240, covered: 2, missing: [3, 4], vs_recommended: 680 },
    { chain: "Migros", total: 1540, covered: 4, missing: [], vs_recommended: 340 }],
  one_store: null, sources: ["Kaynak: Market Fiyatı"], source_info: { kinds: ["resmi", "own", "community"], resmi_date: TODAY, national: true },
};

test("ekler: ayrılma ve bulunma", () => {
  assert.deepEqual(["BİM", "A101", "ŞOK", "Migros", "CarrefourSA", "Hakmar", "Tarım Kredi", "A100", "Market 3"].map(ayrilma),
    ["BİM'den", "A101'den", "ŞOK'tan", "Migros'tan", "CarrefourSA'dan", "Hakmar'dan", "Tarım Kredi'den", "A100'den", "Market 3'ten"]);
  assert.deepEqual(["BİM", "A101", "ŞOK", "Migros", "CarrefourSA", "Market 6"].map(bulunma),
    ["BİM'de", "A101'de", "ŞOK'ta", "Migros'ta", "CarrefourSA'da", "Market 6'da"]);
});

test("başlık, alt satır, durak", () => {
  assert.equal(headline(R.plans[1]!, 2, m), "En iyi plan: BİM + Migros · ₺560");
  assert.equal(headline(R.plans[2]!, 2, m), "3 duraklı plan: BİM + Migros + A101 · ₺520");
  assert.equal(subline(R.plans[1]!, m), "Tek duraktan ₺340 ucuz");
  assert.equal(subline(R.plans[0]!, m), "Tek durak yeter");
  assert.equal(stopTitle({ chain: "BİM", total: 1020, items: Array(8).fill(line(1, "BİM", 1)) }, m), "BİM'den al (8 ürün) · ₺1.020");
});

test("satır: adet, birim, birim fiyat, yaş (marka yazılmaz)", () => {
  const l = { id: 1, text: "Zeytinyağı 1 L", qty: 2, group_label: null, price: 59.9, line_total: 119.8, unit_price_per: 59.9, per_unit: "L", brand: "Örnekmarka", source: "resmi", date: "2026-11-06" };
  assert.equal(lineTitle(l), "2× Zeytinyağı 1 L");
  assert.equal(lineTitle({ ...l, qty: 1 }), "Zeytinyağı 1 L");
  assert.equal(lineTitle({ ...l, text: "elma", qty: 2.5, unit: "kg" }), "2,5 kg elma");
  assert.equal(lineTitle({ ...l, qty: 6, unit: "adet" }), "6× Zeytinyağı 1 L");
  assert.equal(lineSub(l, TODAY, m), "₺59,9/L · dün");
  assert.equal(lineSub({ ...l, date: "2026-11-04" }, TODAY, m), "₺59,9/L · 3 gün önce");
  assert.equal(lineSub({ ...l, date: TODAY, brand: null, unit_price_per: null }, TODAY, m), "");
  assert.equal(lineSub(l, TODAY, m, "2026-11-06"), "₺59,9/L");  // dipnot zaten "dün" diyor
  assert.equal(lineSub({ ...l, source: "own", date: "2026-10-01" }, TODAY, m), "₺59,9/L");
});

test("karşılaştırma satırları", () => {
  assert.deepEqual(compareRows(R, m), ["Tamamı BİM'den: ₺1.240 (2 ürün BİM'de yok)", "Tamamı Migros'tan: ₺1.540 (+₺340)",
    "3 durak: sadece ₺40 daha ucuz — değmez"]);
  const one = { ...R, recommended_k: 1, single: [{ chain: "BİM", total: 900, covered: 4, missing: [], vs_recommended: 0 }, ...R.single.slice(1)] };
  assert.deepEqual(compareRows(one, m).slice(0, 1), ["Tamamı Migros'tan: ₺1.540 (+₺340)"]);   // önerilenle aynı satır yok
});

test("kaynak satırı ve adlar", () => {
  assert.equal(sourceLine(R, TODAY), "Fiyatlar: Market Fiyatı · bugün · fişlerin · topluluk");
  assert.equal(sourceLine({ ...R, source_info: { kinds: ["resmi"], resmi_date: "2026-11-06", national: false } }, TODAY), "Fiyatlar: Market Fiyatı · dün");
  assert.equal(sourceLine({ ...R, source_info: { kinds: ["own", "community"], resmi_date: null, national: false } }, TODAY), "Fiyatlar: fişlerin · topluluk");
  assert.equal(namesLine("Fiyatı bilinmeyen", [9, 7], new Map([[9, "Örnek Şey"]])), "Fiyatı bilinmeyen: Örnek Şey");
  assert.equal(namesLine("Bu planda yok", [], new Map()), "");
});

test("piyasa yazısı", () => {
  const v = (verdict: "ucuz" | "normal" | "pahali", pct: number, day = "2026-11-01") =>
    ({ median: 34, min: 32.5, max: 40, pct, verdict, cheapest_chain: "BİM", day, source: "resmi" });
  const h = (vs: ReturnType<typeof v> | null) => [{ date: "2026-10-01", price: 30, vs_market: v("ucuz", -20, "2026-10-01") }, { date: "2026-11-01", price: 39, vs_market: vs }];
  assert.equal(marketCaption(h(v("ucuz", -8.4)), m), "Son alımın piyasanın %8 altında — iyi aldın");
  assert.equal(marketCaption(h(v("normal", 2)), m), "Son alımın piyasa fiyatında (ortanca ₺34)");
  assert.equal(marketCaption(h(v("pahali", 15)), m), "Son alımın piyasanın %15 üstünde; aynı gün BİM'de ₺32,5");
  assert.equal(marketCaption(h(v("pahali", 15, "2026-10-30")), m), "Son alımın piyasanın %15 üstünde; o günlerde BİM'de ₺32,5");
  assert.equal(marketCaption([{ date: "2026-11-01", price: 39 }], m), null);
  assert.equal(marketCaption(h(null), m), "Son alımın piyasanın %20 altında — iyi aldın");   // vs_market'li son alım
  assert.equal(marketSource({ region: "İstanbul", days: [{ day: "x", min: 1, median: 1, max: 1, cheapest_chain: "BİM", chains: 1, source: "resmi" }] }), "Piyasa: Market Fiyatı · İstanbul");
  assert.equal(marketSource({ region: "İstanbul", days: [] }), "Piyasa: topluluk");
});
