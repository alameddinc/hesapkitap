// Tutmayan ekstreyi düzelt metinleri (mobil src/lib/ekstreDuzelt.ts ile birebir aynı olmalı). Tutarlar uydurma.
import assert from "node:assert/strict";
import { test } from "node:test";

import { DUZELT, grupla, grupOf, onizleme, sonucMetni, turAnahtari, tutarOku, tutarYaz } from "./ekstreDuzelt.ts";

test("satır grupları", () => {
  const ana = "5400********1111";
  const rows = [
    { txn_type: "harcama", amount: 100, card: ana, installment_no: null, is_deleted: 0 },
    { txn_type: "harcama", amount: 50, card: ana, installment_no: 2, is_deleted: 0 },
    { txn_type: "harcama", amount: 30, card: "5400********2222", installment_no: null, is_deleted: 0 },
    { txn_type: "iade", amount: -20, card: ana, installment_no: null, is_deleted: 0 },
    { txn_type: "odeme", amount: -500, card: ana, installment_no: null, is_deleted: 0 },
    { txn_type: "faiz_ucret", amount: 12, card: ana, installment_no: null, is_deleted: 1 },
  ];
  assert.equal(grupOf(rows[2], ana), "ekkart");
  assert.deepEqual(grupla(rows, ana).map((g) => [g.label, g.rows.length]),
    [["Harcamalar", 1], ["Taksitler", 1], ["Ek kart", 1], ["İadeler", 1], ["Ödemeler", 1], ["Faiz ve ücretler", 1]]);
  assert.deepEqual(grupla(rows, ana, ["odeme"]).map((g) => g.key), ["odeme"]);
});

test("düzeltme metinleri", () => {
  assert.equal(DUZELT.sablonla(8), "Şablonla düzelt (8 satır)");
  assert.equal(DUZELT.onayla, "Rakamlar doğru, onayla");
  assert.equal(onizleme({ ok: true, diff: null }), "Bu değişiklikle: Tuttu ✓");
  assert.equal(onizleme({ ok: false, diff: -10 }), "Bu değişiklikle: Kalan fark ₺10,00");
  assert.equal(onizleme(null), null);
  assert.equal(sonucMetni("eklendi", { ok: true, diff: null }), "Satır eklendi · Tuttu ✓");
  assert.equal(sonucMetni("duzeltildi", { ok: false, diff: 73 }), "Satır düzeltildi · Kalan fark ₺73,00");
});

test("tür seçici ve tutar girişi", () => {
  assert.equal(turAnahtari("faiz_ucret", "Kart aidatı ücreti"), "ucret");
  assert.equal(turAnahtari("faiz_ucret", "BSMV"), "faiz");
  assert.equal(turAnahtari("iade"), "iade");
  assert.equal(turAnahtari("bilinmeyen"), "harcama");
  assert.equal(tutarOku("1.234,50"), 1234.5);
  assert.equal(tutarOku("120"), 120);
  assert.equal(tutarOku("-30,5"), 30.5);
  assert.equal(tutarOku("₺ 12.5"), 12.5);
  assert.equal(tutarOku("0"), null);
  assert.equal(tutarOku("abc"), null);
  assert.equal(tutarYaz(30), "30,00");
  assert.equal(tutarYaz(-87.25), "87,25");
});
