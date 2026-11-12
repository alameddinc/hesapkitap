// Hata açıklaması (mobil src/lib/hataAcikla.ts ile aynı metinler): teknik/İngilizce metin süzülür, sunucunun Türkçe metni
// geçer, durum koduna göre neden + adım + kod. Metinler uydurma.
import assert from "node:assert/strict";
import { test } from "node:test";

import { ApiError } from "./apiError.ts";
import { gosterilir, hataAcikla, hataMetni, temizMetin } from "./hataAcikla.ts";

test("teknik / İngilizce metin süzülür, Türkçe metin geçer", () => {
  for (const t of ["Failed to fetch", "TypeError: Cannot read properties of undefined (reading 'map')", "Internal Server Error",
    "Unexpected token '<'", "NetworkError when attempting to fetch resource.", "500", "KeyError: 'amount'", "Load failed"])
    assert.equal(temizMetin(t), null, t);
  for (const t of ["Alt kategori adı boş olamaz", "Bu dekont zaten eklenmiş", "Tutar en az 1 olmalı"])
    assert.equal(temizMetin(t), t);
  assert.equal(gosterilir("x".repeat(401) + "ş"), false);
  assert.equal(temizMetin(null), null);
});

test("sunucunun Türkçe metni nedende; 422 adımı", () => {
  const a = hataAcikla(new ApiError("Kategorisiz altına alt kategori eklenmez", 422, { detail: "Kategorisiz altına alt kategori eklenmez" }));
  assert.equal(a.neden, "Kategorisiz altına alt kategori eklenmez.");
  assert.equal(a.adim, "Girdiğin bilgiyi kontrol edip tekrar dene.");
  assert.equal(a.kod, "gecersiz");
  assert.equal(a.tekrar, false);
  assert.equal(a.ayrinti, "HTTP 422");
});

test("500: teknik metin değil genel neden, Yardım al adımı", () => {
  const a = hataAcikla(new ApiError("Internal Server Error", 500, null));
  assert.equal(a.neden, "Sunucuda beklenmeyen bir hata oldu.");
  assert.match(a.adim, /Yardım al/);
  assert.equal(a.kod, "sunucu_500");
  assert.equal(a.tekrar, true);
});

test("ağ: çevrimdışı ve sunucuya ulaşılamadı ayrı", () => {
  assert.equal(hataAcikla(new ApiError("x", 0, { network: true, offline: true })).kod, "baglanti_yok");
  const b = hataAcikla(new ApiError("Sunucuya ulaşılamadı.", 0, { network: true, offline: false }));
  assert.equal(b.kod, "sunucu_yok");
  assert.equal(b.neden, "Sunucuya şu an ulaşılamıyor.");
  // lib/idem.ts ağ hatası (düz Error + status 0)
  assert.equal(hataAcikla(Object.assign(new Error("Sunucuya ulaşılamadı"), { status: 0, offline: false })).kod, "sunucu_yok");
});

test("durum kodları: neden ve adım", () => {
  const k = (s: number, m = "") => hataAcikla(new ApiError(m, s, null));
  assert.equal(k(401).kod, "oturum");
  assert.equal(k(403).neden, "Bu işlem için yetkin yok.");
  assert.equal(k(404).adim, "Listeyi yenile; başka bir cihazda silinmiş olabilir.");
  assert.equal(k(409).kod, "cakisma");
  assert.equal(k(413).kod, "dosya_buyuk");
  assert.equal(k(429).kod, "cok_istek");
  assert.equal(k(503).kod, "sunucu_mesgul");
  assert.equal(k(502, "Bad Gateway").neden, "Bağlı bir servise şu an ulaşılamıyor.");
  assert.equal(hataAcikla(new ApiError("Bu ayın 5 okuması doldu", 402, { quota_full: true })).kod, "okuma_hakki");
});

test("metin ve düz hata: yedek cümle; kendi Türkçe cümlesi adım istemez", () => {
  const a = hataAcikla("Önce tutarı yaz");
  assert.equal(a.neden, "Önce tutarı yaz.");
  assert.equal(a.adim, "");
  assert.equal(hataAcikla("Silinemedi").neden, "Silinemedi.");  // Türkçe harfsiz uygulama cümlesi de gösterilir
  assert.equal(hataAcikla("Failed to fetch").neden, "Bir şey ters gitti.");
  assert.equal(hataAcikla(new TypeError("x is undefined"), "Kaydedilemedi").neden, "Kaydedilemedi.");
  assert.equal(hataAcikla(new TypeError("x is undefined")).ayrinti, "TypeError: x is undefined");
  assert.equal(hataMetni(new Error("Failed to fetch"), "Fiş yeniden okunamadı"), "Fiş yeniden okunamadı.");
});
