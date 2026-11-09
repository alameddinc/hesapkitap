// Kayıt bildirimi: tarihin dönemi ve adı (tarihler uydurma).
import assert from "node:assert/strict";
import { test } from "node:test";

import { donemAdi, kayitDonemi, kaydedildiMetni } from "./kayitYeri.ts";

test("ay dönümü 1: takvim ayı", () => {
  assert.deepEqual(kayitDonemi("2026-11-09", 1, "2026-11"), { month: "2026-11", ad: "Kasım" });
});

test("ay dönümü 5: dönüm gününden itibaren sonraki ayın dönemi", () => {
  assert.equal(kayitDonemi("2026-11-04", 5, "2026-12").month, "2026-11");
  assert.equal(kayitDonemi("2026-11-05", 5, "2026-12").month, "2026-12");
  assert.equal(kayitDonemi("2026-12-20", 5, "2026-12").month, "2027-01");
});

test("ay dönümü 5: ad dönemin başladığı aydan (2026-12 = 5 Kas – 4 Ara = Kasım dönemi)", () => {
  assert.deepEqual(kayitDonemi("2026-11-05", 5, "2026-12"), { month: "2026-12", ad: "Kasım" });
  assert.deepEqual(kayitDonemi("2026-12-20", 5, "2026-12"), { month: "2027-01", ad: "Aralık" });
  assert.deepEqual(kayitDonemi("2027-01-10", 5, "2026-12"), { month: "2027-02", ad: "Ocak 2027" });
});

test("başka yıldaki dönem yılla yazılır", () => {
  assert.equal(donemAdi("2025-12", "2026-11"), "Aralık 2025");
  assert.equal(donemAdi("2026-09", "2026-11"), "Eylül");
  assert.equal(kaydedildiMetni("Eylül"), "Kaydedildi · Eylül dönemi");
});
