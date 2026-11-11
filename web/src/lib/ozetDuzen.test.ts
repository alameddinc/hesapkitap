// Özet düzeni: sonradan eklenen "Dönem sonu" kayıtlı düzende de görünür, kaldırılınca geri gelmez.
import assert from "node:assert/strict";
import { test } from "node:test";

import { kayitDuzeni, ozetDuzeni, SADE_RED, sadeOnerisi, sadeyeGecir } from "./ozetDuzen.ts";

const KNOWN = new Set(["kartlar", "donem-sonu", "uyarilar", "butceler"]);
const known = (id: string) => KNOWN.has(id);
const VARSAYILAN = ["kartlar", "donem-sonu", "uyarilar"];

test("kayıt yoksa varsayılan", () => {
  assert.deepEqual(ozetDuzeni(null, known, VARSAYILAN), VARSAYILAN);
});

test("eski kayıtlı düzen: yeni bileşen kartların ardına girer", () => {
  assert.deepEqual(ozetDuzeni(["uyarilar", "kartlar", "butceler"], known, VARSAYILAN), ["uyarilar", "kartlar", "donem-sonu", "butceler"]);
  assert.deepEqual(ozetDuzeni(["butceler"], known, VARSAYILAN), ["donem-sonu", "butceler"]); // kartlar yoksa başa
  assert.deepEqual(ozetDuzeni(["donem-sonu", "kartlar"], known, VARSAYILAN), ["donem-sonu", "kartlar"]); // taşındıysa yerinde
});

test("kaldırılınca işaret saklanır, geri gelmez; eklenince işaret kalkar", () => {
  const kayit = kayitDuzeni(["kartlar", "uyarilar"]);
  assert.deepEqual(kayit, ["kartlar", "uyarilar", "-donem-sonu", "-faiz-ucret", "-sadaka"]);
  assert.deepEqual(ozetDuzeni(kayit, known, VARSAYILAN), ["kartlar", "uyarilar"]);
  assert.deepEqual(kayitDuzeni(["kartlar", "donem-sonu", "faiz-ucret", "sadaka"]), ["kartlar", "donem-sonu", "faiz-ucret", "sadaka"]);
});

test("faiz ve ücret kartı: eski kayıtlı düzende kartların hemen ardına (dönem sonundan önce) girer", () => {
  const k = (id: string) => KNOWN.has(id) || id === "faiz-ucret";
  assert.deepEqual(ozetDuzeni(["kartlar", "donem-sonu", "uyarilar"], k, VARSAYILAN), ["kartlar", "faiz-ucret", "donem-sonu", "uyarilar"]);
  assert.deepEqual(ozetDuzeni(["kartlar", "uyarilar"], k, VARSAYILAN), ["kartlar", "faiz-ucret", "donem-sonu", "uyarilar"]);
  // kaldırıldıysa geri gelmez; sunucu yeteneği yoksa (known false) hiç girmez
  assert.deepEqual(ozetDuzeni(["kartlar", "-faiz-ucret"], k, VARSAYILAN), ["kartlar", "donem-sonu"]);
  assert.deepEqual(ozetDuzeni(["kartlar"], known, VARSAYILAN), ["kartlar", "donem-sonu"]);
});

test("sadaka bileşeni: eski kayıtlı düzende uyarıların ardına girer; kaldırıldıysa geri gelmez", () => {
  const k = (id: string) => KNOWN.has(id) || id === "sadaka";
  assert.deepEqual(ozetDuzeni(["kartlar", "donem-sonu", "uyarilar", "butceler"], k, VARSAYILAN), ["kartlar", "donem-sonu", "uyarilar", "sadaka", "butceler"]);
  assert.deepEqual(ozetDuzeni(["kartlar", "donem-sonu", "uyarilar", "-sadaka"], k, VARSAYILAN), ["kartlar", "donem-sonu", "uyarilar"]);
  // eski sunucu (sadaka_reminder yok): bileşen bilinmez, girmez
  assert.deepEqual(ozetDuzeni(["kartlar", "donem-sonu", "uyarilar"], known, VARSAYILAN), ["kartlar", "donem-sonu", "uyarilar"]);
});

// ---------------------------------------------------------------- sade Özet
const SADE = ["sade", "baslarken", "ayrinti", "kartlar", "donem-sonu", "uyarilar"];
const k2 = (id: string) => KNOWN.has(id) || id === "sade" || id === "ayrinti" || id === "baslarken" || id === "faiz-ucret";

test("sade: kayıt yoksa sade varsayılan; Tek bakış hep en üstte, ayraç hep var", () => {
  assert.deepEqual(ozetDuzeni(null, k2, SADE), SADE);
  assert.deepEqual(ozetDuzeni(["kartlar", "sade", "ayrinti", "uyarilar", "-donem-sonu", "-faiz-ucret"], k2, SADE), ["sade", "kartlar", "ayrinti", "uyarilar"]);
  assert.deepEqual(ozetDuzeni(["sade", "kartlar", "-donem-sonu", "-faiz-ucret"], k2, SADE), ["sade", "ayrinti", "kartlar"]);
  // sade düzende yeni bileşen ardına girdiği bileşenin arkasına, o yoksa sona (Ayrıntılar'a)
  assert.deepEqual(ozetDuzeni(["sade", "ayrinti", "uyarilar", "-donem-sonu", "-faiz-ucret"], k2, SADE), ["sade", "ayrinti", "uyarilar"]);
  assert.deepEqual(ozetDuzeni(["sade", "ayrinti", "butceler", "-faiz-ucret"], k2, SADE), ["sade", "ayrinti", "butceler", "donem-sonu"]);
  // faiz kartı sade düzene kendiliğinden girmez (Tek bakış'ın 3. satırı aynı bilgi); elle eklenirse kalır
  assert.deepEqual(ozetDuzeni(["sade", "ayrinti", "kartlar", "-donem-sonu"], k2, SADE), ["sade", "ayrinti", "kartlar"]);
  assert.deepEqual(ozetDuzeni(["sade", "ayrinti", "kartlar", "faiz-ucret", "-donem-sonu"], k2, SADE), ["sade", "ayrinti", "kartlar", "faiz-ucret"]);
});

test("eski kayıtlı düzen zorla değişmez; öneri bir kez (Uygula / Kalsın)", () => {
  const eski = ["uyarilar", "kartlar", "butceler"];
  assert.deepEqual(ozetDuzeni(eski, k2, SADE), ["uyarilar", "kartlar", "faiz-ucret", "donem-sonu", "butceler"]);
  assert.equal(sadeOnerisi(eski, k2), true);
  assert.equal(sadeOnerisi(null, k2), false); // kaydı yok: zaten sade varsayılan
  assert.equal(sadeOnerisi(["sade", "ayrinti", "kartlar"], k2), false);
  // Uygula: sıra ve kaldırılanlar korunur, faiz kartı düşer, Başlarken üstte
  const yeni = sadeyeGecir(["baslarken", "uyarilar", "kartlar", "faiz-ucret", "butceler"], ["baslarken"]);
  assert.deepEqual(yeni, ["sade", "baslarken", "ayrinti", "uyarilar", "kartlar", "butceler"]);
  assert.deepEqual(kayitDuzeni(yeni), [...yeni, "-donem-sonu", "-faiz-ucret", "-sadaka"]);
  // Kalsın: işaret saklanır, öneri bir daha çıkmaz; düzen aynı
  const kalsin = kayitDuzeni(["uyarilar", "kartlar", "donem-sonu", "faiz-ucret", "sadaka"], true);
  assert.deepEqual(kalsin, ["uyarilar", "kartlar", "donem-sonu", "faiz-ucret", "sadaka", SADE_RED]);
  assert.equal(sadeOnerisi(kalsin, k2), false);
  assert.deepEqual(ozetDuzeni(kalsin, k2, SADE), ["uyarilar", "kartlar", "donem-sonu", "faiz-ucret"]); // sadaka k2’de bilinmiyor
});
