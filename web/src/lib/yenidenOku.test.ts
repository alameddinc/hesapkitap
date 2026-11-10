// "Şablonla yeniden oku" metinleri (mobil src/lib/yenidenOku.ts ile birebir aynı olmalı). Banka / tutar uydurma.
import assert from "node:assert/strict";
import { test } from "node:test";

import { OKU, okuKimlik, okuSatiri } from "./yenidenOku.ts";

test("yeniden okuma metinleri", () => {
  assert.equal(okuSatiri("Örnek Bank", "6631", 2), "Örnek Bank ••6631 · 2 satır farklı");
  assert.equal(okuKimlik(12), "ekstre_oku:12");
  assert.equal(OKU.uygulandi(1), "Ekstre düzeltildi · 1 satır");
  assert.equal(OKU.rakam(-274.8, true), "Rakamlar şimdi ₺274,80 tutmuyor; düzeltilince tutuyor.");
  assert.equal(OKU.rakam(10, false), "Rakamlar şimdi ₺10,00 tutmuyor.");
  assert.equal(OKU.rakamDurum({ ok: false, diff: -87.25 }, true), "Rakamlar şimdi ₺87,25 tutmuyor; düzeltilince tutuyor.");
  assert.equal(OKU.rakamDurum({ ok: true, diff: null }, true), "Özet rakamları tutuyor; işlem listesi ekstreyle aynı olacak.");
});
