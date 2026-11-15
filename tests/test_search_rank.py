"""Ürün araması sıralaması (app/search_rank.py) ve alışveriş listesinde serbest marka / mağaza + son seçilenler
(shop_free_pick). 15.11.2026 telefondan: "şeker" yazınca öneride önce "Şekerleme" çıktı (kişi şekerlemeyi daha sık
alıyordu; kaynak sırası eşleşmenin önündeydi) ve aranan marka seçicide yoktu. Ürünler, markalar, fiyatlar uydurmadır."""
from datetime import date

import pytest

from app import price_sources, product_groups as pg, search_rank as sr, system
from tests.test_book import owner  # noqa: F401  (fixture)

TODAY = date.today().isoformat()


@pytest.fixture
def resmi(monkeypatch):
    monkeypatch.setenv("HESAPKITAP_PRICE_SOURCES", "own,community,resmi")
    monkeypatch.setattr(price_sources, "official_configured", lambda: True)


def _official(rows, region="İstanbul"):
    with system.connect() as c:
        c.executemany("INSERT INTO official_prices (chain, group_key, title, brand, price, day, region) VALUES (?, ?, ?, ?, ?, ?, ?)",
                      [(ch, g, f"Örnek {g}", "Örnekmarka", p, TODAY, region) for ch, g, p in rows])


def _cache(name, key, label):
    with system.connect() as c:
        pg._store(c, pg.name_norm(name), {"key": key, "label": label, "category": None}, "ai")


def _buy(client, name, price, n=1):
    for _ in range(n):
        r = client.post("/api/entries", json={"entry_date": TODAY, "description": "Örnekmarket", "amount": price,
                                              "kind": "gider", "category": "Market & Gıda", "payment": "nakit",
                                              "items": [{"name": name, "qty": 1, "amount": price}]})
        assert r.status_code == 200, r.text


def _texts(client, q):
    return [x["text"] for x in client.get("/api/shopping-list/suggest", params={"q": q}).json()["items"]]


# ---------------------------------------------------------------- katman

@pytest.mark.parametrize("q,text,want", [
    ("şeker", "Şeker", sr.EXACT),
    ("ŞEKER", "şeker", sr.EXACT),
    ("seker", "Şeker", sr.EXACT),                 # Türkçe klavyesi yok: katlanarak birebir
    ("şeker", "Şeker Pancarı", sr.FIRST_WORD),
    ("şeker", "Şekerleme", sr.FIRST_PREFIX),
    ("şeker", "Toz Şeker", sr.WORD_PREFIX),
    ("şeker", "Pudra Şekeri", sr.WORD_PREFIX),
    ("eker", "Şeker", sr.INSIDE),
    ("sekr", "Şeker", sr.FUZZY),                  # bir harf eksik
    ("şekre", "Şeker", sr.FUZZY),                 # iki harf yer değişmiş
    ("sekar", "Şeker", sr.FUZZY),                 # bir harf farklı
    ("şekerl", "Şekerleme", sr.FIRST_PREFIX),
    ("toz şek", "Toz Şeker", sr.FIRST_PREFIX),
    ("şek toz", "Toz Şeker", sr.WORD_PREFIX),
])
def test_tier(q, text, want):
    assert sr.tier(q, text, strict=True) == want


@pytest.mark.parametrize("q,text", [
    ("", "Şeker"), ("ş", "Şeker"), ("s", "Su"),   # 2 harften kısa: hiçbir şey
    ("ek", "Şeker"),                               # 3 harften kısa: içinde geçen sayılmaz
    ("sek", "Sucuk"), ("skr", "Şeker"),            # 4 harften kısa: bulanık yok
    ("elmas", "Şeker"),
])
def test_tier_none(q, text):
    assert sr.tier(q, text, strict=True) is None


def test_turkish_letters_typed_are_kept():
    # "sü" yazan Su'yu değil Süt'ü arıyor: düzgün yazılmış adda harf de tutmalı
    assert sr.tier("sü", "Su", strict=True) == sr.FUZZY
    assert sr.tier("sü", "Süt", strict=True) == sr.FIRST_PREFIX
    assert sr.tier("su", "Süt", strict=True) == sr.FIRST_PREFIX     # Türkçe harfsiz yazınca ikisi de
    assert sr.tier("su", "Su", strict=True) == sr.EXACT
    # fiş adı çoğu zaman Türkçe harfsiz ("KIRMIZI" → "Kirmizi"): katlanarak eşleşir; düzgün yazılmış grup adında değil
    assert sr.tier("kırmızı", "Kirmizi Mercimek") == sr.FIRST_WORD
    assert sr.tier("kırmızı", "Kırmızı Mercimek", strict=True) == sr.FIRST_WORD


# ---------------------------------------------------------------- alışveriş listesi önerisi

def test_exact_before_more_often_bought_prefix(client):
    _cache("SEKER", "seker", "Şeker")
    _cache("SEKERLEME", "sekerleme", "Şekerleme")
    _cache("TOZ SEKER", "toz-seker", "Toz Şeker")
    _buy(client, "Şekerleme", 40, n=3)              # kişi şekerlemeyi sık alıyor (eskiden en üstte çıkıyordu)
    got = _texts(client, "şeker")
    assert got[:2] == ["Şeker", "Şekerleme"]
    assert got.index("Toz Şeker") > got.index("Şekerleme")
    assert _texts(client, "seker")[:2] == ["Şeker", "Şekerleme"]   # Türkçe harfsiz
    assert _texts(client, "ŞEKER")[:2] == ["Şeker", "Şekerleme"]   # büyük harf
    # aynı katmanda kişinin geçmişi önce: "şek" ikisinde de ilk kelimenin başı → sık alınan şekerleme önce
    assert _texts(client, "şek")[0] == "Şekerleme"


def test_typo_and_short_queries(client):
    _cache("SEKER", "seker", "Şeker")
    assert "Şeker" in _texts(client, "sekr")       # bir harf hata
    assert "Şeker" in _texts(client, "şekre")
    assert _texts(client, "") == [] and _texts(client, "ş") == [] and _texts(client, " s ") == []
    assert "Şeker" not in _texts(client, "ek")     # iki harf: yalnız kelime başı


def test_enter_text_resolves_exact_group(client):
    """Ekle'ye basınca (öneri seçmeden) yazılan ad kendi grubuna bağlanır: "şeker" → Şeker (Şekerleme değil)."""
    _buy(client, "Şekerleme", 40, n=2)
    r = client.post("/api/shopping-list", json={"text": "şeker"}).json()
    assert r["group"]["key"].split("|")[0] == "seker"


def test_prices_groups_exact_first(client, resmi):
    _official([("BİM", "sekerleme", 50), ("BİM", "toz-seker|1kg", 45), ("BİM", "seker|1kg", 40), ("A101", "seker|1kg", 42)])
    _buy(client, "Şekerleme", 40, n=2)              # kendi aldığın şekerleme: eski sırada önce gelirdi
    got = client.get("/api/prices/groups", params={"q": "şeker"}).json()
    keys = [g["key"] for g in got]
    assert keys[0] == "seker|1kg" and keys.index("sekerleme") < keys.index("toz-seker|1kg")


# ---------------------------------------------------------------- serbest marka / mağaza + son seçilenler

def test_free_text_brand_and_store_and_recent(client):
    assert "shop_free_pick" in client.get("/api/meta").json()["capabilities"]
    a = client.post("/api/shopping-list", json={"text": "Süt"}).json()
    b = client.post("/api/shopping-list", json={"text": "Ekmek"}).json()
    assert client.get("/api/shopping-list/recent-picks").json() == {"brands": [], "stores": []}
    r = client.patch(f"/api/shopping-list/{a['id']}", json={"brand": "Örnekmarka Yok"}).json()
    assert r["brand"] == "Örnekmarka Yok"            # fiyat verisinde olmayan marka da kaydedilir
    r = client.patch(f"/api/shopping-list/{b['id']}", json={"store": "Örnek Bakkal"}).json()
    assert r["store"] == "Örnek Bakkal"
    client.patch(f"/api/shopping-list/{b['id']}", json={"brand": "Örnekfırın"})
    rp = client.get("/api/shopping-list/recent-picks").json()
    assert rp == {"brands": ["Örnekfırın", "Örnekmarka Yok"], "stores": ["Örnek Bakkal"]}
    # satırdan kaldırılsa da son seçilenlerde kalır; aynı ad (harf farkı) bir kez, yeni seçilen başa
    client.patch(f"/api/shopping-list/{a['id']}", json={"brand": None})
    client.patch(f"/api/shopping-list/{a['id']}", json={"brand": "ÖRNEKMARKA YOK"})
    rp = client.get("/api/shopping-list/recent-picks").json()
    assert [x.casefold() for x in rp["brands"]] == ["örnekmarka yok", "örnekfırın"]
    assert client.patch(f"/api/shopping-list/{a['id']}", json={"brand": "x" * 41}).status_code == 422


def test_auto_brand_is_not_a_recent_pick(client):
    from app import db

    with db.connect() as c:  # addan okunan marka (shop_brand_auto): kişi seçmedi
        c.execute("INSERT INTO shopping_list (text, brand, brand_auto, store) VALUES ('örnek çay', 'Örnekçay', 1, NULL)")
        c.execute("INSERT INTO shopping_list (text, brand, brand_auto, store) VALUES ('örnek su', 'Örneksu', 0, 'Örnek Bakkal')")
    assert client.get("/api/shopping-list/recent-picks").json() == {"brands": ["Örneksu"], "stores": ["Örnek Bakkal"]}
