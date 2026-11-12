import assert from "node:assert/strict";
import { test } from "node:test";
import { netDurumu } from "./netDurum.ts";

test("yeni sunucu: kesilecek kart ekstresi dahil rakam; eski sunucu ya da yetenek yok: eski net", () => {
  assert.deepEqual(netDurumu({ net: 100000, net_display: 92649.6, card_estimated: 7350.4 }, true), { value: 92649.6, kesilecek: true, yeni: true });
  assert.deepEqual(netDurumu({ net: 100000, net_display: 100000, card_estimated: 0 }, true), { value: 100000, kesilecek: false, yeni: true });
  assert.deepEqual(netDurumu({ net: 100000, card_estimated: 7350.4 }, true), { value: 100000, kesilecek: false, yeni: false });
  assert.deepEqual(netDurumu({ net: 100000, net_display: 92649.6, card_estimated: 7350.4 }, false), { value: 100000, kesilecek: false, yeni: false });
});
