"""Uydurma verilerle demo veritabanı: arayüzü denemek ve ekran görüntüsü için.

Kullanım: EKSTRE_DB=/tmp/demo.db python -m app.demo
Gerçek kişi/kart bilgisi içermez; ekstreler tests/fixtures altındaki sahte metinlerdir.
"""
from datetime import date
from pathlib import Path

from app import db
from app.parsers import kuveytturk, vakifbank

FIX = Path(__file__).resolve().parent.parent / "tests" / "fixtures"


def seed() -> None:
    db.init_db()
    if db.list_statements():
        print("Veritabanı boş değil; demo verisi eklenmedi.")
        return
    for name, mod in (("vakifbank_a.txt", vakifbank), ("vakifbank_b.txt", vakifbank), ("kuveytturk.txt", kuveytturk)):
        text = (FIX / name).read_text(encoding="utf-8")
        db.save_statement(f"demo:{name}".encode(), name.replace(".txt", ".pdf"), mod.parse_text(text))
    db.auto_link()

    maas = db.create_recurring({"description": "Maaş", "amount": 85000, "kind": "gelir", "category": "Maaş",
                                "day_of_month": 1, "start_month": "2026-01"})
    kira = db.create_recurring({"description": "Kira", "amount": 22000, "kind": "gider", "category": "Kira & Aidat",
                                "day_of_month": 5, "start_month": "2026-01"})
    for rid, desc, amt, kind, cat, day in ((maas, "Maaş", 85000, "gelir", "Maaş", 1), (kira, "Kira", 22000, "gider", "Kira & Aidat", 5)):
        db.create_entry({"entry_date": f"2026-10-{day:02d}", "description": desc, "amount": amt, "kind": kind,
                         "category": cat, "recurring_id": rid, "payment": "havale" if kind == "gider" else None})
    db.create_recurring({"description": "Spor salonu", "amount": 1500, "kind": "gider", "category": "Sağlık",
                         "day_of_month": 10, "start_month": "2026-01"})
    db.create_entry({"entry_date": "2026-10-08", "description": "Diş hekimi", "amount": 2400, "kind": "gider",
                     "category": "Sağlık", "payment": "havale"})
    db.create_entry({"entry_date": "2026-10-12", "description": "Pazar alışverişi", "amount": 640, "kind": "gider",
                     "category": "Market & Gıda", "payment": "nakit"})
    db.create_entry({"entry_date": "2026-10-03", "description": "Serbest proje", "amount": 12000, "kind": "gelir",
                     "category": "Ek Gelir"})
    db.create_bill({"provider": "Örnek Elektrik", "amount": 845.30, "due_date": "2026-10-28", "category": "Elektrik",
                    "description": "Eylül 2026"})
    db.create_bill({"provider": "Örnek İnternet", "amount": 549.90, "due_date": "2026-10-24", "category": "İnternet & TV"})
    db.set_budget("Kafe & Restoran", 2500)
    db.set_budget("Market & Gıda", 4000)
    db.set_budget("Yemek Siparişi", 1000)
    db.create_goal({"kind": "category", "name": "AI & Yazılım Abonelik", "baseline": 4794.67, "target": 1500,
                    "start_month": "2026-11", "note": "kullanmadıklarımı kapat"})
    db.set_subscription_state("SCRIBD", "iptal", date(2026, 10, 15).isoformat())
    print(f"Demo verisi yüklendi: {db.DB_PATH}")


if __name__ == "__main__":
    seed()
