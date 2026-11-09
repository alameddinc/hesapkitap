// Kart şablonu öğrenme neden kodları: sunucudaki kodların hepsinin Türkçe karşılığı var, sıralama başarısızları öne alır.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";

import { NEDEN, nedenMetni, nedenSirasi } from "./sablonNeden.ts";

test("sunucunun neden kodlarının hepsi çevrilmiş", () => {
  const py = readFileSync(new URL("../../../app/card_reader.py", import.meta.url), "utf8");
  const doc = py.slice(py.indexOf("Kodlar: ok"), py.indexOf('"""', py.indexOf("Kodlar: ok")));
  const kodlar = doc.replace("Kodlar:", "").split("|").map((s) => s.trim().replace(/\.$/, "")).filter((k) => k && k !== "ok");
  assert.ok(kodlar.length > 10);
  for (const k of kodlar) assert.ok(NEDEN[k], `çevirisi yok: ${k}`);
  const bf = readFileSync(new URL("../../../app/card_reader_backfill.py", import.meta.url), "utf8");
  for (const k of ["would_learn", "already_local", "no_file", "password", "unreadable"]) assert.ok(bf.includes(k) && NEDEN[k]);
});

test("bilinmeyen kod olduğu gibi; sıralama: başarısızlar önce, çoktan aza", () => {
  assert.equal(nedenMetni("yeni_kod"), "yeni_kod");
  assert.equal(nedenMetni("lines"), "işlem satırı sayısı farklı");
  assert.deepEqual(nedenSirasi({ learned: 5, lines: 1, ai_equation: 3 }), [["ai_equation", 3], ["lines", 1], ["learned", 5]]);
});
