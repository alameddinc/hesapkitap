"""Kategorisizlerde toplu işlemler (apply-many tek geri alma, sıralama, tek seferlikler, yapay zekâya hepsini sor),
ödeme aracısı önekleri (textnorm.PAYMENT_GATEWAYS) ve görünen işyeri adını düzeltme (merchant_aliases).
Bütün adlar uydurmadır ("ORNEK BILISIM", "Elif Örnek" vb.)."""
from tests.test_uncategorized import _card, _entry, _groups, _month, gemini  # noqa: F401 (fixture)


# ---------------------------------------------------------------- ödeme aracısı önekleri

def test_gateway_prefix_dropped_from_name():
    from app.textnorm import drop_gateway, merchant_name, strip_gateway

    assert merchant_name("PAYCO/ORNEK BILISIM") == "ORNEK BILISIM"
    assert merchant_name("Payco/Örnek Bilişim") == "ORNEK BILISIM"
    assert merchant_name("ELEKSE/ORNEK BILISIM/ISTANBUL") == "ORNEK BILISIM"
    assert merchant_name("PAYCO/ELEKSE/ORNEK BILISIM") == "ORNEK BILISIM"  # iç içe aracı
    assert merchant_name("IYZ*ORNEK KITAPEVI") == merchant_name("IYZICO *ORNEK KITAPEVI") == "ORNEK KITAPEVI"
    assert merchant_name("PAYTR*ORNEK SPOR 123456") == "ORNEK SPOR"
    for gw in ("PARAM", "SIPAY", "MOKA", "PAPARA", "TOSLA", "HEPSIPAY", "PAYU", "LIDIO", "CRAFTGATE"):
        assert merchant_name(f"{gw}/ORNEK DUKKAN") == "ORNEK DUKKAN", gw
    # aracıdan sonra bir şey yoksa ad aracının kendisi; aracı olmayan önek dokunulmaz
    assert merchant_name("IYZICO/") == "IYZICO" and merchant_name("PAYCO") == "PAYCO"
    assert merchant_name("ORNEKSOFT*ABC123/LONDON") == "ORNEKSOFT"
    assert strip_gateway("ORNEK/MARKET") == "ORNEK/MARKET"
    assert strip_gateway(strip_gateway("PAYCO/ORNEK BILISIM")) == "ORNEK BILISIM"
    assert drop_gateway("Payco/Örnek Bilişim · not") == "Örnek Bilişim · not"


def test_rules_match_real_merchant_first():
    from app.categorize import DEFAULT_RULES, categorize

    rules = list(DEFAULT_RULES)
    # aracının kuralı (IYZICO → Online Alışveriş) asıl işyerinin kuralını ezmez
    assert categorize("IYZICO/ORNEK KITAPEVI", "harcama", rules) == "Kitap & Hobi"
    # asıl işyeri bilinmiyorsa eski davranış: tam metin
    assert categorize("IYZICO/ORNEK XYZ", "harcama", rules) == "Online Alışveriş"
    # aracı adına yazılmış kullanıcı kuralı (eskiden 'PAYCO' kuralı öğrenilirdi) çalışmaya devam eder
    assert categorize("PAYCO/ORNEK XYZ", "harcama", [("PAYCO", "Eğitim"), *rules]) == "Eğitim"
    assert categorize("PAYCO/ORNEK BILISIM", "harcama", [(r"ORNEK BILISIM", "Elektronik"), ("PAYCO", "Eğitim")]) == "Elektronik"


def test_groups_merge_across_gateways_raw_kept(client, tmpdb):
    from app import db

    m = _month()
    ids = _card(tmpdb, [(f"{m}-03", "PAYCO/ORNEK BILISIM", 40, "Kategorisiz"),
                        (f"{m}-04", "ELEKSE/ORNEK BILISIM/ISTANBUL", 60, "Kategorisiz"),
                        (f"{m}-05", "IYZ*ORNEK BILISIM", 10, "Kategorisiz")])
    by = {g["key"]: g for g in _groups(client, m)["groups"]}
    assert set(by) == {"i:c:ORNEKBILISIM"}
    g = by["i:c:ORNEKBILISIM"]
    assert g["label"] == "ORNEK BILISIM" and g["count"] == 3 and g["total"] == 110 and g["renamable"] is True
    with db.connect() as c:  # ham açıklama aynen
        assert c.execute("SELECT description FROM transactions WHERE id = ?", (ids[0],)).fetchone()[0] == "PAYCO/ORNEK BILISIM"
    # elle kayıtta görünen ad da aracısız, yazılış korunur
    _entry(client, "Payco/Örnek Yazılım", 25)
    labels = {g["label"] for g in _groups(client, m)["groups"]}
    assert "Örnek Yazılım" in labels


# ---------------------------------------------------------------- toplu uygula

def test_apply_many_single_undo(client, tmpdb):
    from app import db, finance

    m = _month()
    prev = finance.add_months(m, -1)
    _card(tmpdb, [(f"{m}-03", "ORNEKFIRIN MODA", 40, "Kategorisiz"), (f"{m}-04", "PAYCO/ORNEK BILISIM", 300, "Kategorisiz")])
    _card(tmpdb, [(f"{prev}-04", "ORNEKFIRIN MODA", 25, "Kategorisiz")], period_end=f"{prev}-20", manual=0)
    _entry(client, "Elif Örnek · kira", 5000, counterparty="Elif Örnek")
    _entry(client, "Ornekbufe", 15)
    rules = len(db.list_rules())
    before = client.get("/api/summary").json()["totals"]
    items = [{"key": "i:c:ORNEKFIRINMODA", "category": "Kafe & Restoran"},
             {"key": "i:c:ORNEKBILISIM", "category": "Elektronik"},
             {"key": "k:c:Elif Örnek", "category": "Kira & Aidat"},
             {"key": "i:c:YOK", "category": "Giyim"}]
    r = client.post("/api/uncategorized/apply-many", json={"items": items, "remember": True, "month": m})
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["groups"] == 3 and out["changed"] == 3 and out["rules"] == 2 and out["people"] == 1
    assert out["skipped"] == ["i:c:YOK"] and out["undo_id"]
    assert out["also_changed"] >= 1  # geçen ayın ORNEKFIRIN satırı kuralla düzeldi
    assert [g["key"] for g in _groups(client, m)["groups"]] == ["i:c:ORNEKBUFE"]
    assert len(db.list_rules()) == rules + 2 and db.get_counterparty("Elif Örnek")["category"] == "Kira & Aidat"
    after = client.get("/api/summary").json()["totals"]
    assert after["expense"] == before["expense"] and after["net"] == before["net"]  # rakamlar değişmez
    # tek Geri al: hepsi
    u = client.post("/api/uncategorized/undo", json={"undo_id": out["undo_id"]})
    assert u.status_code == 200 and u.json()["ok"] is True
    assert _groups(client)["count"] == 5 and len(db.list_rules()) == rules
    assert (db.get_counterparty("Elif Örnek") or {}).get("category") in (None, "")


def test_apply_many_validation_and_without_remember(client, tmpdb):
    from app import db

    _entry(client, "Örnek Market", 120)
    _entry(client, "Örnek Kırtasiye", 30)
    rules = len(db.list_rules())
    bad = client.post("/api/uncategorized/apply-many", json={"items": [{"key": "i:c:ORNEKMARKET", "category": "Kategorisiz"}]})
    assert bad.status_code == 422
    assert client.post("/api/uncategorized/apply-many", json={"items": []}).status_code == 422
    assert client.post("/api/uncategorized/apply-many", json={"items": [{"key": "i:c:YOK", "category": "Giyim"}]}).status_code == 404
    out = client.post("/api/uncategorized/apply-many", json={"remember": False, "items": [
        {"key": "i:c:ORNEKMARKET", "category": "Diğer"}, {"key": "i:c:ORNEKKIRTASIYE", "category": "Diğer"}]}).json()
    assert out["changed"] == 2 and out["rules"] == 0 and len(db.list_rules()) == rules
    assert _groups(client)["count"] == 0


def test_sort_and_one_off(client, tmpdb):
    _entry(client, "Örnek Market", 20)
    _entry(client, "Örnek Market", 30, day="06")
    _entry(client, "Örnek Mobilya", 4000)
    _entry(client, "Örnek Büfe", 40)
    _entry(client, "Elif Örnek", 100, kind="gelir", counterparty="Elif Örnek")
    g = _groups(client)
    assert g["groups"][0]["label"] == "Örnek Market"  # varsayılan (eski istemciler): en çok işlem
    assert g["one_off_max"] == 250 and g["ai_max"] >= 100
    r = client.get("/api/uncategorized/groups", params={"sort": "total"}).json()
    assert [x["label"] for x in r["groups"]][:2] == ["Örnek Mobilya", "Elif Örnek"]
    one = {x["label"]: x["one_off"] for x in r["groups"]}
    assert one == {"Örnek Mobilya": False, "Elif Örnek": False, "Örnek Market": False, "Örnek Büfe": True}
    assert client.get("/api/uncategorized/groups", params={"sort": "x"}).status_code == 422


def test_ai_suggest_all_in_one_call(client, tmpdb, gemini):  # noqa: F811
    m = _month()
    _card(tmpdb, [(f"{m}-04", f"ORNEKDUKKAN {chr(65 + i // 26)}{chr(65 + i % 26)}", 10 + i, "Kategorisiz") for i in range(60)])
    keys = [x["key"] for x in _groups(client, m)["groups"]]
    assert len(keys) == 60
    out = client.post("/api/uncategorized/ai-suggest", json={"keys": keys, "month": m}).json()
    assert out["asked"] == 60 and len(gemini) == 1  # hepsi tek çağrıda (tek hak)


# ---------------------------------------------------------------- görünen adı düzelt

def test_rename_merchant_everywhere_and_undo(client, tmpdb):
    from app import db, finance

    m = _month()
    prev = finance.add_months(m, -1)
    ids = _card(tmpdb, [(f"{m}-03", "PAYCO/ORNEK BLSM", 40, "Kategorisiz")])
    _card(tmpdb, [(f"{prev}-03", "ELEKSE/ORNEK BLSM", 20, "Elektronik")], period_end=f"{prev}-20")
    with db.connect() as c:
        c.execute("INSERT INTO savings_goals (kind, name, baseline, target, start_month) VALUES ('merchant', 'ORNEK BLSM', 60, 30, ?)", (m,))
    before = client.get("/api/summary").json()["totals"]
    r = client.post("/api/merchants/rename", json={"key": "i:c:ORNEKBLSM", "display": "  Örnek  Bilişim ", "month": m})
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["display"] == "Örnek Bilişim" and out["sources"] == ["ORNEK BLSM"] and out["changed"] == 2 and out["undo_id"]
    rows = finance.transactions(None)
    names = {r["merchant"] for r in rows if r["source"] == "card"}
    assert names == {"Örnek Bilişim"}
    assert {r["description"] for r in rows if r["source"] == "card"} == {"PAYCO/ORNEK BLSM", "ELEKSE/ORNEK BLSM"}  # ham metin aynen
    assert [g["label"] for g in _groups(client)["groups"]] == ["Örnek Bilişim"]
    with db.connect() as c:
        assert c.execute("SELECT name FROM savings_goals").fetchone()[0] == "Örnek Bilişim"
    assert client.get("/api/summary").json()["totals"] == before
    # tek işlemden de: uid
    r2 = client.post("/api/merchants/rename", json={"uid": f"c{ids[0]}", "display": "Örnek Teknoloji"}).json()
    assert r2["display"] == "Örnek Teknoloji"
    assert {r["merchant"] for r in finance.transactions(None) if r["source"] == "card"} == {"Örnek Teknoloji"}
    # geri al (sondan): önce "Örnek Bilişim"e, sonra ekstredeki ada
    assert client.post("/api/uncategorized/undo", json={"undo_id": r2["undo_id"]}).status_code == 200
    assert {r["merchant"] for r in finance.transactions(None) if r["source"] == "card"} == {"Örnek Bilişim"}
    assert client.post("/api/uncategorized/undo", json={"undo_id": out["undo_id"]}).status_code == 200
    assert {r["merchant"] for r in finance.transactions(None) if r["source"] == "card"} == {"ORNEK BLSM"}
    with db.connect() as c:
        assert c.execute("SELECT name FROM savings_goals").fetchone()[0] == "ORNEK BLSM"
    # boş ad: düzeltmeyi kaldırır
    client.post("/api/merchants/rename", json={"uid": f"c{ids[0]}", "display": "Örnek Bilişim"})
    r3 = client.post("/api/merchants/rename", json={"uid": f"c{ids[0]}", "display": ""}).json()
    assert r3["display"] == "ORNEK BLSM" and db.merchant_aliases() == {}
    # hatalı istekler
    assert client.post("/api/merchants/rename", json={"display": "X"}).status_code == 422
    assert client.post("/api/merchants/rename", json={"uid": "c999999", "display": "X"}).status_code == 404
    assert client.post("/api/merchants/rename", json={"uid": "e1", "display": "X"}).status_code == 404


def test_capabilities_bulk():
    from app import version

    caps = version.handshake()["capabilities"]
    assert "uncategorized_bulk" in caps and "merchant_rename" in caps


# ---------------------------------------------------------------- göç: işyeri adına bağlı ayarlar

def test_migrate_gateway_keys_idempotent(client, tmpdb):
    from app import db

    m = _month()
    _card(tmpdb, [(f"{m}-03", "PAYCO/ORNEK DERGI", 49, "Dijital Abonelik"), (f"{m}-04", "PAYCO/ORNEK OYUN", 9, "Dijital Abonelik"),
                  (f"{m}-05", "ELEKSE/ORNEK SPOR", 300, "Spor")])
    with db.connect() as c:
        c.execute("INSERT INTO subscription_state (merchant, status, changed_at) VALUES ('PAYCO', 'iptal', ?)", (f"{m}-01",))
        c.execute("INSERT INTO savings_goals (kind, name, baseline, target, start_month) VALUES ('merchant', 'ELEKSE', 300, 200, ?)", (m,))
        c.execute("INSERT INTO savings_goals (kind, name, baseline, target, start_month) VALUES ('merchant', 'PAYCO', 58, 20, ?)", (m,))
        totals = c.execute("SELECT SUM(amount), COUNT(*) FROM transactions").fetchone()
    rep = db.migrate_gateway_keys()
    assert rep == {"subscriptions": 2, "goals": 1, "ambiguous_goals": 1}
    states = db.subscription_states()
    assert states["ORNEK DERGI"]["status"] == states["ORNEK OYUN"]["status"] == "iptal" and "PAYCO" in states
    with db.connect() as c:
        assert {r[0] for r in c.execute("SELECT name FROM savings_goals")} == {"ORNEK SPOR", "PAYCO"}
        assert tuple(c.execute("SELECT SUM(amount), COUNT(*) FROM transactions").fetchone()) == tuple(totals)
    # ikinci çalıştırma bir şey değiştirmez
    assert db.migrate_gateway_keys()["subscriptions"] == 0
    assert db.subscription_states() == states
