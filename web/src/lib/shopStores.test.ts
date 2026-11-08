// Alışveriş listesi v4 yazıları (uydurma veri; mobil ile aynı metinler).
import assert from "node:assert/strict";
import { test } from "node:test";

import {
  applyHidden, betterSizeText, closedCount, hiddenText, localSub, missingCheapestText, offerSource, offerSub, offersTitle, optimizePath,
  pinnedChoiceText, pinnedUnpricedText, sizeSub, sizesHidden, storeChoices, storeLine, storesButtonText, storesSettingValue, toggleHidden,
  togglePick, unitPriceText, withBrand, keySizeLabel, sizeOptions,
} from "./shopStores.ts";

test("grup anahtarının boyu ve boy seçenekleri", () => {
  assert.equal(keySizeLabel("sut|1l"), "1 L");
  assert.equal(keySizeLabel("su|6x500ml"), "6×500 ml");
  assert.equal(keySizeLabel("elma|kg"), "kg");
  assert.equal(keySizeLabel("yumurta|30ad"), "30 adet");
  assert.equal(keySizeLabel("peynir|0.5kg"), "0,5 kg");
  assert.equal(keySizeLabel("sut"), null);
  const brands = [{ brand: "Örneksüt", sizes: ["sut|1l", "sut|2l", "sut|500ml"] }, { brand: "Mandıra Köy", sizes: ["sut|1l", "sut|5l"] }];
  assert.deepEqual(sizeOptions("sut|1l", brands, null), ["sut|500ml", "sut|1l", "sut|2l", "sut|5l"]);
  assert.deepEqual(sizeOptions("sut|1l", brands, "Mandıra Köy"), ["sut|1l", "sut|5l"]);
  assert.deepEqual(sizeOptions("sut", [], null), []);
});

const m = (n: number) => `₺${n.toLocaleString("tr-TR", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
const TODAY = "2026-11-08";

test("kapalı sayısı, düğme ve Ayarlar değeri", () => {
  assert.equal(closedCount({ chains: ["CarrefourSA", "Hakmar"], local: true }), 3);
  assert.equal(closedCount(null), 0);
  assert.equal(storesButtonText({ chains: [], local: false }), "Mağazalar");
  assert.equal(storesButtonText({ chains: ["Hakmar"], local: true }), "Mağazalar · 2 kapalı");
  assert.equal(storesSettingValue({ chains: [], local: false }), "Hepsi açık");
  assert.equal(storesSettingValue({ chains: ["Hakmar"], local: false }), "1 kapalı");
  assert.equal(hiddenText(0), null);
  assert.equal(hiddenText(3), "3 mağaza gizli");
});

test("yerel marketler alt yazısı", () => {
  assert.equal(localSub([], 0), "Zincir olmayan mahalle marketleri, manavlar");
  assert.equal(localSub(["Örnek Bakkal", "Köşe Manav"], 2), "Örnek Bakkal, Köşe Manav");
  assert.equal(localSub(["A Bakkal", "B Manav", "C Market", "D Fırın"], 6), "A Bakkal, B Manav, C Market ve 3 yer daha");
});

test("zincir anahtarı tam listeyi verir; iyimser güncelleme", () => {
  assert.deepEqual(toggleHidden(["Hakmar"], "CarrefourSA", true), ["Hakmar", "CarrefourSA"]);
  assert.deepEqual(toggleHidden(["Hakmar", "CarrefourSA"], "hakmar", false), ["CarrefourSA"]);
  const d = applyHidden({ hidden: { chains: [], local: false }, chains: [{ chain: "BİM", hidden: false }, { chain: "Hakmar", hidden: false }],
    local: { hidden: false, names: [], count: 0 } }, { chains: ["Hakmar"], local: true });
  assert.deepEqual(d.chains.map((c) => c.hidden), [false, true]);
  assert.equal(d.local.hidden, true);
});

test("birim fiyat ve boy satırı", () => {
  assert.equal(unitPriceText(199, "L", m), "₺199,00/L");
  assert.equal(unitPriceText(null, "kg", m), null);
  assert.equal(sizeSub({ chain: "BİM (Dost)", price: 32.5, unit_price_per: 32.5, per_unit: "L" }, m), "En ucuz: BİM ₺32,50 · ₺32,50/L");
  assert.equal(sizeSub({ chain: "Migros", price: 59.9 }, m), "En ucuz: Migros ₺59,90");
  assert.equal(sizeSub({ chain: null, price: null }, m), "Fiyat yok");
  const rows = [{ key: "sut|500ml", hidden_count: 1 }, { key: "sut|1l", hidden_count: 0 }, { key: "sut|2l", hidden_count: 2 }];
  assert.equal(sizesHidden(rows, "sut|500ml"), 1);
  assert.equal(sizesHidden(rows, "sut|1l"), 2);
  assert.equal(sizesHidden([], "x"), 0);
});

test("mağazalara göre: başlık, kaynak, seçenekler", () => {
  assert.equal(offersTitle("Örneksüt", "1 L"), "Örneksüt · 1 L");
  assert.equal(offersTitle(null, "1 L"), "Her marka · 1 L");
  assert.equal(offerSource({ source: "own", date: "2026-11-03" }, TODAY), "Senin fişin · 3 Kas");
  assert.equal(offerSource({ source: "community", n: 4 }, TODAY), "4 kişi");
  assert.equal(offerSource({ source: "resmi", date: "2026-11-07" }, TODAY), "dün");
  assert.equal(offerSub({ chain: "A101", price: 30, unit_price_per: 30, per_unit: "L", source: "resmi", date: TODAY }, TODAY, m), "₺30,00/L · bugün");
  assert.deepEqual(storeChoices([{ chain: "BİM" }, { chain: "Migros" }, { chain: "BİM" }], "Hakmar"), ["BİM", "Migros", "Hakmar"]);
  assert.deepEqual(storeChoices([{ chain: "BİM" }], "bim"), ["BİM"]);
  assert.equal(pinnedChoiceText("Migros"), "Bu ürünü Migros mağazasından alacağım");
});

test("liste satırı: mağaza ve marka", () => {
  assert.equal(storeLine({ store: "Migros" }), "Migros · sen seçtin");
  assert.equal(storeLine({ store: "Migros", store_missing: true }), "Migros: fiyat yok");
  assert.equal(storeLine({ store: null }), null);
  assert.equal(withBrand("Süt", "Örneksüt"), "Süt · Örneksüt");
  assert.equal(withBrand("Örneksüt süt", "Örneksüt"), "Örneksüt süt");
  assert.equal(withBrand("Süt", null), "Süt");
});

test("Nereye gideyim? boy önerisi, bağlı durak, manuel seçim", () => {
  assert.equal(betterSizeText({ key: "sut|1l", label: "Süt · 1 L", size_label: "1 L", chain: "BİM", packs: 2, price: 32.5, total: 65, saving: 12 }, m),
    "En kârlı boy: 2 × 1 L · BİM ₺65,00 (₺12,00 daha az)");
  assert.equal(pinnedUnpricedText([{ text: "süt" }, { text: "ekmek" }]), "Fiyatı yok: süt, ekmek");
  assert.equal(pinnedUnpricedText([]), null);
  assert.equal(missingCheapestText({ chain: "Hakmar", price: 41.5 }, m), "En ucuz: Hakmar ₺41,50");
  assert.deepEqual(togglePick(["BİM"], "Migros"), ["BİM", "Migros"]);
  assert.deepEqual(togglePick(["BİM", "Migros", "A101"], "Hakmar"), ["BİM", "Migros", "A101"]);
  assert.deepEqual(togglePick(["BİM", "Migros"], "BİM"), ["Migros"]);
  assert.equal(optimizePath(null), "/api/shopping-list/optimize");
  assert.equal(optimizePath(["Migros", "BİM"]), `/api/shopping-list/optimize?chains=Migros,${encodeURIComponent("BİM")}`);
});
