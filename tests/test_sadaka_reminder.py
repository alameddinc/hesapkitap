"""Sadaka hatırlatması, süreklilik ve gelire oranı (app/sadaka_reminder.py). Seçim yokken kapalı + "Hayırlı Cumalar"
bildirimi (en çok 2: ilk Cuma, kayıt olmazsa sonraki ayın ilk Cuması), kayıttan sonra "Her Cuma hatırlatalım mı?",
ritim (Her Cuma / Ayda bir / Hatırlatma), Cuma'dan Cuma'ya hafta, süreklilik, son sadaka (bağış kayıtları + "Bağış &
Sadaka" giderleri; zekât sayılmaz), sessiz saat, tekrar yok, rıza / özellik kapalı, ortak defter (kişi başı, gizli sayfa),
gelire oranı (faiz geliri hariç, gelirsiz ay, eşik, özet cümlesi, ortak defter). Kişiler ve tutarlar uydurma."""
import json

import pytest
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from tests.test_book import _member, owner  # noqa: F401 - fixture
from tests.test_push import FCM_TOKEN, keys  # noqa: F401 - fixture

TZ = ZoneInfo("Europe/Istanbul")
FRI = date(2026, 11, 13)       # Cuma (ayın ikinci Cuması); haftası Cumartesi 7 – Cuma 13 Kasım
FIRST_FRI = date(2026, 11, 6)  # ayın ilk Cuması


def _don(day: str, kind: str = "sadaka", amount: float = 100):
    from app import donations

    return donations.add(kind, amount, day)


def _entry(db, day: str, amount: float = 250, kind: str = "gider", category: str = "Bağış & Sadaka", desc: str = "Örnek Vakfı",
           created_by=None):
    data = {"entry_date": day, "description": desc, "amount": amount, "kind": kind, "category": category}
    if created_by:
        data["created_by"] = created_by
    return db.create_entry(data)


def _card_txn(db, day: str, desc: str = "ORNEK DERNEK BAGIS", card="5400******9602"):
    with db.connect() as c:
        sid = c.execute("INSERT INTO statements (file_hash, bank, card_no, period_end, due_date, total_debt) "
                        "VALUES (?, 'VakıfBank', ?, '2026-11-24', '2026-12-05', 1000)", (f"h-{card}-{day}", card)).lastrowid
        return c.execute("INSERT INTO transactions (statement_id, txn_date, description, amount, txn_type, category) "
                         "VALUES (?, ?, ?, 300, 'harcama', 'Bağış & Sadaka')", (sid, day, desc)).lastrowid


# ---------------------------------------------------------------- seçim yokken

@pytest.fixture(autouse=True)
def _new_app(monkeypatch, request):
    """İlk Cuma bildirimi yalnız 1.0.7+ telefonu olana: sürüm süzgecini sınayan test dışında hepsi yeni sürüm varsayar."""
    from app import sadaka_reminder as sr

    if request.node.name != "test_offer_only_to_new_app_version":
        monkeypatch.setattr(sr, "_has_new_app", lambda uid: True)


def test_offer_only_to_new_app_version(client, monkeypatch):
    from app import push
    from app import sadaka_reminder as sr

    assert sr._ver("1.0.7 (39)") == (1, 0, 7) and sr._ver("1.0.10") > sr._ver("1.0.7") and sr._ver("") == ()
    devs = [{"app_version": "1.0.6 (38)"}]
    monkeypatch.setattr(push, "devices", lambda uid: devs)
    monkeypatch.setattr(sr, "_uid", lambda: 7)
    assert sr.push_items(FRI) == [] and sr._state(7)["offers"] == 0  # eski sürüm: gönderilmez, sayaç artmaz
    devs.append({"app_version": "1.0.7 (39)"})
    [it] = sr.push_items(FRI)
    assert it["id"] == "sadaka:cuma:1"


def test_monthly_title_is_plain(client):
    from app import sadaka_reminder as sr

    assert sr.push_text("aylik", sr.FRIDAY, None)[0] == "Sadaka hatırlatması"
    assert sr.push_text("haftalik", sr.FRIDAY, None)[0] == "Hayırlı Cumalar"

def test_unchosen_is_off_and_asks_after_save(client):
    from app import sadaka_reminder as sr

    _don("2026-10-20")  # kaydı olsa da seçim yapılmadan düzenli hatırlatma ve Özet kartı yok
    s = sr.status(FRI)
    assert s["available"] and s["rhythm"] == "kapali" and not s["chosen"] and s["show"] is False and s["title"] is None
    assert s["ask_after_save"] and s["ask_title"] == "Her Cuma hatırlatalım mı?"
    assert [c["label"] for c in s["choices"]] == ["Her Cuma", "Ayda bir", "Hatırlatma"]
    assert [c["rhythm"] for c in s["choices"]] == ["haftalik", "aylik", "kapali"] and s["choices"][0]["recommended"]
    assert s["last_date"] == "2026-10-20" and s["days_since"] == 24
    sr.set_pref("kapali")
    assert sr.status(FRI)["ask_after_save"] is False  # seçti: bir daha sorulmaz


def test_first_friday_offer_then_next_months_first_friday_max_two(client):
    from app import sadaka_reminder as sr

    assert sr.push_items(date(2026, 11, 12)) == []  # Perşembe
    [it] = sr.push_items(FRI)  # özelliği açık gördüğü ilk Cuma (ayın ikinci Cuması da olsa)
    assert it["kind"] == "sadaka" and it["id"] == "sadaka:cuma:1" and it["route"] == "#/diger/zekat/sadaka"
    assert (it["title"], it["body"]) == ("Hayırlı Cumalar", "Bu hafta sadaka verdiysen not edelim mi?")
    assert sr.push_items(FRI) == []                    # aynı gün tekrar yok
    assert sr.push_items(date(2026, 11, 20)) == []     # sonraki Cuma değil
    assert sr.push_items(date(2026, 11, 27)) == []
    [it2] = sr.push_items(date(2026, 12, 4))           # Aralık'ın ilk Cuması: ikinci ve son
    assert it2["id"] == "sadaka:cuma:2" and it2["title"] == "Hayırlı Cumalar"
    assert sr.push_items(date(2027, 1, 1)) == [] and sr.push_items(date(2027, 2, 5)) == []  # en çok 2: susar
    assert sr.status(date(2027, 2, 5))["rhythm"] == "kapali"


def test_offer_skipped_when_recorded(client):
    from app import sadaka_reminder as sr

    _don("2026-11-07")  # bu haftanın (Cumartesi–Cuma) kaydı var: o Cuma gitmez, sayılmaz
    assert sr.push_items(FRI) == [] and sr._state(None)["offers"] == 0
    [it] = sr.push_items(date(2026, 11, 20))
    assert it["id"] == "sadaka:cuma:1"
    _don("2026-11-25")  # ilk bildirimden sonra kayıt yaptı (soruyu formda gördü): ikinci bildirim gitmez
    assert sr.push_items(date(2026, 12, 4)) == [] and sr.push_items(date(2027, 1, 1)) == []


def test_offer_stops_after_choice_and_weekly_starts(client):
    from app import sadaka_reminder as sr

    [it] = sr.push_items(FIRST_FRI)
    assert it["id"] == "sadaka:cuma:1"
    sr.set_pref("haftalik")
    s = sr.status(FRI)
    assert s["chosen"] and s["show"] and s["tone"] == "empty" and not s["ask_after_save"]
    assert s["title"] == "Henüz sadaka kaydın yok" and s["text"] == "Bu Cuma için henüz kayıt yok"
    [it] = sr.push_items(FRI)
    assert it["id"] == "sadaka:h:2026-11-13" and it["route"] == "#/diger/zekat/sadaka"
    assert (it["title"], it["body"]) == ("Hayırlı Cumalar", "Bu hafta sadaka verdiysen not edelim mi?")


def test_offer_none_when_feature_off_then_after_enable(client):
    from app import db, sadaka_reminder as sr

    db.set_setting("modules", json.dumps({"zekat": False}))
    assert sr.push_items(FRI) == [] and sr.status(FRI)["ask_after_save"] is False
    db.set_setting("modules", json.dumps({"zekat": True}))
    assert sr.push_items(date(2026, 11, 20))[0]["id"] == "sadaka:cuma:1"  # açtıktan sonraki ilk Cuma


# ---------------------------------------------------------------- ritim, hafta, süreklilik

def test_weekly_is_friday_to_friday(client):
    from app import sadaka_reminder as sr

    sr.set_pref("haftalik")
    assert sr.period("haftalik", date(2026, 11, 7)) == (date(2026, 11, 7), "2026-11-13")   # Cumartesi: yeni hafta
    assert sr.period("haftalik", FRI) == (date(2026, 11, 7), "2026-11-13")                 # Cuma: haftanın son günü
    assert sr.period("haftalik", date(2026, 11, 6))[1] == "2026-11-06"                     # önceki Cuma önceki hafta
    _don("2026-11-06")  # Cuma verildi
    s = sr.status(date(2026, 11, 6))
    assert s["in_period"] and (s["title"], s["text"], s["tone"]) == ("Bu hafta sadakan kayıtlı", "Son kayıt bugün", "ok")
    s = sr.status(date(2026, 11, 7))  # Cumartesi: yeni hafta, bu Cuma için kayıt yok (suçlama yok)
    assert not s["in_period"] and (s["title"], s["text"]) == ("Son sadakan dün", "Bu Cuma için henüz kayıt yok")
    assert s["next_reminder"] == "2026-11-13" and s["when"] == "Her Cuma sabahı"


def test_streak_weekly_and_monthly(client):
    from app import sadaka_reminder as sr

    for d in ("2026-10-16", "2026-10-23", "2026-10-30", "2026-11-06"):  # dört Cuma üst üste
        _don(d)
    sr.set_pref("haftalik")
    s = sr.status(date(2026, 11, 10))  # bu haftanın (7–13) kaydı yok: önceki 4 hafta sayılır, "bozuldu" denmez
    assert s["streak"] == 4 and s["streak_text"] == "4 Cumadır sadakan kayıtlı"
    assert s["text"] == "Bu Cuma için henüz kayıt yok" and "bozul" not in json.dumps(s, ensure_ascii=False)
    [it] = sr.push_items(FRI)
    assert it["body"] == "4 Cumadır sadakan kayıtlı · bu haftakini not edelim mi?" and it["title"] == "Hayırlı Cumalar"
    _don("2026-11-12")
    assert sr.status(FRI)["streak"] == 5
    # iki hafta kaçtı: süreklilik yok (satır da yok), yalnız "henüz kayıt yok"
    s = sr.status(date(2026, 11, 28))
    assert s["streak"] == 0 and s["streak_text"] is None
    # Cuma sınırı: Cumartesi verilen, ertesi Cuma'nın haftasına sayılır
    assert sr.streak("haftalik", 4, [date(2026, 11, 14), date(2026, 11, 13)], date(2026, 11, 20)) == 2
    assert sr.streak("haftalik", 4, [date(2026, 11, 21), date(2026, 11, 13)], date(2026, 11, 27)) == 1
    # süreklilik kapalı: satır ve bildirimdeki sayı yok
    sr.set_pref(show_streak=False)
    s = sr.status(date(2026, 11, 13))
    assert s["streak"] == 5 and s["streak_text"] is None and not s["show_streak"]
    # aylık
    sr.set_pref("aylik", show_streak=True)
    for d in ("2026-08-03", "2026-09-10"):
        _don(d)
    s = sr.status(date(2026, 12, 2))  # Ağustos–Kasım kayıtlı, Aralık henüz yok
    assert s["streak"] == 4 and s["streak_text"] == "4 aydır her ay sadakan kayıtlı"
    assert s["text"] == "Bu ay için henüz kayıt yok"
    [it] = sr.push_items(date(2026, 12, 4))
    assert it["id"] == "sadaka:a:2026-12" and it["body"] == "Bu ay sadaka verdiysen not edelim mi?"


def test_monthly_texts_and_explicit_off(client):
    from app import sadaka_reminder as sr

    _don("2026-10-20")
    sr.set_pref("aylik")
    s = sr.status(FRI)
    assert s["show"] and s["day"] == 4 and s["period_key"] == "2026-11" and not s["in_period"]
    assert (s["title"], s["text"], s["tone"]) == ("Son sadakan 24 gün önce", "Bu ay için henüz kayıt yok", "nudge")
    assert s["when"] == "Her ayın ilk Cuması" and s["next_reminder"] == "2026-12-04"
    _don("2026-11-02")
    assert sr.status(FRI)["title"] == "Bu ay sadakan kayıtlı"
    sr.set_pref("kapali")
    s = sr.status(FRI)
    assert s["rhythm"] == "kapali" and s["chosen"] and s["show"] is False and s["title"] is None
    assert sr.push_items(date(2026, 12, 4)) == [] and sr.push_items(FRI) == []


def test_future_dated_and_long_ago_texts(client):
    from app import sadaka_reminder as sr

    _don("2026-12-01")  # gelecek tarih sayılmaz
    sr.set_pref("aylik")
    assert sr.status(FRI)["last_date"] is None
    _don("2026-06-01")
    assert sr.status(FRI)["title"] == "Son sadakan 5 ay önce"
    assert sr.ago(400) == "bir yıldan uzun süre önce" and sr.ago(44) == "44 gün önce"


# ---------------------------------------------------------------- kaynaklar

def test_category_expense_counts_and_zakat_does_not(client, tmpdb):
    from app import donations, sadaka_reminder as sr, zakat

    eid = _entry(tmpdb, "2026-11-03")
    assert sr.dates(FRI) == [date(2026, 11, 3)]  # işaretlenmemiş "Bağış & Sadaka" gideri sayılır
    me = next(p for p in client.get("/api/zakat").json()["people"] if p["is_self"])["id"]
    client.patch(f"/api/zakat/people/{me}", json={"hawl_start": "2026-03-01"})
    donations.mark("entry", eid, "zekat")  # zekât olarak işaretlendi: sayılmaz
    assert sr.dates(FRI) == []
    # zekât ödemesinin açtığı gider kaydı ("Bağış & Sadaka" kategorisinde) da sayılmaz
    zakat.add_payment(me, 1500, "2026-11-04", None, payment="nakit")
    assert any(e["category"] == "Bağış & Sadaka" and e["entry_date"] == "2026-11-04" for e in tmpdb.list_entries())
    assert sr.dates(FRI) == []
    # kart ekstresindeki bağış satırı sayılır; zekât işaretlenirse sayılmaz
    tid = _card_txn(tmpdb, "2026-11-05")
    assert sr.dates(FRI) == [date(2026, 11, 5)]
    donations.mark("card", tid, "zekat")
    assert sr.dates(FRI) == []
    donations.mark("card", tid, "sadaka")
    led = sr.ledger(FRI)
    assert [(x["date"], x["amount"]) for x in led] == [(date(2026, 11, 5), 300)]  # hareket + işaret tek sayılır
    # gider olarak da yazılan elle sadaka: gider kaydıyla tek
    donations.add("sadaka", 80, "2026-11-06", payment="nakit")
    assert sorted(x["amount"] for x in sr.ledger(FRI)) == [80, 300]


def test_api_get_put_and_validation(client):
    today = date.today()
    r = client.get("/api/donations/reminder")
    assert r.status_code == 200 and r.json()["rhythm"] == "kapali" and r.json()["rhythms"]["haftalik"] == "Her Cuma"
    assert client.post("/api/donations", json={"kind": "sadaka", "amount": 50,
                                               "given_date": today.isoformat()}).status_code == 200
    s = client.get("/api/donations/reminder").json()
    assert s["rhythm"] == "kapali" and s["ask_after_save"] and s["title"] is None
    s = client.put("/api/donations/reminder", json={"rhythm": "aylik"}).json()
    assert s["rhythm"] == "aylik" and s["in_period"] and s["title"] == "Bu ay sadakan kayıtlı" and not s["ask_after_save"]
    assert client.put("/api/donations/reminder", json={"rhythm": "gunluk"}).status_code == 422
    assert client.put("/api/donations/reminder", json={"rhythm": "haftalik", "day": 7}).status_code == 422
    s = client.put("/api/donations/reminder", json={"show_streak": False}).json()
    assert s["rhythm"] == "aylik" and s["show_streak"] is False  # yalnız gelen alan değişir
    s = client.put("/api/donations/reminder", json={"rhythm": "haftalik"}).json()
    assert s["rhythm"] == "haftalik" and s["chosen"] and s["title"] == "Bu hafta sadakan kayıtlı"
    caps = client.get("/api/meta").json()["capabilities"]
    assert "sadaka_reminder" in caps and "sadaka_ratio" in caps


def test_module_off_or_no_consent(client, monkeypatch):
    from app import db, sadaka_reminder as sr

    _don("2026-10-20")
    sr.set_pref("aylik")
    db.set_setting("modules", json.dumps({"zekat": False}))
    s = sr.status(FRI)
    assert s["available"] is False and s["show"] is False and s["last_date"] is None and not s["ask_after_save"]
    assert sr.push_items(date(2026, 12, 4)) == [] and sr.ratio(6, FRI)["available"] is False
    assert client.put("/api/donations/reminder", json={"rhythm": "haftalik"}).status_code == 409
    # hazır sürüm: zekât rızası verilmeden özellik kapalı (modules seçilmemiş): seçimsiz Cuma bildirimi de yok
    db.set_setting("modules", None)
    db.set_setting(sr.LOCAL_KEY, None)  # seçim yok
    monkeypatch.setenv("HESAPKITAP_HOSTED", "1")
    assert sr.available() is False and sr.push_items(FRI) == []
    monkeypatch.delenv("HESAPKITAP_HOSTED")
    assert sr.available() is True and len(sr.push_items(FRI)) == 1


# ---------------------------------------------------------------- zamanlama

def test_push_only_on_reminder_day_and_once_per_period(client, keys):  # noqa: F811
    from app import push, sadaka_reminder as sr, system

    _don("2026-10-20")
    sr.set_pref("aylik")  # ayın ilk Cuması
    assert sr.push_items(date(2026, 11, 5)) == []   # Perşembe
    assert sr.push_items(FRI) == []                 # ikinci Cuma
    [it] = sr.push_items(FIRST_FRI)
    assert it["id"] == "sadaka:a:2026-11" and it["title"] == "Sadaka hatırlatması"  # aylıkta selam yok (11.11)
    sr.set_pref("haftalik")
    assert sr.push_items(date(2026, 11, 12)) == []  # Perşembe
    [it] = sr.push_items(FRI)
    assert it["id"] == "sadaka:h:2026-11-13" and it["body"] == "Bu hafta sadaka verdiysen not edelim mi?"
    u = system.create_user("ayse@ornek.com", None)
    now = datetime(2026, 11, 13, 9, 30, tzinfo=TZ)
    assert len(push.notify(u["id"], [it], now)) == 1
    assert push.notify(u["id"], [it], now + timedelta(hours=3)) == []  # aynı dönem için bir kez
    _don("2026-11-10")  # bu hafta verildiyse yok
    assert sr.push_items(FRI) == []
    push.set_prefs(u["id"], {"types": {"sadaka": False}})
    assert push.notify(u["id"], [{**it, "id": "sadaka:h:2026-11-20"}], now) == []
    assert push.TYPES["sadaka"] == ("Sadaka hatırlatması", True) and push.NOUNS["sadaka"] == "sadaka hatırlatması"


def test_daily_job_friday_morning_and_quiet_hours(owner, keys):  # noqa: F811
    from app import push, sadaka_reminder as sr, system

    admin = system.user_by_name("admin")
    push.register(admin["id"], FCM_TOKEN, "android")
    assert owner.post("/api/donations", json={"kind": "sadaka", "amount": 40, "given_date": "2026-10-30"}).status_code == 200
    assert owner.put("/api/donations/reminder", json={"rhythm": "haftalik"}).status_code == 200
    push.set_prefs(admin["id"], {"quiet": {"from": "22:00", "to": "10:00"}})  # Cuma sabahı 10:00'a kadar sessiz
    assert push.run_daily(datetime(2026, 11, 12, 9, 30, tzinfo=TZ)) == {admin["id"]: 0}  # Perşembe: yok
    assert push.run_daily(datetime(2026, 11, 13, 9, 5, tzinfo=TZ)) == {admin["id"]: 1}
    [q] = push.queue(admin["id"])
    assert q["payload"]["kind"] == "sadaka" and q["payload"]["id"] == "sadaka:h:2026-11-13"
    assert datetime.fromtimestamp(q["not_before"], TZ) == datetime(2026, 11, 13, 10, 0, tzinfo=TZ)
    assert push.flush(datetime(2026, 11, 13, 9, 30, tzinfo=TZ)) == 0
    assert push.flush(datetime(2026, 11, 13, 10, 1, tzinfo=TZ)) == 1
    msg = json.loads(keys.by_host("fcm")[0].content)["message"]
    assert msg["data"]["route"] == "#/diger/zekat/sadaka" and msg["notification"]["title"] == "Hayırlı Cumalar"
    assert "₺" not in msg["notification"]["body"] and "TL" not in msg["notification"]["body"]  # tutar yok
    assert push.run_daily(datetime(2026, 11, 13, 15, 0, tzinfo=TZ)) == {}  # aynı gün ikinci çalıştırma yok
    assert push.run_daily(datetime(2026, 11, 20, 9, 30, tzinfo=TZ)) == {admin["id"]: 1}  # yeni hafta
    assert sr.period("haftalik", date(2026, 11, 20))[1] == "2026-11-20"


def test_daily_job_offer_once_and_not_when_type_off(owner, keys):  # noqa: F811
    from app import db, push, sadaka_reminder as sr, system

    admin = system.user_by_name("admin")
    push.register(admin["id"], FCM_TOKEN, "android")
    push.set_prefs(admin["id"], {"types": {"sadaka": False}})
    assert push.run_daily(datetime(2026, 11, 13, 9, 30, tzinfo=TZ)) == {admin["id"]: 0}
    with db.use_space(None, admin):
        assert sr._state(admin["id"])["offers"] == 0  # tür kapalıyken sayılmaz
    push.set_prefs(admin["id"], {"types": {"sadaka": True}})
    assert push.run_daily(datetime(2026, 11, 20, 9, 30, tzinfo=TZ)) == {admin["id"]: 1}
    [q] = push.queue(admin["id"])
    assert q["payload"]["id"] == "sadaka:cuma:1" and q["payload"]["title"] == "Hayırlı Cumalar"
    assert q["payload"]["route"] == "#/diger/zekat/sadaka"
    assert push.run_daily(datetime(2026, 11, 27, 9, 30, tzinfo=TZ)) == {admin["id"]: 0}


# ---------------------------------------------------------------- ortak defter

def test_shared_book_each_person_own_sadaka_and_rhythm(owner, keys):  # noqa: F811
    from app import db, sadaka_reminder as sr, system

    m = _member(owner, role="full")
    admin, esim = system.user_by_name("admin"), system.user_by_name("esim")
    assert owner.post("/api/donations", json={"kind": "sadaka", "amount": 40, "given_date": "2026-11-02"}).status_code == 200
    assert m.post("/api/donations", json={"kind": "sadaka", "amount": 20, "given_date": "2026-10-01"}).status_code == 200
    _entry(db, "2026-10-15", created_by=esim["id"])  # "Bağış & Sadaka" gideri: ekleyenin (üye)
    # ikisi de seçim yapmadı: ikisine de ayrı Cuma bildirimi (kişi başı sayaç)
    with db.use_space(None, admin):
        assert [i["id"] for i in sr.push_items(date(2026, 11, 20))] == ["sadaka:cuma:1"]
    with db.use_space(None, esim):
        assert [i["id"] for i in sr.push_items(date(2026, 11, 20))] == ["sadaka:cuma:1"]
    # herkes kendi ritmi
    assert owner.put("/api/donations/reminder", json={"rhythm": "aylik"}).json()["rhythm"] == "aylik"
    assert m.put("/api/donations/reminder", json={"rhythm": "aylik"}).json()["rhythm"] == "aylik"
    with db.use_space(None, admin):
        assert sr.dates(FRI) == [date(2026, 11, 2)] and sr.status(FRI)["in_period"] is True
    with db.use_space(None, esim):
        assert sr.dates(FRI) == [date(2026, 10, 15), date(2026, 10, 1)]
        s = sr.status(FRI)
        assert not s["in_period"] and s["title"] == "Son sadakan 29 gün önce"
    assert m.put("/api/donations/reminder", json={"rhythm": "haftalik"}).json()["rhythm"] == "haftalik"
    assert owner.get("/api/donations/reminder").json()["rhythm"] == "aylik"
    # eski kayıt (created_by yok): defter sahibinin
    with db.connect() as c:
        c.execute("INSERT INTO donations (kind, amount, given_date) VALUES ('sadaka', 10, '2026-11-11')")
    with db.use_space(None, esim):
        assert date(2026, 11, 11) not in sr.dates(FRI)
    with db.use_space(None, admin):
        assert sr.dates(FRI)[0] == date(2026, 11, 11)


def test_member_viewer_can_choose_rhythm(owner):  # noqa: F811
    m = _member(owner, role="viewer")
    assert m.post("/api/donations", json={"kind": "sadaka", "amount": 20, "given_date": "2026-10-01"}).status_code == 403
    r = m.put("/api/donations/reminder", json={"rhythm": "aylik"})
    assert r.status_code == 200 and r.json()["chosen"] and r.json()["tone"] == "empty"


def test_member_hidden_page_gets_nothing(owner, keys):  # noqa: F811
    from app import db, sadaka_reminder as sr, system

    m = _member(owner, role="full")
    assert m.post("/api/donations", json={"kind": "sadaka", "amount": 20, "given_date": "2026-10-01"}).status_code == 200
    owner.put("/api/book/rules", json={"hidden_pages": ["zekat"]})
    for path in ("/api/donations/reminder", "/api/donations/ratio"):
        assert m.get(path).status_code == 403
    assert m.put("/api/donations/reminder", json={"rhythm": "haftalik"}).status_code == 403
    with db.use_space(None, system.user_by_name("esim")):
        assert sr.available() is False and sr.push_items(FRI) == [] and sr.status(FRI)["show"] is False
    with db.use_space(None, system.user_by_name("admin")):
        assert sr.available() is True


# ---------------------------------------------------------------- gelire oranı

def test_turkish_suffixes():
    from app import sadaka_reminder as sr

    assert [f"%{n}{sr.ablative(n)}" for n in ("2,1", "1,4", "6", "40", "3", "100", "0,5", "12")] == \
        ["%2,1'den", "%1,4'ten", "%6'dan", "%40'tan", "%3'ten", "%100'den", "%0,5'ten", "%12'den"]
    assert [f"%{n}{sr.dative(n)}" for n in ("1,4", "2,1", "2", "6", "9", "10", "1,3")] == \
        ["%1,4'e", "%2,1'e", "%2'ye", "%6'ya", "%9'a", "%10'a", "%1,3'e"]
    assert sr.pct(2.0) == "2" and sr.pct(1.44) == "1,4" and sr.pct(0.06) == "0,1"
    assert sr.ratio_sentence(210, 10000, 157.08, 11220) == \
        "Son 3 ayda gelirin %12 arttı; sadakanın gelire oranı %2,1'den %1,4'e indi."
    assert sr.ratio_sentence(140, 10000, 210, 10000) == \
        "Son 3 ayda gelirin aynı kaldı; sadakanın gelire oranı %1,4'ten %2,1'e çıktı."
    assert sr.ratio_sentence(200, 10000, 190, 9000) == \
        "Son 3 ayda gelirin %10 azaldı; sadaka oranın gelirine göre aynı kaldı."


def test_ratio_months_interest_excluded_and_no_income(client, tmpdb):
    from app import db, finance, sadaka_reminder as sr

    today = date(2026, 11, 13)
    cur = finance.current_period(today)
    ms = [finance.add_months(cur, -i) for i in range(7, 0, -1)]  # 7 tamamlanmış dönem
    for m in ms:
        d = db.occurrence_date(m, 15)
        _entry(tmpdb, d, 10000, kind="gelir", category="Maaş", desc="ORNEK AS MAAS")
        _entry(tmpdb, d, 900, kind="gelir", category="Diğer Gelir", desc="VADESIZ FAIZ")  # faiz geliri: tabana girmez
        _don(d, amount=200 if m in ms[:4] else 100)
    r = sr.ratio(6, today)
    assert r["available"] and r["ratio_available"] and len(r["months"]) == 6 and r["basis"] == "defter"
    last = r["months"][-1]
    assert last["partial"] and last["month"] == cur and last["income"] == 0 and last["ratio"] is None
    assert last["note"] == "gelir kaydı yok"
    row = next(x for x in r["months"] if x["month"] == ms[-1])
    assert row["income"] == 10000 and row["sadaka"] == 100 and row["ratio"] == 1.0 and row["note"] is None
    assert row["zakat"] == 0 and row["label"].split()[0] in sr.AY
    # son 3 dönem %1, önceki 3 dönem %2 (gelir aynı): oran indi
    assert r["sentence"] == "Son 3 ayda gelirin aynı kaldı; sadakanın gelire oranı %2'den %1'e indi."
    assert len(sr.ratio(12, today)["months"]) == 12


def test_ratio_threshold_and_missing_month_no_sentence(client, tmpdb):
    from app import db, finance, sadaka_reminder as sr

    today = date(2026, 11, 13)
    cur = finance.current_period(today)
    ms = [finance.add_months(cur, -i) for i in range(6, 0, -1)]
    for i, m in enumerate(ms):
        d = db.occurrence_date(m, 15)
        _entry(tmpdb, d, 999 if i == 2 else 8000, kind="gelir", category="Ek Gelir", desc="ORNEK SERBEST IS")
        _don(d, amount=50)
    r = sr.ratio(6, today)
    low = next(x for x in r["months"] if x["month"] == ms[2])
    assert low["ratio"] is None and low["note"] == "gelir kaydı yok" and low["sadaka"] == 50  # eşik altı: oran yok
    assert r["sentence"] is None  # karşılaştırma dönemlerinden birinde oran yok: cümle yok


def test_ratio_shared_book_member_needs_own_income(owner):  # noqa: F811
    from app import db, finance, sadaka_reminder as sr, system

    m = _member(owner, role="full")
    admin, esim = system.user_by_name("admin"), system.user_by_name("esim")
    today = date(2026, 11, 13)
    prev = finance.add_months(finance.current_period(today), -1)
    d = db.occurrence_date(prev, 15)
    _entry(db, d, 20000, kind="gelir", category="Maaş", desc="ORNEK AS MAAS")  # ekleyen yok: sahibin
    assert m.post("/api/donations", json={"kind": "sadaka", "amount": 300, "given_date": d}).status_code == 200
    assert owner.post("/api/donations", json={"kind": "sadaka", "amount": 400, "given_date": d}).status_code == 200
    with db.use_space(None, esim):
        r = sr.ratio(6, today)
        row = next(x for x in r["months"] if x["month"] == prev)
        assert not r["ratio_available"] and r["basis"] == "kisi" and r["sentence"] is None
        assert row["sadaka"] == 300 and row["ratio"] is None and row["income"] is None and row["zakat"] is None
        assert r["note"] == "Ortak defterde oran yalnız kendi gelir kaydın varsa hesaplanır."
    with db.use_space(None, admin):
        row = next(x for x in sr.ratio(6, today)["months"] if x["month"] == prev)
        assert row["sadaka"] == 400 and row["income"] == 20000 and row["ratio"] == 2.0
    _entry(db, d, 15000, kind="gelir", category="Maaş", desc="ORNEK LTD MAAS", created_by=esim["id"])
    with db.use_space(None, esim):
        row = next(x for x in sr.ratio(6, today)["months"] if x["month"] == prev)
        assert row["income"] == 15000 and row["ratio"] == 2.0  # yalnız kendi geliri
    assert m.get("/api/donations/ratio", params={"months": 6}).status_code == 200


def test_text_scan_tone():
    """Kullanıcıya giden bütün metinler: dini kalıp yok ("Hayırlı Cumalar" selamı sahibin isteği, tek istisna), tutar /
    "daha fazla" baskısı yok, "vermedin" / "bozuldu" gibi suçlayan ifade yok, rozet / puan yok."""
    from app import sadaka_reminder as sr

    out = [sr.GREETING, sr.OFFER_BODY, sr.ASK_TITLE, *[c["label"] + " " + c["hint"] for c in sr.CHOICES],
           *sr.RHYTHMS.values()]
    for rh in ("haftalik", "aylik"):
        for last, inp in ((None, False), (date(2026, 11, 1), False), (date(2026, 11, 12), True)):
            out += sr.texts(rh, 4, last, inp, FRI)
        out += sr.push_text(rh, 4, sr.streak_text(rh, 4, 4)) + sr.push_text(rh, 4, None)
        out += [sr.streak_text(rh, 4, 3) or "", sr.when(rh, 4) or ""]
    out += [sr.ratio_sentence(210, 10000, 157, 11220), sr.ratio_sentence(140, 10000, 210, 10000),
            sr.ratio_sentence(200, 10000, 190, 9000)]
    text = " | ".join(out).lower()
    allowed = text.replace("hayırlı cumalar", "")
    for bad in ("allah", "kabul", "inşallah", "hadis", "ayet", "sevap", "günah", "mübarek", "hayırlı",
                "vermedin", "bozul", "kaçırdın", "daha fazla", "daha çok", "₺", " tl", "rozet", "puan", "seri"):
        assert bad not in allowed, bad
    assert "hayırlı cumalar" in text
