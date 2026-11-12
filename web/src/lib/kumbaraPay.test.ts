import assert from "node:assert/strict";
import { test } from "node:test";
import { gecmisMetni, iyelik, kisiMetni, ozetMetni, temelMetni, toplamiYapEtiketi, yetmeyenNot, type PayDurumu, type PayKisi } from "./kumbaraPay.ts";

const tl = (n: number) => `₺${n.toLocaleString("tr-TR")}`;
const kisi = (o: Partial<PayKisi>): PayKisi => ({ id: 1, user_id: null, name: "Ayşe", app: false, me: false, manual: false, share: 1000,
  paid: 0, left: 1000, over: 0, state: "bekliyor", total: 0, months_full: 0, months_counted: 0, ...o });

test("tamlayan eki", () => {
  assert.equal(iyelik("Ayşe"), "Ayşe'nin");
  assert.equal(iyelik("Mehmet"), "Mehmet'in");
  assert.equal(iyelik("Zeynep"), "Zeynep'in");
  assert.equal(iyelik("Oya"), "Oya'nın");
  assert.equal(iyelik("Umut"), "Umut'un");
  assert.equal(iyelik("Gül"), "Gül'ün");
  assert.equal(iyelik("Ali"), "Ali'nin");
  assert.equal(iyelik("Burak"), "Burak'ın");
});

test("kişi satırı: suçlayıcı olmayan dil", () => {
  assert.deepEqual(kisiMetni(kisi({ state: "eksik", paid: 500, left: 500 }), tl),
    { rozet: "Kısmen", ton: "uyari", cumle: "Bu ay Ayşe'nin payından ₺500 kaldı" });
  assert.equal(kisiMetni(kisi({ me: true, name: "deniz" }), tl).cumle, "Bu ay payından ₺1.000 kaldı");
  assert.equal(kisiMetni(kisi({ state: "tamam", paid: 1500, left: 0, over: 500 }), tl).cumle, "Bu ayki payı tamam, ₺500 fazla");
  assert.equal(kisiMetni(kisi({ state: "tamam", me: true, paid: 1000, left: 0 }), tl).cumle, "Bu ayki payın tamam");
  assert.equal(kisiMetni(kisi({ state: "paysiz", share: null, paid: 300 }), tl).cumle, "Bu ay ₺300 attı");
  for (const s of ["eksik", "bekliyor", "tamam", "paysiz"] as const) {
    assert.doesNotMatch(kisiMetni(kisi({ state: s }), tl).cumle, /geride|borç|faiz/i);
  }
});

test("özet, temel ve geçmiş", () => {
  const s: PayDurumu = { month: "2026-11", target: 3000, basis: "aylik", total: 3000, paid: 2400, remind_day: 25, people: [],
    removed: [], behind: [2], all_done: false, history_months: 3 };
  assert.equal(ozetMetni(s, tl), "Kasım: ₺2.400 / ₺3.000 geldi");
  assert.equal(ozetMetni({ ...s, all_done: true, paid: 3000, behind: [] }, tl), "Kasım payları tamam: ₺3.000 geldi");
  // biri fazla, biri eksik attı: toplam dolu ama kalan var
  assert.equal(ozetMetni({ ...s, paid: 3000 }, tl), "Kasım: ₺3.000 / ₺3.000 geldi; 1 kişinin payında kalan var");
  assert.match(temelMetni(s, tl), /Ayda toplam ₺3.000/);
  assert.match(temelMetni({ ...s, basis: "hedef" }, tl), /Hedef ve son tarihe göre/);
  assert.equal(gecmisMetni({ months_full: 4, months_counted: 5 }), "4/5 ay tam");
  assert.equal(gecmisMetni({ months_full: 0, months_counted: 0 }), null);
});

test("paylar toplamı hedefe yetmiyor: nötr not ve eylem", () => {
  const s: PayDurumu = { month: "2026-11", target: 6000, basis: "aylik", total: 6000, paid: 6000, remind_day: 25, people: [],
    removed: [], behind: [], all_done: true, history_months: 0, needed: 7000 };
  assert.equal(yetmeyenNot(s, tl), "Hedefe yetişmek için ayda ₺7.000 gerekiyor; paylar toplamı ₺6.000.");
  assert.equal(toplamiYapEtiketi(s, tl), "Toplamı ₺7.000 yap");
  assert.doesNotMatch(yetmeyenNot(s, tl)!, /geride|yetersiz|eksik|faiz/i);
  assert.equal(yetmeyenNot({ ...s, needed: null }, tl), null);
  assert.equal(yetmeyenNot({ ...s, needed: undefined }, tl), null); // eski sunucu
});

test("v2: tamamlanacak, devir, dağılım, muaf, hatırlatma mesajı", async () => {
  const { tamamlanacakMetni, devirMetni, dagilimMetni, hatirlatMesaji, aydan } = await import("./kumbaraPay.ts");
  assert.equal(tamamlanacakMetni({ due: [{ month: "2026-10", amount: 500 }], due_total: 500 }, tl, "2026-11"), "Tamamlanacak: ₺500 (Ekim)");
  assert.equal(tamamlanacakMetni({ due: [{ month: "2025-12", amount: 800 }, { month: "2026-10", amount: 500 }], due_total: 1300 }, tl, "2026-11"),
    "Tamamlanacak: ₺1.300 (Aralık 2025 ₺800, Ekim ₺500)");
  assert.equal(tamamlanacakMetni({ due: [], due_total: 0 }, tl, "2026-11"), null);
  assert.equal(devirMetni({ carried_in: 500 }, tl), "Geçen aydan ₺500 fazla geldi");
  assert.equal(dagilimMetni({ person_id: 1, past: [{ month: "2026-10", amount: 500 }], month: "2026-11", month_amount: 200, extra: 0 }, tl, "2026-11"),
    "₺500 Ekim eksiğine, ₺200 Kasım payına sayıldı.");
  assert.equal(dagilimMetni({ person_id: 1, past: [{ month: "2026-09", amount: 400 }, { month: "2026-10", amount: 1000 }], month: "2026-11", month_amount: 0, extra: 0 }, tl, "2026-11"),
    "₺400 Eylül ve ₺1.000 Ekim eksiğine sayıldı.");
  assert.equal(dagilimMetni({ person_id: 1, past: [], month: "2026-11", month_amount: 700, extra: 0 }, tl, "2026-11"), null);
  assert.deepEqual(kisiMetni(kisi({ state: "muaf", share: 0, left: null }), tl), { rozet: "Bu ay yok", ton: "notr", cumle: "Bu ay için pay yok olarak işaretli" });
  const s: PayDurumu = { month: "2026-11", target: 15000, basis: "aylik", total: 15000, paid: 2000, remind_day: 25, people: [], removed: [], behind: [], all_done: false, history_months: 0 };
  assert.equal(hatirlatMesaji("Annemin birikimi", s, kisi({ share: 5000, paid: 2000, paid_for_month: 2000, state: "eksik" }), tl),
    "Annemin birikimi: Kasım payın ₺5.000, şu ana kadar ₺2.000 geldi.");
  assert.equal(hatirlatMesaji("Annemin birikimi", s, kisi({ share: 5000, paid: 0, paid_for_month: 0, due: [{ month: "2026-10", amount: 500 }], due_total: 500 }), tl),
    "Annemin birikimi: Kasım payın ₺5.000, şu ana kadar ₺0 geldi. Ekim'den ₺500 tamamlanacak.");
  assert.equal(hatirlatMesaji("X", s, kisi({ state: "muaf" }), tl), null);
  assert.equal(aydan("2026-09"), "Eylül'den");
  for (const t of [hatirlatMesaji("A", s, kisi({ due: [{ month: "2026-10", amount: 5 }], due_total: 5 }), tl)!, tamamlanacakMetni({ due: [{ month: "2026-10", amount: 5 }], due_total: 5 }, tl, "2026-11")!]) {
    assert.doesNotMatch(t, /borç|gecik|geride/i);
  }
  // altın: birim kumbaranın biriminde
  const gr = (n: number) => `${n.toLocaleString("tr-TR")} gr`;
  assert.equal(kisiMetni(kisi({ state: "eksik", left: 0.66 }), gr).cumle, "Bu ay Ayşe'nin payından 0,66 gr kaldı");
});

test("ay seçenekleri yıl geçişinde doğru", async () => {
  const { aySecenekleri } = await import("./kumbaraPay.ts");
  const a = aySecenekleri("2026-11", 1, 2);
  assert.deepEqual(a.map((x) => x.v), ["2026-10", "2026-11", "2026-12", "2027-01"]);
  assert.equal(a[3]!.t, "Ocak 2027");
});
