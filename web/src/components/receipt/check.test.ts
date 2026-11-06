// Karışan rakam önerisinin istemci tarafı (yetenek digit_fix): sunucu önerisi şu anki kalemlere hâlâ uyuyor mu,
// uygulanınca kalemler fişi tutuyor mu. Kalemler ve tutarlar uydurmadır.
import assert from "node:assert/strict";
import { test } from "node:test";

import { applyDigitFixes, checkItems, digitPairs, liveDigit, type ReceiptCheck, type ReceiptItem } from "./check.ts";

const items: ReceiptItem[] = [
  { name: "SUT 1L", qty: 1, amount: 64.5 },
  { name: "MEYVE PURESI", qty: 1, amount: 69, unit_price: 69 },
  { name: "EKMEK", qty: 2, amount: 10, unit_price: 5 },
];
const server: ReceiptCheck = {
  ...checkItems(items, 163.5),
  digit_fix: [{ index: 1, name: "MEYVE PURESI", from: 69, to: 89, digits: "6→8", unit_price_from: 69, unit_price_to: 89, evidence: ["ocr"], score: 4 }],
  digit_fix_all: null, residual: 0, suspect: [], groups: [], line_count: null,
};

test("öneri canlı: kalem değişmediyse gösterilir, uygulanınca fiş tutar", () => {
  const live = liveDigit(server, items, checkItems(items, 163.5));
  assert.equal(live.fixes.length, 1);
  const fixed = applyDigitFixes(items, live.fixes);
  assert.deepEqual([fixed[1]!.amount, fixed[1]!.unit_price], [89, 89]);
  const after = checkItems(fixed, 163.5);
  assert.ok(after.ok);
  assert.equal(liveDigit(server, fixed, after).fixes.length, 0); // fiş tuttu: öneri yok
});

test("kalem elle değiştiyse ya da fiş toplamı değiştiyse öneri düşer", () => {
  const edited = items.map((it, i) => (i === 1 ? { ...it, amount: 70 } : it));
  assert.equal(liveDigit(server, edited, checkItems(edited, 163.5)).fixes.length, 0);
  assert.equal(liveDigit(server, items, checkItems(items, 170)).fixes.length, 0);
  assert.equal(liveDigit(undefined, items, checkItems(items, 163.5)).fixes.length, 0); // eski sunucu
});

test("birim fiyat: sunucu vermediyse tutar ÷ adet (eski tutarı tutuyorduysa), tutmuyorduysa değişmez", () => {
  const two = applyDigitFixes(items, [{ index: 2, name: "EKMEK", from: 10, to: 16, digits: "0→6" }]);
  assert.equal(two[2]!.unit_price, 8);
  const odd: ReceiptItem[] = [{ name: "KAHVE", qty: 2, amount: 69, unit_price: 36.5 }];
  assert.equal(applyDigitFixes(odd, [{ index: 0, name: "KAHVE", from: 69, to: 89, digits: "6→8" }])[0]!.unit_price, 36.5);
});

test("hepsini düzelt, kalan fark, şüpheli satırlar ve KDV grubu yalnız toplam aynıyken", () => {
  const many: ReceiptCheck = {
    ...checkItems(items, 200),
    digit_fix: [
      { index: 0, name: "SUT 1L", from: 64.5, to: 84.5, digits: "6→8" },
      { index: 1, name: "MEYVE PURESI", from: 69, to: 89, digits: "6→8" },
    ],
    digit_fix_all: { indices: [0, 1], total_ok: false }, residual: -3.5, suspect: [2],
    groups: [{ rate: 1, items_total: 133.5, tax: 1.32, printed: 1.52, indices: [0, 1], ok: false },
      { rate: 20, items_total: 10, tax: 1.67, printed: 1.67, indices: [2], ok: true }],
    line_count: null,
  };
  const live = liveDigit(many, items, checkItems(items, 200));
  assert.equal(live.all, null); // total_ok false: "Hepsini düzelt" yok
  assert.equal(live.residual, -3.5);
  assert.deepEqual([...live.suspect], [2]);
  assert.deepEqual(live.badRates, [1]);
  const ok = liveDigit({ ...many, digit_fix_all: { indices: [0, 1], total_ok: true } }, items, checkItems(items, 200));
  assert.equal(ok.all?.length, 2);
  const one = applyDigitFixes(items, [many.digit_fix![0]!]);
  const after = liveDigit({ ...many, digit_fix_all: { indices: [0, 1], total_ok: true } }, one, checkItems(one, 200));
  assert.equal(after.all, null); // biri uygulandı: kalan tek öneri
  assert.equal(after.fixes.length, 1);
  assert.equal(after.suspect.size, 0);
});

test("rakam çiftleri metni", () => {
  assert.equal(digitPairs("6→8"), "6 ile 8");
  assert.equal(digitPairs("6→8, 3→8"), "6 ile 8, 3 ile 8");
});
