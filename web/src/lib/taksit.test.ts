// Özet → "Bu ay ödenecek taksitler" metinleri (mobil src/lib/taksit.ts ile birebir aynı olmalı). Tutarlar uydurma.
import assert from "node:assert/strict";
import { test } from "node:test";

import { taksitBaslik, taksitDurum, taksitGoster, taksitKalan, taksitKalemAlt, taksitSayi, type TaksitKalem, type TaksitOzet } from "./taksit.ts";

const para = (n: number) => `₺${n}`;
const oz = (o: Partial<TaksitOzet> = {}): TaksitOzet => ({
  month: "2026-11", current: true, total: 4250, count: 6, in_spend: 3050, expected: 1200, remaining: 18400, remaining_count: 7, ...o,
});

test("görünürlük: eski sunucu (alan yok) ve taksitsiz dönem gizli", () => {
  assert.equal(taksitGoster(undefined), false);
  assert.equal(taksitGoster(oz({ total: 0, remaining: 0 })), false);
  assert.equal(taksitGoster(oz({ total: 0, remaining: 500 })), true); // bu ay yok ama kalan borç var
  assert.equal(taksitGoster(oz({ total: 0, remaining: 500, current: false })), false);
  assert.equal(taksitGoster(oz()), true);
});

test("başlık, sayı, durum ve kalan borç", () => {
  assert.equal(taksitBaslik(oz()), "Bu ay ödenecek taksitler");
  assert.equal(taksitBaslik(oz({ current: false, month: "2026-10" })), "Ekim dönemi taksitleri");
  assert.equal(taksitSayi(oz()), "6\u00a0taksit"); // bölünmeyen boşluk
  assert.equal(taksitSayi(oz({ count: 0 })), "");
  assert.equal(taksitDurum(oz(), para), "₺3050 harcamana dahil · ₺1200 henüz gidere yazılmadı (ekstre bekleniyor)");
  assert.equal(taksitDurum(oz({ expected: 0, in_spend: 4250 }), para), "Harcamana dahil");
  assert.equal(taksitDurum(oz({ in_spend: 0, expected: 4250 }), para), "Ekstresi gelmedi · harcamana henüz dahil değil");
  assert.equal(taksitKalan(oz(), para), "Kalan taksit borcu ₺18400 · 7\u00a0alışveriş");
  assert.equal(taksitKalan(oz({ current: false }), para), null);
  assert.equal(taksitKalan(oz({ remaining: 0 }), para), null);
});

// bölünmeyen boşluklar (dar ekran) karşılaştırmada düz boşluk sayılır
const duz = (x: string) => x.replace(/\u00a0/g, " ");

test("kalem alt satırı", () => {
  const k: TaksitKalem = { uid: "c1", source: "card", title: "ORNEK MOBILYA", description: "", card: "VakıfBank …9012",
    installment: "3/6", no: 3, of: 6, amount: 8750, in_spend: true, fee: false, due_date: "2026-11-25" };
  assert.equal(duz(taksitKalemAlt(k)), "VakıfBank …9012 · 3/6. taksit · son ödeme 25 Kas");
  assert.equal(duz(taksitKalemAlt({ ...k, no: 6, in_spend: false, due_date: null })), "VakıfBank …9012 · son taksit (6/6) · ekstre bekliyor");
  assert.equal(duz(taksitKalemAlt({ ...k, fee: true, card: null, due_date: null })), "vade farkı/vergi · 3/6");
  assert.equal(duz(taksitKalemAlt({ ...k, spender_name: "Ayşe", due_date: null })), "VakıfBank …9012 · 3/6. taksit · Ayşe");
});
