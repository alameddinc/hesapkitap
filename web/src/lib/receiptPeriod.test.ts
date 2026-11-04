// Fiş listesi dönemi: hazır seçimler (ay/yıl) ve tarih aralığı uç sorgusuna doğru çevrilir; ocakta geçen ay geçen yılın aralığı.
import assert from "node:assert/strict";
import { test } from "node:test";

import { receiptQuery, receiptsExportUrl } from "./receiptPeriod.ts";

test("hazır dönemler", () => {
  assert.equal(receiptQuery("this_month", "2026-11-04"), "period=month&value=2026-11");
  assert.equal(receiptQuery("last_month", "2026-11-04"), "period=month&value=2026-10");
  assert.equal(receiptQuery("last_month", "2027-01-15"), "period=month&value=2026-12");
  assert.equal(receiptQuery("this_year", "2026-11-04"), "period=year&value=2026");
  assert.equal(receiptQuery("last_year", "2026-11-04"), "period=year&value=2025");
});

test("tarih aralığı ve biçim", () => {
  assert.equal(receiptQuery("range", "2026-11-04", { from: "2026-01-01", to: "2026-06-30" }), "from=2026-01-01&to=2026-06-30");
  assert.equal(receiptsExportUrl("this_year", "2026-11-04", "csv"), "/api/receipts/export?period=year&value=2026&format=csv");
});
