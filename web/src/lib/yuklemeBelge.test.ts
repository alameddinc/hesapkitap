// Toplu yüklemede okunan fiş/fatura: onay bekleyenler deposu, metinler, fatura taslağı, dosya seçimi (veriler uydurma).
import assert from "node:assert/strict";
import { test } from "node:test";

import type { UploadResult } from "../api";
import {
  sifirlaTest, belgeAbone, belgeBekliyor, belgeleriAl, belgeOzet, belgeSil, bekleyenBelgeler, durumMetni, faturaTaslak,
  gonderilir, yapilacakBaslik, yapilacakNeden, type Satir,
} from "./yuklemeBelge.ts";

const fatura = (): Satir => ({
  filename: "ornek-fatura.pdf", status: "document", doc_type: "bill", message: "Fatura okundu — onayla",
  doc: { type: "bill", provider: "Örnek Elektrik", amount: 412.5, due_date: "2026-11-25", category: "Faturalar",
    description: "Ekim tüketimi", file_name: "abc.pdf", confidence: "yüksek" },
});
const fis = (): Satir => ({
  filename: "fis.jpg", status: "document", doc_type: "receipt",
  doc: { type: "receipt", merchant: "Örnek Market", total: 86.4, date: "2026-11-10", confidence: "yüksek" },
});

test("yalnız okunan belgeler bekleyenlere girer, anahtarlanır; aynı satır iki kez eklenmez", () => {
  sifirlaTest();
  let n = 0;
  const off = belgeAbone(() => { n++; });
  const res: Satir[] = [fatura(), { filename: "ekstre.pdf", status: "saved" }, fis()];
  belgeleriAl(res, (name) => (name === "fis.jpg" ? new File(["x"], "fis.jpg", { type: "image/jpeg" }) : null));
  belgeleriAl(res);
  assert.equal(bekleyenBelgeler().length, 2);
  assert.ok(res[0]!.belgeKey && res[2]!.belgeKey && res[0]!.belgeKey !== res[2]!.belgeKey);
  assert.equal(res[1]!.belgeKey, undefined);
  assert.equal(bekleyenBelgeler()[1]!.file?.name, "fis.jpg");
  assert.equal(n, 1);
  assert.ok(belgeBekliyor(res[0]!.belgeKey));
  belgeSil(res[0]!.belgeKey);
  belgeSil(res[0]!.belgeKey); // ikinci kez: değişiklik yok
  assert.equal(n, 2);
  assert.ok(!belgeBekliyor(res[0]!.belgeKey) && belgeBekliyor(res[2]!.belgeKey));
  off();
  sifirlaTest();
});

test("metinler: sunucu mesajı, yoksa türüne göre; Yapılacaklar sözleri mobille aynı", () => {
  assert.equal(durumMetni(fatura()), "Fatura okundu — onayla");
  assert.equal(durumMetni(fis()), "Fiş okundu — onayla");
  assert.equal(yapilacakBaslik(fatura()), "Okunan fatura onay bekliyor");
  assert.equal(yapilacakBaslik(fis()), "Okunan fiş onay bekliyor");
  assert.equal(yapilacakNeden(fatura()), "ödemelere eklemek için onayla");
  assert.equal(yapilacakNeden(fis()), "gider olarak kaydetmek için onayla");
});

test("özet ve fatura taslağı", () => {
  assert.deepEqual(belgeOzet(fatura()), { ad: "Örnek Elektrik", tutar: 412.5, tarih: "2026-11-25", tarihEtiket: "son ödeme" });
  assert.deepEqual(belgeOzet(fis()), { ad: "Örnek Market", tutar: 86.4, tarih: "2026-11-10", tarihEtiket: "" });
  assert.deepEqual(faturaTaslak(fatura()), { provider: "Örnek Elektrik", amount: 412.5, due_date: "2026-11-25",
    category: "Faturalar", description: "Ekim tüketimi", file_name: "abc.pdf", confidence: "yüksek", cached: false });
  const bos: UploadResult = { filename: "x.pdf", status: "document", doc_type: "bill" };
  assert.deepEqual(belgeOzet(bos), { ad: null, tutar: null, tarih: null, tarihEtiket: "" });
});

test("dosya seçimi: fotoğraf yalnız docs açıkken", () => {
  const jpg = { name: "IMG_0001.JPG", type: "image/jpeg" };
  const heic = { name: "IMG_0002.HEIC", type: "" };
  assert.ok(gonderilir({ name: "ekstre.PDF", type: "application/pdf" }, false));
  assert.ok(gonderilir({ name: "dokum.xlsx", type: "" }, false));
  assert.ok(!gonderilir(jpg, false) && !gonderilir(heic, false));
  assert.ok(gonderilir(jpg, true) && gonderilir(heic, true));
  assert.ok(!gonderilir({ name: "not.txt", type: "text/plain" }, true));
});
