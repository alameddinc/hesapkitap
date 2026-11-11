import assert from "node:assert/strict";
import { test } from "node:test";
import { kalinParcalar } from "./kalin.ts";

test("** işaretli parça kalın; HTML metin olarak kalır", () => {
  assert.deepEqual(kalinParcalar("Uygulama → **Kartlar** → kartını seç."),
    [{ text: "Uygulama → ", bold: false }, { text: "Kartlar", bold: true }, { text: " → kartını seç.", bold: false }]);
  assert.deepEqual(kalinParcalar("<b>x</b>"), [{ text: "<b>x</b>", bold: false }]);
  assert.deepEqual(kalinParcalar("**PDF**"), [{ text: "PDF", bold: true }]);
});
