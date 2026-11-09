// Özet "Dönem sonu" ve Planlama dökümü metinleri (mobil src/lib/beklenen.ts ile birebir aynı olmalı). Tutarlar uydurma.
import assert from "node:assert/strict";
import { test } from "node:test";

import {
  DONEM_SONU, donemSonuAlt, donemSonuDokum, donemSonuPlandan, donemSonuVar, eksiMi, isaretli, tamTl, type Beklenen, type DonemSonu,
} from "./beklenen.ts";

const d = (s: string) => s.replace(/⁠/g, ""); // WJ (işaret ile tutar ayrılmasın) karşılaştırmada yok
const sifir = { recorded_expense: 0, pending_installments: 0, pending_subscriptions: 0, pending_recurring_expense: 0,
  pending_bills: 0, pending_expense: 0, recorded_income: 0, pending_income: 0 };
// örnek: gider 101.678,95 + ekstresi gelmemiş taksit 35.487,50; bekleyen gelir 215.087,61; tahmini kart 45.804,16
const e = (o: Partial<Beklenen> = {}): Beklenen => ({
  month: "2026-11", source: "plan", expense: 137166.45, income: 215087.61, net: 77921.16, variable_estimate: 45804.16,
  forecast_net: 32117, parts: { ...sifir, recorded_expense: 101678.95, pending_installments: 35487.5, pending_expense: 35487.5,
    pending_income: 215087.61 }, ...o,
});

test("tam lira ve işaret", () => {
  assert.equal(tamTl(137166.45), "₺137.166");
  assert.equal(d(tamTl(-12400.6)), "−₺12.401");
  assert.equal(d(isaretli(77921.16, tamTl)), "+₺77.921");
  assert.equal(isaretli(77921.16, tamTl), "+⁠₺77.921"); // "+" ile tutar bölünmez
  assert.equal(isaretli(-500, tamTl), "−⁠₺500");
  assert.equal(isaretli(0, tamTl), "₺0");
  assert.equal(eksiMi(-1), true);
  assert.equal(eksiMi(0), false);
});

test("kart: geçmiş dönem (null), eski sunucu (forecast yok) görünmez", () => {
  assert.equal(donemSonuVar(e()), true);
  assert.equal(donemSonuVar(null), false);
  assert.equal(donemSonuVar(undefined), false);
  assert.equal(donemSonuVar(e({ forecast_net: undefined, variable_estimate: undefined })), false);
  assert.equal(d(donemSonuAlt(e() as DonemSonu, tamTl)), "Yeni harcama yapmazsan +₺77.921");
  assert.equal(DONEM_SONU.her, "Her zamanki harcamanla");
});

test("Planlama dökümü: kırılım yalnız bekleyen varsa, sıfır bekleyen yok, ara toplam ve sonuç", () => {
  const x = e() as DonemSonu;
  assert.deepEqual(donemSonuDokum(x).map((r) => [r.key, r.amount, r.level, r.total ?? null]), [
    ["income", 215087.61, 0, null],
    ["pending_income", 215087.61, 1, null], // kayıtlı gelir 0: satırı yok
    ["expense", -137166.45, 0, null],
    ["recorded_expense", -101678.95, 1, null],
    ["pending_installments", -35487.5, 1, null],
    ["net", 77921.16, 0, "ara"],
    ["variable_estimate", -45804.16, 0, null],
    ["forecast_net", 32117, 0, "son"],
  ]);
  assert.deepEqual(donemSonuDokum(x).filter((r) => r.level).map((r) => r.label),
    ["Bekleniyor", "Kayıtlı", "Ekstresi gelmemiş taksitler"]);
  // bekleyen yok, tahmini kart harcaması yok: yalnız Gelir, Kesin gider, iki sonuç
  const sade = { ...x, parts: { ...sifir, recorded_expense: 900, recorded_income: 1000 }, income: 1000, expense: 900, net: 100,
    variable_estimate: 0, forecast_net: 100 };
  assert.deepEqual(donemSonuDokum(sade).map((r) => r.key), ["income", "expense", "net", "forecast_net"]);
});

test("Planlama ayından kaynak; eski sunucu (parts yok) null", () => {
  assert.equal(donemSonuPlandan({ income: 0, expense: 0, net: 1, estimated: 0, net_with_estimate: 1 }), null);
  const k = donemSonuPlandan({ income: 215087.61, expense: 137166.45, net: 77921.16, estimated: 45804.16, net_with_estimate: 32117, parts: e().parts })!;
  assert.deepEqual([k.income, k.expense, k.net, k.variable_estimate, k.forecast_net], [215087.61, 137166.45, 77921.16, 45804.16, 32117]);
});
