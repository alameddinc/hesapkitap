// Alırsam ne olur? / Alsam mı? sözleri: faize yönlendirmez (tutarlar uydurma).
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";

import { ALSAM_KILIT_METNI, alsamSoz, TAKSIT_SECENEK, VARSAYILAN_TAKSIT, type AlsamGirdi } from "./alsamMi.ts";

const tl = (x: number) => `₺${x.toFixed(2)}`;
// borç planı testindeki yasak ifadeler (tests/test_debt_plan.py BANNED) + alım ekranına özgü yönlendirmeler
const YASAK = [/kredi\s*çek/i, /kredi\s*al/i, /kredi\s*kullan/i, /ihtiyaç\s*kredi/i, /taksitlendir/i, /borç\s*transfer/i,
  /nakit\s*avans/i, /ertele/i, /refinans/i, /yapılandır/i, /asgari/i, /kampanya/i, /taksitle\s*al/i, /taksit\s*öner/i,
  /\+₺[\d.,]+\s*fark\b/i, /bütçene\s*uyuyor/i, /rahat(ça)?\s*alabilirsin/i];

function hepsi(g: AlsamGirdi): string[] {
  const s = alsamSoz(g, tl, undefined, "Buzdolabı");
  return [s.maliyet.t, s.faiz ?? "", s.sonuc.t, s.biriktir ?? "", s.biriktirAlt];
}

const DURUMLAR: AlsamGirdi[] = [];
for (const [n] of TAKSIT_SECENEK) for (const extra of [0, 1860]) for (const neg of [[], ["2026-12"]]) for (const tight of [[], ["2027-01"]])
  DURUMLAR.push({ amount: 23000, n, extra: n > 1 ? extra : 0, monthly: (23000 + (n > 1 ? extra : 0)) / n, negative: neg, tight });

test("varsayılan seçim peşin; seçenekler peşinle başlar", () => {
  assert.equal(VARSAYILAN_TAKSIT, 1);
  assert.deepEqual(TAKSIT_SECENEK[0], [1, "Peşin"]);
  for (const f of ["../components/Simulator.tsx"]) {
    const src = readFileSync(new URL(f, import.meta.url), "utf-8");
    assert.match(src, /useState\(VARSAYILAN_TAKSIT\)/, `${f}: taksit sayısı VARSAYILAN_TAKSIT ile başlamalı`);
    assert.match(src, /useState<"yok" \| "var">\("yok"\)/, `${f}: vade farkı varsayılan olarak yok`);
  }
});

test("hiçbir durumda faize yönlendiren söz yok", () => {
  for (const g of DURUMLAR) for (const t of hepsi(g)) {
    assert.deepEqual(YASAK.filter((r) => r.test(t)).map(String), [], `${JSON.stringify(g)} → ${t}`);
  }
  assert.deepEqual(YASAK.filter((r) => r.test(ALSAM_KILIT_METNI)).map(String), []);
  assert.doesNotMatch(ALSAM_KILIT_METNI, /taksitin sonraki aylar/i);
});

test("vade farkı varsa: adıyla 'faiz', tutarıyla; onay (✓) yok; biriktirip peşin alma yolu", () => {
  for (const g of DURUMLAR.filter((x) => x.extra > 0)) {
    const s = alsamSoz(g, tl);
    assert.match(s.maliyet.t, /^Vade farkı: ₺1860\.00 \(faiz\)/);
    assert.match(s.faiz ?? "", /Vade farkı faizdir: ₺1860\.00 fazladan ödersin/);
    assert.ok(!s.sonuc.t.includes("✓") && s.sonuc.ton !== "iyi", s.sonuc.t);
    assert.match(s.biriktir ?? "", /^Biriktirerek al: \d+ ayda ₺23000\.00 biriktirirsen .*peşin alırsın ve ₺1860\.00 vade farkı ödemezsin\.$/);
  }
  const ok = alsamSoz({ amount: 23000, n: 6, extra: 1860, monthly: 4143.33, negative: [], tight: [] }, tl);
  assert.equal(ok.sonuc.t, "Hiçbir ay eksiye düşmüyor; ama ₺1860.00 vade farkı (faiz) ödersin.");
});

test("peşin ve vade farksız taksit", () => {
  const p = alsamSoz({ amount: 23000, n: 1, extra: 0, monthly: 23000, negative: [], tight: [] }, tl, undefined, "Buzdolabı");
  assert.equal(p.maliyet.t, "Peşin · vade farkı yok");
  assert.equal(p.sonuc.t, "✓ Hiçbir ay eksiye düşmüyorsun.");
  assert.equal(p.faiz, null);
  assert.equal(p.biriktir, null);
  assert.equal(p.biriktirAlt, "“Buzdolabı” için ₺23000.00 hedef");
  const t = alsamSoz({ amount: 24000, n: 6, extra: 0, monthly: 4000, negative: [], tight: [] }, tl);
  assert.equal(t.maliyet.t, "Vade farkı yok · ayda ₺4000.00");
  assert.equal(t.biriktir, "Biriktirerek al: 6 ayda ₺24000.00 biriktirirsen (ayda ₺4000.00) peşin alırsın.");
  assert.equal(t.biriktirAlt, "Ayda ₺4000.00 · 6 ayda peşin alırsın");
  const neg = alsamSoz({ amount: 24000, n: 1, extra: 0, monthly: 24000, negative: ["2026-12"], tight: [] }, tl);
  assert.equal(neg.sonuc.t, "⚠ Aralık’ta paran eksiye düşer.");
});

// İki depoda aynı dosya: HESAPKITAP_MOBILE_DIR=../../hesapkitap-mobile npm test
const MOBILE = process.env.HESAPKITAP_MOBILE_DIR;
test("mobil src/lib/alsamMi.ts bu dosyayla aynı", { skip: !MOBILE && "HESAPKITAP_MOBILE_DIR verilmedi" }, () => {
  const web = readFileSync(new URL("./alsamMi.ts", import.meta.url), "utf-8");
  const mob = readFileSync(`${MOBILE}/src/lib/alsamMi.ts`, "utf-8");
  assert.equal(mob, web);
});
