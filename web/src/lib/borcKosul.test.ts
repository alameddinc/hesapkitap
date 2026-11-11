// Borcun koşulları (yetenek debt_terms): formun gönderdiği gövde ve varsayılan tarih türü sunucuyla aynı.
import assert from "node:assert/strict";
import { test } from "node:test";

import { ALACAKLI, KOSULLAR, TARIH_TURU, kosulGovdesi, kosulOzeti, varsayilanKesin } from "./borcKosul.ts";

// tests/test_debt_plan.py BANNED + test_debt_schedule.py RESTRUCTURE
const BANNED = [/kredi\s*(çek|al|kullan)/, /taksitlendir/, /borç\s*transfer/, /nakit\s*avans/, /ertele/, /refinans/,
  /yapılandır/, /öteleme/, /vade(yi|sini)?\s*uzat/];

test("varsayılan tarih türü: kurumda ve taksitlide kesin, kişide söz", () => {
  assert.equal(varsayilanKesin("kisi", "parca"), false);
  assert.equal(varsayilanKesin(null, "tek"), false);
  for (const c of ["vergi_sgk", "banka", "magaza", "diger"] as const) assert.equal(varsayilanKesin(c, "parca"), true);
  assert.equal(varsayilanKesin("kisi", "taksitli"), true);
});

test("gövde: tarih türü yalnız kullanıcı değiştirdiyse ve tarih varsa gider", () => {
  assert.deepEqual(kosulGovdesi({ creditor: "kisi", terms: "tek", strict: null, hasDue: true }),
    { creditor_type: "kisi", pay_terms: "tek" });
  assert.deepEqual(kosulGovdesi({ creditor: "magaza", terms: "parca", strict: false, hasDue: true }),
    { creditor_type: "magaza", pay_terms: "parca", due_strict: false });
  assert.deepEqual(kosulGovdesi({ creditor: "banka", terms: "parca", strict: true, hasDue: false }),
    { creditor_type: "banka", pay_terms: "parca" });
  // taksitli: koşul plan alanlarından gelir (sunucu pay_terms'ü kendisi yazar)
  assert.deepEqual(kosulGovdesi({ creditor: "vergi_sgk", terms: "taksitli", strict: true, hasDue: true }),
    { creditor_type: "vergi_sgk" });
});

test("özet satırı", () => {
  assert.equal(kosulOzeti({ creditor_type: "magaza", terms: "tek", strict: true, due_date: "2026-12-01" }),
    "Mağaza / senet · Tek seferde ödenecek · Kesin tarih");
  assert.equal(kosulOzeti({ creditor_type: null, terms: "bilinmiyor", strict: false, due_date: null }), "");
});

test("metinler faizli bir yol önermez", () => {
  const all = [...ALACAKLI.flatMap((x) => [x.label, x.hint]), ...KOSULLAR.flatMap((x) => [x.label, x.note]),
    ...TARIH_TURU.flatMap((x) => [x.label, x.note])].join(" | ").toLocaleLowerCase("tr-TR");
  for (const p of BANNED) assert.doesNotMatch(all, p);
});
