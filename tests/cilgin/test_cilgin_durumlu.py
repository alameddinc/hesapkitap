"""ÇILGIN TESTÇİLER (B): durumlu rastgele işlem dizileri + kötü niyetli girdiler. Her adımdan sonra değişmezler.

İki koşucu, aynı kural kümesi (Kurallar):
- test_cilgin_hypothesis: hypothesis RuleBasedStateMachine (küçültme/shrink: kırılan diziyi en kısa hâline indirir).
  Varsayılan koşuda derandomize (CI'da her seferinde aynı); gece işi `--hypothesis-seed=$HK_SEED` ile her gece yeni.
- test_cilgin_tohumlu: hypothesis'siz, HK_SEED tohumlu düz rastgele yürüyüş (kırılınca tohum ve adım listesi yazar).

Özellikler (her kural kendi özelliğini doğrular, sonra ortak değişmezler — ortak.invariants):
  geri al = hiç yapmamak  (kayıt sil, Ödendi geri al, döküm yüklemesini geri al, ay dönümü A→B→A)
  aynı belge 3 kez = 1 kez (ekstre, döküm CSV'si, aynı sorgu numaralı dekont)
  ters sıralı döküm = düz sıralı döküm
  kötü girdi (eksi / çok büyük tutar, aynı hesaba aktarım, olmayan kayıt) → 4xx, durum değişmez, 500 yok
  geçmiş/gelecek tarih, 29 Şubat, ay dönümü değişimi → çökme yok, değişmezler tutar
Bulunan hatalar test_cilgin_bulgular.py'de minimal tekrarla xfail(strict).
"""
from __future__ import annotations

import json
import random
from datetime import date, timedelta

import pytest

from tests.cilgin import ortak
from tests.simulasyon import belgeler

START = date(2028, 2, 20)   # 29 Şubat 2028 (artık yıl) yürüyüşün içinde
CARD = "5400***0991"
EDGE_AMOUNTS = [0.01, 0.5, 1.0, 99.99, 1234.56, 25000.0, 999999.99]


class Kurallar:
    """Kural gövdeleri (hypothesis ve tohumlu koşucu ortak). self.d: Defter; self.log: adımlar."""

    def kur(self, tmp_dir):
        self.d = ortak.Defter(tmp_dir, START, cycle_day=self.cycle)
        self.log: list[str] = []
        for name, kind, bal in (("Örnek Banka A", "banka", 40000.0), ("Örnek Banka B", "banka", 15000.0),
                                ("Cüzdan", "nakit", 2000.0)):
            r = self.d.call("post", "/api/accounts", json={"name": name, "kind": kind, "balance": bal,
                                                           "as_of": (START - timedelta(days=1)).isoformat()})
            assert r.status_code == 200, r.text
        self.ids = self.d.ids()
        self.entries: list[int] = []
        self.stmts: list[tuple[int, float]] = []
        self.refs: list[str] = []
        self.debts: list[int] = []
        self.jar = None
        self.cut = None

    def kapat(self):
        self.d.close()

    # ---------------------------------------------------------------- yardımcılar

    def snap(self):
        return ortak.snapshot(self.d.today)

    def check(self, what: str):
        self.log.append(what)
        try:
            bad = ortak.invariants(self.d.today)
        except Exception as e:  # noqa: BLE001
            raise AssertionError(f"hesap çöktü ({what}): {e!r}\nadımlar: {self.log[-12:]}") from e
        assert not bad, f"değişmez bozuldu ({what}): {bad}\nadımlar: {self.log[-12:]}"

    def ok(self, r, what: str, allow=(200,)):
        assert r.status_code < 500, f"{what}: 500 {r.text[:300]}\nadımlar: {self.log[-12:]}"
        assert r.status_code in allow, f"{what}: {r.status_code} {r.text[:300]}"
        return r.json() if r.headers.get("content-type", "").startswith("application/json") else None

    def acc(self, k: int) -> int:
        names = ["Örnek Banka A", "Örnek Banka B", "Cüzdan"]
        return self.ids[names[k % len(names)]]

    # ---------------------------------------------------------------- kurallar

    def gun_ilerle(self, n: int):
        self.d.set_day(self.d.today + timedelta(days=n))
        self.check(f"gün +{n} → {self.d.today}")

    def kayit(self, kind: str, amount: float, acc_k: int | None, offset: int, payment: str):
        on = self.d.today + timedelta(days=offset)
        body = {"entry_date": on.isoformat(), "description": f"Örnek {kind} {amount}", "amount": amount,
                "kind": kind, "category": "Market" if kind == "gider" else "Diğer Gelir", "payment": payment}
        if acc_k is not None and payment != "kredi_karti":
            body["account_id"] = self.acc(acc_k)
        if payment == "kredi_karti":
            body["card_last4"] = CARD[-4:]
        r = self.d.call("post", "/api/entries", json=body)
        out = self.ok(r, "kayıt", allow=(200, 422))
        if r.status_code == 200:
            self.entries.append(out["id"])
        self.check(f"{kind} {amount} {on} {payment} acc={acc_k}")

    def kayit_geri_al(self, amount: float, acc_k: int, offset: int):
        before = self.snap()
        on = self.d.today + timedelta(days=offset)
        r = self.d.call("post", "/api/entries", json={"entry_date": on.isoformat(), "description": "Geri alınacak",
                                                      "amount": amount, "kind": "gider", "category": "Market",
                                                      "payment": "havale", "account_id": self.acc(acc_k)})
        eid = self.ok(r, "kayıt")["id"]
        self.ok(self.d.call("delete", f"/api/entries/{eid}"), "kayıt sil")
        after = self.snap()
        assert after == before, f"kayıt ekle+sil ≠ hiç: {diff(before, after)}\nadımlar: {self.log[-12:]}"
        self.check(f"kayıt ekle+sil {amount} {on}")

    def kotu_girdi(self, k: int):
        before = self.snap()
        a, b = self.acc(0), self.acc(1)
        bad = [("post", "/api/entries", {"json": {"entry_date": self.d.today.isoformat(), "description": "x",
                                                  "amount": -5, "kind": "gider"}}),
               ("post", "/api/own-transfers", {"json": {"date": self.d.today.isoformat(), "amount": 10,
                                                        "from_account_id": a, "to_account_id": a}}),
               ("post", "/api/own-transfers", {"json": {"date": self.d.today.isoformat(), "amount": 1e10,
                                                        "from_account_id": a, "to_account_id": b}}),
               ("post", "/api/statements/999999/pay", {"json": {"amount": 10, "paid_at": self.d.today.isoformat()}}),
               ("delete", "/api/entries/999999", {"idempotent": True}),
               ("post", "/api/jars/999999/moves", {"json": {"amount": 10}}),
               ("post", "/api/debts/999999/pay", {"json": {"paid_at": self.d.today.isoformat()}}),
               ("patch", f"/api/accounts/{a}", {"json": {"balance": "abc"}}),
               ("post", "/api/entries", {"json": {"entry_date": "2028-02-30", "description": "x", "amount": 5,
                                                  "kind": "gider"}})]
        m, url, kw = bad[k % len(bad)]
        idem = kw.pop("idempotent", False)  # olmayan kaydı silmek: 200 (idempotent) ya da 404 kabul
        r = self.d.call(m, url, **kw)
        assert r.status_code < 500, f"kötü girdi {url} → 500: {r.text[:300]}"
        assert 400 <= r.status_code < 500 or (idem and r.status_code == 200), f"kötü girdi kabul edildi: {m} {url} {kw} → {r.status_code} {r.text[:200]}"
        assert self.snap() == before, f"reddedilen istek durumu değiştirdi: {url}"
        self.check(f"kötü girdi {url}")

    def aktarim(self, amount: float, a_k: int, b_k: int, ref: bool, times: int):
        a, b = self.acc(a_k), self.acc(b_k)
        if a == b:
            return
        body = {"date": self.d.today.isoformat(), "amount": amount, "from_account_id": a, "to_account_id": b}
        if ref:
            body["ref"] = str(9_000_000_000 + len(self.refs) * 7919 + int(amount * 100) % 1000)
            body["dekont_side"] = "giden"
            self.refs.append(body["ref"])
        first = None
        for i in range(times if ref else 1):
            r = self.d.call("post", "/api/own-transfers", json=body)
            self.ok(r, "aktarım", allow=(200,) if i == 0 else (200, 409))
            if i == 0:
                first = self.snap()
            else:
                assert self.snap() == first, f"aynı dekont {i + 1}. kez: durum değişti {diff(first, self.snap())}"
        self.check(f"aktarım {amount} {a}->{b} ref={ref} x{times}")

    def bakiye(self, acc_k: int, value: float):
        self.ok(self.d.call("patch", f"/api/accounts/{self.acc(acc_k)}", json={"balance": value}), "bakiye")
        self.check(f"bakiye {acc_k}={value}")

    def ekstre(self, times: int, total: float, back: int):
        cut = self.d.today - timedelta(days=back)
        due = cut + timedelta(days=10)
        nxt = cut + timedelta(days=30)
        data = ortak.stmt_text(CARD, cut, due, nxt, nxt + timedelta(days=10), [(cut - timedelta(days=3), "ORNEK MARKET", total)])
        snaps = []
        for _ in range(times):
            r = self.d.call("post", "/api/statements/upload",
                            files=[("files", (f"e-{cut}.pdf", data, "application/pdf"))])
            self.ok(r, "ekstre")
            snaps.append(self.snap())
        assert all(s == snaps[0] for s in snaps), f"aynı ekstre {times} kez: {diff(snaps[0], snaps[-1])}"
        sid = next((x["id"] for x in self.d.db.list_statements() if x["period_end"] == cut.isoformat()), None)
        if sid and all(s != sid for s, _ in self.stmts):
            self.stmts.append((sid, total))
        self.check(f"ekstre {cut} {total} x{times}")

    def ode(self, k: int, acc_k: int | None, undo: bool):
        if not self.stmts:
            return
        sid, total = self.stmts[k % len(self.stmts)]
        before = self.snap()
        unpaid_before = not next((x for x in self.d.db.list_statements() if x["id"] == sid), {}).get("paid_at")
        body = {"amount": total, "paid_at": self.d.today.isoformat()}
        if acc_k is not None:
            body["account_id"] = self.acc(acc_k) if acc_k >= 0 else None
        self.ok(self.d.call("post", f"/api/statements/{sid}/pay", json=body), "ödendi", allow=(200, 422))
        if undo:
            self.ok(self.d.call("delete", f"/api/statements/{sid}/pay"), "ödendi geri al")
            after = self.snap()
            # Ödendi'yi kaldırmak ekstreyi ÖDENMEMİŞ yapar (tek ödeme durumu); önceden ödenmiş ekstrede önceki hâle
            # dönmez (tasarım). Özellik yalnız ödenmemiş ekstrede aranır.
            assert unpaid_before is False or after == before, f"Ödendi + geri al ≠ hiç: {diff(before, after)}\nadımlar: {self.log[-12:]}"
        self.check(f"ödendi {sid} acc={acc_k} geri_al={undo}")

    def dokum(self, n: int, reverse: bool, times: int, undo: bool, seed: int):
        rng = random.Random(seed)
        aid = self.ids["Örnek Banka A"]
        cur = next(a for a in self.d.db.list_accounts() if a["id"] == aid)
        bal = cur["current"] or 0.0
        rows = []
        day = self.d.today - timedelta(days=n + 1)
        for i in range(n):
            day += timedelta(days=1)
            amt = round(rng.choice([-1, -1, 1]) * rng.uniform(10, 3000), 2)
            bal = round(bal + amt, 2)
            rows.append((day, rng.choice(["ORNEK MARKET POS", "FAST ORNEK KISI", "ORNEK GELEN HAVALE"]), amt, bal))
        if not rows:
            return
        before = self.snap()
        pre = {i["id"] for i in self.ok(self.d.call("get", "/api/imports"), "yüklemeler")}
        data = belgeler.bank_csv("DENEME KISIOGLU", rows, reverse=reverse)
        pv = self.ok(self.d.call("post", "/api/bank-import/preview", files={"file": ("d.csv", data, "text/csv")}),
                     "önizleme")
        form = {"mapping": json.dumps(pv["mapping"]), "header_row": str(pv["header_row"]), "account_id": str(aid)}
        snaps = []
        for _ in range(times):
            self.ok(self.d.call("post", "/api/bank-import", files={"file": ("d.csv", data, "text/csv")}, data=form),
                    "döküm")
            snaps.append(self.snap())
        assert all(s == snaps[0] for s in snaps), f"aynı döküm {times} kez: {diff(snaps[0], snaps[-1])}"
        if undo:
            imports = self.ok(self.d.call("get", "/api/imports"), "yüklemeler")
            new = [i["id"] for i in imports if i["id"] not in pre]  # bu adımın bütün yüklemeleri, yeniden eskiye
            codes = [self.d.call("post", f"/api/imports/{bid}/undo").status_code for bid in new]
            assert all(c < 500 for c in codes), codes
            if new and all(c == 200 for c in codes):
                after = self.snap()
                assert after == before, f"döküm + geri al ≠ hiç: {diff(before, after)}\nadımlar: {self.log[-12:]}"
        self.check(f"döküm {n} satır ters={reverse} x{times} geri_al={undo}")

    def ay_donumu(self, day: int):
        before = self.snap()
        old = self.d.db.cycle_start_day()
        self.ok(self.d.call("put", "/api/settings/cycle", json={"mode": "manual", "day": day}), "ay dönümü")
        self.check(f"ay dönümü {old}→{day}")
        self.ok(self.d.call("put", "/api/settings/cycle", json={"mode": "manual", "day": old}), "ay dönümü geri")
        after = self.snap()
        assert after == before, f"ay dönümü {old}→{day}→{old} ≠ hiç: {diff(before, after)}"
        self.check(f"ay dönümü geri {old}")

    def borc(self, amount: float, direction: str, acc_k: int | None, pay_frac: float, overpay: bool):
        body = {"person": "Örnek Kişi", "direction": direction, "amount": amount,
                "debt_date": self.d.today.isoformat(), "creditor_type": "kisi"}
        r = self.d.call("post", "/api/debts", json=body)
        did = self.ok(r, "borç")["id"]
        self.debts.append(did)
        pay = {"amount": round(amount * (1.5 if overpay else pay_frac), 2) or 0.01, "paid_at": self.d.today.isoformat()}
        if acc_k is not None:
            pay["account_id"] = self.acc(acc_k)
        r = self.d.call("post", f"/api/debts/{did}/pay", json=pay)
        self.ok(r, "borç öde", allow=(422,) if overpay else (200,))
        self.check(f"borç {direction} {amount} öde {pay['amount']} fazla={overpay}")

    def kumbara(self, amount: float, cek: bool):
        if self.jar is None:
            r = self.d.call("post", "/api/jars", json={"name": "Örnek hedef", "account_id": self.ids["Örnek Banka A"]})
            self.jar = self.ok(r, "kumbara")["id"]
        r = self.d.call("post", f"/api/jars/{self.jar}/moves", json={"amount": amount,
                                                                     "direction": "cek" if cek else "yatir"})
        self.ok(r, "kumbara hareket", allow=(200, 422))
        self.check(f"kumbara {'çek' if cek else 'yatır'} {amount}")

    def fatura(self, amount: float, offset: int, pay: bool, undo: bool):
        r = self.d.call("post", "/api/bills", json={"provider": "Örnek Elektrik", "amount": amount,
                                                    "due_date": (self.d.today + timedelta(days=offset)).isoformat()})
        bid = self.ok(r, "fatura")["id"]
        if pay:
            before = None
            if undo:
                before = self.snap()
            self.ok(self.d.call("post", f"/api/bills/{bid}/pay", json={"amount": amount,
                                                                      "paid_at": self.d.today.isoformat(),
                                                                      "payment": "havale"}), "fatura öde")
            if undo:
                self.ok(self.d.call("delete", f"/api/bills/{bid}/pay"), "fatura geri al")
                assert self.snap() == before, f"fatura öde + geri al ≠ hiç: {diff(before, self.snap())}"
        self.check(f"fatura {amount} vade+{offset} öde={pay} geri={undo}")


def diff(a: dict, b: dict) -> dict:
    return {k: (a.get(k), b.get(k)) for k in set(a) | set(b) if a.get(k) != b.get(k)}


# ---------------------------------------------------------------- tohumlu koşucu (hypothesis gerekmez)

def _walk(k: Kurallar, rng: random.Random, n: int) -> None:
    amt = lambda: rng.choice(EDGE_AMOUNTS + [round(rng.uniform(1, 20000), 2)])  # noqa: E731
    acts = [
        lambda: k.gun_ilerle(rng.choice([1, 1, 2, 3, 7, 13, 29, 31])),
        lambda: k.kayit(rng.choice(["gider", "gelir"]), amt(), rng.choice([0, 1, 2, None]),
                        rng.choice([0, 0, -1, -3, -40, -400, 1, 5, 30]), rng.choice(["havale", "nakit", "kredi_karti"])),
        lambda: k.kayit_geri_al(amt(), rng.randint(0, 2), rng.choice([0, -1, -10, 3])),
        lambda: k.kotu_girdi(rng.randint(0, 50)),
        lambda: k.aktarim(amt(), rng.randint(0, 2), rng.randint(0, 2), rng.random() < 0.5, rng.randint(1, 3)),
        lambda: k.bakiye(rng.randint(0, 2), rng.choice([0.0, -500.0, 12345.67, round(rng.uniform(0, 90000), 2)])),
        lambda: k.ekstre(rng.randint(1, 3), round(rng.uniform(100, 40000), 2), rng.randint(0, 25)),
        lambda: k.ode(rng.randint(0, 9), rng.choice([None, -1, 0, 1]), rng.random() < 0.5),
        lambda: k.dokum(rng.randint(1, 8), rng.random() < 0.5, rng.randint(1, 3), rng.random() < 0.4,
                        rng.randint(0, 10 ** 6)),
        lambda: k.ay_donumu(rng.randint(1, 28)),
        lambda: k.borc(round(rng.uniform(100, 20000), 2), rng.choice(["borc", "alacak"]), rng.choice([None, 0, 1]),
                       rng.choice([0.25, 0.5, 1.0]), rng.random() < 0.2),
        lambda: k.kumbara(amt(), rng.random() < 0.3),
        lambda: k.fatura(amt(), rng.choice([-20, -1, 0, 3, 40]), rng.random() < 0.6, rng.random() < 0.5),
    ]
    for _ in range(n):
        rng.choice(acts)()


@pytest.mark.parametrize("cycle", [1, 17, 28])
def test_cilgin_tohumlu(cycle, tmp_path, client):
    seed = ortak.seed() + cycle
    k = Kurallar()
    k.cycle = cycle
    k.kur(tmp_path)
    try:
        _walk(k, random.Random(seed), ortak.steps(60))
    except AssertionError as e:
        raise AssertionError(f"HK_SEED={seed - cycle} (ay dönümü {cycle}) ile tekrar edilir:\n{e}") from e
    finally:
        k.kapat()


@pytest.mark.uzun
@pytest.mark.parametrize("cycle", [1, 15, 17, 28])
def test_cilgin_tohumlu_uzun(cycle, tmp_path, client):
    seed = ortak.seed() * 7 + cycle
    k = Kurallar()
    k.cycle = cycle
    k.kur(tmp_path)
    try:
        _walk(k, random.Random(seed), ortak.steps(400))
    except AssertionError as e:
        raise AssertionError(f"tohum {seed} (HK_SEED={ortak.seed()}, ay dönümü {cycle}):\n{e}") from e
    finally:
        k.kapat()
