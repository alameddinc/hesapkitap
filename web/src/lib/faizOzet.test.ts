import assert from "node:assert/strict";
import { test } from "node:test";
import type { InterestPaid } from "../api.ts";
import { donemObegi, faizKartMetni } from "./faizOzet.ts";

const para = (x: number) => `₺${x}`;
const p = (o: Partial<InterestPaid>): InterestPaid => ({
  total: 0, interest: 0, tax: 0, fee: 0, count: 0, statements: 1, has_cards: true, state: "yok", ...o,
});

test("faiz var: tutar ve kırılım (sıfır olan parça yok)", () => {
  const m = faizKartMetni(p({ state: "var", total: 1195.31, interest: 658.31, tax: 87, fee: 450, count: 4 }), para)!;
  assert.equal(m.title, "Bu dönem ödenen faiz ve ücret");
  assert.equal(m.value, "₺1195.31");
  assert.equal(m.sub, "faiz ₺658.31 · vergi ₺87 · ücret ₺450");
  assert.equal(m.tone, "warn");
  assert.equal(faizKartMetni(p({ state: "var", total: 450, fee: 450 }), para)!.sub, "ücret ₺450");
});

test("faiz yok: olumlu nötr metin, tutar yok; ekstre gelmediyse erken hüküm yok", () => {
  const m = faizKartMetni(p({ state: "yok" }), para)!;
  assert.equal(m.title, "Bu dönem faiz ödemedin");
  assert.equal(m.value, null);
  assert.equal(m.tone, "ok");
  const e = faizKartMetni(p({ state: "ekstre_yok", statements: 0 }), para)!;
  assert.equal(e.title, "Bu dönem faiz ve ücret görünmüyor");
  assert.match(e.sub, /ekstresi gelince/);
});

test("kart yok: eski sunucu, ortak defterde gizli ya da hiç kart ekstresi yok", () => {
  assert.equal(faizKartMetni(undefined, para), null);
  assert.equal(faizKartMetni(null, para), null);
  assert.equal(faizKartMetni(p({ has_cards: false, state: "ekstre_yok" }), para), null);
});

test("geçmiş dönem öbeği; metinde asgari / faize yönlendirme yok", () => {
  assert.equal(donemObegi(false, "Eylül"), "Bu dönem");
  assert.equal(donemObegi(true, "Eylül"), "Eylül döneminde");
  assert.equal(faizKartMetni(p({ state: "yok" }), para, donemObegi(true, "Eylül"))!.title, "Eylül döneminde faiz ödemedin");
  for (const st of ["var", "yok", "ekstre_yok"] as const) {
    const m = faizKartMetni(p({ state: st, total: 5, interest: 5 }), para)!;
    assert.doesNotMatch(`${m.title} ${m.sub}`.toLowerCase(), /asgari/);
  }
});

test("bu dönemin ekstresi yok: son ekstreli dönemin değeri (sunucu last_period)", () => {
  const lp = { month: "2026-11", name: "Ekim", label: "Ekim 2026", total: 126.46, interest: 97.28, tax: 29.18, fee: 0, count: 6, statements: 2 };
  const m = faizKartMetni(p({ state: "ekstre_yok", statements: 0, last_period: { ...lp, state: "var" } }), para)!;
  assert.equal(m.title, "Ekim döneminde ödenen faiz ve ücret");
  assert.equal(m.value, "₺126.46");
  assert.equal(m.sub, "Bu dönemin ekstresi gelince güncellenir");
  assert.equal(m.tone, "warn");
  // Sade "Bu ay" öbeği verse de son dönemin adı yazılır
  const y = faizKartMetni(p({ state: "ekstre_yok", statements: 0, last_period: { ...lp, total: 0, interest: 0, tax: 0, count: 0, state: "yok" } }), para, "Bu ay")!;
  assert.equal(y.title, "Ekim döneminde faiz ödemedin");
  assert.equal(y.value, null);
  assert.equal(y.tone, "ok");
  // önceki dönemde de ekstre yok (null): eski nötr metin
  assert.equal(faizKartMetni(p({ state: "ekstre_yok", statements: 0, last_period: null }), para)!.tone, "neutral");
});
