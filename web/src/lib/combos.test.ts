// Alışveriş listesi v5 seçicisi (uydurma veri; mobil ile aynı metinler).
import assert from "node:assert/strict";
import { test } from "node:test";

import {
  ANY_PATCH, comboBadges, comboPatch, comboSummary, comboTitle, comboToast, filterCombos, isAnySel, isSelected, sortCombos, type ComboRow,
} from "./combos.ts";

const m = (n: number) => `₺${n.toLocaleString("tr-TR", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
const R = (chain: string, brand: string | null, key: string, price: number, unit?: number): ComboRow =>
  ({ chain, brand, key, size_label: key === "sut|1l" ? "1 L" : key === "sut|2l" ? "2 L" : null, price, source: "resmi",
    ...(unit != null && { unit_price_per: unit, per_unit: "L" }) });
const rows = [R("BİM", "Örneksüt", "sut|1l", 34.5, 34.5), R("A101", null, "sut|1l", 33, 33), R("Tarım Kredi Kooperatif Market", "Mandıra Köy", "sut|2l", 59.9, 29.95),
  R("BİM", "Yayla Örnek", "sut|5l", 120)];

test("başlık ve bildirim", () => {
  assert.equal(comboTitle(rows[0]!), "BİM · Örneksüt 1 L");
  assert.equal(comboTitle(rows[1]!), "A101 · Markasız 1 L");
  assert.equal(comboTitle(rows[3]!), "BİM · Yayla Örnek 5 L"); // size_label yoksa anahtardan
  assert.equal(comboTitle({ chain: "BİM (Dost)", brand: "Örneksüt", key: "sut" }), "BİM · Örneksüt");
  assert.equal(comboToast(rows[2]!), "Tarım Kredi Kooperatif Market · Mandıra Köy 2 L seçildi");
});

test("süzgeç ve sıralama", () => {
  assert.equal(filterCombos(rows, { chain: "bim", brand: null, size: null }).length, 2);
  assert.equal(filterCombos(rows, { chain: null, brand: "Örneksüt", size: "sut|1l" }).length, 1);
  assert.equal(filterCombos(rows, { chain: "Hakmar", brand: null, size: null }).length, 0);
  assert.deepEqual(sortCombos(rows, "price").map((r) => r.price), [33, 34.5, 59.9, 120]);
  assert.deepEqual(sortCombos(rows, "unit").map((r) => r.price), [59.9, 33, 34.5, 120]); // birim fiyatsız sonda
});

test("rozetler", () => {
  const b = comboBadges(rows);
  assert.equal(b.cheapest, rows[1]);
  assert.equal(b.bestValue, rows[2]);
  assert.deepEqual(comboBadges([rows[0]!]), { cheapest: null, bestValue: null });
});

test("seçim, PATCH ve özet", () => {
  const sel = { store: "BİM", brand: "örneksüt", key: "sut|1l" };
  assert.equal(isSelected(sel, rows[0]!), true);
  assert.equal(isSelected(sel, rows[1]!), false);
  assert.equal(isSelected({ store: "A101", brand: null, key: "sut|1l" }, rows[1]!), true);
  assert.equal(isAnySel({ store: null, brand: null, key: "sut|1l" }), true);
  assert.equal(isAnySel(sel), false);
  assert.deepEqual(comboPatch(rows[2]!), { store: "Tarım Kredi Kooperatif Market", brand: "Mandıra Köy", group_key: "sut|2l" });
  assert.deepEqual(ANY_PATCH, { brand: null, store: null });
  assert.equal(comboSummary(sel, rows, m), "BİM · Örneksüt 1 L · ₺34,50");
  assert.equal(comboSummary({ store: null, brand: null, key: null }, rows, m), "Marka fark etmez");
  assert.equal(comboSummary({ store: "Hakmar", brand: "Örneksüt", key: "sut|1l" }, rows, m), "Hakmar · Örneksüt 1 L");
});
