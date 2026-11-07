// Fiyat UX v2 yazıları (uydurma veri).
import assert from "node:assert/strict";
import { test } from "node:test";

import { adviceText, assumedText, barMain, bestInline, communityParts, marketLineLabel, sizeOf } from "./priceAdvice.ts";
import { lik } from "./priceView.ts";

const m = (n: number) => `₺${n.toLocaleString("tr-TR", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;

test("lik: sayının eki ünlü uyumuna göre", () => {
  assert.deepEqual([6, 2, 3, 4, 10, 12, 30, 100].map(lik), ["6'lık", "2'lik", "3'lük", "4'lük", "10'luk", "12'lik", "30'luk", "100'lük"]);
});

test("adviceText: başka zincirde ucuz, adetli tasarruf", () => {
  const t = adviceText({ kind: "cheaper_at", chain: "BİM", price: 32.5, paid: 59, diff: 26.5, qty: 6, saving: 159, source: "resmi" }, m)!;
  assert.equal(t.title, "Bir dahaki sefere BİM'den al: ₺32,50");
  assert.equal(t.detail, "Sen ₺59,00 ödedin · 6'lık alımda ₺159,00 tasarruf.");
  assert.equal(t.tone, "save");
  const one = adviceText({ kind: "cheaper_at", chain: "ŞOK", price: 30, paid: 34, diff: 4, qty: 1, saving: 4, source: "community" }, m)!;
  assert.equal(one.title, "Bir dahaki sefere ŞOK'tan al: ₺30,00");
  assert.equal(one.detail, "Sen ₺34,00 ödedin · ₺4,00 daha ucuz.");
});

test("adviceText: iyi fiyat ve mağaza karşılaştırması", () => {
  assert.deepEqual(adviceText({ kind: "good_price", paid: 33, median: 35.95, pct: -8.2 }, m),
    { tone: "good", title: "İyi fiyata alıyorsun", detail: "Son alımın ₺33,00; piyasa ortancası ₺35,95 (%8 altında)." });
  const s = adviceText({ kind: "store_compare", stores: [{ store: "Köşe Kafe", price: 180 }, { store: "Espresso Bar", price: 230 }], cheapest: "Köşe Kafe", diff: 50 }, m)!;
  assert.equal(s.title, "Köşe Kafe ₺50,00 ucuz");
  assert.equal(s.detail, "Köşe Kafe'de ₺180,00, Espresso Bar'da ₺230,00");
  assert.equal(adviceText({ kind: "store_compare", stores: [{ store: "A", price: 1 }], cheapest: "A", diff: 0 }, m), null);
  assert.equal(adviceText(null, m), null);
});

test("communityParts: topluluk önce, Market Fiyatı yanında; fark varsa açıkça", () => {
  assert.deepEqual(communityParts({ source: "community", price: 38.5, n: 5, official: { price: 35 } }, m),
    { main: "Kullanıcılar ₺38,50 ödüyor (5 kişi, bu hafta)", extra: ["Market Fiyatı ₺35,00"] });
  assert.deepEqual(communityParts({ source: "community", price: 38.5, n: 4, official: { price: 31 }, differs: true, discounted_price: 33.9 }, m)!.extra,
    ["Market Fiyatı farklı: ₺31,00", "indirimli ₺33,90"]);
  assert.equal(communityParts({ source: "resmi", price: 1 }, m), null);
});

test("alışveriş: satır altı, varsayılan boy, çubuk", () => {
  assert.equal(sizeOf("Süt · 1 L"), "1 L");
  assert.equal(sizeOf("Ekmek"), null);
  assert.equal(bestInline({ chain: "BİM", price: 32.5, source: "resmi" }, "1 L", m), "En ucuz: BİM ₺32,50 · 1 L");
  assert.equal(assumedText({ key: "sut|1l", label: "Süt · 1 L", size_label: "1 L" }), "1 L kabul edildi");
  assert.equal(barMain({ title: "", subtitle: "", cta: "BİM'e git", chains: ["BİM"], total: 195, saving: 0, count: 6 }, m), "BİM'e git · ₺195,00 · 6 ürün");
  assert.equal(marketLineLabel({ median: 35.95 }, m), "Piyasa ortancası ₺35,95");
});
