"""Kategorisizleri düzenle (app/uncategorized.py): Özet uyarısıyla aynı küme, işyeri/kişi gruplaması, ücretsiz öneri
kaynakları (geçmiş/kural/bilinen), uygula + öğren + geri al, yapay zekâ önerisi (sahte Gemini; hak doluysa 402).
Veriler uydurmadır ("Örnek Market" vb.)."""
import json

import httpx
import pytest

from tests import test_plans
from tests.test_doc_reads import _set_used, _used
from tests.test_plans import _user

hosted = test_plans.hosted  # fixture


def _month():
    from app import finance

    return finance.current_period()


def _entry(client, desc, amount, category="Kategorisiz", kind="gider", day="05", **extra):
    r = client.post("/api/entries", json={"entry_date": f"{_month()}-{day}", "description": desc, "amount": amount,
                                           "kind": kind, "category": category, "payment": "nakit", **extra})
    assert r.status_code == 200, r.text
    return r.json()["id"]


def _card(db, rows, period_end=None, manual=1):
    """Kart işlemleri: (tarih, açıklama, tutar, kategori[, not]). manual=1: kategori kullanıcı tarafından verilmiş."""
    from app import finance

    pe = period_end or f"{_month()}-20"
    with db.connect() as c:
        c.execute("INSERT OR IGNORE INTO statements (file_hash, bank, card_no, period_end) VALUES (?, 'Örnek Bank', "
                  "'5400***1111', ?)", (f"h-{pe}", pe))
        sid = c.execute("SELECT id FROM statements WHERE period_end = ?", (pe,)).fetchone()[0]
        ids = []
        for r in rows:
            d, desc, amt, cat, *note = r
            ids.append(c.execute("INSERT INTO transactions (statement_id, txn_date, description, amount, txn_type, "
                                 "category, category_manual, note) VALUES (?, ?, ?, ?, 'harcama', ?, ?, ?)",
                                 (sid, d, desc, amt, cat, manual, note[0] if note else None)).lastrowid)
    finance._CACHE.clear()
    return ids


def _groups(client, month=None):
    r = client.get("/api/uncategorized/groups", params={"month": month} if month else {})
    assert r.status_code == 200, r.text
    return r.json()


def _insight_count(client):
    t = next((i["title"] for i in client.get("/api/summary").json()["insights"] if i["title"].endswith("işlem kategorisiz")), None)
    return int(t.split()[0]) if t else 0


def test_capabilities():
    from app import version

    caps = version.handshake()["capabilities"]
    assert "uncategorized_groups" in caps and "uncategorized_ai" in caps


def test_groups_same_set_as_insight_and_grouping(client, tmpdb):
    m = _month()
    _entry(client, "Örnek Market", 120)
    _entry(client, "Örnek Market", 80, day="06")
    _entry(client, "Elif Örnek · kira", 5000, counterparty="Elif Örnek")
    _entry(client, "Elif Örnek", 300, kind="gelir", counterparty="Elif Örnek")  # gelen para: ayrı grup
    _entry(client, "Örnek Kafe", 50, category="Kafe & Restoran")  # kategorili: gruba girmez
    _entry(client, "Örnek Gelir", 10, category="Diğer Gelir", kind="gelir")  # gelirin genel kategorisi: sayılmaz
    _card(tmpdb, [(f"{m}-03", "ORNEK MARKET", 40, "Kategorisiz"), (f"{m}-04", "ORNEKSOFT*ABC123/LONDON", 99, "Kategorisiz")])
    g = _groups(client, m)
    assert g["count"] == _insight_count(client) == 6
    by = {x["key"]: x for x in g["groups"]}
    market = by["i:c:ORNEKMARKET"]
    assert market["count"] == 3 and market["total"] == 240 and market["kind"] == "isyeri" and market["flow"] == "gider"
    assert market["label"] == "Ornek Market" and len(market["sample_ids"]) == 3
    assert by["k:c:Elif Örnek"]["count"] == 1 and by["k:c:Elif Örnek"]["kind"] == "kisi"
    assert by["k:g:Elif Örnek"]["flow"] == "gelir"
    assert g["groups"][0]["key"] == "i:c:ORNEKMARKET"  # en kalabalık önce
    assert g["ai"] == {"available": False, "left": None}  # Gemini anahtarı yok
    # tüm zaman: geçen ayın kaydı da girer
    from app import finance

    prev = finance.add_months(m, -1)
    client.post("/api/entries", json={"entry_date": f"{prev}-10", "description": "Örnek Market", "amount": 5,
                                      "kind": "gider", "category": "Kategorisiz", "payment": "nakit"})
    assert _groups(client, m)["count"] == 6
    assert _groups(client)["count"] == _groups(client, "all")["count"] == 7
    assert client.get("/api/uncategorized/groups", params={"month": "2026/10"}).status_code == 422


def test_suggestion_sources(client, tmpdb):
    from app import db

    m = _month()
    # geçmiş: aynı işyerine kullanıcı daha önce iki kez "Market & Gıda" bir kez "Ev & Tamirat" vermiş
    _card(tmpdb, [(f"{m}-01", "ORNEKPAZAR KADIKOY", 10, "Market & Gıda"), (f"{m}-02", "ORNEKPAZAR KADIKOY", 11, "Market & Gıda"),
                  (f"{m}-02", "ORNEKPAZAR KADIKOY", 12, "Ev & Tamirat"), (f"{m}-03", "ORNEKPAZAR KADIKOY", 13, "Kategorisiz")])
    # kural: kullanıcı kuralı sonradan eklendi; elle kayıt eski haliyle kategorisiz
    _entry(client, "Örnekgiyim Mağaza", 200)
    db.add_rule("ORNEKGIYIM", "Giyim")
    # bilinen: kart Excel'i sektörü notta
    _card(tmpdb, [(f"{m}-05", "ORNEKISYERI XYZ", 70, "Kategorisiz", "MARKET VE ALISVERIS MERKEZLERI")])
    # kişi: kişi kategorisi tanımlı ama kayıt kategorisiz kalmış
    _entry(client, "Elif Örnek · aidat", 900, counterparty="Elif Örnek")
    with db.connect() as c:
        c.execute("INSERT INTO counterparties (name, category, track_debt) VALUES ('Elif Örnek', 'Kira & Aidat', 0)")
    # hiçbir şey bilinmiyor
    _entry(client, "Bilinmez Ltd", 30)
    by = {x["key"]: x["suggestion"] for x in _groups(client, m)["groups"]}
    assert by["i:c:ORNEKPAZARKADIKOY"]["category"] == "Market & Gıda" and by["i:c:ORNEKPAZARKADIKOY"]["source"] == "gecmis"
    assert 0 < by["i:c:ORNEKPAZARKADIKOY"]["confidence"] <= 1
    assert by["i:c:ORNEKGIYIMMAGAZA"] == {"category": "Giyim", "source": "kural", "confidence": 0.8}
    from app.card_excel import sector_category

    expect = sector_category("MARKET VE ALISVERIS MERKEZLERI")
    if expect:
        assert by["i:c:ORNEKISYERIXYZ"] == {"category": expect, "source": "bilinen", "confidence": 0.6}
    assert by["k:c:Elif Örnek"]["category"] == "Kira & Aidat" and by["k:c:Elif Örnek"]["source"] == "gecmis"
    assert by["i:c:BILINMEZLTD"] is None


def test_apply_remember_rule_and_undo(client, tmpdb):
    from app import db, finance

    m = _month()
    prev = finance.add_months(m, -1)
    ids = _card(tmpdb, [(f"{m}-03", "ORNEKFIRIN MODA", 40, "Kategorisiz"), (f"{m}-04", "ORNEKFIRIN MODA", 60, "Kategorisiz")])
    _card(tmpdb, [(f"{prev}-04", "ORNEKFIRIN MODA", 25, "Kategorisiz")], period_end=f"{prev}-20", manual=0)
    eid = _entry(client, "Ornekfirin Moda", 15)
    before_rules = len(db.list_rules())
    before_summary = client.get("/api/summary").json()["totals"]
    r = client.post("/api/uncategorized/apply", json={"key": "i:c:ORNEKFIRINMODA", "category": "Kafe & Restoran",
                                                      "remember": True, "month": m})
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["changed"] == 3 and out["remembered"] == "kural" and out["rule"] and out["undo_id"]
    assert out["also_changed"] >= 1  # geçen ayın kural dışı kalmış kart satırı da düzeldi
    assert _groups(client, m)["count"] == 0 and _groups(client)["count"] == 0
    assert len(db.list_rules()) == before_rules + 1
    with db.connect() as c:
        assert {r[0] for r in c.execute(f"SELECT category_manual FROM transactions WHERE id IN ({ids[0]}, {ids[1]})")} == {1}
        assert c.execute("SELECT category FROM entries WHERE id = ?", (eid,)).fetchone()[0] == "Kafe & Restoran"
    # rakamlar değişmez: yalnız kategori
    after_summary = client.get("/api/summary").json()["totals"]
    assert after_summary["expense"] == before_summary["expense"] and after_summary["net"] == before_summary["net"]
    # geri al: kayıtlar, kural
    u = client.post("/api/uncategorized/undo", json={"undo_id": out["undo_id"]})
    assert u.status_code == 200 and u.json()["ok"] is True
    finance._CACHE.clear()
    assert _groups(client)["count"] == 4 and len(db.list_rules()) == before_rules
    with db.connect() as c:
        assert c.execute("SELECT category_manual FROM transactions WHERE id = ?", (ids[0],)).fetchone()[0] == 1  # eski hali
    # başka bir geri alma kaydı (ör. ekstre yüklemesi) bu uçla geri alınamaz
    from app import undo

    with undo.batch("Ekstre: örnek.pdf") as b:
        db.add_rule("BASKA", "Giyim")
    assert client.post("/api/uncategorized/undo", json={"undo_id": b["id"]}).status_code == 404


def test_apply_without_remember_and_person(client, tmpdb):
    from app import db

    m = _month()
    _entry(client, "Örnek Market", 120)
    rules = len(db.list_rules())
    out = client.post("/api/uncategorized/apply", json={"key": "i:c:ORNEKMARKET", "category": "Market & Gıda",
                                                        "remember": False}).json()
    assert out["changed"] == 1 and out["remembered"] is None and len(db.list_rules()) == rules
    # kişi: giden para → kişi kategorisi öğrenilir; kişinin kategorili eski kaydına dokunulmaz
    old = _entry(client, "Elif Örnek · hediye", 100, category="Hediye & Çiçek", counterparty="Elif Örnek")
    _entry(client, "Elif Örnek · kira", 5000, counterparty="Elif Örnek")
    out = client.post("/api/uncategorized/apply", json={"key": "k:c:Elif Örnek", "category": "Kira & Aidat", "month": m}).json()
    assert out["changed"] == 1 and out["remembered"] == "kisi"
    assert db.get_counterparty("Elif Örnek")["category"] == "Kira & Aidat"
    with db.connect() as c:
        assert c.execute("SELECT category FROM entries WHERE id = ?", (old,)).fetchone()[0] == "Hediye & Çiçek"
    # gelen para grubunda kişi kategorisi öğrenilmez (giden havaleleri yanlış kategoriye sokmasın)
    _entry(client, "Elif Örnek", 300, kind="gelir", counterparty="Elif Örnek")
    out = client.post("/api/uncategorized/apply", json={"key": "k:g:Elif Örnek", "category": "Ek Gelir"}).json()
    assert out["changed"] == 1 and out["remembered"] is None
    assert db.get_counterparty("Elif Örnek")["category"] == "Kira & Aidat"
    # boş grup / geçersiz kategori
    assert client.post("/api/uncategorized/apply", json={"key": "i:c:YOK", "category": "Giyim"}).status_code == 404
    assert client.post("/api/uncategorized/apply", json={"key": "i:c:ORNEKMARKET", "category": "Kategorisiz"}).status_code == 422


# ---------------------------------------------------------------- yapay zekâ (sahte Gemini)

@pytest.fixture()
def gemini(monkeypatch):
    from app import receipt

    monkeypatch.setenv("GEMINI_API_KEY", "AIzaTEST0000")
    calls = []

    def fake_post(url, json=None, timeout=None, headers=None):
        calls.append(json)
        prompt = json["contents"][0]["parts"][0]["text"]
        items = [{"name": "ORNEKSOFT", "category": "Yazılım & Bulut", "confidence": 0.9},
                 {"name": "ORNEKOYUN", "category": "Uydurma Kategori", "confidence": 0.9}]
        assert "Elif" not in prompt and "99" not in prompt  # kişi adı ve tutar gitmez
        return httpx.Response(200, json={"candidates": [{"content": {"parts": [{"text": _json({"items": items})}]}}]})

    monkeypatch.setattr(receipt.httpx, "post", fake_post)
    return calls


def _json(d) -> str:
    return json.dumps(d, ensure_ascii=False)


def test_ai_suggest_disabled_without_key(client):
    r = client.post("/api/uncategorized/ai-suggest", json={"keys": ["i:c:X"]})
    assert r.status_code == 404


def test_ai_suggest_only_merchants(client, tmpdb, gemini):
    m = _month()
    _card(tmpdb, [(f"{m}-04", "ORNEKSOFT*ABC123/LONDON", 99, "Kategorisiz"), (f"{m}-05", "ORNEKOYUN", 15, "Kategorisiz")])
    _entry(client, "Elif Örnek · kira", 5000, counterparty="Elif Örnek")
    g = _groups(client, m)
    assert g["ai"]["available"] is True
    keys = [x["key"] for x in g["groups"]]
    r = client.post("/api/uncategorized/ai-suggest", json={"keys": keys, "month": m})
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["asked"] == 2 and len(gemini) == 1
    assert out["suggestions"] == {"i:c:ORNEKSOFT": {"category": "Yazılım & Bulut", "source": "yz", "confidence": 0.9}}
    # yalnız kişi grubu: Gemini'ye gidilmez
    r = client.post("/api/uncategorized/ai-suggest", json={"keys": ["k:c:Elif Örnek"]}).json()
    assert r["asked"] == 0 and len(gemini) == 1


def test_ai_suggest_quota_hosted(hosted, gemini, tmpdb):
    c, u = _user(hosted)
    c.post("/api/entries", json={"entry_date": f"{_month()}-05", "description": "Örneksoft", "amount": 9,
                                 "kind": "gider", "category": "Kategorisiz", "payment": "nakit"})
    g = c.get("/api/uncategorized/groups").json()
    assert g["ai"]["available"] is True and g["ai"]["left"] == 50
    r = c.post("/api/uncategorized/ai-suggest", json={"keys": [x["key"] for x in g["groups"]]})
    assert r.status_code == 200 and _used(u["id"]) == 1  # 1 çağrı = 1 hak
    _set_used(u["id"], 50)
    r = c.post("/api/uncategorized/ai-suggest", json={"keys": [x["key"] for x in g["groups"]]})
    assert r.status_code == 402 and r.json()["quota_full"] is True
    assert "hakkın doldu" in r.json()["detail"] and len(gemini) == 1


def test_ai_suggest_quota_self_hosted(client, gemini, monkeypatch):
    """Kendi sunucusu, ortak anahtarı kullanan yönetici-olmayan kullanıcı: kota dolunca 402 (Türkçe neden)."""
    from app import receipt

    _entry(client, "Örneksoft", 9)
    monkeypatch.setattr(receipt, "_shared_key_user", lambda: {"id": 7, "role": "user", "ai_quota": 0})
    monkeypatch.setattr("app.system.ai_used_pool", lambda u, month=None: 0)
    g = _groups(client)
    assert g["ai"] == {"available": True, "left": 0}
    r = client.post("/api/uncategorized/ai-suggest", json={"keys": [x["key"] for x in g["groups"]]})
    assert r.status_code == 402 and "hakkın doldu" in r.json()["detail"] and not gemini
