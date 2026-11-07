// Alışveriş listesi v3 yazıları ve miktar adımları (uydurma veri; mobil ile aynı metinler).
import assert from "node:assert/strict";
import { test } from "node:test";

import {
  amountText, askText, bestParts, boughtText, chainName, convertQty, fmtQty, groupByPlan, linkedText, namesText, parseQty,
  planSummary, priceFootnote, rowTitle, savingCopy, stepQty, tripChains, tripTotals, validQty,
} from "./shopList.ts";

const m = (n: number) => `₺${n.toLocaleString("tr-TR", { maximumFractionDigits: 2 })}`;
const TODAY = "2026-11-07";

test("miktar yazısı ve satır adı", () => {
  assert.equal(fmtQty(2.5), "2,5");
  assert.equal(fmtQty(1500), "1500");
  assert.equal(amountText(2.5, "kg"), "2,5 kg");
  assert.equal(amountText(6, null), "6 adet");
  assert.equal(rowTitle({ text: "elma", qty: 2.5, unit: "kg" }), "2,5 kg elma");
  assert.equal(rowTitle({ text: "süt", qty: 6, unit: "adet" }), "6× süt");
  assert.equal(rowTitle({ text: "Ekmek", qty: 1, unit: "adet" }), "Ekmek");
  assert.equal(rowTitle({ text: "Ekmek", qty: null }), "Ekmek");
  assert.equal(rowTitle({ text: "kıyma", qty: 500, unit: "g" }), "500 g kıyma");
});

test("miktar okuma ve sınır", () => {
  assert.equal(parseQty("2,5"), 2.5);
  assert.equal(parseQty(" 750 "), 750);
  assert.equal(parseQty("2.5"), 2.5);
  assert.ok(Number.isNaN(parseQty("iki")));
  assert.ok(Number.isNaN(parseQty("")));
  assert.ok(validQty(999, "adet") && !validQty(1000, "adet") && validQty(1500, "g") && !validQty(0, "kg"));
});

test("adım düğmeleri: birim başına adım, kata yuvarlar, en az bir adım", () => {
  assert.equal(stepQty(1, "adet", 1), 2);
  assert.equal(stepQty(1, "adet", -1), 1);
  assert.equal(stepQty(2.5, "kg", 1), 3);
  assert.equal(stepQty(2.3, "kg", 1), 2.5);
  assert.equal(stepQty(2.3, "kg", -1), 2);
  assert.equal(stepQty(0.5, "kg", -1), 0.5);
  assert.equal(stepQty(500, "g", 1), 600);
  assert.equal(stepQty(750, "ml", 1), 1000);
  assert.equal(stepQty(1.5, "L", -1), 1);
  assert.equal(stepQty(Number.NaN, "kg", 1), 0.5);
  assert.equal(stepQty(999, "adet", 1), 999);
});

test("birim değişince miktar", () => {
  assert.equal(convertQty(1.5, "kg", "g"), 1500);
  assert.equal(convertQty(750, "g", "kg"), 0.75);
  assert.equal(convertQty(2, "L", "ml"), 2000);
  assert.equal(convertQty(3, "adet", "kg"), 1);
  assert.equal(convertQty(2, "kg", "L"), 1);
  assert.equal(convertQty(1, "kg", "adet"), 1);
});

test("zincir adı: marka, şube ve semt atılır", () => {
  assert.equal(chainName("BİM (Dost)"), "BİM");
  assert.equal(chainName("Örnek Fırın · Üsküdar"), "Örnek Fırın");
  assert.equal(chainName("Örnek Market - Kadıköy"), "Örnek Market");
  assert.equal(chainName("A101"), "A101");
  assert.equal(chainName(""), "");
});

test("tek dipnot: il ve ulusal yazılmaz", () => {
  assert.equal(priceFootnote([{ source: "resmi", date: "2026-11-06" }, { source: "resmi", date: "2026-11-05" }], TODAY), "Fiyatlar: Market Fiyatı (TÜBİTAK BİLGEM) · dün");
  assert.equal(priceFootnote([{ source: "own", date: "2026-11-01" }], TODAY), null);
  assert.equal(priceFootnote([{ source: "resmi" }], TODAY), "Fiyatlar: Market Fiyatı (TÜBİTAK BİLGEM)");
  // sunucunun kaynak yazısı varsa o yazılır (demoda uydurma veri notu; TÜBİTAK adı geçmez)
  const demo = "Örnek veri: demodaki fiyatlar uydurmadır, gerçek market fiyatı değildir";
  assert.equal(priceFootnote([{ source: "resmi", date: "2026-11-06", attribution: demo }], TODAY), `${demo} · dün`);
  assert.ok(!priceFootnote([{ source: "resmi", attribution: demo }], TODAY)!.includes("TÜBİTAK"));
});

test("satır fiyatı: birim fiyat × miktar", () => {
  assert.deepEqual(bestParts({ chain: "BİM", price: 40, line_total: 100 }, "kg", m), { sub: "En ucuz: BİM ₺40 · kg", right: "₺100" });
  assert.deepEqual(bestParts({ chain: "BİM (Dost)", price: 34.5, line_total: 34.5 }, "1 L", m), { sub: "En ucuz: BİM · 1 L", right: "₺34,5" });
  assert.deepEqual(bestParts({ chain: "BİM", price: 150, line_total: null }, null, m), { sub: "En ucuz: BİM ₺150", right: null });
  assert.deepEqual(bestParts({ chain: "BİM", price: 10 }, null, m), { sub: "En ucuz: BİM", right: "₺10" });
});

test("kaydedilen plan: zincir zincir, planda olmayanlar ayrı", () => {
  const items = [{ id: 1 }, { id: 2 }, { id: 3 }, { id: 4 }];
  const plan = { chains: [{ chain: "BİM", total: 90, item_ids: [3, 1] }, { chain: "Migros", total: 40, item_ids: [2, 9] }] };
  const g = groupByPlan(items, plan);
  assert.deepEqual(g.groups.map((x) => [x.chain, x.items.map((i) => i.id)]), [["BİM", [3, 1]], ["Migros", [2]]]);
  assert.deepEqual(g.rest.map((i) => i.id), [4]);
  assert.deepEqual(groupByPlan(items, null).rest.length, 4);
  assert.equal(planSummary({ chains: plan.chains, total: 1200 }, m), "Planın: BİM + Migros · ₺1.200");
  assert.equal(boughtText(3, 5), "3/5 alındı");
});

test("geçmiş alışveriş yazıları: dürüst tasarruf", () => {
  assert.equal(tripChains(["BİM", "Migros"]), "BİM + Migros");
  assert.equal(tripChains([]), "Alışveriş");
  assert.equal(tripTotals({ planned_total: 130, actual_total: 160 }, m), "Plan ₺130 · Ödenen ₺160");
  assert.equal(tripTotals({ planned_total: 130, actual_total: null }, m), "Plan ₺130");
  assert.deepEqual(savingCopy({ linked: true, saving: 31 }, m), { tone: "good", text: "Piyasa ortancasına göre ₺31 tasarruf ettin" });
  assert.deepEqual(savingCopy({ linked: true, saving: -12.5 }, m), { tone: "over", text: "Piyasa ortancasının ₺12,5 üstünde" });
  assert.equal(savingCopy({ linked: false, saving: null }, m).text, "Fişini okutunca gerçek fiyatlar burada görünür");
});

test("fiş bağlama yazıları", () => {
  assert.equal(namesText(["süt", "elma", "ekmek", "çay"], 5), "süt, elma, ekmek ve 2 ürün daha");
  assert.equal(namesText(["süt", "elma"], 2), "süt ve elma");
  assert.equal(namesText([], 3), "3 ürün");
  assert.equal(askText({ entry_id: 7, target: "list", chain: "Migros", count: 2, names: ["Süt", "elma"] }), "Migros fişindeki Süt ve elma listende var.");
  assert.equal(askText({ entry_id: 7, target: "trip", chain: null, count: 1, names: ["Süt"] }), "Fişteki Süt son alışverişinde var.");
  assert.equal(linkedText({ status: "linked", target: "list", count: 3, chain: "BİM", saving: 31 }, m), "BİM fişi alışveriş listene bağlandı: 3 ürün işaretlendi · ₺31 tasarruf");
  assert.equal(linkedText({ status: "linked", target: "trip", count: 3, chain: "BİM", saving: -4 }, m), "BİM fişi son alışverişine bağlandı");
});
