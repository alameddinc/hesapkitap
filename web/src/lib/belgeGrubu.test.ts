// Özet "N belge bekleniyor" grubu (mobil src/lib/belgeGrubu.ts ile birebir aynı olmalı). Bankalar uydurma.
import assert from "node:assert/strict";
import { test } from "node:test";

import { beklenenBelge, grupBaslik, grupla, grupOzet, kisaAd, type BelgeMaddesi } from "./belgeGrubu.ts";

const m = (id: string, kind: string, o: Partial<BelgeMaddesi> = {}): BelgeMaddesi => ({ id, kind, title: `${id} · başlık`, ...o });

test("tek belge grup olmaz", () => {
  const items = [m("a", "odeme"), m("b", "ekstre")];
  assert.deepEqual(grupla(items).map((u) => u.tip), ["tek", "tek"]);
});

test("iki ve daha çok belge ilk belgenin yerinde tek grupta; diğerleri sırasıyla", () => {
  const items = [m("o", "odeme"), m("e1", "ekstre"), m("s", "esles"), m("d", "dokum"), m("e2", "ekstre")];
  const u = grupla(items);
  assert.deepEqual(u.map((x) => x.tip), ["tek", "grup", "tek"]);
  const g = u[1]!;
  assert.ok(g.tip === "grup" && g.items.map((x) => x.id).join() === "e1,d,e2");
  assert.equal(grupBaslik(3), "3 belge bekleniyor");
  assert.ok(beklenenBelge({ kind: "dokum" }) && !beklenenBelge({ kind: "belge_onay" }));
});

test("kısa ad: sunucunun short'u, yoksa başlıktan", () => {
  assert.equal(kisaAd(m("x", "ekstre", { short: "Örnek Bank ••1234 (ekstre 14 Kas)" })), "Örnek Bank ••1234 (ekstre 14 Kas)");
  assert.equal(kisaAd({ id: "y", kind: "ekstre", title: "Mavi Bank ••5678 · Kasım ekstren hazır", bank: "Mavi Bank", last4: "5678" }),
    "Mavi Bank ••5678 (ekstre)");
  assert.equal(kisaAd({ id: "z", kind: "dokum", title: "Örnek Bank Vadesiz · Ekim dökümü" }), "Örnek Bank Vadesiz (döküm)");
  assert.equal(grupOzet([m("a", "dokum", { short: "A (döküm)" }), m("b", "ekstre", { short: "B ••1 (ekstre 3 Kas)" })]),
    "A (döküm), B ••1 (ekstre 3 Kas)");
});
