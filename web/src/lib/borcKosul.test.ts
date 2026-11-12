// Borcun koşulları (yetenek debt_terms): formun gönderdiği gövde ve varsayılan tarih türü sunucuyla aynı.
import assert from "node:assert/strict";
import { test } from "node:test";

import { ALACAKLI, KIMDEN, KOSULLAR, KOSULLAR_ALACAK, TARIH_TURU, TARIH_TURU_ALACAK, kosulGovdesi, kosulMetni, kosulOzeti,
  kosulRozetleri, varsayilanKesin, yoneUygun } from "./borcKosul.ts";

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

// alacak (yetenek debt_terms_receivable): aynı yapı, yöne göre sözler
test("alacak: Kimden, Nasıl geri ödenecek, söz varsayılan", () => {
  const m = kosulMetni("alacak");
  assert.equal(m.kime, "Kimden");
  assert.equal(m.nasil, "Nasıl geri ödenecek");
  assert.deepEqual(m.turler.map((x) => x.label), ["Kişi", "Kurum / firma", "Diğer"]);
  assert.deepEqual(m.kosullar.map((x) => x.label), ["Parça parça", "Tek seferde", "Taksitli (ödeme planı)", "Bilmiyorum"]);
  assert.deepEqual(m.tarih.map((x) => x.label), ["Söz verdi (esnek)", "Kesin tarih"]);
  assert.equal(kosulMetni("borc").kime, "Kime");
  // tarih türü: alacakta kurumda da söz; taksitlide kesin
  assert.equal(varsayilanKesin("kurum", "parca", "alacak"), false);
  assert.equal(varsayilanKesin("kisi", "taksitli", "alacak"), true);
  // tür yöne uysun (sunucu type_for)
  assert.equal(yoneUygun("alacak", "banka"), "kurum");
  assert.equal(yoneUygun("alacak", "kisi"), "kisi");
  assert.equal(yoneUygun("borc", "kurum"), "diger");
  assert.equal(yoneUygun("borc", "magaza"), "magaza");
  // gövde aynı: tarih türü yalnız değiştirilince
  assert.deepEqual(kosulGovdesi({ creditor: "kurum", terms: "tek", strict: true, hasDue: true }),
    { creditor_type: "kurum", pay_terms: "tek", due_strict: true });
});

test("alacak rozetleri", () => {
  assert.deepEqual(kosulRozetleri({ direction: "alacak", creditor_type: "kisi", terms: "parca", strict: false, due_date: "2026-12-01" }),
    ["Kişi", "Parça parça", "Söz"]);
  assert.deepEqual(kosulRozetleri({ direction: "alacak", creditor_type: "kurum", terms: "taksitli", strict: true, due_date: null, schedule: {} }),
    ["Kurum / firma", "Taksitli (ödeme planı)"]);
  assert.deepEqual(kosulRozetleri({ direction: "alacak", creditor_type: "kisi", terms: "parca", strict: false, due_date: "2026-12-01" }, false),
    ["Kişi", "Parça parça"]);
  assert.equal(kosulOzeti({ direction: "borc", creditor_type: "magaza", terms: "tek", strict: true, due_date: "2026-12-01" }),
    "Mağaza / senet · Tek seferde ödenecek · Kesin tarih");
});

test("alacak metinleri faiz sormaz, faizli yol önermez", () => {
  const all = [...KIMDEN.flatMap((x) => [x.label, x.hint]), ...KOSULLAR_ALACAK.flatMap((x) => [x.label, x.note]),
    ...TARIH_TURU_ALACAK.flatMap((x) => [x.label, x.note])].join(" | ").toLocaleLowerCase("tr-TR");
  for (const p of BANNED) assert.doesNotMatch(all, p);
  assert.doesNotMatch(all, /faiz/);
});
