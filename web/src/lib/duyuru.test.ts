// Yönetici duyurusu: hedef özeti, onay metni, form denetimi, geçmiş satırı.
import assert from "node:assert/strict";
import { test } from "node:test";

import { formHatasi, gecmisOzeti, hedefOzeti, onayMetni, rotaAdi, type Duyuru } from "./duyuru.ts";

const h = { users: 1234, devices: 1500, by_platform: { ios: 600, android: 900 }, opted_out: 3, enabled: true };

test("hedef özeti ve onay", () => {
  assert.equal(hedefOzeti(h), "1.234 kişi · 1.500 cihaz (iOS 600, Android 900)");
  assert.equal(hedefOzeti({ ...h, users: 0, devices: 0, by_platform: { ios: 0, android: 0 } }), "0 kişi · 0 cihaz");
  assert.equal(onayMetni(h, false), "1.234 kişiye, 1.500 cihaza gidecek. Gönderilsin mi?");
  assert.equal(onayMetni({ ...h, devices: 2 }, true), "Duyuru yalnız senin 2 cihazına gidecek. Gönderilsin mi?");
});

test("form denetimi", () => {
  const ok = { title: "1.0.5 geldi", body: "Güncelle", route: "", below_version: "" };
  assert.equal(formHatasi(ok), null);
  assert.equal(formHatasi({ ...ok, title: "  " }), "Başlık boş olamaz");
  assert.equal(formHatasi({ ...ok, body: "" }), "Metin boş olamaz");
  assert.match(formHatasi({ ...ok, title: "x".repeat(81) })!, /en çok 80/);
  assert.match(formHatasi({ ...ok, body: "y".repeat(301) })!, /en çok 300/);
  assert.equal(formHatasi({ ...ok, route: "store" }), null);
  assert.equal(formHatasi({ ...ok, route: "#/diger/borc-plani" }), null);
  assert.match(formHatasi({ ...ok, route: "https://ornek.com" })!, /Yol/);
  assert.equal(formHatasi({ ...ok, below_version: "1.0.5" }), null);
  assert.match(formHatasi({ ...ok, below_version: "yeni" })!, /Sürüm/);
});

test("geçmiş satırı", () => {
  const a: Duyuru = { id: 1, created_at: "2026-11-11 11:00:00", finished_at: null, admin_name: "admin", title: "t", body: "b",
    route: "store", platform: "android", below_version: "1.0.5", test_only: false, target_users: 10, target_devices: 12,
    sent: 9, failed: 1, queued: 2, status: "queued" };
  assert.equal(gecmisOzeti(a), "Android · 1.0.5 altı · 10 kişi, 12 cihaz · 9 ulaştı · 1 başarısız · 2 kişi sessiz saatte bekliyor");
  assert.equal(gecmisOzeti({ ...a, test_only: true, below_version: null, status: "sending" }), "Yalnız bana · 10 kişi, 12 cihaz · gönderiliyor");
  assert.equal(rotaAdi("store"), "Mağaza sayfası");
  assert.equal(rotaAdi(null), "Özet");
});
