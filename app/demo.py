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
    _extras()
    print(f"Demo verisi yüklendi: {db.DB_PATH}")


def _extras() -> None:
    """Yeni özellikler için uydurma veri: geçmiş aylar, hesaplar, kumbara, altın/döviz, zekât, sadaka."""
    from app import donations, zakat

    # geçmiş aylar: grafik ve karşılaştırma dolsun
    for m, (gelir, market, kafe) in {"2026-05": (85000, 5200, 1900), "2026-06": (85000, 4700, 2600),
                                     "2026-07": (91000, 6100, 2100), "2026-08": (91000, 5300, 3100),
                                     "2026-09": (91000, 4900, 2300)}.items():
        db.create_entry({"entry_date": f"{m}-01", "description": "Maaş", "amount": gelir, "kind": "gelir", "category": "Maaş"})
        db.create_entry({"entry_date": f"{m}-05", "description": "Kira", "amount": 22000, "kind": "gider",
                         "category": "Kira & Aidat", "payment": "havale"})
        db.create_entry({"entry_date": f"{m}-14", "description": "Örnek Market", "amount": market, "kind": "gider",
                         "category": "Market & Gıda", "payment": "nakit"})
        db.create_entry({"entry_date": f"{m}-20", "description": "Örnek Kafe", "amount": kafe, "kind": "gider",
                         "category": "Kafe & Restoran", "payment": "nakit"})
    # hesaplar ve yemek kartı
    db.set_account_balance("Örnek Bank", 48250.40, "2026-10-15", "elle", "banka")
    db.set_account_balance("Cüzdan", 1850, "2026-10-15", "elle", "nakit")
    acc = next(a["id"] for a in db.list_accounts() if a["name"] == "Örnek Bank")
    # kumbara
    jid = db.create_jar({"name": "Tatil", "goal": 30000, "deadline": "2027-06-30", "account_id": acc,
                         "monthly": 2000, "auto_day": 2}, initial=14600)
    db.add_jar_move(jid, 2000, "2026-09-02", "Eylül")
    db.add_jar_move(jid, 2000, "2026-10-02", "Ekim")
    db.create_jar({"name": "Acil durum", "goal": 50000, "account_id": acc}, initial=21000)
    # borç / alacak
    db.create_debt({"person": "Ali", "direction": "alacak", "amount": 1500, "debt_date": "2026-10-04", "asset": "TRY"})
    # altın ve döviz (zekât ile ortak liste)
    me = zakat.people()[0]["id"]
    es = zakat.add_person("Eşim")
    zakat.add_asset(me, "CEYREK", 20, False, "kasada", "2025-03-10", 176000)
    zakat.add_asset(me, "ALTIN_24", 50, False, "gram altın", "2025-06-02", 215000)
    zakat.add_asset(me, "USD", 1200, False, None, "2025-08-01", 49200)
    zakat.add_asset(es, "ALTIN_22", 100, True, "düğün takısı", "2024-06-15", None)
    zakat.update_person(me, {"hawl_start": "2025-11-19"})
    zakat.update_person(es, {"hawl_start": "2025-11-19"})
    # sadaka
    donations.add("sadaka", 200, "2026-10-03")
    donations.add("bagis", 500, "2026-09-12", "Kızılay")
    donations.add("fitre", 600, "2026-03-18")


if __name__ == "__main__":
    seed()
