"""Ödeme aracıları: benzer yazım toleransı, veriden aday (gece taraması), yönetici onayı/ret, ada bağlı ayar taşıma.

Bütün işyeri ve aracı adları uydurmadır ("YENIARACI", "ORNEK KITAP"); gerçek kişi / ekstre yok.
"""
from datetime import date, datetime

import pytest

from tests.test_uncategorized import _card, _month


@pytest.fixture(autouse=True)
def _inline_sync(monkeypatch):
    monkeypatch.setenv("HESAPKITAP_GATEWAY_SYNC", "inline")  # onay sonrası taşıma testte aynı iş parçacığında


def _second_book(rows):
    """İkinci kullanıcının kendi defteri + kart işlemleri."""
    from app import db, system

    u = system.create_user("ikinci", "x" * 60)
    with db.use_space(system.user_db_path(u), u):
        _card(db, rows)
    return u


# ---------------------------------------------------------------- benzer yazım

def test_fuzzy_one_edit_on_long_names_only():
    from app.textnorm import gateway_of, is_gateway, merchant_name, normalize, strip_gateway

    assert is_gateway("IYZICO") and is_gateway("IYZICOO") and is_gateway("IYYZICO")  # ekleme
    assert is_gateway("PAYTRR") and is_gateway("SIPAYY") and is_gateway("PAPRA")  # ekleme / silme
    assert is_gateway("PAPAEA")  # değiştirme
    assert not is_gateway("PAYT")  # 4 harfe inen silme: kısa ad benzer sayılmaz
    assert not is_gateway("MOKO") and not is_gateway("IYX")  # kısa aracıların (MOKA, IYZ) benzeri yok
    assert strip_gateway(normalize("Iyzicoo/Örnek Kitap")) == "ORNEK KITAP"
    assert merchant_name("PAYTRR/ORNEK OYUN") == "ORNEK OYUN"
    assert gateway_of("PAYTRR/ORNEK OYUN") == ("PAYTRR", "ORNEK OYUN")


def test_fuzzy_blocked_words_and_location_right_side():
    from app.textnorm import is_gateway, merchant_name

    assert not is_gateway("TESLA") and not is_gateway("IDEAL") and not is_gateway("PARAF")
    assert merchant_name("TESLA/ORNEK SARJ") == "TESLA"
    # benzer yazım + sağda yalnız şehir: aracı değil, işyeri + şube
    assert merchant_name("SIPAYY/ISTANBUL") == "SIPAYY"
    assert merchant_name("SIPAY/ISTANBUL") == "ISTANBUL"  # tam eşleşmede eski davranış aynen


def test_seed_names_and_not_generic():
    from app.textnorm import PAYMENT_GATEWAYS, merchant_name

    assert merchant_name("QNBPAY/ORNEK ELEKTRONIK") == "ORNEK ELEKTRONIK"
    assert merchant_name("MONEYPAY*ORNEK KAFE") == "ORNEK KAFE"
    assert "PARA" not in PAYMENT_GATEWAYS and merchant_name("PARA/ORNEK") == "PARA"
    assert merchant_name("MIGROS/ISTANBUL") == "MIGROS" and merchant_name("A101/KADIKOY") == "A101"


def test_strip_is_fast():
    """strip_gateway sık çağrılıyor: benzer yazım ön hesaplı sözlükten, çağrı başına mikro saniyeler."""
    import time

    from app.textnorm import strip_gateway

    t0 = time.perf_counter()
    for _ in range(20000):
        strip_gateway("IYZICOO/ORNEK MARKET ISTANBUL TR")
        strip_gateway("ORNEK MARKET ISTANBUL TR")
    assert time.perf_counter() - t0 < 1.0


# ---------------------------------------------------------------- aday taraması

def _candidate_rows(m):
    return [(f"{m}-03", "YENIARACI/ORNEK KITAP", 40, "Kategorisiz"),
            (f"{m}-04", "YENIARACI/ORNEK OYUN ISTANBUL TR", 20, "Kategorisiz"),
            (f"{m}-05", "YENIARACI*ORNEK SPOR", 30, "Kategorisiz"),
            (f"{m}-06", "ORNEKZINCIR/ISTANBUL", 10, "Kategorisiz"),  # zincir + şehir
            (f"{m}-07", "ORNEKZINCIR/KADIKOY", 10, "Kategorisiz"),
            (f"{m}-08", "ORNEKZINCIR/ANKARA", 10, "Kategorisiz"),
            (f"{m}-09", "ORNEKZINCIR/ORNEK SUBE", 10, "Kategorisiz")]


def test_scan_two_books_finds_candidate_not_city_chain(tmpdb):
    from app import gateways, system

    m = _month()
    _card(tmpdb, _candidate_rows(m))
    _second_book([(f"{m}-03", "YENIARACI/ORNEK KITAP", 45, "Kategorisiz"),
                  (f"{m}-04", "YENIARACI/ORNEK MOBILYA", 300, "Kategorisiz")])
    got = gateways.scan(date(2026, 11, 12))
    assert got["candidates"] == 1 and got["new"] == 1
    ov = gateways.overview()
    [c] = ov["candidates"]
    assert c["prefix"] == "YENIARACI" and c["merchants"] == 4 and c["books"] == 2 and c["rows"] == 5
    assert c["first_seen"] == "2026-11-12" and c["status"] == "bekliyor"
    assert c["samples"][0] == "ORNEK KITAP" and len(c["samples"]) <= 5  # iki defterde görülen önce
    assert "ORNEK OYUN" in c["samples"]  # konum sözcükleri atıldı
    # kişisel veri yok: tabloda tutar / tarih / kullanıcı sütunu yok
    with system.connect() as cx:
        cols = {r[1] for r in cx.execute("PRAGMA table_info(payment_gateways)")}
    assert not cols & {"amount", "user_id", "book_id", "txn_date", "description"}


def test_single_book_needs_five_merchants(tmpdb):
    from app import gateways

    m = _month()
    _card(tmpdb, [(f"{m}-0{i}", f"TEKDEFTER/ORNEK ISYERI {chr(65 + i)}", 10, "Kategorisiz") for i in range(1, 5)])
    assert gateways.scan(date(2026, 11, 12))["candidates"] == 0  # tek defter, 4 işyeri: aday değil
    _card(tmpdb, [(f"{m}-06", "TEKDEFTER/ORNEK ISYERI Z", 10, "Kategorisiz")])
    assert gateways.scan(date(2026, 11, 13))["candidates"] == 1


def test_known_prefixes_not_candidates(tmpdb):
    """Bilinen aracı (benzer yazım dahil), pazar yeri öneki ve bilinen zincir aday olmaz; iç içe aracıda baş aday."""
    from app import gateways

    m = _month()
    rows = []
    for i, w in enumerate(("A", "B", "C", "D", "E")):
        rows += [(f"{m}-01", f"IYZICOO/ORNEK {w}", 1, "Kategorisiz"), (f"{m}-02", f"GOOGLE *ORNEK {w}", 1, "Kategorisiz"),
                 (f"{m}-03", f"MIGROS/ORNEK {w}", 1, "Kategorisiz"), (f"{m}-04", f"IYZICO/ICARACI/ORNEK {w}", 1, "Kategorisiz")]
    _card(tmpdb, rows)
    gateways.scan(date(2026, 11, 12))
    assert [c["prefix"] for c in gateways.overview()["candidates"]] == ["ICARACI"]


def test_account_rows_counted_but_person_transfers_skipped(tmpdb):
    from app import db, gateways

    m = _month()
    with db.connect() as c:
        for i, w in enumerate(("A", "B", "C", "D", "E")):
            c.execute("INSERT INTO entries (entry_date, month, description, amount, kind, import_hash, note) "
                      "VALUES (?, ?, ?, 10, 'gider', ?, ?)", (f"{m}-0{i + 1}", m, f"Hesap {w}", f"h{i}",
                                                             f"Hesap hareketi · HESAPARACI/ORNEK {w}"))
        c.execute("INSERT INTO entries (entry_date, month, description, amount, kind, import_hash, counterparty) "
                  "VALUES (?, ?, 'KISIARACI/ORNEK KISI', 10, 'gider', 'hk', 'Örnek Kişi')", (f"{m}-07", m))
    gateways.scan(date(2026, 11, 12))
    assert [c["prefix"] for c in gateways.overview()["candidates"]] == ["HESAPARACI"]


def test_weekly_push_once(tmpdb, monkeypatch):
    from app import gateways, push

    sent = []
    monkeypatch.setattr(push, "system_alert", lambda pid, title, text, route=None: sent.append((pid, text, route)) or 1)
    m = _month()
    _card(tmpdb, _candidate_rows(m))
    _second_book([(f"{m}-03", "YENIARACI/ORNEK KITAP", 45, "Kategorisiz")])
    gateways.scan(date(2026, 11, 12))
    assert sent == [("sistem:odeme-araci:2026-11-12", "1 yeni ödeme aracı adayı", gateways.ROUTE)]
    _card(tmpdb, [(f"{m}-0{i}", f"IKINCIARACI/ORNEK {w}", 1, "Kategorisiz") for i, w in enumerate("ABCDE", 1)])
    gateways.scan(date(2026, 11, 14))  # yeni aday var ama hafta dolmadı
    assert len(sent) == 1
    gateways.scan(date(2026, 11, 19))
    assert sent[-1][1] == "1 yeni ödeme aracı adayı" and len(sent) == 2  # yalnız haber verilmemiş olan sayılır
    gateways.scan(date(2026, 11, 27))
    assert len(sent) == 2  # yeni aday yok


def test_nightly_once_per_day(tmpdb, monkeypatch):
    from app import gateways

    calls = []
    monkeypatch.setattr(gateways, "scan", lambda today=None: calls.append(today) or {})
    assert gateways.nightly(datetime(2026, 11, 12, 3)) is None
    gateways.nightly(datetime(2026, 11, 12, 4, 5))
    gateways.nightly(datetime(2026, 11, 12, 5))
    assert calls == [date(2026, 11, 12)]


# ---------------------------------------------------------------- onay / ret / elle

def test_approve_applies_without_deploy_and_reject_never_again(tmpdb):
    from app import gateways
    from app.textnorm import merchant_name

    m = _month()
    _card(tmpdb, _candidate_rows(m))
    _second_book([(f"{m}-03", "YENIARACI/ORNEK KITAP", 45, "Kategorisiz")])
    gateways.scan(date(2026, 11, 12))
    assert merchant_name("YENIARACI/ORNEK KITAP") == "YENIARACI"
    gateways.approve("yeniaracı")
    assert merchant_name("YENIARACI/ORNEK KITAP") == "ORNEK KITAP"
    assert merchant_name("YENIARACII/ORNEK KITAP") == "ORNEK KITAP"  # onaylananın benzer yazımı da
    ov = gateways.overview()
    assert not ov["candidates"] and [a["prefix"] for a in ov["approved"]] == ["YENIARACI"]
    gateways.reject("YENIARACI")  # onaylıyı kaldır → reddedildi
    assert merchant_name("YENIARACI/ORNEK KITAP") == "YENIARACI"
    gateways.scan(date(2026, 11, 13))
    assert not gateways.overview()["candidates"]  # reddedilen bir daha aday olmaz
    assert gateways.forget("YENIARACI")
    gateways.scan(date(2026, 11, 14))
    assert [c["prefix"] for c in gateways.overview()["candidates"]] == ["YENIARACI"]


def test_rejected_blocks_fuzzy_match():
    from app import gateways
    from app.textnorm import merchant_name

    assert merchant_name("PAYTRS/ORNEK") == "ORNEK"
    gateways.reject("PAYTRS")
    assert merchant_name("PAYTRS/ORNEK") == "PAYTRS"
    with pytest.raises(ValueError):
        gateways.reject("PAYTR")  # sabit liste buradan kaldırılmaz


def test_other_process_sees_approval_after_reload_window(monkeypatch):
    """Başka süreçte onay: textnorm sürüm damgasına GATEWAY_RELOAD_SECONDS'ta bir bakar."""
    from app import gateways, system, textnorm

    textnorm.current_gateways()
    with system.connect() as c:  # başka süreç yazmış gibi: doğrudan tablo + sürüm
        gateways._init(c)
        c.execute("INSERT INTO payment_gateways (prefix, status) VALUES ('UZAKARACI', 'onaylandi')")
        gateways._bump(c)
    assert textnorm.merchant_name("UZAKARACI/ORNEK") == "UZAKARACI"  # önbellek süresi dolmadı
    textnorm._gw["next"] = 0.0  # süre doldu
    assert textnorm.merchant_name("UZAKARACI/ORNEK") == "ORNEK"


def test_admin_api(client, tmpdb):
    m = _month()
    _card(tmpdb, _candidate_rows(m))
    _second_book([(f"{m}-03", "YENIARACI/ORNEK KITAP", 45, "Kategorisiz")])
    from app import gateways

    gateways.scan(date(2026, 11, 12))
    r = client.get("/api/admin/payment-gateways")
    assert r.status_code == 200 and r.json()["candidates"][0]["prefix"] == "YENIARACI"
    assert "IYZICO" in r.json()["builtin"] and r.json()["thresholds"]["single_book"] == 5
    p = client.get("/api/admin/payment-gateways/preview", params={"prefix": "ornekzincir"}).json()
    assert p == {"prefix": "ORNEKZINCIR", "known": False, "rows": 4, "merchants": 1, "samples": ["ORNEK"], "hidden": 0}
    assert client.get("/api/admin/payment-gateways/preview", params={"prefix": "IYZICO"}).json()["known"] is True
    assert client.get("/api/admin/payment-gateways/preview", params={"prefix": "//"}).status_code == 422
    r = client.post("/api/admin/payment-gateways/approve", json={"prefix": "YENIARACI"})
    assert r.status_code == 200 and r.json()["approved"][0]["prefix"] == "YENIARACI"
    r = client.post("/api/admin/payment-gateways", json={"prefix": "Elle Aracı"})
    assert {a["prefix"] for a in r.json()["approved"]} == {"YENIARACI", "ELLE ARACI"}
    assert r.json()["approved"][0]["source"] in ("elle", "veri")
    assert client.post("/api/admin/payment-gateways", json={"prefix": "IYZICO"}).status_code == 422
    r = client.post("/api/admin/payment-gateways/reject", json={"prefix": "ELLE ARACI"})
    assert [x["prefix"] for x in r.json()["rejected"]] == ["ELLE ARACI"]
    r = client.post("/api/admin/payment-gateways/forget", json={"prefix": "ELLE ARACI"})
    assert r.json()["rejected"] == []


def test_admin_api_requires_admin(client, monkeypatch):
    from app import config

    monkeypatch.setattr(config, "is_admin", lambda: False)
    assert client.get("/api/admin/payment-gateways").status_code == 403
    assert client.post("/api/admin/payment-gateways/approve", json={"prefix": "X1"}).status_code == 403


# ---------------------------------------------------------------- ada bağlı ayarlar

def test_approval_moves_name_keyed_settings_in_every_book(tmpdb):
    """Onaydan önce 'YENIARACI' adına konmuş abonelik durumu, kısma hedefi, öğrenilmiş kural ve sabit kalem anahtarı
    yeni (asıl satıcı) adlarla da eşleşir; tutar ve kategori değişmez."""
    from app import db, finance, gateways, system

    m = _month()
    _card(tmpdb, [(f"{m}-03", "YENIARACI/ORNEK DERGI", 49, "Dijital Abonelik"),
                  (f"{m}-04", "YENIARACI/ORNEK OYUN", 9, "Dijital Abonelik"),
                  (f"{m}-05", "DIGERARACI/ORNEK SPOR", 300, "Spor")])
    with db.connect() as c:
        c.execute("INSERT INTO subscription_state (merchant, status, changed_at) VALUES ('YENIARACI', 'iptal', ?)", (f"{m}-01",))
        c.execute("INSERT INTO savings_goals (kind, name, baseline, target, start_month) VALUES ('merchant', 'DIGERARACI', 300, 200, ?)", (m,))
        c.execute("INSERT INTO match_rules (source, pattern, entry_key, category, hits) VALUES ('ekstre', 'YENIARACI', 'DERGI', 'Dijital Abonelik', 3)")
        c.execute("INSERT INTO recurring (description, amount, kind, start_month, match_key) VALUES ('Spor salonu', 300, 'gider', '2026-01', 'm:DIGERARACI')")
        totals = tuple(c.execute("SELECT SUM(amount), COUNT(*), GROUP_CONCAT(category) FROM transactions").fetchone())
    u = _second_book([(f"{m}-03", "YENIARACI/ORNEK DERGI", 49, "Dijital Abonelik")])
    with db.use_space(system.user_db_path(u), u):
        db.set_subscription_state("YENIARACI", "dusuruldu", f"{m}-01", new_amount=19)
    gateways.add("YENIARACI")
    gateways.add("DIGERARACI")
    states = db.subscription_states()
    assert states["ORNEK DERGI"]["status"] == states["ORNEK OYUN"]["status"] == "iptal" and "YENIARACI" in states
    with db.connect() as c:
        assert {r[0] for r in c.execute("SELECT name FROM savings_goals")} == {"ORNEK SPOR"}
        assert {r[0] for r in c.execute("SELECT pattern FROM match_rules")} == {"YENIARACI", "ORNEK DERGI", "ORNEK OYUN"}
        assert c.execute("SELECT match_key FROM recurring").fetchone()[0] == "m:ORNEKSPOR"
        assert tuple(c.execute("SELECT SUM(amount), COUNT(*), GROUP_CONCAT(category) FROM transactions").fetchone()) == totals
    with db.use_space(system.user_db_path(u), u):  # ikinci defter de taşındı
        assert db.subscription_states()["ORNEK DERGI"]["new_amount"] == 19
    # tekrar: değişiklik yok
    assert db.sync_gateway_keys() is None
    finance._CACHE.clear()


def test_sync_on_first_run_uses_legacy_list(tmpdb):
    """Bu sürümden önce v1 göçü yapılmış defter: 'IZICO' (o gün listede yoktu) adına konmuş durum yeni adlara gelir."""
    from app import db

    m = _month()
    _card(tmpdb, [(f"{m}-03", "IZICO/AmazonPrimeTR", 49, "Dijital Abonelik"),
                  (f"{m}-04", "IZICO /SPOTIFY.COM ISTANBUL TR", 59, "Dijital Abonelik")])
    with db.connect() as c:
        c.execute("INSERT INTO subscription_state (merchant, status, changed_at) VALUES ('IZICO', 'aktif', ?)", (f"{m}-01",))
        c.execute("DELETE FROM settings WHERE key = ?", (db.GATEWAY_KEYS_SETTING,))
        c.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('gateway_keys_v1', '1')")
    rep = db.sync_gateway_keys()
    assert rep["subscriptions"] == 2
    names = set(db.subscription_states())
    assert "AMAZON PRIME" in names and any(n.startswith("SPOTIFY") for n in names)
    from app import gateways
    from app.textnorm import current_gateways

    sig = db.get_setting(db.GATEWAY_KEYS_SETTING)
    assert sig == current_gateways().sig and len(sig) == 16  # defterde yalnız özet; adlar system.db'de
    assert gateways.recall_set(sig)["fuzzy"] is True
    assert db.sync_gateway_keys() is None


# ---------------------------------------------------------------- hazır sürüm: tek kişide görülen ad örnek olmaz

def test_hosted_samples_only_from_two_books(tmpdb, monkeypatch):
    """Hazır sürümde yönetici başkasının defterini görmez: örnek işyeri adı yalnız ≥2 defterde görülenlerden; tek
    defterde görülenin adı yazılmaz, sayısı (hidden) kalır. Önizlemede de aynı."""
    from app import gateways

    monkeypatch.setenv("HESAPKITAP_HOSTED", "1")
    m = _month()
    _card(tmpdb, _candidate_rows(m))
    _second_book([(f"{m}-03", "YENIARACI/ORNEK KITAP", 45, "Kategorisiz"),
                  (f"{m}-04", "YENIARACI/ORNEK MOBILYA", 300, "Kategorisiz")])
    gateways.scan(date(2026, 11, 12))
    [c] = gateways.overview()["candidates"]
    assert c["samples"] == ["ORNEK KITAP"] and c["hidden"] == 3 and c["merchants"] == 4 and c["rows"] == 5
    p = gateways.preview("YENIARACI")
    assert p["samples"] == ["ORNEK KITAP"] and p["hidden"] == 3 and p["merchants"] == 4 and p["rows"] == 5


def test_hosted_single_book_candidate_has_no_names(tmpdb, monkeypatch):
    from app import gateways

    monkeypatch.setenv("HESAPKITAP_HOSTED", "1")
    m = _month()
    _card(tmpdb, [(f"{m}-0{i}", f"TEKDEFTER/ORNEK ISYERI {w}", 10, "Kategorisiz") for i, w in enumerate("ABCDE", 1)])
    gateways.scan(date(2026, 11, 12))
    [c] = gateways.overview()["candidates"]
    assert c["samples"] == [] and c["hidden"] == 5 and c["merchants"] == 5 and c["books"] == 1
    p = gateways.preview("tekdefter")
    assert p["samples"] == [] and p["hidden"] == 5 and p["rows"] == 5


def test_self_hosted_shows_single_book_samples(tmpdb, monkeypatch):
    """Kendi sunucusunu kuranda (hosted değil) eski davranış: tek defterde görülen ad da örnek."""
    from app import gateways

    monkeypatch.delenv("HESAPKITAP_HOSTED", raising=False)
    m = _month()
    _card(tmpdb, [(f"{m}-0{i}", f"TEKDEFTER/ORNEK ISYERI {w}", 10, "Kategorisiz") for i, w in enumerate("ABCDE", 1)])
    gateways.scan(date(2026, 11, 12))
    [c] = gateways.overview()["candidates"]
    assert len(c["samples"]) == 5 and c["hidden"] == 0
