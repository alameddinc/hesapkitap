// Özet düzeni: sonradan eklenen "Dönem sonu" kayıtlı düzende de görünür, kaldırılınca geri gelmez.
import assert from "node:assert/strict";
import { test } from "node:test";

import { gorunumDegistir, kayitDuzeni, ozetDuzeni, sadeMi, sakliDuzen } from "./ozetDuzen.ts";
import { simdikiGorunum } from "./gorunum.ts";

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

test("varsayılan Minimal; kayıtlı (klasik) düzen Ayrıntılı kalır; eski '-sade' işareti zarar vermez", () => {
  assert.equal(sadeMi(ozetDuzeni(null, k2, SADE)), true);
  const eski = ["uyarilar", "kartlar", "butceler", "-sade"];
  assert.deepEqual(ozetDuzeni(eski, k2, SADE), ["uyarilar", "kartlar", "faiz-ucret", "donem-sonu", "butceler"]);
  assert.equal(simdikiGorunum(false, ozetDuzeni(eski, k2, SADE)), "ayrintili");
  assert.equal(simdikiGorunum(false, ozetDuzeni(null, k2, SADE)), "minimal");
  assert.equal(simdikiGorunum(true, ozetDuzeni(null, k2, SADE)), "sade");
});

test("görünüm Minimal ↔ Ayrıntılı gidiş-dönüş: her görünümün sırası (işaretleriyle) saklanır", () => {
  const AYR = ["kartlar", "faiz-ucret", "donem-sonu", "uyarilar"];
  const v = { minimal: SADE, ayrintili: AYR };
  const benim = ozetDuzeni(["uyarilar", "kartlar", "butceler", "-donem-sonu"], k2, SADE); // kendi Ayrıntılı sırası
  assert.deepEqual(benim, ["uyarilar", "kartlar", "faiz-ucret", "butceler"]);
  // → Minimal: saklı sıra yok, Minimal varsayılanı; Ayrıntılı sırası "=ad" olarak saklanır
  const m = gorunumDegistir(benim, [], "minimal", k2, v);
  assert.deepEqual(m.layout, SADE);
  const kayit = kayitDuzeni(m.layout, m.sakli);
  assert.deepEqual(sakliDuzen(kayit), ["uyarilar", "kartlar", "faiz-ucret", "butceler", "-donem-sonu", "-sadaka"]);
  assert.deepEqual(ozetDuzeni(kayit, k2, SADE), ozetDuzeni(SADE, k2, SADE)); // "=ad" görünen düzene karışmaz
  // Minimal'de sırayı değiştir, → Ayrıntılı: kendi sırası geri gelir, Minimal sırası saklanır
  const mDegisti = ["sade", "kartlar", "ayrinti", "baslarken", "donem-sonu", "uyarilar"];
  const a = gorunumDegistir(mDegisti, sakliDuzen(kayit), "ayrintili", k2, v);
  assert.deepEqual(a.layout, benim);
  // → Minimal yine: değiştirilmiş Minimal sırası
  const m2 = gorunumDegistir(a.layout, a.sakli, "minimal", k2, v);
  assert.deepEqual(m2.layout, mDegisti);
  // aynı görünüme geçiş bir şey değiştirmez
  assert.deepEqual(gorunumDegistir(m2.layout, m2.sakli, "minimal", k2, v), m2);
});
