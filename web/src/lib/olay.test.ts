// Sorun kaydı (istemci): yetenek yoksa hiçbir şey gitmez; tekrar engeli; kendi ucunun ve çevrimdışılığın hatası kaydedilmez.
import assert from "node:assert/strict";
import { test } from "node:test";

import { agKaydet } from "./apiError.ts";
import { gonder, olayDestek, olayKaydet, olayKuyrugu, olaySifirla, olayDinle } from "./olay.ts";

const g = globalThis as { fetch: unknown; window?: unknown; location?: unknown };

test("eski sunucu (yetenek yok): kuyruk atılır, istek gitmez", async () => {
  olaySifirla();
  const calls: unknown[] = [];
  const real = g.fetch;
  g.fetch = (...a: unknown[]) => { calls.push(a); return Promise.resolve(new Response(JSON.stringify({ capabilities: ["push"] }))); };
  try {
    olayKaydet("js_hatasi", { detail: "TypeError: x", screen: "#/ozet" });
    await gonder();
    assert.equal(calls.length, 1); // yalnız /api/auth/status
    assert.equal((calls[0] as [string])[0], "/api/auth/status");
    assert.deepEqual(olayKuyrugu(), []);
    olayKaydet("js_hatasi", { detail: "yine" });
    assert.deepEqual(olayKuyrugu(), []);
  } finally { g.fetch = real; olaySifirla(); }
});

test("yetenek varsa toplu gönderim; aynı olay 60 sn içinde bir kez; platform web", async () => {
  olaySifirla();
  olayDestek(true);
  const calls: [string, RequestInit][] = [];
  const real = g.fetch;
  g.fetch = (u: string, i: RequestInit) => { calls.push([u, i]); return Promise.resolve(new Response("{}")); };
  try {
    olayKaydet("ekran_coktu", { detail: "Error: a", screen: "#/islemler" });
    olayKaydet("ekran_coktu", { detail: "Error: a", screen: "#/islemler" });
    olayKaydet("ag_zaman_asimi", { detail: "GET /api/summary", screen: "#/ozet" });
    assert.equal(olayKuyrugu().length, 2);
    await gonder();
    assert.equal(calls.length, 1);
    assert.equal(calls[0][0], "/api/incidents/client");
    const body = JSON.parse(String(calls[0][1].body));
    assert.deepEqual(body.events.map((e: { code: string; platform: string }) => [e.code, e.platform]),
      [["ekran_coktu", "web"], ["ag_zaman_asimi", "web"]]);
  } finally { g.fetch = real; olaySifirla(); }
});

test("ağ olayı: kendi ucu kaydedilmez, yol kimliksiz", () => {
  olaySifirla();
  olayDestek(true);
  const w = { addEventListener: () => undefined };
  g.window = w;
  g.location = { hash: "#/ozet", href: "http://x/" };
  try {
    olayDinle();
    agKaydet("sunucuya_ulasilamadi", "POST", "/api/incidents/client");
    assert.equal(olayKuyrugu().length, 0);
    agKaydet("sunucuya_ulasilamadi", "PATCH", "/api/entries/123?x=1");
    const q = olayKuyrugu();
    assert.equal(q.length, 1);
    assert.equal(q[0].code, "sunucuya_ulasilamadi");
    assert.equal(q[0].detail, "PATCH /api/entries/{id}");
  } finally { delete g.window; delete g.location; olaySifirla(); }
});
