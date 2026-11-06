// Para/tarih biçimi: web ile mobil aynı metni yazıyor mu (denetim C6, ortak format paketinin ilk adımı).
//
// Ortak durum tablosu (CASES) iki istemcinin de uyması gereken çıktıdır; beklenen değerler mobil
// `hesapkitap-mobile/src/lib/format.ts` ile üretildi. Bu depo mobil kodu içermediği için CI yalnız web'i tabloya karşı
// sınar. Mobil depo yanındaysa aynı tabloyu mobile de uygular:
//   HESAPKITAP_MOBILE_DIR=../../hesapkitap-mobile npm test
//
// Bilinçli farklar (tabloda yok, ortak pakette karar verilecek):
// - Kısa biçim: web tlCompact "85,3 bin ₺" / mobil tlShort "₺85 bin" (10 binin altında mobil kuruşsuz tam sayı).
// - web tl, işaretten sonra görünmez U+2060 (kelime birleştirici) koyar: satır sonunda "−" ile "₺" ayrılmasın. Görünen
//   metin aynıdır; karşılaştırma bu karakteri atarak yapılır.
// - mobil tl(null) "—" döner; web tl yalnız sayı alır.
import assert from "node:assert/strict";
import { test } from "node:test";

import { dateTR, dayMonthTR, ek, monthLabel, pctSigned, pctTR, tl, yonelme } from "./format.ts";

test("ek: yüzdenin iyelik eki (mobil ile aynı)", () => {
  assert.deepEqual([1, 6, 10, 14, 40, 62, 100].map((n) => `${n}'${ek(n)}`), ["1'i", "6'sı", "10'u", "14'ü", "40'ı", "62'si", "100'ü"]);
});

test("yonelme: ada yönelme eki (mobil ile aynı)", () => {
  assert.equal(yonelme("Ayşe"), "Ayşe'ye");
  assert.equal(yonelme("Mehmet"), "Mehmet'e");
  assert.equal(yonelme("Ortak"), "Ortak'a");
  assert.equal(yonelme("Ali"), "Ali'ye");
  assert.equal(yonelme("Burak"), "Burak'a");
});

const visible = (s: string) => s.replace(/⁠/g, "");

/** [tutar, beklenen tl()] — eksi U+2212, binlik nokta, kuruş virgül, her zaman 2 hane. */
export const CASES: [number, string][] = [
  [0, "₺0,00"],
  [-0, "₺0,00"],
  [1, "₺1,00"],
  [0.5, "₺0,50"],
  [0.004, "₺0,00"],
  [0.005, "₺0,01"],
  [1.005, "₺1,01"],
  [12.3, "₺12,30"],
  [999.999, "₺1.000,00"],
  [1234.5, "₺1.234,50"],
  [1234.56, "₺1.234,56"],
  [-1234.56, "−₺1.234,56"],
  [-0.01, "−₺0,01"],
  [-0.004, "−₺0,00"],
  [85300, "₺85.300,00"],
  [9999.4, "₺9.999,40"],
  [1234567.891, "₺1.234.567,89"],
  [-2500000, "−₺2.500.000,00"],
  [1e9, "₺1.000.000.000,00"],
];

/** [ISO tarih, "gün Ay" kısa] — mobil dayMonth ile aynı. */
const DAY_MONTH: [string, string][] = [
  ["2026-10-30", "30 Eki"],
  ["2026-01-05", "5 Oca"],
  ["2026-02-28 23:59:59", "28 Şub"],
  ["2026-12-01", "1 Ara"],
];

test("web tl() ortak tabloya uyuyor", () => {
  for (const [v, want] of CASES) assert.equal(visible(tl(v)), want, `tl(${v})`);
});

test("eksi işareti U+2212 ve ₺ ile ayrılmaz (U+2060)", () => {
  assert.equal(tl(-5), "−⁠₺5,00");
});

test("tarih biçimleri", () => {
  for (const [iso, want] of DAY_MONTH) assert.equal(dayMonthTR(iso), want, iso);
  assert.equal(dateTR("2026-01-05 13:07:14"), "05.01.2026");
  assert.equal(monthLabel("2026-10"), "Ekim 2026");
  assert.equal(monthLabel("2026-10", true), "Eki 26");
});

test("yüzde", () => {
  assert.equal(pctTR(12.34), "%12,3");
  assert.equal(pctTR(-5), "%5");
  assert.equal(pctSigned(12.34), "+%12,3");
  assert.equal(pctSigned(-5), "−%5");
  assert.equal(pctSigned(0), "%0");
});

const MOBILE = process.env.HESAPKITAP_MOBILE_DIR;
test("mobil tl() ve dayMonth aynı tabloya uyuyor", { skip: !MOBILE && "HESAPKITAP_MOBILE_DIR verilmedi" }, async () => {
  const m = await import(`${MOBILE}/src/lib/format.ts`) as {
    tl: (n: number) => string; dayMonth: (s: string) => string; monthLabel: (ym: string) => string;
  };
  for (const [v, want] of CASES) assert.equal(m.tl(v), want, `mobil tl(${v})`);
  for (const [v] of CASES) assert.equal(visible(tl(v)), m.tl(v), `web == mobil tl(${v})`);
  for (const [iso, want] of DAY_MONTH) assert.equal(m.dayMonth(iso), want, `mobil dayMonth(${iso})`);
  assert.equal(`${m.monthLabel("2026-10")} 2026`, monthLabel("2026-10"));
});
