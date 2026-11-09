// Ölçüm yardımcıları: demodan gelen kayıt bağlantısı, yetenek yoksa hiç gönderilmez.
import assert from "node:assert/strict";
import { test } from "node:test";

import { demodanGeldi, olcumGonder } from "./olcum.ts";

test("demodan gelen bağlantı: hash ya da adres sorgusu", () => {
  assert.equal(demodanGeldi({ hash: "#/kayit?k=demo", search: "" }), true);
  assert.equal(demodanGeldi({ hash: "#/kayit", search: "?k=demo" }), true);
  assert.equal(demodanGeldi({ hash: "#/kayit?kod=ABCD-EFGH&k=demo", search: "" }), true);
  assert.equal(demodanGeldi({ hash: "#/kayit?k=reklam", search: "" }), false);
  assert.equal(demodanGeldi({ hash: "", search: "" }), false);
});

test("eski sunucu (yetenek yok): istek gönderilmez", () => {
  const calls: unknown[] = [];
  const g = globalThis as { fetch: unknown };
  const real = g.fetch;
  g.fetch = (...a: unknown[]) => { calls.push(a); return Promise.resolve(new Response("{}")); };
  try {
    olcumGonder("paywall_view", undefined);
    olcumGonder("paywall_view", ["push"]);
    assert.equal(calls.length, 0);
    olcumGonder("paywall_view", ["funnel_events"]);
    assert.equal(calls.length, 1);
    const [url, init] = calls[0] as [string, RequestInit];
    assert.equal(url, "/api/events");
    assert.deepEqual(JSON.parse(String(init.body)), { name: "paywall_view" }); // yalnız olay adı: kimlik yok
  } finally {
    g.fetch = real;
  }
});
