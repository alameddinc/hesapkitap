// Destek: bağlam maskeleme ve bildirim özetleri (uydurma veriler).
import assert from "node:assert/strict";
import { test } from "node:test";
import { baglamMetni, bildirimOzeti, dosyaMetni, maskeleBaglam } from "./destekMetin.ts";

test("bağlam: e-posta ve uzun rakam dizileri maskelenir, 300 karakter", () => {
  assert.equal(maskeleBaglam("ornek@posta.com kartı 5400 1234 5678 9876 hata"), "[e-posta] kartı [numara] hata");
  assert.equal(maskeleBaglam("kod 1234 sayfa 12"), "kod 1234 sayfa 12"); // kısa sayı kalır
  assert.equal(maskeleBaglam("x".repeat(400)).length, 300);
  assert.equal(maskeleBaglam(null), "");
});

test("bildirim özetleri", () => {
  assert.equal(dosyaMetni({ file_type: "pdf", pages: 3 }), "PDF · 3 sayfa");
  assert.equal(dosyaMetni({}), "Bilinmiyor");
  assert.equal(bildirimOzeti({ bank_name: "Ornek Bank", code: "ekstre_bulunamadi", file_type: "pdf", pages: 1 }),
    "Ornek Bank · Kart ekstresi bulunamadı · PDF · 1 sayfa");
  assert.equal(bildirimOzeti({ bank_guess: "Ornek", code_label: "Sayfa / süre sınırı" }), "Ornek · Sayfa / süre sınırı");
  assert.equal(baglamMetni({ nerede: "Ekstre yükleme", kod: "sinir" }), "Bağlam: Ekstre yükleme · kod sinir");
  assert.equal(baglamMetni({ nerede: "Fiş" }), "Bağlam: Fiş");
});
