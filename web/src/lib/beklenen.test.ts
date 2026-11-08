// Özet → "Beklenen" metinleri (mobil src/lib/beklenen.ts ile birebir aynı olmalı). Tutarlar uydurma.
import assert from "node:assert/strict";
import { test } from "node:test";

import { beklenenArtan, beklenenEksi, beklenenGider, beklenenVar, tamTl, type Beklenen } from "./beklenen.ts";

const d = (s: string | null) => s && s.replace(/\u2060/g, ""); // WJ (işaret ile tutar ayrılmasın) karşılaştırmada yok
const sifir = { recorded_expense: 0, pending_installments: 0, pending_subscriptions: 0, pending_recurring_expense: 0,
  pending_bills: 0, pending_expense: 0, recorded_income: 0, pending_income: 0 };
// örnek: gider 101.678,95 + ekstresi gelmemiş taksit 35.487,50; gelir 0 + bekleyen 215.087,61
const e = (p: Partial<typeof sifir> = {}, o: Partial<Beklenen> = {}): Beklenen => ({
  month: "2026-11", source: "plan", expense: 137166.45, income: 215087.61, net: 77921.16,
  parts: { ...sifir, recorded_expense: 101678.95, pending_installments: 35487.5, pending_expense: 35487.5, pending_income: 215087.61, ...p }, ...o,
});

test("tam lira biçimi", () => {
  assert.equal(tamTl(137166.45), "₺137.166");
  assert.equal(d(tamTl(-12400.6)), "−₺12.401");
});

test("gider satırı: yalnız ekstre bekleniyorsa 'Ekstre gelince', başka bekleyen gider de varsa 'Beklenenlerle'", () => {
  assert.equal(beklenenGider(e(), tamTl), "Ekstre gelince ~₺137.166");
  assert.equal(beklenenGider(e({ pending_subscriptions: 300, pending_expense: 35787.5 }), tamTl), "Ekstre gelince ~₺137.166");
  assert.equal(beklenenGider(e({ pending_recurring_expense: 20000, pending_expense: 55487.5 }), tamTl), "Beklenenlerle ~₺137.166");
  assert.equal(beklenenGider(e({ pending_installments: 0, pending_bills: 900, pending_expense: 900 }), tamTl), "Beklenenlerle ~₺137.166");
  assert.equal(beklenenGider(e({ pending_installments: 0, pending_expense: 0 }), tamTl), null); // yalnız gelir bekleniyor
});

test("artan satırı: işaret ve renk Artan gibi; bekleyen yoksa ya da geçmiş dönem (null) satır yok", () => {
  assert.equal(d(beklenenArtan(e(), tamTl)), "Beklenen: +₺77.921");
  assert.equal(beklenenArtan(e(), tamTl), "Beklenen: +\u2060₺77.921"); // "+" ile tutar bölünmez
  const eksi = e({}, { net: -12400 });
  assert.equal(d(beklenenArtan(eksi, tamTl)), "Beklenen: −₺12.400");
  assert.equal(beklenenEksi(eksi), true);
  assert.equal(beklenenEksi(e()), false);
  assert.equal(beklenenArtan(e({ pending_installments: 0, pending_expense: 0, pending_income: 0 }), tamTl), null);
  assert.equal(beklenenVar(null), false);
  assert.equal(beklenenVar(undefined), false);
  assert.equal(beklenenGider(null, tamTl), null);
});
