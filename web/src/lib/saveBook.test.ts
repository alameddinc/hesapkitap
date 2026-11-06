// Kaydı hangi deftere yazalım: seçim kimde görünür, başlık ne zaman gider, harcayan ve Aile okuma hakkı metni.
import assert from "node:assert/strict";
import { test } from "node:test";

import {
  bookHeaders, bookHint, bookOverride, canChooseBook, familyReadsText, openBookOf, savedElsewhere, spenderField,
  spenderKeys, targetIsShared, targetViewer,
} from "./saveBook.ts";

const uye = { member: true, book: "ortak" as const, role: "editor", me: 7, people: [{ id: 3 }, { id: 7 }, { id: 9 }] };

test("seçim yalnız yetenek varken ve üyede", () => {
  assert.equal(canChooseBook(["save_book_choice"], uye), true);
  assert.equal(canChooseBook([], uye), false);
  assert.equal(canChooseBook(undefined, uye), false);
  assert.equal(canChooseBook(["save_book_choice"], { member: false, people: [{ id: 1 }, { id: 2 }] }), false);
  assert.equal(canChooseBook(["save_book_choice"], { ...uye, can_switch: false }), false);
  assert.equal(canChooseBook(["save_book_choice"], null), false);
});

test("açık defter ve başlık", () => {
  assert.equal(openBookOf(uye), "ortak");
  assert.equal(openBookOf({ ...uye, book: "kendi" }), "kendi");
  assert.equal(openBookOf({ member: false }), null);
  assert.equal(bookOverride("ortak", "ortak"), null);
  assert.equal(bookOverride("kendi", "ortak"), "kendi");
  assert.equal(bookOverride("ortak", "kendi"), "ortak");
  assert.equal(bookOverride("kendi", null), null);
  assert.deepEqual(bookHeaders(null), {});
  assert.deepEqual(bookHeaders("kendi"), { "X-HK-Book": "kendi" });
});

test("diğer deftere kaydedilince bildirim", () => {
  assert.equal(savedElsewhere("kendi", "ortak"), "Benim defterine kaydedildi");
  assert.equal(savedElsewhere("ortak", "kendi"), "Ortak deftere kaydedildi");
  assert.equal(savedElsewhere("ortak", "ortak"), null);
  assert.equal(bookHint("kendi"), "Yalnız sen görürsün.");
  assert.equal(bookHint("ortak"), "Ailedeki herkes görür (yetkisine göre).");
});

test("hedef ortak defter mi, sadece görür", () => {
  assert.equal(targetIsShared("ortak", uye), true);
  assert.equal(targetIsShared("kendi", uye), false);
  assert.equal(targetIsShared(null, { member: false, people: [{ id: 1 }, { id: 2 }] }), true); // sahip
  assert.equal(targetIsShared(null, { member: false, people: [] }), false);
  assert.equal(targetViewer("ortak", { ...uye, role: "viewer" }), true);
  assert.equal(targetViewer("kendi", { ...uye, role: "viewer" }), false);
  assert.equal(targetViewer("ortak", uye), false);
});

test("kim harcadı: sıra ve gövde", () => {
  assert.deepEqual(spenderKeys(uye.people, 7), ["u7", "u3", "u9", "ortak"]);
  assert.deepEqual(spenderField(null, 7), {});
  assert.deepEqual(spenderField("u7", 7), {});
  assert.deepEqual(spenderField("u3", 7), { spender: "u3" });
  assert.deepEqual(spenderField("ortak", 7), { spender: "ortak" });
});

test("Aile okuma hakkı yalnız kişinin kalanından azsa", () => {
  assert.equal(familyReadsText({ left: 20, family_left: 12, family_limit: 120 }), "Aile bugün 12/120 kaldı");
  assert.equal(familyReadsText({ left: 20, family_left: 40, family_limit: 120 }), null);
  assert.equal(familyReadsText({ left: 20, family_left: 20, family_limit: 120 }), null);
  assert.equal(familyReadsText({ left: 20 }), null);
  assert.equal(familyReadsText({ left: 5, family_left: -1, family_limit: 120 }), "Aile bugün 0/120 kaldı");
  assert.equal(familyReadsText(null), null);
});
