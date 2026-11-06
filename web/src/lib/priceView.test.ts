import assert from "node:assert/strict";
import { test } from "node:test";

import { ayrilma, bestChain, changePct, dayWord, niceTicks, pctWord, pricedPoints } from "./priceView.ts";

test("niceTicks: en küçük ve en büyük fiyat eksenin içinde, adımlar yuvarlak", () => {
  const t = niceTicks(127.5, 138);
  assert.ok(t.lo <= 127.5 && t.hi >= 138);
  assert.deepEqual(t.ticks, [125, 130, 135, 140]);
  const s = niceTicks(34.5, 36);
  assert.ok(s.lo <= 34.5 && s.hi >= 36);
  assert.ok(s.ticks.every((v) => Math.abs(v * 2 - Math.round(v * 2)) < 1e-9));
  const one = niceTicks(50, 50);
  assert.ok(one.lo < 50 && one.hi > 50);
});

test("pricedPoints: fiyatsız alım düşer, tarihe göre sıralanır, tahmini işaretli", () => {
  const p = pricedPoints([
    { date: "2026-11-06", price: 129, store: "File" },
    { date: "2026-10-12", price: 138, store: "File", estimated: true },
    { date: "2026-10-20", price: null, store: "BİM" },
  ]);
  assert.deepEqual(p.map((x) => [x.date, x.price, x.estimated]), [["2026-10-12", 138, true], ["2026-11-06", 129, false]]);
});

test("özet parçaları: −%7, bugün, 12 Eki", () => {
  assert.equal(pctWord(changePct(138, 129)), "−%7");
  assert.equal(pctWord(changePct(30, 35)), "+%17");
  assert.equal(dayWord("2026-11-06", "2026-11-06"), "bugün");
  assert.equal(dayWord("2026-11-05", "2026-11-06"), "dün");
  assert.equal(dayWord("2026-10-12", "2026-11-06"), "12 Eki");
  assert.equal(dayWord("2025-12-30", "2026-11-06"), "30 Ara 2025");
});

test("ayrilma: market adına ayrılma eki", () => {
  assert.deepEqual(["File", "BİM", "A101", "Şok", "Migros", "Marmara", "Torku"].map(ayrilma),
    ["File'den", "BİM'den", "A101'den", "Şok'tan", "Migros'tan", "Marmara'dan", "Torku'dan"]);
});

test("bestChain: en çok ürün, eşitse en ucuz", () => {
  assert.equal(bestChain([
    { chain: "BİM", total: 380, covered: 5, missing: 2 },
    { chain: "A101", total: 412, covered: 6, missing: 1 },
    { chain: "File", total: 405, covered: 6, missing: 1 },
  ])?.chain, "File");
  assert.equal(bestChain([]), null);
});
