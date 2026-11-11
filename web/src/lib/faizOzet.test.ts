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
