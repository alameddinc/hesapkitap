// Zekât / sadaka "Nasıl ödedin?": gövde, eski sunucu uyumu, kayıtlı ödemenin düzeltilmesi (veriler uydurma).
import assert from "node:assert/strict";
import { test } from "node:test";

import { NASIL_START, altinAsimi, altinVarliklari, nasilDurumu, nasilGovde, nasilSecenek, odemeAltYazi, odemeDuzeltGovde } from "./nasilOdedin.ts";

test("seçenekler: altın yeteneği yoksa altın seçenekleri yok", () => {
  assert.deepEqual(nasilSecenek(false), ["banka", "kredi_karti", "nakit", "havale"]);
  assert.deepEqual(nasilSecenek(true).slice(-2), ["altin_bozdurma", "altin"]);
});

test("banka: hesap isteğe bağlı; eski sunucuya nakit + hesap gider", () => {
  assert.deepEqual(nasilGovde(NASIL_START, true), { payment: "banka", account_id: null, card_last4: null });
  assert.deepEqual(nasilGovde({ ...NASIL_START, account: 3 }, false), { payment: "nakit", account_id: 3, card_last4: null });
});

test("altın: hesap/kart gitmez; varlık ve miktar ikisi de varsa düşülür", () => {
  const s = { ...NASIL_START, how: "altin" as const, account: 3, asset: 7, qty: 2 };
  assert.deepEqual(nasilGovde(s, true), { payment: "altin", account_id: null, card_last4: null, asset_id: 7, asset_qty: 2 });
  assert.deepEqual(nasilGovde({ ...s, qty: null }, true), { payment: "altin", account_id: null, card_last4: null });
  assert.deepEqual(nasilGovde({ ...s, how: "altin_bozdurma", asset: null }, true),
    { payment: "altin_bozdurma", account_id: null, card_last4: null });
  // havale: kime
  assert.equal(nasilGovde({ ...NASIL_START, how: "havale", recipient: " Örnek Vakfı " }, true).recipient, "Örnek Vakfı");
});

test("düzeltme: yalnız değişenler; şekil aynıysa gönderilmez, değişemiyorsa hiç gönderilmez", () => {
  const p = { id: 1, amount: 1000, pay_date: "2026-10-20", note: null, how: "banka" as const, account_id: 3,
    card_last4: null, method_editable: true };
  const ayni = { amount: 1000, pay_date: "2026-10-20", nasil: nasilDurumu(p) };
  assert.deepEqual(odemeDuzeltGovde(p, ayni, true), {});
  assert.deepEqual(odemeDuzeltGovde(p, { ...ayni, amount: 1200 }, true), { amount: 1200 });
  const altin = { ...ayni, nasil: { ...ayni.nasil, how: "altin" as const, asset: 7, qty: 2 } };
  assert.deepEqual(odemeDuzeltGovde(p, altin, true),
    { payment: "altin", account_id: null, card_last4: null, asset_id: 7, asset_qty: 2 });
  assert.deepEqual(odemeDuzeltGovde({ ...p, method_editable: false }, altin, true), {});
  assert.deepEqual(odemeDuzeltGovde(p, altin, false), {}); // eski sunucu: şekil değişmez
  // şekli girilmemiş eski ödeme: seçilen şekil gönderilir (gider açılır)
  const eski = { id: 2, amount: 300, pay_date: "2026-10-02", note: null };
  assert.deepEqual(odemeDuzeltGovde(eski, { amount: 300, pay_date: "2026-10-02", nasil: NASIL_START }, true),
    { payment: "banka", account_id: null, card_last4: null });
});

test("satır alt yazısı ve altın varlıkları", () => {
  assert.equal(odemeAltYazi({ id: 1, amount: 1, pay_date: "2026-10-20", note: "Geçmiş yıl", how: "altin", asset_qty: 2,
    asset_label: "Çeyrek altın", asset_unit: "adet" }), "Altın vererek · Çeyrek altın 2 adet · Geçmiş yıl");
  assert.equal(odemeAltYazi({ id: 1, amount: 1, pay_date: "2026-10-20", note: null }), "");
  assert.deepEqual(altinVarliklari([
    { id: 1, kind: "CEYREK", source: "varlik", label: "Çeyrek altın", qty: 4, unit: "adet" },
    { id: 2, kind: "USD", source: "varlik", label: "Dolar", qty: 100, unit: "$" },
    { kind: "TRY", source: "hesap", label: "Örnek Bank", qty: 1, unit: "₺" },
  ]), [{ id: 1, label: "Çeyrek altın", qty: 4, unit: "adet" }]);
});

test("elindekinden fazla altın düşülemez", () => {
  const golds = [{ id: 7, label: "Çeyrek altın", qty: 4, unit: "adet" }];
  const s = { ...NASIL_START, how: "altin" as const, asset: 7, qty: 5 };
  assert.equal(altinAsimi(s, golds), true);
  assert.equal(altinAsimi({ ...s, qty: 4 }, golds), false);
  assert.equal(altinAsimi({ ...s, how: "banka" }, golds), false);
  assert.equal(altinAsimi({ ...s, asset: null }, golds), false);
});
