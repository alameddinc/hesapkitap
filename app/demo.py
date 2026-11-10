"""Uydurma verilerle demo veritabanı: arayüzü denemek ve ekran görüntüsü için.

Kullanım: EKSTRE_DB=/tmp/demo.db python -m app.demo
Gerçek kişi/kart bilgisi içermez; ekstreler tests/fixtures altındaki sahte metinlerdir. Gerçek banka, zincir, işyeri ya
da kurum adı da yoktur: okunan ekstrelerdeki adlar kurgu adlarla değiştirilir (app/demo_names.py).
"""
from datetime import date
from pathlib import Path

from app import db, demo_names
from app.parsers import kuveytturk, vakifbank

FIX = Path(__file__).resolve().parent.parent / "tests" / "fixtures"


def seed(statements: tuple[str, ...] = ("vakifbank_a.txt", "vakifbank_b.txt", "kuveytturk.txt"), bills: bool = True) -> None:
    """statements: yüklenecek örnek ekstreler (canlı demoda biri "örnek yükle" düğmesine bırakılır). bills=False:
    faturalar eklenmez (canlı demo şablonu onları tarih kaydırmasından sonra add_bills ile bugüne göre yazar)."""
    db.init_db()
    if db.list_statements():
        print("Veritabanı boş değil; demo verisi eklenmedi.")
        return
    demo_names.add_rules()  # kurgu işyeri adlarının kategorisi (ekstrelerden önce)
    mods = {"vakifbank_a.txt": vakifbank, "vakifbank_b.txt": vakifbank, "kuveytturk.txt": kuveytturk}
    for name, mod in ((n, mods[n]) for n in statements):
        text = (FIX / name).read_text(encoding="utf-8")
        st = demo_names.scrub_statement(mod.parse_text(text))
        db.save_statement(f"demo:{name}".encode(), demo_names.FILES[name], st)
    db.auto_link()

    maas = db.create_recurring({"description": "Maaş", "amount": 200000, "kind": "gelir", "category": "Maaş",
                                "day_of_month": 1, "start_month": "2026-01"})
    kira = db.create_recurring({"description": "Kira", "amount": 22000, "kind": "gider", "category": "Kira & Aidat",
                                "day_of_month": 5, "start_month": "2026-01"})
    for rid, desc, amt, kind, cat, day in ((maas, "Maaş", 200000, "gelir", "Maaş", 1), (kira, "Kira", 22000, "gider", "Kira & Aidat", 5)):
        db.create_entry({"entry_date": f"2026-10-{day:02d}", "description": desc, "amount": amt, "kind": kind,
                         "category": cat, "recurring_id": rid, "payment": "havale" if kind == "gider" else None})
    # aynı kiranın kategorisiz ikinci kaydı: hiçbir ödemeye bağlanmaz → Yapılacaklar / Planlama "İki sabit kalem aynı
    # olabilir" önerisi (app/recurring_dupes.py; Birleştir ya da Ayrı kalsın)
    db.create_recurring({"description": "Ev kirası", "amount": 22000, "kind": "gider", "day_of_month": 5,
                         "start_month": "2026-03"})
    db.create_recurring({"description": "Spor salonu", "amount": 1500, "kind": "gider", "category": "Sağlık",
                         "day_of_month": 10, "start_month": "2026-01"})
    db.create_entry({"entry_date": "2026-10-08", "description": "Diş hekimi", "amount": 2400, "kind": "gider",
                     "category": "Sağlık", "payment": "havale"})
    db.create_entry({"entry_date": "2026-10-12", "description": "Pazar alışverişi", "amount": 640, "kind": "gider",
                     "category": "Market & Gıda", "payment": "nakit"})
    db.create_entry({"entry_date": "2026-10-03", "description": "Serbest proje", "amount": 12000, "kind": "gelir",
                     "category": "Ek Gelir"})
    # elle girilen taksitli kart harcamaları (ekstresi olmayan kart): Özet → "Bu ay ödenecek taksitler", Planlama
    db.create_entry({"entry_date": "2026-08-18", "description": "Örnek Beyaz Eşya · Buzdolabı", "amount": 18000,
                     "kind": "gider", "category": "Elektronik", "payment": "kredi_karti", "installments": 6})
    db.create_entry({"entry_date": "2026-10-09", "description": "Örnek Gözlükçü", "amount": 4500, "kind": "gider",
                     "category": "Sağlık", "payment": "kredi_karti", "installments": 3})
    if bills:
        add_bills(date(2026, 10, 20))  # app.demo_mode.ANCHOR (canlı demo: kaydırmadan sonra gerçek bugünle)
    db.set_budget("Kafe & Restoran", 2500)
    db.set_budget("Market & Gıda", 4000)
    db.set_budget("Yemek Siparişi", 1000)
    for cat, names in (("Market & Gıda", ("Manav", "Temizlik")), ("Sağlık", ("Diş", "İlaç"))):
        for n in names:  # alt kategori listesi (kayıtlara atanmaz: ekranlar eskisi gibi açılır)
            db.add_subcategory(cat, n)
    db.create_goal({"kind": "category", "name": "AI & Yazılım Abonelik", "baseline": 4794.67, "target": 1500,
                    "start_month": "2026-11", "note": "kullanmadıklarımı kapat"})
    from app.textnorm import merchant_name

    db.set_subscription_state(merchant_name("ÖRNEK E-KİTAP ABONELİĞİ"), "iptal",
                              date(2026, 10, 15).isoformat())
    _extras()
    print(f"Demo verisi yüklendi: {db.DB_PATH}")


# Faturalar: bu ayınki ödenmemiş, önceki 12 ayınki ödenmiş (Faturalar sayfası: aylık seri, ortalama, en yüksek ay,
# önceki aya göre değişim). Tutar faturanın ayına göre: elektrik yazın klimayla, doğalgaz kışın yükselir; internet sabit,
# son 7 fatura zamlı. Ödenmiş fatura = /api/bills kaydı + gider kaydı (db.pay_bill, gerçek ödemeyle aynı yol); havaleyle
# ödenir: kart ekstresinde satırı yok, giderde bir kez sayılır.
BILLS = (  # (kurum, kategori, bugünden kaç gün sonra son ödeme, {ay (1–12): tutar} | (zamdan önce, son 7 fatura))
    ("Örnek Elektrik", "Elektrik", 8, {1: 795.40, 2: 730.10, 3: 680.90, 4: 610.30, 5: 655.70, 6: 880.40, 7: 1185.60,
                                       8: 1240.90, 9: 960.20, 10: 845.30, 11: 690.50, 12: 760.80}),
    ("Örnek İnternet", "İnternet & TV", 4, (449.90, 549.90)),
    ("Örnek Doğalgaz", "Doğalgaz", 7, {1: 2890.40, 2: 2610.70, 3: 1720.20, 4: 890.60, 5: 380.40, 6: 210.80, 7: 165.30,
                                       8: 158.90, 9: 230.60, 10: 520.40, 11: 1180.30, 12: 2350.80}),
)
_AYLAR = ("Ocak", "Şubat", "Mart", "Nisan", "Mayıs", "Haziran", "Temmuz", "Ağustos", "Eylül", "Ekim", "Kasım", "Aralık")


def add_bills(today: date) -> None:
    """Faturaları today'e göre yaz: aylar takvim ayıdır (tarih kaydırması ay sınırında iki faturayı bir aya düşürmesin)."""
    from datetime import timedelta

    for provider, cat, ahead, amounts in BILLS:
        due0 = today + timedelta(days=ahead)
        for back in range(13):  # 0: bu ayın (ödenmemiş) faturası, 1–12: geçmiş aylar (ödenmiş)
            y, m = divmod(due0.year * 12 + due0.month - 1 - back, 12)
            m += 1
            due = due0 if not back else date(y, m, min(due0.day, 28))
            amount = amounts[m] if isinstance(amounts, dict) else amounts[back < 7]
            used = date(y, m, 1) - timedelta(days=1)  # fatura bir önceki ayın tüketimi
            bid = db.create_bill({"provider": provider, "amount": amount, "due_date": due.isoformat(), "category": cat,
                                  "description": f"{_AYLAR[used.month - 1]} {used.year}"})
            if back:
                db.pay_bill(bid, amount, (due - timedelta(days=3)).isoformat(), "havale")


def _extras() -> None:
    """Yeni özellikler için uydurma veri: geçmiş aylar, hesaplar, kumbara, altın/döviz, zekât, sadaka."""
    from app import donations, zakat

    # geçmiş 12 ay: grafik ve karşılaştırma dolsun (faturalar add_bills; kira Ocak'ta zamlandı)
    for m, (gelir, market, kafe) in {"2025-10": (175000, 4600, 1700), "2025-11": (175000, 4800, 2200),
                                     "2025-12": (175000, 5600, 2900), "2026-01": (185000, 5100, 1800),
                                     "2026-02": (185000, 4400, 1600), "2026-03": (185000, 5300, 2400),
                                     "2026-04": (185000, 4900, 2000), "2026-05": (185000, 5200, 1900),
                                     "2026-06": (185000, 4700, 2600), "2026-07": (200000, 6100, 2100),
                                     "2026-08": (200000, 5300, 3100), "2026-09": (200000, 4900, 2300)}.items():
        db.create_entry({"entry_date": f"{m}-01", "description": "Maaş", "amount": gelir, "kind": "gelir", "category": "Maaş"})
        db.create_entry({"entry_date": f"{m}-05", "description": "Kira", "amount": 22000 if m >= "2026-01" else 18500,
                         "kind": "gider", "category": "Kira & Aidat", "payment": "havale"})
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
    # borç kapatma planı (app/debt_plan.py): iki kart + iki elden borç; aylık tutar kayıtlı (örnek plan birkaç aya yayılır,
    # biri söz verilen tarihten sonra kapanır: "Söz verdiğim tarihe göre" sıralamasının farkı görünsün). Kurgu adlar.
    db.create_debt({"person": "Kerem", "direction": "borc", "amount": 30000, "debt_date": "2026-06-12",
                    "due_date": "2026-11-25", "note": "Düğün masrafı", "asset": "TRY"})
    db.create_debt({"person": "Selin", "direction": "borc", "amount": 8000, "debt_date": "2026-08-03",
                    "note": "Elden", "asset": "TRY"})
    # aynı kişiye ikinci borç (söz tarihi önce): "Bu ay ödenecekler"de Kerem tek satır, açınca iki borç
    db.create_debt({"person": "Kerem", "direction": "borc", "amount": 1500, "debt_date": "2026-09-14",
                    "due_date": "2026-11-05", "note": "Araba tamiri", "asset": "TRY"})
    # ödeme planı olan borç (app/debt_schedule.py; yalnız kayıt): ayda ₺1.500, 8 taksit, her ayın 26'sı. Kurgu kurum.
    db.create_debt({"person": "Örnek Vergi Dairesi", "direction": "borc", "amount": 12000, "debt_date": "2026-10-01",
                    "asset": "TRY", "plan_installment": 1500, "plan_count": 8, "plan_start": "2026-10", "plan_day": 26,
                    "creditor_type": "vergi_sgk", "plan_interest": "bilmiyorum"})
    from app import debt_plan

    debt_plan.save_settings({"monthly": 30000, "order": "faiz"})
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
    donations.add("bagis", 500, "2026-09-12", "Örnek Vakıf")
    donations.add("fitre", 600, "2026-03-18")
    # market fişleri (fiyat takibi, öneri, piyasa notu; app/demo_prices.py)
    from app import demo_mode, demo_prices

    demo_prices.receipts(demo_mode.ANCHOR)


if __name__ == "__main__":
    seed()
