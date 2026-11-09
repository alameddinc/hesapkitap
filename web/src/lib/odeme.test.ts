// Ödemeler liste anahtarı ve ekstre tutar seçenekleri (veriler uydurma).
import assert from "node:assert/strict";
import { test } from "node:test";

import { ekstreSecenek, kalanNotu, payableKey, secimOf } from "./odeme.ts";

const est = (title: string) => ({ type: "estimate", id: null, title, due_date: "2026-12-18", amount: 100, min_payment: null, paid_amount: null });

test("iki kartın aynı günkü tahmini ayrı anahtar alır; sunucu key'i önce gelir", () => {
  const a = est("Örnek Bank 5100***1111"), b = est("Örnek Bank 5200***2222");
  assert.notEqual(payableKey(a), payableKey(b));
  assert.equal(payableKey({ ...a, key: "estimate:x" }), "estimate:x");
  assert.equal(payableKey({ type: "statement", id: 7, title: "X", due_date: "2026-11-18" }), "statement-7-X-2026-11-18");
});

test("ekstre seçenekleri: asgari yalnız borçtan küçük ve karşılanmamışsa", () => {
  const st = { type: "statement", id: 1, title: "X", due_date: "2026-11-18", amount: 24680.5, min_payment: 4936.1, paid_amount: null };
  assert.deepEqual(ekstreSecenek(st), { full: 24680.5, min: 4936.1 });
  assert.equal(ekstreSecenek({ ...st, paid_amount: 4936.1 }).min, null); // asgari ödendi: kalanı öde
  assert.equal(ekstreSecenek({ ...st, paid_amount: 4936.1 }, true).min, 4936.1); // düzeltmede seçilebilir
  assert.equal(ekstreSecenek({ ...st, min_payment: null }).min, null);
  assert.equal(ekstreSecenek({ ...st, min_payment: 24680.5 }).min, null);
});

test("seçim yazılan tutardan; Başka tutar seçiliyken değişmez", () => {
  const s = { full: 1000, min: 200 };
  assert.equal(secimOf(1000, s, false), "tam");
  assert.equal(secimOf(200, s, false), "asgari");
  assert.equal(secimOf(500, s, false), "diger");
  assert.equal(secimOf(1000, s, true), "diger");
  assert.equal(secimOf(null, s, false), "diger");
  assert.match(kalanNotu("₺800,00"), /^Kalan ₺800,00 ödenecekler listesinde kalır\. Kalanına faiz işleyebilir/);
});
