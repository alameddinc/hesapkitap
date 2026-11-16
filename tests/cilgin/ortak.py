"""Çılgın testçilerin ortak araçları: taze defter, saat, anlık görüntü (snapshot), değişmezler, tohum.

Tohum: HK_SEED ortam değişkeni (tamsayı); yoksa günün tarihi (YYYYMMDD) — her gün başka dizi, aynı gün aynı dizi.
Kırılan koşu tohumu assert mesajında ve stdout'ta yazar: `HK_SEED=<tohum> pytest tests/cilgin` ile tekrar edilir.
"""
from __future__ import annotations

import itertools
import os
from datetime import date

import pytest

from tests.simulasyon import belgeler, saat

_n = itertools.count(1)


def seed() -> int:
    v = os.environ.get("HK_SEED", "").strip()
    if v.isdigit():
        return int(v)
    return int(date.today().strftime("%Y%m%d"))


def steps(default: int) -> int:
    """Adım sayısı: HK_ADIM (gece koşusu uzun tur) yoksa varsayılan."""
    v = os.environ.get("HK_ADIM", "").strip()
    return int(v) if v.isdigit() else default


class Defter:
    """Taze, izole bir defter (her hypothesis örneği / her rastgele dizi kendi SQLite dosyasında)."""

    def __init__(self, tmp_dir, start: date, cycle_day: int = 17):
        from fastapi.testclient import TestClient

        from app import db
        from app.api.main import app

        self.mp = pytest.MonkeyPatch()
        self.clock = saat.SimClock(start)
        saat.install(self.mp, self.clock)
        belgeler.install(self.mp)
        k = next(_n)
        self.mp.setattr(db, "DB_PATH", str(tmp_dir / f"cilgin-{os.getpid()}-{k}.db"))
        self.mp.setattr(db, "UPLOAD_DIR", str(tmp_dir / "uploads"))
        self.mp.setattr(db, "BILL_DIR", str(tmp_dir / "bills"))
        db.init_db()
        self.db = db
        self.c = TestClient(app)
        self.today = start
        self.set_day(start)
        db.set_setting("cycle_mode", "manual")
        db.set_cycle_start_day(cycle_day)
        db.remember_owner_name("DENEME KİŞİOĞLU")

    def set_day(self, d: date) -> None:
        self.today = d
        self.clock.set_day(d)
        saat.freeze(self.mp, d)

    def close(self) -> None:
        self.mp.undo()

    def call(self, method: str, url: str, **kw):
        self.clock.tick()
        return getattr(self.c, method)(url, **kw)

    def ids(self) -> dict[str, int]:
        with self.db.connect() as c:
            return {r["name"]: r["id"] for r in c.execute("SELECT id, name FROM accounts")}


def snapshot(d: date) -> dict:
    """Karşılaştırılabilir özet: bakiyeler, free, spendable, kart borcu, later, beklenen gelir."""
    from app import finance

    n = finance.net_worth(d)
    return {"bal": {a["name"]: a["current"] for a in n["accounts"]}, "free": n["free"], "spendable": n["spendable"],
            "cash": n["cash"], "card_debt": n["card_debt"], "later": n["spendable_later"]["total"],
            "unknown": n["unknown_payments"], "payable": n["payable"], "receivable": n["receivable"],
            "parts": {p["key"]: p["amount"] for p in n["spendable_parts"]}}


def invariants(d: date) -> list[str]:
    """Her adımdan sonra: hesap çökmez, değişmezler tutar, denetim kendi içinde çökmez. Bozulanların listesi."""
    from app import finance, finance_audit

    bad = []
    n = finance.net_worth(d)
    fb = n["forecast_balance"]
    income = (n["spendable_after_income"] or {}).get("income") or 0.0
    tot = round(sum(p["amount"] if p["sign"] == "+" else -p["amount"] for p in n["spendable_parts"]), 2)
    if abs(tot - n["spendable"]) > 0.01:
        bad.append(f"kalemler toplamı {tot} ≠ spendable {n['spendable']}")
    if n["spendable"] > n["free"] + 0.005:
        bad.append(f"spendable {n['spendable']} > free {n['free']}")
    if fb["amount"] > fb["no_new_spend"] + 0.005:
        bad.append(f"forecast {fb['amount']} > no_new_spend {fb['no_new_spend']}")
    if fb["no_new_spend"] > round(n["spendable"] + income, 2) + 0.005:
        bad.append(f"no_new_spend {fb['no_new_spend']} > spendable+gelir {n['spendable']}+{income}")
    if n["spendable_later"]["total"] < 0 or any(x["amount"] <= 0 for x in n["spendable_later"]["items"]):
        bad.append(f"later eksi/sıfır kalem {n['spendable_later']}")
    neg = [p for p in n["spendable_parts"] if p["sign"] == "−" and p["amount"] < -0.005]
    if neg:
        bad.append(f"eksi işaretli kalem eksi değer: {neg}")
    if abs(n["free"] - round(n["cash"] - n["unknown_payments"] - n["reserved"], 2)) > 0.01:
        bad.append(f"free {n['free']} ≠ nakit − belirsiz − kumbara")
    a = finance_audit.check(d)
    if "_hata" in (a.get("counts") or {}):
        bad.append(f"denetim çöktü: {a.get('refs', {}).get('_hata')}")
    finance.payables(d)
    finance.expected(finance.current_period(d), d)
    return bad


def stmt_text(card_no: str, cut: date, due: date, nxt: date, nxt_due: date, buys: list, prev: float = 0.0,
              pays: list | None = None) -> bytes:
    return belgeler.register(belgeler.card_statement("DENEME KİŞİOĞLU", card_no, cut, due, nxt, nxt_due, prev,
                                                     pays or [], buys))
