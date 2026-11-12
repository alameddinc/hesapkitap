// Garanti arşivi yardımcıları: fiş okumasından taslak, "Arıza / servis" özeti, süre yazısı. Uydurma ürün/mağaza adları.
import assert from "node:assert/strict";
import { test } from "node:test";

import { okumadanTaslak, servisMetni, sureYazi, type Garanti } from "./garantiMetin.ts";

test("tek kalemli fiş: ad, fiyat, mağaza, tarih dolu", () => {
  const t = okumadanTaslak({ merchant: "Örnek Elektronik", date: "2026-10-20", total: 2900, receipt_file: "a".repeat(64) + ".jpg",
    items: [{ name: "KETTLE K2 1.7L", product: "Örnek Kettle K2", brand: "Kuzey", qty: 1, amount: 2900 }] });
  assert.equal(t.source, "photo");
  assert.equal(t.name, "Örnek Kettle K2");
  assert.equal(t.price, 2900);
  assert.equal(t.store, "Örnek Elektronik");
  assert.equal(t.purchase_date, "2026-10-20");
  assert.equal(t.items[0].brand, "Kuzey");
  assert.equal(t.receipt_file, "a".repeat(64) + ".jpg");
});

test("çok kalemli fiş: kişi seçer; tutarsız kalem ve bozuk tarih atılır", () => {
  const t = okumadanTaslak({ merchant: "Örnek Market", date: "20.10.2026", total: 3150,
    items: [{ name: "Örnek Ütü", amount: 2900 }, { name: "Poşet", amount: 0.5 }, { name: "İndirim", amount: -100 }, { name: "", amount: 5 }] });
  assert.equal(t.name, null);
  assert.equal(t.price, null);
  assert.equal(t.purchase_date, null);
  assert.deepEqual(t.items.map((i) => i.name), ["Örnek Ütü", "Poşet"]);
});

test("kalemsiz okuma: toplam fiyat olur", () => {
  const t = okumadanTaslak({ merchant: "Örnek Mağaza", total: 1250, items: [] });
  assert.equal(t.price, 1250);
  assert.equal(t.name, null);
});

test("servis özeti", () => {
  const g = { name: "Örnek Süpürge X1", brand: "Kuzey", model: "X1", serial_no: "SN-0001", purchase_date: "2026-03-10",
    store: "Örnek Elektronik", price: 8499.9, ends_at: "2029-03-10", extra_months: 12 } as Garanti;
  const tarih = (iso: string) => iso.split("-").reverse().join(".");
  const para = (x: number) => `₺${x}`;
  assert.equal(servisMetni(g, tarih, para), "Örnek Süpürge X1 (Kuzey X1)\nSeri no: SN-0001\nAlış: 10.03.2026, Örnek Elektronik, ₺8499.9\n"
    + "Garanti bitişi: 10.03.2029 (12 ay ek garanti dahil)");
  assert.equal(servisMetni({ ...g, brand: null, model: null, serial_no: null, store: null, price: null, extra_months: 0 }, tarih, para),
    "Örnek Süpürge X1\nAlış: 10.03.2026\nGaranti bitişi: 10.03.2029");
});

test("süre yazısı", () => {
  assert.equal(sureYazi(24), "2 yıl");
  assert.equal(sureYazi(18), "18 ay");
  assert.equal(sureYazi(0), "0 ay");
});
