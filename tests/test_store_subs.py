"""Uygulama mağazası abonelikleri (app/store_subs.py, app/store_screens.py, app/sub_catalog.py).

Bütün veriler uydurmadır: kart numaraları, tutarlar, uygulama adları ("Örnek Meditasyon"), ekran görüntüsü metinleri
ve makbuz e-postaları elle yazılmış örneklerdir; gerçek bir ekstreden ya da e-postadan alınmamıştır.
"""
from datetime import date, timedelta
from email.message import EmailMessage

import pytest

CARD = "4111********1111"


def _stmt(db, pe: date, rows: list[tuple[date, str, float]], kind: str = "harcama", card: str = CARD) -> int:
    from app.parsers.base import Statement, Transaction

    txns = [Transaction(d, desc, amt, kind if amt > 0 else "iade", card) for d, desc, amt in rows]
    s = Statement(bank="Örnek Bank", card_no=card, period_end=pe, due_date=pe + timedelta(days=10),
                  total_debt=round(sum(a for _, _, a in rows), 2), min_payment=None, credit_limit=None, transactions=txns)
    return db.save_statement(f"ornek-{card}-{pe}".encode(), f"ornek-{pe}.pdf", s)


def _months(year: int, months: range, day: int, desc: str, amt, cut: int = 20) -> list:
    """Her ay aynı gün aynı satır; amt sayı ya da {ay: tutar}."""
    out = []
    for m in months:
        a = amt.get(m, amt.get(0)) if isinstance(amt, dict) else amt
        out.append((date(year, m, cut), [(date(year, m, day), desc, a)]))
    return out


def _load(db, plan: list[tuple[date, list]]):
    by: dict[date, list] = {}
    for pe, rows in plan:
        by.setdefault(pe, []).extend(rows)
    for pe, rows in sorted(by.items()):
        _stmt(db, pe, rows)


TODAY = date(2026, 11, 25)


@pytest.fixture
def apple_two(tmpdb):
    """Tek mağazadan iki abonelik: her ay 14'ü ₺59,99 ve yılda bir 3 Mart ₺599,99."""
    plan = _months(2026, range(4, 12), 14, "APPLE.COM/BILL ITUNES.COM", 59.99)
    plan += [(date(2025, 3, 20), [(date(2025, 3, 3), "APPLE.COM/BILL", 599.99)]),
             (date(2026, 3, 20), [(date(2026, 3, 3), "APPLE.COM/BILL", 599.99)])]
    _load(tmpdb, plan)
    return tmpdb


def _by(items, **kw):
    return [i for i in items if all(i.get(k) == v for k, v in kw.items())]


# ---------------------------------------------------------------- satırdan mağaza

def test_store_of_lines():
    from app.store_subs import store_of

    assert store_of("APPLE.COM/BILL ITUNES.COM") == ("apple", None)
    assert store_of("POS ISLEMI APPLE.COM/BILL 866-712-7753") == ("apple", None)
    assert store_of("GOOGLE *YouTube g.co/helppay#") == ("google", "YouTube")
    assert store_of("GOOGLE *Google One") == ("google", "One") or store_of("GOOGLE *Google One")[1] in ("Google One", "One")
    assert store_of("GOOGLE *Calm DUBLIN") == ("google", "Calm")
    assert store_of("GOOGLE PLAY ISTANBUL") == ("google", None)
    assert store_of("GOOGLE *ADS1234567") is None  # reklam: mağaza değil
    assert store_of("GOOGLE*CLOUD ABC123") is None
    assert store_of("APPLE STORE ZORLU") is None  # fiziksel mağaza
    assert store_of("MIGROS 1234") is None


def test_day_and_year_suffix():
    from app.store_subs import day_text, year_loc

    assert [day_text(d) for d in (1, 3, 6, 10, 14, 20, 30)] == ["1'i", "3'ü", "6'sı", "10'u", "14'ü", "20'si", "30'u"]
    assert year_loc(2026) == "2026'da" and year_loc(2025) == "2025'te" and year_loc(2027) == "2027'de"


# ---------------------------------------------------------------- adaylar

def test_two_subscriptions_same_store(apple_two):
    from app import store_subs

    items = store_subs.items(TODAY)
    assert len(items) == 2
    mon = _by(items, cycle="aylik")[0]
    yr = _by(items, cycle="yillik")[0]
    # adsız da tam abonelik
    assert mon["title"] == "Apple aboneliği" and mon["name"] is None and mon["status"] == "abonelik" and mon["counted"]
    assert mon["cycle_text"] == "her ay 14'ü" and mon["amount"] == 59.99 and mon["day"] == 14
    assert mon["next_date"] == "2026-12-14" and mon["next_text"] == "Sonraki tahsilat 14 Aralık (tahmini)"
    assert mon["first_text"] == "İlk kez Nisan 2026'da çıktı · 8 kez çekildi"
    assert yr["cycle_text"] == "her yıl 3 Mart" and yr["amount"] == 599.99 and yr["next_date"] == "2027-03-03"
    assert yr["monthly"] == 50.0 and yr["yearly"] == 599.99
    assert {m["store"] for m in mon["manage"]} == {"apple"}
    assert mon["manage"][0]["url"] == "https://apps.apple.com/account/subscriptions"
    t = store_subs.totals(items)
    assert t["monthly"] == 59.99 and t["yearly"] == round(59.99 * 12 + 599.99, 2)
    assert t["text"] == "Uygulama abonelikleri: ayda ₺59,99 · yılda ₺1.319,87"


def test_price_increase_and_google_name(tmpdb):
    from app import store_subs

    _load(tmpdb, _months(2026, range(5, 12), 9, "GOOGLE *YouTube g.co/helppay#", {0: 69.99, 10: 79.99, 11: 79.99}))
    [it] = store_subs.items(TODAY)
    assert it["name"] == "YouTube Premium" and it["named_by"] == "ekstre" and it["catalog_id"] == "youtube_premium"
    assert it["logo"]["color"] == "#FF0000" and it["logo"]["glyph"] == "YT"
    assert it["price_up"] is None  # son iki tahsilat aynı
    assert it["amount"] == 79.99 and it["prev_amount"] is None
    # zam bu ay: 79,99 → 89,99
    _stmt(tmpdb, date(2026, 12, 20), [(date(2026, 12, 9), "GOOGLE *YouTube g.co/helppay#", 89.99)])
    [it] = store_subs.items(date(2026, 12, 21))
    assert it["price_up"] == {"from": 79.99, "to": 89.99, "date": "2026-12-09"} and it["count"] == 8
    push = store_subs.price_up_items(date(2026, 12, 21))
    assert push and push[0]["title"] == "YouTube Premium zamlandı" and "₺79,99 → ₺89,99" in push[0]["body"]
    assert push[0]["route"] == f"#/diger/tasarruf/abonelik/{it['id']}"


def test_unnamed_google_other_name(tmpdb):
    from app import store_subs

    _load(tmpdb, _months(2026, range(8, 12), 2, "GOOGLE *Calm", 129.99))
    [it] = store_subs.items(TODAY)
    assert it["name"] == "Calm" and it["title"] == "Calm" and it["catalog_id"] is None and it["logo"]["glyph"] == "C"


def test_one_time_purchase(apple_two):
    from app import store_subs

    _stmt(apple_two, date(2026, 10, 21), [(date(2026, 10, 5), "APPLE.COM/BILL", 299.99)])
    items = store_subs.items(TODAY)
    one = _by(items, amount=299.99)[0]
    assert one["status"] == "aday" and one["cycle"] is None and not one["counted"]
    assert one["cycle_text"] == "tek çekim · 5 Ekim 2026" and one["first_text"].endswith("bir kez çekildi")
    before = store_subs.totals(items)
    store_subs.act(one["id"], "degil", today=TODAY)
    one = store_subs.find(one["id"], TODAY)
    assert one["status"] == "degil" and store_subs.totals(store_subs.items(TODAY)) == before
    # planlama: "abonelik değil" denen tahsilat tekrar eden abonelik sayılmaz
    from app import finance

    ids = store_subs.excluded_txn_ids(TODAY)
    assert len(ids) == 1
    proj = finance.card_projection()
    assert not any(s["txn_id"] in ids for acc in proj.values() for s in acc["subscriptions"])
    # geri al
    store_subs.act(one["id"], "sifirla", today=TODAY)
    assert store_subs.find(one["id"], TODAY)["status"] == "aday"


def test_single_charge_then_choose_cycle(tmpdb):
    from app import store_subs

    _stmt(tmpdb, date(2026, 11, 20), [(date(2026, 11, 2), "APPLE.COM/BILL", 39.99)])
    [it] = store_subs.items(TODAY)
    assert it["status"] == "aday" and not it["counted"]
    store_subs.act(it["id"], "abonelik", cycle="aylik", today=TODAY)
    it = store_subs.find(it["id"], TODAY)
    assert it["status"] == "abonelik" and it["counted"] and it["next_date"] == "2026-12-02"


def test_guesses_from_price_and_name_from_catalog(tmpdb):
    from app import store_subs

    _load(tmpdb, _months(2026, range(6, 12), 3, "APPLE.COM/BILL", 129.99))
    [it] = store_subs.items(TODAY)
    assert it["guesses"] and it["guesses"][0]["id"] == "icloud" and it["guesses"][0]["plan"] == "iCloud+ 200 GB"
    store_subs.act(it["id"], "adlandir", catalog_id="icloud", today=TODAY)
    it = store_subs.find(it["id"], TODAY)
    assert it["title"] == "iCloud+" and it["named_by"] == "sen" and it["guesses"] == [] and it["logo"]["id"] == "icloud"
    # "Bilmiyorum": öneri yok, adsız kalır
    _stmt(tmpdb, date(2026, 11, 21), [(date(2026, 11, 8), "APPLE.COM/BILL", 64.99)])
    other = next(i for i in store_subs.items(TODAY) if i["amount"] == 64.99)
    assert other["guesses"]
    store_subs.act(other["id"], "bilmiyorum", today=TODAY)
    assert store_subs.find(other["id"], TODAY)["guesses"] == []


def test_label_survives_new_history(tmpdb):
    """Karar parmak izine bağlı: daha eski ekstre yüklenip aday kimliği değişse de bulunur."""
    from app import store_subs

    _load(tmpdb, _months(2026, range(9, 12), 14, "APPLE.COM/BILL", 59.99))
    [it] = store_subs.items(TODAY)
    store_subs.act(it["id"], "adlandir", name="Örnek Meditasyon", today=TODAY)
    _load(tmpdb, _months(2026, range(6, 9), 14, "APPLE.COM/BILL", 59.99))
    [it2] = store_subs.items(TODAY)
    assert it2["id"] != it["id"] and it2["title"] == "Örnek Meditasyon" and it2["count"] == 6


def test_refund_drops_charge(tmpdb):
    from app import store_subs

    _stmt(tmpdb, date(2026, 11, 20), [(date(2026, 11, 2), "APPLE.COM/BILL", 149.99),
                                      (date(2026, 11, 6), "APPLE.COM/BILL", -149.99)])
    assert store_subs.items(TODAY) == []


def test_reminder_three_days_before(apple_two):
    from app import push, store_subs

    mon = next(i for i in store_subs.items(TODAY) if i["cycle"] == "aylik")
    assert store_subs.reminders(date(2026, 12, 10)) == []
    [r] = store_subs.reminders(date(2026, 12, 11))
    assert r["kind"] == "abonelik" and r["title"] == "3 gün sonra Apple aboneliği ₺59,99 yenileniyor"
    assert r["body"].startswith("14 Aralık · App Store.") and r["route"] == f"#/diger/tasarruf/abonelik/{mon['id']}"
    assert r["id"] == f"abonelik:yenileme:{mon['id']}:2026-12-14"
    assert store_subs.when_text(1) == "Yarın"
    # yıllık: 3 Mart'a 3 gün kala
    [y] = store_subs.reminders(date(2027, 2, 28))
    assert "Apple aboneliği ₺599,99" in y["title"]
    # push günlük işinde (Tasarruf Premium; kendi sunucusunda her şey açık)
    u = {"id": 1, "username": "ornek"}
    items = push.collect(u, date(2026, 12, 11), {"types": {k: True for k in push.TYPES}})
    assert any(i["id"] == r["id"] for i in items)
    # kapatılınca yok
    items = push.collect(u, date(2026, 12, 11), {"types": {**{k: True for k in push.TYPES}, "abonelik": False}})
    assert not any(i["id"] == r["id"] for i in items)


def test_named_reminder_text(tmpdb):
    from app import store_subs

    _load(tmpdb, _months(2026, range(7, 12), 9, "GOOGLE *YouTube", 79.99))
    [r] = store_subs.reminders(date(2026, 12, 6))
    assert r["title"] == "3 gün sonra YouTube Premium ₺79,99 yenileniyor" and "Google Play" in r["body"]


def test_cancel_then_charged_again(apple_two):
    from app import finance, push, store_subs

    mon = next(i for i in store_subs.items(TODAY) if i["cycle"] == "aylik")
    store_subs.act(mon["id"], "iptal", when="2026-10-20", today=TODAY)
    mon = store_subs.find(mon["id"], TODAY)
    assert mon["status"] == "iptal" and not mon["counted"] and mon["charged_after_cancel"] == 59.99  # 14 Kasım
    assert store_subs.reminders(date(2026, 12, 11)) == []  # iptal edilene hatırlatma yok
    [a] = store_subs.cancel_alerts(TODAY)
    assert a["title"] == "Apple aboneliği iptal edilmişti ama yine çekildi" and a["key"].startswith("iptal_cekim:store-")
    keys = [i.get("key") for i in finance.insights(finance.current_period(TODAY), TODAY)]
    assert a["key"] in keys
    items = push.statement_items(TODAY)
    hit = [i for i in items if i["id"] == a["key"]]
    assert hit and hit[0]["kind"] == "abonelik" and hit[0]["route"] == store_subs.route(mon["id"])
    store_subs.act(mon["id"], "aktif", today=TODAY)
    assert store_subs.find(mon["id"], TODAY)["status"] == "abonelik"


def test_stale_subscription(tmpdb):
    from app import store_subs

    _load(tmpdb, _months(2026, range(2, 6), 14, "APPLE.COM/BILL", 59.99))
    [it] = store_subs.items(TODAY)
    assert it["stale"] and not it["counted"] and it["next_date"] is None
    assert it["stale_text"] == "Son çekim 14 Mayıs 2026 · durmuş olabilir"


def test_savings_rows_marked_store(client, apple_two):
    subs = client.get("/api/savings").json()["subscriptions"]
    apple = next(s for s in subs if s["merchant"] == "APPLE")
    assert apple["store"] == "apple"  # yeni istemci mağaza grubunda gösterir; eski istemci aynen görür


def test_card_projection_skips_cancelled_store(tmpdb):
    """Planlama: iptal denen mağaza aboneliği tahminden düşer."""
    from app import finance, store_subs

    _load(tmpdb, _months(2026, range(8, 12), 14, "APPLE.COM/BILL", 59.99))
    assert any(s["amount"] == 59.99 for acc in finance.card_projection().values() for s in acc["subscriptions"])
    [it] = store_subs.items(TODAY)
    store_subs.act(it["id"], "iptal", when="2026-11-20", today=TODAY)
    assert not any(acc["subscriptions"] for acc in finance.card_projection().values())


# ---------------------------------------------------------------- ekran görüntüsü

IOS_TR = """9:41
‹ Apple Hesabı
Abonelikler
ETKİN
YouTube
YouTube Premium (Aylık)
14 Aralık tarihinde yenilenir
₺79,99
iCloud+
200 GB depolama alanlı iCloud+
3 Aralık tarihinde yenilenir
₺129,99
Örnek Meditasyon
Yıllık Plan
3 Mart 2027 tarihinde yenilenir
₺599,99
ETKİN DEĞİL
Disney+
10 Mayıs 2026 tarihinde sona erdi
"""

IOS_EN = """Subscriptions
ACTIVE
YouTube
YouTube Premium (Monthly)
Renews 14 December
₺79.99
Sample Fitness
Annual
Renews March 3, 2027
₺599.99
INACTIVE
Netflix
Expired May 10, 2026
"""

PLAY_TR = """Abonelikler
Etkin
YouTube Premium
YouTube
Aylık ₺79,99
Sonraki ödeme: 14 Ara 2026
Google One
100 GB · Yıllık
₺599,99/yıl · Sonraki ödeme: 3 Mar 2027
Süresi doldu
Örnek Oyun Kulübü
22 Eki 2026 tarihinde sona erdi
"""

PLAY_EN = """Payments & subscriptions
Subscriptions
Active
YouTube Premium · YouTube
₺79.99/month
Next payment: Dec 14, 2026
Expired
Calm
Ended Oct 2, 2026
"""


@pytest.mark.parametrize("text,store,expect", [
    (IOS_TR, "apple", [("YouTube Premium", 79.99, "aylik", "2026-12-14", True), ("iCloud+", 129.99, None, "2026-12-03", True),
                       ("Örnek Meditasyon", 599.99, "yillik", "2027-03-03", True), ("Disney+", None, None, "2026-05-10", False)]),
    (IOS_EN, "apple", [("YouTube Premium", 79.99, "aylik", "2026-12-14", True), ("Sample Fitness", 599.99, "yillik", "2027-03-03", True),
                       ("Netflix", None, None, "2026-05-10", False)]),
    (PLAY_TR, "google", [("YouTube Premium", 79.99, "aylik", "2026-12-14", True), ("Google One", 599.99, "yillik", "2027-03-03", True),
                         ("Örnek Oyun Kulübü", None, None, "2026-10-22", False)]),
    (PLAY_EN, "google", [("YouTube Premium", 79.99, "aylik", "2026-12-14", True), ("Calm", None, None, "2026-10-02", False)]),
])
def test_screen_text_tr_en(text, store, expect):
    from app import store_screens

    r = store_screens.parse_screen_text(text, date(2026, 11, 12))
    assert r["store"] == store
    got = [(i["name"], i["amount"], i["cycle"], i["date"].isoformat(), i["active"]) for i in r["items"]]
    assert got == expect


def test_screen_plan_names():
    from app import store_screens

    ios = store_screens.parse_screen_text(IOS_TR, date(2026, 11, 12))["items"]
    assert ios[0]["plan"] is None and ios[1]["plan"] == "200 GB depolama alanlı iCloud+"
    play = store_screens.parse_screen_text(PLAY_TR, date(2026, 11, 12))["items"]
    assert play[1]["plan"] == "100 GB"


def test_screenshot_matches_candidates_and_adds_pending(tmpdb):
    """Ekran görüntüsü: YouTube adayı adlanır (tutar + gün + mağaza), iCloud ekstrede yok → bekleyen, iptal atlanır."""
    from app import store_screens, store_subs

    _load(tmpdb, _months(2026, range(6, 12), 14, "APPLE.COM/BILL", 79.99))
    _load(tmpdb, _months(2026, range(6, 12), 20, "APPLE.COM/BILL", 39.99, cut=25))
    rows = store_screens.parse_screen_text(IOS_TR, date(2026, 11, 12))["items"]
    res = store_subs.ingest(rows, "ekran", date(2026, 11, 12))
    assert res["matched"] == 1 and res["pending"] == 2 and res["skipped"] == 1
    items = store_subs.items(date(2026, 11, 12))
    yt = next(i for i in items if i["amount"] == 79.99 and not i["pending"])
    assert yt["title"] == "YouTube Premium" and yt["named_by"] == "ekran" and yt["next_date"] == "2026-12-14"
    assert "(tahmini)" not in yt["next_text"]  # ekrandaki yenileme günü
    ic = next(i for i in items if i["pending"] and i["title"] == "iCloud+")
    assert ic["first_text"] == "Henüz ekstreye düşmemiş · ekran görüntüsünden" and ic["counted"] and ic["cycle"] == "aylik"
    assert ic["next_date"] == "2026-12-03" and ic["store"] == "apple"
    unnamed = next(i for i in items if i["amount"] == 39.99)
    assert unnamed["title"] == "Apple aboneliği"  # ekranda yok: adsız kalır
    # aynı ekran ikinci kez: yeni satır yok
    again = store_subs.ingest(rows, "ekran", date(2026, 11, 12))
    assert not any(r["new"] for r in again["rows"])
    # bekleyen abonelik ekstreye düşünce aynı aday olur (tek satır)
    _stmt(tmpdb, date(2026, 12, 20), [(date(2026, 12, 3), "APPLE.COM/BILL", 129.99)])
    items = store_subs.items(date(2026, 12, 21))
    assert [i["title"] for i in items].count("iCloud+") == 1
    assert next(i for i in items if i["title"] == "iCloud+")["pending"] is False


def test_screenshot_cancelled_marks_candidate(tmpdb):
    from app import store_subs

    _load(tmpdb, _months(2026, range(3, 6), 10, "APPLE.COM/BILL", 349.9))
    rows = [{"name": "Disney+", "catalog_id": "disney_plus", "store": "apple", "amount": 349.9, "cycle": "aylik",
             "date": date(2026, 6, 10), "date_kind": "bitis", "active": False}]
    store_subs.ingest(rows, "ekran", date(2026, 7, 1))
    [it] = store_subs.items(date(2026, 7, 1))
    assert it["status"] == "iptal" and it["title"] == "Disney+" and it["cancelled_at"] == "2026-06-10"


def test_screenshot_endpoint_text_and_ai(client, monkeypatch, tmpdb):
    from app import receipt

    _load(tmpdb, _months(2026, range(7, 12), 9, "GOOGLE *YouTube", 79.99))
    png = b"\x89PNG\r\n\x1a\n" + b"0" * 64
    r = client.post("/api/subscriptions/apps/screenshot", files=[("files", ("ekran.png", png, "image/png"))],
                    data={"ocr_text": PLAY_EN.replace("Dec 14", "Dec 9"), "store": "google"})
    assert r.status_code == 200, r.text
    j = r.json()
    assert j["read"][0]["source"] == "metin" and j["matched"] == 1 and j["rows"][0]["match"]["pending"] is False
    # metin yok: yapay zekâ (sahte) okur
    monkeypatch.setattr(receipt, "enabled", lambda: True)
    seen = {}

    def fake(prompt, data, mime, **kw):
        seen["prompt"] = prompt
        return ({"is_subscription_list": True, "store": "apple", "items": [
            {"app": "Örnek Meditasyon", "plan": "Yıllık", "price": 599.99, "currency": "TRY", "period": "yillik",
             "date": "2027-03-03", "date_kind": "yenileme", "active": True}]}, {})
    monkeypatch.setattr(receipt, "_gemini_json", fake)
    png2 = b"\x89PNG\r\n\x1a\n" + b"1" * 64
    j = client.post("/api/subscriptions/apps/screenshot", files=[("files", ("e2.png", png2, "image/png"))]).json()
    assert j["read"][0]["source"] == "yapay_zeka" and j["pending"] == 1 and "Abonelikler" in seen["prompt"]
    # aynı görüntü tekrar: aynı kaynak satırı yenilenir, ikinci kez eklenmez (hazır sürümde önbellekten, hak düşmez)
    j = client.post("/api/subscriptions/apps/screenshot", files=[("files", ("e2.png", png2, "image/png"))]).json()
    assert j["read"][0]["source"] in ("onbellek", "yapay_zeka") and not j["rows"][0]["new"]
    apps = client.get("/api/subscriptions/apps").json()
    titles = {i["title"] for i in apps["items"]} | {i["title"] for i in apps["other"]}
    assert "Örnek Meditasyon" in titles and "YouTube Premium" in titles
    # PDF / belge değil
    bad = client.post("/api/subscriptions/apps/screenshot", files=[("files", ("x.pdf", b"%PDF-1", "application/pdf"))])
    assert bad.status_code == 422


def test_ai_not_list():
    from app import store_screens

    assert store_screens.from_ai({"is_subscription_list": False, "items": []})["items"] == []


# ---------------------------------------------------------------- makbuz e-postası (isteğe bağlı)

APPLE_RECEIPT_EN = """Receipt
APPLE ACCOUNT ornek@example.com
DATE 3 Mar 2026
ORDER ID MXYZ12AB34
DOCUMENT NO. 123456789012
App Store
Sample Meditation: Sleep
Sample Meditation Annual (Yearly)
Renews 3 March 2027
₺599,99
TOTAL ₺599,99
"""

APPLE_RECEIPT_TR = """Makbuz
APPLE HESABI ornek@example.com
TARİH 14 Eki 2026
SİPARİŞ NO MABC98ZY76
App Store
YouTube: Videolar ve Müzik
YouTube Premium (Aylık)
14 Kasım 2026 tarihinde yenilenir
₺79,99
TOPLAM ₺79,99
"""

PLAY_RECEIPT_TR = """Google Play sipariş makbuzunuz
Sipariş numarası: GPA.1234-5678-9012-34567
Sipariş tarihi: 9 Kas 2026 10:15
Ürün
Google One 100 GB (Google One)
Aylık ₺59,99
Otomatik yenilenen abonelik · Sonraki ödeme: 9 Ara 2026
Toplam: ₺59,99/ay
"""

PLAY_RECEIPT_EN = """Your Google Play Order Receipt from Nov 9, 2026
Order number: GPA.9876-5432-1098-76543
Order date: Nov 9, 2026 10:15 AM GMT+3
Item
Calm Premium (Calm)
₺129.99/month
Auto-renewing subscription. Next payment: Dec 9, 2026
Total: ₺129.99/month
"""


@pytest.mark.parametrize("subject,text,sender,expect", [
    ("Your receipt from Apple.", APPLE_RECEIPT_EN, "no_reply@email.apple.com",
     ("apple", "2026-03-03", "MXYZ12AB34", "Sample Meditation", 599.99, "yillik", "2027-03-03")),
    ("Apple'dan makbuzunuz", APPLE_RECEIPT_TR, "no_reply@email.apple.com",
     ("apple", "2026-10-14", "MABC98ZY76", "YouTube Premium", 79.99, "aylik", "2026-11-14")),
    ("Google Play sipariş makbuzunuz (9 Kas 2026)", PLAY_RECEIPT_TR, "googleplay-noreply@google.com",
     ("google", "2026-11-09", "GPA.1234-5678-9012-34567", "Google One", 59.99, "aylik", "2026-12-09")),
    ("Your Google Play Order Receipt from Nov 9, 2026", PLAY_RECEIPT_EN, "googleplay-noreply@google.com",
     ("google", "2026-11-09", "GPA.9876-5432-1098-76543", "Calm Premium", 129.99, "aylik", "2026-12-09")),
])
def test_receipt_parse_tr_en(subject, text, sender, expect):
    from app import store_screens

    r = store_screens.parse_receipt(subject, text, date(2026, 11, 10), sender)
    it = r["items"][0]
    got = (r["store"], r["date"].isoformat(), r["order"], it["name"], it["amount"], it["cycle"], it["date"].isoformat())
    assert got[:3] == expect[:3]
    assert expect[3] in it["name"] or it["name"] in expect[3]
    assert got[4:] == expect[4:]


def test_not_a_receipt():
    from app import store_screens

    assert store_screens.parse_receipt("Güvenlik uyarısı", "Hesabına yeni bir cihazdan giriş yapıldı.",
                                       date(2026, 11, 10), "no_reply@accounts.google.com") is None


def _mail(sender, subject, body, mid):
    m = EmailMessage()
    m["From"] = f"Mağaza <{sender}>"
    m["To"] = "fatura-abc@posta.example"
    m["Subject"] = subject
    m["Date"] = "Wed, 14 Oct 2026 10:00:00 +0300"
    m["Message-ID"] = mid
    m.set_content(body)
    return m.as_bytes()


def test_receipt_email_names_candidate(tmpdb):
    """Gmail filtresiyle iletilen Apple makbuzu (From ve imza korunur): adsız aday YouTube Premium olur; okuma hakkı yok."""
    from app import auth, db, inbound, store_subs, system

    owner = auth.create_user("ornek@example.com", "ornek-sifre-12345")
    to = inbound.address(owner)
    with db.use_space(system.user_db_path(owner), owner):
        db.init_db()
        _load(db, _months(2026, range(6, 11), 14, "APPLE.COM/BILL", 79.99))
    r = inbound.handle(_mail("no_reply@email.apple.com", "Apple'dan makbuzunuz", APPLE_RECEIPT_TR, "<r1@x>"), to,
                       verify=lambda raw: {"email.apple.com"})
    assert r["status"] == "ok" and r["kind"] == "makbuz"
    assert "App Store makbuzu: YouTube Premium · ₺79,99 · yenileme 14.11 · ekstredeki tahsilatla eşleşti" in r["detail"]
    with db.use_space(system.user_db_path(owner), owner):
        [it] = store_subs.items(date(2026, 10, 20))
        assert it["title"] == "YouTube Premium" and it["named_by"] == "makbuz"
    # imzasız: atlanır
    r = inbound.handle(_mail("no_reply@email.apple.com", "Apple'dan makbuzunuz", APPLE_RECEIPT_TR, "<r2@x>"), to,
                       verify=lambda raw: set())
    assert r["status"] == "skip"
    # mağazadan ama makbuz değil
    r = inbound.handle(_mail("no_reply@email.apple.com", "Apple Hesabı güncellemesi", "Bilgilendirme", "<r3@x>"), to,
                       verify=lambda raw: {"email.apple.com"})
    assert r["status"] == "skip" and r["detail"] == "Mağaza makbuzu değil"


def test_receipt_forwarded_by_user(tmpdb):
    """Kullanıcının kendi adresinden elle ilettiği Google Play makbuzu (ekstrede henüz yok → bekleyen)."""
    from app import auth, db, inbound, store_subs, system

    owner = auth.create_user("ornek@example.com", "ornek-sifre-12345")
    to = inbound.address(owner)
    r = inbound.handle(_mail("ornek@example.com", "Fwd: Google Play sipariş makbuzunuz", PLAY_RECEIPT_TR, "<f1@x>"), to,
                       verify=lambda raw: {"example.com"})
    assert r["status"] == "ok" and "henüz ekstrede yok" in r["detail"]
    with db.use_space(system.user_db_path(owner), owner):
        items = store_subs.items(date(2026, 11, 10))
        assert [i["title"] for i in items] == ["Google One"] and items[0]["pending"]


# ---------------------------------------------------------------- uçlar, katalog, ortak defter

def test_api_flow(client, apple_two):
    j = client.get("/api/subscriptions/apps").json()
    assert j["available"] and len(j["items"]) == 2 and j["totals"]["text"].startswith("Uygulama abonelikleri: ayda ₺59,99")
    assert j["help"]["steps"]["apple"][2] == "Abonelikler'e dokun"
    assert j["help"]["open"]["apple"]["app_url"] == "itms-apps://apps.apple.com/account/subscriptions"
    mon = next(i for i in j["items"] if i["cycle"] == "aylik")
    r = client.put(f"/api/subscriptions/apps/{mon['id']}", json={"action": "adlandir", "catalog_id": "apple_music"})
    assert r.status_code == 200 and r.json()["item"]["title"] == "Apple Music"
    assert client.put(f"/api/subscriptions/apps/{mon['id']}", json={"action": "adlandir", "catalog_id": "yok"}).status_code == 422
    assert client.put("/api/subscriptions/apps/yok", json={"action": "degil"}).status_code == 404
    r = client.put(f"/api/subscriptions/apps/{mon['id']}", json={"action": "iptal", "date": "2026-11-01"})
    assert r.json()["item"]["status"] == "iptal" and r.json()["item"]["cancelled_at"] == "2026-11-01"
    # elle ekle (katalogdan), düzelt, sil
    r = client.post("/api/subscriptions/apps/manual", json={"catalog_id": "netflix", "amount": 289.99, "cycle": "aylik",
                                                           "next_date": "2026-12-01", "store": "diger"})
    assert r.status_code == 200
    sid = r.json()["source_id"]
    other = client.get("/api/subscriptions/apps").json()["other"]
    assert other[0]["title"] == "Netflix" and other[0]["first_text"].endswith("elle eklendi")
    assert client.patch(f"/api/subscriptions/apps/sources/{sid}", json={"amount": 379.99}).json()["ok"]
    assert client.get("/api/subscriptions/apps").json()["other"][0]["amount"] == 379.99
    assert client.delete(f"/api/subscriptions/apps/sources/{sid}").json()["ok"]
    assert client.get("/api/subscriptions/apps").json()["other"] == []
    cat = client.get("/api/subscriptions/catalog").json()
    ids = {x["id"] for x in cat["items"]}
    assert {"netflix", "spotify", "youtube_premium", "disney_plus", "amazon_prime", "exxen", "blutv", "gain", "tod",
            "bein_connect", "apple_music", "icloud", "apple_one", "google_one", "microsoft_365", "chatgpt", "tabii"} <= ids
    assert all("patterns" not in x for x in cat["items"])
    assert "store_subscriptions" in client.get("/api/meta").json()["capabilities"]


def test_catalog_match():
    from app import sub_catalog

    assert sub_catalog.match_name("YouTube Premium (Aylık)") == "youtube_premium"
    assert sub_catalog.match_name("YouTube Music") == "youtube_music"
    assert sub_catalog.match_name("200 GB depolama alanlı iCloud+") == "icloud"
    assert sub_catalog.match_name("Maximum Fitness") is None
    assert sub_catalog.match_description("EXXEN DIJITAL") == "exxen"
    assert sub_catalog.CATALOG[0]["fg"] in ("#111111", "#FFFFFF")
    assert sub_catalog.BY_ID["exxen"]["fg"] == "#111111"  # sarı zeminde koyu yazı


def test_member_hidden_page_and_pending(tmpdb):
    """Ortak defter: Tasarruf kapalıysa uçlar kapalı; üyeye ekstreye düşmemiş kaynaklar gösterilmez."""
    from app import book, store_subs

    _load(tmpdb, _months(2026, range(8, 12), 14, "APPLE.COM/BILL", 59.99))
    store_subs.save_source("ekran", {"name": "iCloud+", "catalog_id": "icloud", "store": "apple", "amount": 129.99,
                                     "cycle": "aylik", "date": date(2026, 12, 3), "active": True}, date(2026, 11, 12))
    assert len(store_subs.items(TODAY)) == 2
    member = {"id": 7, "username": "uye", "member_of": 1, "book_role": "full"}
    with tmpdb.use_space(tmpdb.db_path(), member):
        assert book.member() and [i["pending"] for i in store_subs.items(TODAY)] == [False]
        book.set_rules({"hidden_pages": ["tasarruf"]})
        assert book.denied("GET", "/api/subscriptions/apps") == "Bu sayfa ortak defterde kapalı"
        assert book.denied("POST", "/api/subscriptions/apps/screenshot")


# ---------------------------------------------------------------- okuma hakkı (hazır sürüm)

from tests import test_plans  # noqa: E402

hosted = test_plans.hosted


def test_screenshot_costs_one_read_each(hosted):
    """Hazır sürüm: her ekran görüntüsü 1 okuma hakkı (metinle okunsa da); aynı görüntü tekrar → önbellek, hak düşmez;
    hak dolunca okunmaz (502, quota_full)."""
    from tests.test_doc_reads import _jpeg, _reads, _set_reads
    from tests.test_plans import _user

    c, u = _user(hosted)
    from app import system

    system.update_user(u["id"], plan="premium", plan_until=None)
    before = _reads(c)["reads"]["used"]
    img = _jpeg()
    r = c.post("/api/subscriptions/apps/screenshot", files=[("files", ("a.jpg", img, "image/jpeg"))],
               data={"ocr_text": IOS_EN})
    assert r.status_code == 200, r.text
    assert r.json()["pending"] == 2 and _reads(c)["reads"]["used"] == before + 1
    r = c.post("/api/subscriptions/apps/screenshot", files=[("files", ("a.jpg", img, "image/jpeg"))],
               data={"ocr_text": IOS_EN})
    assert r.json()["read"][0]["source"] == "onbellek" and _reads(c)["reads"]["used"] == before + 1
    _set_reads(u["id"], day=999)
    from PIL import Image
    import io

    buf = io.BytesIO()
    Image.new("RGB", (50, 70), "white").save(buf, "JPEG")
    r = c.post("/api/subscriptions/apps/screenshot", files=[("files", ("b.jpg", buf.getvalue(), "image/jpeg"))],
               data={"ocr_text": PLAY_TR})
    assert r.status_code == 502 and r.json().get("quota_full")
