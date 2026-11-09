// Bağlantısız kayıt kutusu: ekleme, kalıcılık, gönderim kuralları (veriler uydurma).
import assert from "node:assert/strict";
import { test } from "node:test";

import { anahtar, ekle, gonder, oku, yaz, type Depo, type Taslak } from "./bekleyen.ts";

const bellek = (): Depo & { m: Map<string, string> } => {
  const m = new Map<string, string>();
  return { m, getItem: (k) => m.get(k) ?? null, setItem: (k, v) => { m.set(k, v); }, removeItem: (k) => { m.delete(k); } };
};
const t = (id: string, over: Partial<Taslak> = {}): Taslak => ({
  id, method: "POST", url: "/api/entries", body: JSON.stringify({ description: `Örnek ${id}`, amount: 10 }),
  headers: { "Idempotency-Key": `k-${id}` }, ozet: `Gider · Örnek ${id}`, at: 1, ...over,
});

test("aynı eylem iki kez eklenmez, aynı tutarlı ayrı kayıt eklenir; PATCH son hâliyle değişir", () => {
  let l = ekle([], t("a"));
  l = ekle(l, t("a"));
  assert.equal(l.length, 1);
  l = ekle(l, t("b", { body: t("a").body }));
  assert.equal(l.length, 2);
  l = l.slice(0, 1);
  l = ekle(l, t("p1", { method: "PATCH", url: "/api/entries/7", body: "{\"note\":\"x\"}" }));
  l = ekle(l, t("p2", { method: "PATCH", url: "/api/entries/7", body: "{\"note\":\"y\"}" }));
  assert.deepEqual(l.map((x) => x.id), ["a", "p2"]);
});

test("kalıcılık: yazılan okunur, defter başına ayrı, boş liste anahtarı siler", () => {
  const d = bellek();
  yaz(d, anahtar("ortak"), [t("a")]);
  assert.deepEqual(oku(d, anahtar("ortak")).map((x) => x.id), ["a"]);
  assert.deepEqual(oku(d, anahtar("kendi")), []);
  assert.deepEqual(oku(d, anahtar(null)), []);
  yaz(d, anahtar("ortak"), []);
  assert.equal(d.m.size, 0);
  d.setItem(anahtar("x"), "bozuk{");
  assert.deepEqual(oku(d, anahtar("x")), []);
});

test("gönderim: başarılı silinir, aynı anahtar gider, ağ yoksa durur", async () => {
  const giden: string[] = [];
  const r = await gonder([t("a"), t("b"), t("c")], async (x) => {
    giden.push(x.headers["Idempotency-Key"]!);
    return x.id === "b" ? { ok: false, status: 0, message: "ağ yok" } : { ok: true };
  });
  assert.deepEqual(giden, ["k-a", "k-b"]); // c denenmedi: ağ yok
  assert.equal(r.gonderilen, 1);
  assert.deepEqual(r.kalan.map((x) => x.id), ["b", "c"]);
});

test("4xx: hata metniyle durur, kendiliğinden yeniden denenmez; zorla denenir", async () => {
  const r = await gonder([t("a"), t("b")], async (x) => (x.id === "a" ? { ok: false, status: 422, message: "Tutar geçersiz" } : { ok: true }));
  assert.equal(r.gonderilen, 1);
  assert.equal(r.kalan[0]!.hata, "Tutar geçersiz");
  assert.equal(r.kalan[0]!.dur, true);
  let n = 0;
  const r2 = await gonder(r.kalan, async () => { n++; return { ok: true }; });
  assert.equal(n, 0);
  assert.equal(r2.kalan.length, 1);
  const r3 = await gonder(r.kalan, async () => { n++; return { ok: true }; }, true);
  assert.equal(n, 1);
  assert.equal(r3.kalan.length, 0);
});

test("5xx: bekler, durdurulmaz", async () => {
  const r = await gonder([t("a")], async () => ({ ok: false, status: 503, message: "Sunucu yanıt vermiyor" }));
  assert.equal(r.kalan[0]!.dur, undefined);
  assert.equal(r.kalan[0]!.hata, "Sunucu yanıt vermiyor");
});
