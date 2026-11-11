// Borç planı ↔ kumbara düğme kuralı (mobil src/lib/borcKumbara.test.ts ile aynı).
import assert from "node:assert/strict";
import { test } from "node:test";

import { jarBox, jarButton, offerSummary, payAmountEditable, type DebtJarFlags, type PlanJarPay } from "./borcKumbara.ts";

const F = (o: Partial<DebtJarFlags> = {}): DebtJarFlags => ({ available: true, premium: true, can_open: true, can_move: true,
  can_pay: true, lock: null, saved_this_month: 0, ...o });
const PAY: PlanJarPay = { label: "Kumbaradan öde", jar_id: 3, debt_id: 7, amount: 12000, from_jar: 12000, close_early: false,
  partial: false, leftover: 0, text: "Borcun tamamı kumbarada birikti.", confirm: "Deniz: ₺12.000 kumbaradan ödensin mi?" };
const ACT = { label: "Kumbaraya ayır", jar_id: 3, amount: 3000, note: "Borç planı" };

test("ayır: birikim satırında, ayırabilen üyeye", () => {
  assert.equal(jarButton({ done: false, jar_action: ACT }, F(), true), "ayir");
  assert.equal(jarButton({ done: false, jar_action: ACT }, F({ can_move: false }), true), null); // sadece görür
  assert.equal(jarButton({ done: true, jar_action: ACT }, F(), true), null);
});

test("öde: ödeme satırında, yalnız ödeyebilen (sahip / tam yetkili)", () => {
  assert.equal(jarButton({ done: false, jar_pay: PAY }, F(), true), "ode");
  assert.equal(jarButton({ done: false, jar_pay: PAY }, F({ can_pay: false }), true), null);
});

test("eski sunucu: yetenek yoksa hiçbir düğme ve kutu yok", () => {
  assert.equal(jarButton({ done: false, jar_pay: PAY, jar_action: ACT }, F(), false), null);
  assert.equal(jarButton({ done: false, jar_action: ACT }, undefined, true), null);
  assert.equal(jarBox({ state: "locked", label: "Kumbarayla takip et", badge: "Premium", text: "x" }, false), null);
  assert.equal(jarBox(undefined, true), null);
});

test("kredide tutar yazılır; özet satırı", () => {
  assert.equal(payAmountEditable({ ...PAY, close_early: true }), true);
  assert.equal(payAmountEditable(PAY), false);
  const tl = (n: number) => `₺${new Intl.NumberFormat("tr-TR").format(n)}`;
  assert.equal(offerSummary({ state: "offer", label: "Kumbara aç", text: "", name: "Deniz borcu", goal: 12000, deadline: "2027-02-28",
    deadline_label: "Şubat 2027", monthly: 3000 }, tl), "Hedef ₺12.000 · Şubat 2027 · ayda ₺3.000");
});
