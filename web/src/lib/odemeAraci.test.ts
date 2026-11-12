import assert from "node:assert/strict";
import { test } from "node:test";
import { ornekMetni } from "./odemeAraci.ts";

test("örnek satırı: hazır sürümde tek kişide görülen ad gizli, sayısı yazılır", () => {
  assert.equal(ornekMetni([], 5), "Örnekler gizli: tek kişide görüldü");
  assert.equal(ornekMetni(["ORNEK KITAP"], 3), "Örnek: ORNEK KITAP · 3 işyeri tek kişide görüldü, gizli");
  assert.equal(ornekMetni(["ORNEK A", "ORNEK B"]), "Örnek: ORNEK A · ORNEK B");
  assert.equal(ornekMetni([], 0), null);
});
