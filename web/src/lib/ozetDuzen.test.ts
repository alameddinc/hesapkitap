// Özet düzeni: sonradan eklenen "Dönem sonu" kayıtlı düzende de görünür, kaldırılınca geri gelmez.
import assert from "node:assert/strict";
import { test } from "node:test";

import { kayitDuzeni, ozetDuzeni } from "./ozetDuzen.ts";

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
  assert.deepEqual(kayit, ["kartlar", "uyarilar", "-donem-sonu"]);
  assert.deepEqual(ozetDuzeni(kayit, known, VARSAYILAN), ["kartlar", "uyarilar"]);
  assert.deepEqual(kayitDuzeni(["kartlar", "donem-sonu"]), ["kartlar", "donem-sonu"]);
});
