// Ödeme planı borcun alt kaydı: öneri, plan öncesi ödenen ve nötr fark bilgisi (sunucu app/debt_schedule.py ile aynı).
import assert from "node:assert/strict";
import { test } from "node:test";

import { oneriTaksit, planFarki, planFarkMetni, planKalani, planOncesiOdenen } from "./odemePlani.ts";

const tl = (n: number) => `₺${n.toLocaleString("tr-TR")}`;
// borç planı testlerindeki yasak ifadeler (tests/test_debt_plan.py BANNED + test_debt_schedule.py RESTRUCTURE)
const YASAK = [/kredi\s*çek/i, /kredi\s*al/i, /kredi\s*kullan/i, /taksitlendir/i, /borç\s*transfer/i, /nakit\s*avans/i,
  /ertele/i, /refinans/i, /yapılandır/i, /vade(yi|sini)?\s*uzat/i, /öteleme/i, /borcunu\s*böl/i];

test("ilk taksit ayından önce ödenen plana girmez", () => {
  const p = [{ amount: 1000, paid_at: "2026-10-05" }, { amount: 500, paid_at: "2026-11-02" }];
  assert.equal(planOncesiOdenen(p, "2026-11"), 1000);
  assert.equal(planOncesiOdenen(p, "2026-10"), 0);
  assert.equal(planKalani(10000, p, "2026-11"), 9000);
  assert.equal(planKalani(800, p, "2026-12"), 0);
});

test("öneri kalan ÷ sayı, tam liraya yuvarlı", () => {
  assert.equal(oneriTaksit(9000, 6), 1500);
  assert.equal(oneriTaksit(11500, 6), 1917);
  assert.equal(oneriTaksit(0, 6), 0);
  assert.equal(oneriTaksit(9000, 0), 0);
});

test("toplam kalana eşitse bilgi yok; farklıysa nötr cümle", () => {
  assert.equal(planFarki(12000, 2000, 6), null);
  const az = planFarki(11500, 2000, 6)!;
  assert.deepEqual([az.adet, az.son, az.fark], [6, 1500, 500]);
  assert.equal(planFarkMetni(az, tl), "Taksitler toplamı ₺12.000, kalan borç ₺11.500. Son taksit ₺1.500 sayılır.");
  const erken = planFarki(11500, 3000, 6)!;
  assert.deepEqual([erken.adet, erken.son], [4, 2500]);
  assert.match(planFarkMetni(erken, tl), /borç 4\. taksitte biter; son taksit ₺2\.500 sayılır/);
  const cok = planFarki(15000, 2000, 6)!;
  assert.deepEqual([cok.adet, cok.son, cok.fark], [6, 5000, -3000]);
  for (const f of [az, erken, cok]) assert.deepEqual(YASAK.filter((r) => r.test(planFarkMetni(f, tl))).map(String), []);
});
