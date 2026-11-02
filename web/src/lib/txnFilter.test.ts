// İşlemler süzgeci (adres çubuğu): yıl özeti satırının saydığı küme ile listenin süzdüğü küme aynı olmalı.
import assert from "node:assert/strict";
import { test } from "node:test";

import type { Row } from "../api";
import { hasTxnFilter, matchTxnFilter, parseTxnFilter, txnFilterHash, txnFilterLabel } from "./txnFilter.ts";

const row = (o: Partial<Row>): Row => ({ month: "2026-03", category: "Market", merchant: "Örnek Market", card: "Örnek Kart",
  counts_as_spend: true, ...o } as Row);

test("adres gidiş-dönüş: Türkçe ve & içeren kategori bozulmaz", () => {
  const f = { kategori: "Kira & Aidat", yil: 2026, gider: true };
  const h = txnFilterHash(f);
  assert.equal(h, "/islemler?kategori=Kira+%26+Aidat&yil=2026&gider=1");
  assert.deepEqual(parseTxnFilter(`#${h}`),
    { kategori: "Kira & Aidat", isyeri: undefined, kart: undefined, yil: 2026, gider: true, faizsiz: undefined });
});

test("süzgeçsiz adres düz İşlemler", () => {
  assert.equal(txnFilterHash({}), "/islemler");
  assert.equal(hasTxnFilter(parseTxnFilter("#/islemler")), false);
  assert.equal(parseTxnFilter("#/islemler?yil=abc").yil, undefined);
});

test("kategori + yıl + gider: yalnız o yılın gidere sayılan satırları", () => {
  const f = { kategori: "Market", yil: 2026, gider: true };
  assert.equal(matchTxnFilter(row({}), f, "2026-10"), true);
  assert.equal(matchTxnFilter(row({ month: "2025-12" }), f, "2026-10"), false);
  assert.equal(matchTxnFilter(row({ category: "Ulaşım" }), f, "2026-10"), false);
  assert.equal(matchTxnFilter(row({ counts_as_spend: false }), f, "2026-10"), false);
  // gelecek dönemin taksit parçası yıl özetine girmez
  assert.equal(matchTxnFilter(row({ month: "2026-11" }), f, "2026-10"), false);
});

test("işyeri ve kart süzgeci", () => {
  assert.equal(matchTxnFilter(row({}), { isyeri: "Örnek Market" }), true);
  assert.equal(matchTxnFilter(row({ merchant: "Başka" }), { isyeri: "Örnek Market" }), false);
  assert.equal(matchTxnFilter(row({}), { kart: "Örnek Kart" }), true);
  assert.equal(matchTxnFilter(row({ card: null }), { kart: "Örnek Kart" }), false);
});

test("başlık", () => {
  assert.equal(txnFilterLabel({ kategori: "Kira & Aidat", yil: 2026, gider: true }), "Kira & Aidat · 2026 yılı · yalnız gider");
});

test("faizsiz: işyeri/kart sıralaması faiz ve ücreti saymaz", () => {
  const f = { kart: "Örnek Kart", gider: true, faizsiz: true };
  assert.equal(matchTxnFilter(row({ kind: "harcama" }), f), true);
  assert.equal(matchTxnFilter(row({ kind: "faiz_ucret" }), f), false);
  assert.equal(matchTxnFilter(row({ kind: "faiz_ucret" }), { kart: "Örnek Kart", gider: true }), true);
  assert.equal(parseTxnFilter(`#${txnFilterHash(f)}`).faizsiz, true);
  assert.equal(txnFilterLabel(f), "Kart: Örnek Kart · yalnız gider · faiz/ücret hariç");
});

test("eski sunucu bayrak vermezse istemci kuralı (kart ödemesi gider değil)", () => {
  const f = { gider: true };
  assert.equal(matchTxnFilter(row({ counts_as_spend: undefined, kind: "harcama" }), f), true);
  assert.equal(matchTxnFilter(row({ counts_as_spend: undefined, kind: "odeme" }), f), false);
});
