// Kumbara hedef durumu metni (mobil src/lib/kumbaraHedef.ts ile aynı sözler). Girdiler sunucunun app/jar_goal.py çıktısı biçiminde.
import assert from "node:assert/strict";
import { test } from "node:test";

import { ayda, hedefMetni, yilEki, type GoalStatus } from "./kumbaraHedef.ts";

const tl = (n: number) => `₺${new Intl.NumberFormat("tr-TR").format(n)}`;
const F = { para: tl, birim: tl };
const G = (o: Partial<GoalStatus>): GoalStatus => ({
  status: "yolunda", unit: "TRY", left: 20000, months_left: 8, needed_monthly: 2500, pace: 2500, pace_source: "hareket",
  raise_by: null, eta: "2027-07", eta_months: 8, months_early: null, deadline_passed: false, ...o,
});

test("yolunda: rozet + ayda ayır + gerçek hız", () => {
  const t = hedefMetni(G({}), F);
  assert.deepEqual([t.rozet, t.ton, t.satir], ["Yolunda", "iyi", "Hedefe 8 ay: ayda ₺2.500 ayır"]);
  assert.equal(t.ayrinti, "Son aylarda ayda ₺2.500 ayırıyorsun. Bu hızla tam zamanında dolar.");
});

test("geride: ne kadar artırmalı; hız yoksa gereken tamamı", () => {
  const t = hedefMetni(G({ status: "geride", months_left: 4, needed_monthly: 5000, raise_by: 2500 }), F);
  assert.deepEqual([t.rozet, t.ton, t.satir], ["Geride", "uyari", "Hedefe 4 ay: ayda ₺5.000 ayır"]);
  assert.equal(t.ayrinti, "Bu hızla (ayda ₺2.500) yetişmez: ayda ₺2.500 daha ayır.");
  const z = hedefMetni(G({ status: "geride", pace: 0, pace_source: null, needed_monthly: 1000, raise_by: 1000, eta: null }), F);
  assert.equal(z.ayrinti, "Son 3 ayda ayırma yok; ayda ₺1.000 ayırırsan yetişir.");
});

test("önde: tahmini ay ve kaç ay erken", () => {
  const t = hedefMetni(G({ status: "onde", months_left: 14, needed_monthly: 1450, months_early: 6 }), F);
  assert.equal(t.rozet, "Önde");
  assert.equal(t.ayrinti, "Son aylarda ayda ₺2.500 ayırıyorsun. Bu hızla Temmuz 2027'de dolar, 6 ay erken.");
});

test("tarihsiz: rozet yok, tahmini ay; hız yoksa kalan", () => {
  const t = hedefMetni(G({ status: "tarihsiz", months_left: null, needed_monthly: null }), F);
  assert.deepEqual([t.rozet, t.satir], [null, "Bu hızla Temmuz 2027'de dolar"]);
  const z = hedefMetni(G({ status: "tarihsiz", months_left: null, needed_monthly: null, pace: 0, eta: null, pace_source: null }), F);
  assert.deepEqual([z.satir, z.ayrinti], ["₺20.000 kaldı", "Ayırdıkça ne zaman dolacağını gösteririz."]);
});

test("son tarih geçti ve tamamlandı", () => {
  const t = hedefMetni(G({ status: "geride", deadline_passed: true, months_left: 0, left: 2500, needed_monthly: 2500 }), F);
  assert.deepEqual([t.rozet, t.satir], ["Geride", "Son tarih geçti: ₺2.500 kaldı"]);
  assert.deepEqual([hedefMetni(G({ status: "tamam", left: 0 }), F).rozet], ["Tamamlandı"]);
});

test("altın: gram cinsinden, TL karşılığı yaklaşık; plan hızı", () => {
  const birim = (n: number) => `${new Intl.NumberFormat("tr-TR").format(n)} gr`;
  const t = hedefMetni(G({ status: "geride", unit: "ALTIN_24", left: 12, needed_monthly: 1.5, needed_monthly_try: 6200, pace: 1, pace_try: 4100,
    raise_by: 0.5, raise_by_try: 2050 }), { para: tl, birim });
  assert.equal(t.satir, "Hedefe 8 ay: ayda 1,5 gr (≈\u00a0₺6.200) al");
  assert.equal(t.ayrinti, "Bu hızla (ayda 1 gr) yetişmez: ayda 0,5 gr (≈\u00a0₺2.050) daha al.");
  const p = hedefMetni(G({ unit: "CEYREK", pace: 1, pace_source: "plan", needed_monthly: 0.8 }), { para: tl, birim: (n) => `${n} adet` });
  assert.match(p.ayrinti!, /^Planına göre ayda 1 adet alacaksın\./);
});

test("yıl eki", () => {
  assert.deepEqual([2026, 2027, 2030, 2033, 2040, 2050, 2060].map(yilEki), ["da", "de", "da", "te", "ta", "de", "ta"]);
  assert.equal(ayda("2027-03"), "Mart 2027'de");
});
