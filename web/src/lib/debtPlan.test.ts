// Borç planı aylık tutar kutusu: yazarken canlı önizleme yalnız geçerli tutarda çalışır (mobil ile aynı sözler).
import assert from "node:assert/strict";
import { test } from "node:test";

import { AYLIK_EN_COK, aylikTutarHatasi, debtPlanUrl } from "./debtPlan.ts";

test("geçerli tutar hata vermez", () => {
  assert.equal(aylikTutarHatasi(25000), null);
  assert.equal(aylikTutarHatasi(0.5), null);
  assert.equal(aylikTutarHatasi(AYLIK_EN_COK), null);
});

test("boş, 0, rakam olmayan ve çok büyük tutar ayrı mesaj", () => {
  assert.match(aylikTutarHatasi(null)!, /ne kadar ayırabileceğini yaz/);
  assert.match(aylikTutarHatasi(0)!, /0 ile borç kapanmaz/);
  assert.match(aylikTutarHatasi(Number.NaN)!, /rakamla yaz/);
  assert.match(aylikTutarHatasi(-5)!, /rakamla yaz/);
  assert.match(aylikTutarHatasi(AYLIK_EN_COK + 1)!, /çok büyük/);
});

test("önizleme adresi tutarı ve sıralamayı sorguya koyar (kaydetmez)", () => {
  assert.equal(debtPlanUrl(25000, "kucuk"), "/api/debt-plan?monthly=25000&order=kucuk");
  assert.equal(debtPlanUrl(), "/api/debt-plan");
});
