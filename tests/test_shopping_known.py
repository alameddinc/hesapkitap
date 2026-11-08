"""Alışveriş listesi: kendi aldığın ürüne bağlama (yapay zekâdan önce), hemen sınıflandırma (maliyet sınırlarıyla),
kendi ürünlerinden öneri, kafe yeri (own_place), plan satırında daha ucuz zincir (cheaper_elsewhere). Ürünler, işyerleri
ve fiyatlar uydurmadır."""
from datetime import date

import pytest

from app import price_sources, product_groups as pg, shopping, shopping_known, system
from tests.test_book import owner  # noqa: F401  (fixture)

TODAY = date.today().isoformat()


@pytest.fixture
def resmi(monkeypatch):
    monkeypatch.setenv("HESAPKITAP_PRICE_SOURCES", "own,community,resmi")
    monkeypatch.setattr(price_sources, "official_configured", lambda: True)


def _official(rows, day=TODAY, region="İstanbul"):
    with system.connect() as c:
        c.executemany("INSERT INTO official_prices (chain, group_key, title, brand, price, day, region) VALUES (?, ?, ?, ?, ?, ?, ?)",
                      [(ch, g, f"Örnek {g}", "Örnekmarka", p, day, region) for ch, g, p in rows])


def _buy(client, store, name, price, category="Market & Gıda", day=TODAY):
    r = client.post("/api/entries", json={"entry_date": day, "description": store, "amount": price, "kind": "gider",
                                          "category": category, "payment": "nakit",
                                          "items": [{"name": name, "qty": 1, "amount": price}]})
    assert r.status_code == 200, r.text


def _cache(name, key, label, category=None):
    """Yapay zekânın daha önce verdiği grup (sunucu geneli önbellek)."""
    with system.connect() as c:
        pg._store(c, pg.name_norm(name), {"key": key, "label": label, "category": category}, "ai")


def _queued() -> list[str]:
    with system.connect() as c:
        return [r[0] for r in c.execute("SELECT name FROM product_group_queue ORDER BY name")]


# ---------------------------------------------------------------- 1. kendi ürününe bağlama

def test_typed_name_pins_to_own_product_before_ai(client):
    _cache("ORNEKSAN SLIMS MAVI SIGARA", "sigara", "Sigara", "Tütün")
    _cache("DIGERMARKA SIGARA", "sigara", "Sigara", "Tütün")
    _buy(client, "Örnekbüfe", "ORNEKSAN SLIMS MAVI SIGARA", 95)
    _buy(client, "Köşe Bakkal", "DIGERMARKA SIGARA", 80)            # aynı grupta başka ürün: daha ucuz ama başka ürün
    row = client.post("/api/shopping-list", json={"text": "Orneksan slims mavi"}).json()
    assert row["group"]["key"] == "sigara" and not row["group"].get("pending")
    assert row["own_product"] == {"name": "Orneksan Slims Mavi Sigara", "price": 95.0, "place": "Örnekbüfe",
                                  "date": TODAY}
    assert "Orneksan slims mavi" not in _queued()                   # yapay zekâya gitmedi
    [it] = client.get("/api/shopping-list").json()["items"]
    assert it["best"]["chain"] == "Örnekbüfe" and it["best"]["price"] == 95.0 and it["best"]["source"] == "own"
    assert it["best"]["product"] == "Orneksan Slims Mavi Sigara" and it["best"]["line_total"] == 95.0
    opt = client.get("/api/shopping-list/optimize").json()
    line = opt["plans"][0]["stops"][0]["items"][0]
    assert (opt["plans"][0]["stops"][0]["chain"], line["price"], line["product"]) == \
        ("Örnekbüfe", 95.0, "Orneksan Slims Mavi Sigara")


def test_match_rules():
    prods = [{"tokens": frozenset({"ORNEKSAN", "SLIMS", "MAVI", "SIGARA"}), "last_date": "2026-11-01", "count": 3,
              "name": "a"},
             {"tokens": frozenset({"ORNEKSAN", "KIRMIZI", "SIGARA"}), "last_date": "2026-11-02", "count": 1, "name": "b"},
             {"tokens": frozenset({"TUZLU", "KRAKER", "ORNEKKRAK"}), "last_date": "2026-10-01", "count": 1, "name": "c"}]
    assert shopping_known.match("orneksan slims", prods)["name"] == "a"       # ≥2 kelimenin hepsi içinde
    assert shopping_known.match("orneksan sigara", prods)["name"] == "b"      # ikisi de içeriyor: en yeni
    assert shopping_known.match("orneksan", prods) is None                    # tek kelime, iki ürün: belirsiz
    assert shopping_known.match("ornekkrak", prods)["name"] == "c"            # tek kelime, tek ürün
    assert shopping_known.match("orneksan kirmizi sigara uzun", prods)["name"] == "b"  # Jaccard 3/4
    assert shopping_known.match("bambaşka şey", prods) is None


def test_catalog_contains_before_ai(client, monkeypatch):
    from app import product_catalog

    product_catalog.store([
        {"product_id": "p1", "title": "Örnekköy Zeytinyağlı Yaprak Sarma 400 g", "brand": "Örnekköy"},
        {"product_id": "p2", "title": "Örnekköy Zeytinyağlı Yaprak Sarma 200 g", "brand": "Örnekköy"},
        {"product_id": "p3", "title": "Başkaköy Biber Dolma 400 g", "brand": "Başkaköy"}])
    with system.connect() as c:
        gen = c.execute("SELECT generic FROM catalog_products WHERE product_id = 'p1'").fetchone()[0]
    assert gen and product_catalog.contains("yaprak sarma örnekköy") == gen
    assert product_catalog.contains("yaprak") is None                          # tek kelime, iki ürün
    assert product_catalog.contains("yaprak dolma") is None                    # hepsi aynı üründe değil
    # akış: kural / önbellek tanımasa da katalog içerirse grup; kuyruğa girmez
    monkeypatch.setattr(pg, "catalog_contains", lambda name: {"key": "sarma", "label": "Sarma"} if "QQSARMA" in
                        pg.name_norm(name) else None)
    row = client.post("/api/shopping-list", json={"text": "qqsarma zzkoy"}).json()
    assert row["group"] == {"key": "sarma", "label": "Sarma"} and _queued() == []
    product_catalog.store([])


# ---------------------------------------------------------------- 2. hemen sınıflandırma (maliyet sınırları)

@pytest.fixture
def ai(monkeypatch):
    from app import receipt

    monkeypatch.setenv("GEMINI_API_KEY", "test")
    monkeypatch.setattr(pg, "_spawn", lambda fn: fn())     # arka plan iş parçacığı yerine hemen
    calls = []

    def fake(prompt, max_output_tokens=None, kind="kategori", model=None):
        lines = [ln for ln in prompt.splitlines() if ln[:1].isdigit() and ". " in ln]
        calls.append([ln.split(". ", 1)[1] for ln in lines])
        return {"items": [{"i": int(ln.split(".")[0]), "g": "Kraker", "c": "Atıştırmalık"} for ln in lines]}, {}

    monkeypatch.setattr(receipt, "gemini_text_json", fake)
    return calls


def test_pending_row_classified_at_once_only_own_names(owner, ai):  # noqa: F811
    pg.enqueue(["ESKIKUYRUK URUN 1", "ESKIKUYRUK URUN 2"])          # başkalarının eski adları kuyrukta
    row = owner.post("/api/shopping-list", json={"text": "Zzörnek çıtır"}).json()
    assert ai == [["Zzörnek çıtır"]]                                 # tek çağrı, yalnız bu ad
    assert set(_queued()) == {"ESKIKUYRUK URUN 1", "ESKIKUYRUK URUN 2"}
    [it] = owner.get("/api/shopping-list").json()["items"]
    assert it["id"] == row["id"] and it["group"]["key"] == "kraker" and not it["group"].get("pending")
    owner.post("/api/shopping-list", json={"text": "zzörnek  çıtır"})  # aynı ad: önbellekten, ikinci çağrı yok
    assert len(ai) == 1


def test_list_ai_caps(owner, ai, monkeypatch):  # noqa: F811
    monkeypatch.setattr(pg, "LIST_AI_NAMES_PER_USER_DAY", 2)
    for t in ("Zzbir urun", "Zziki urun", "Zzuc urun"):
        owner.post("/api/shopping-list", json={"text": t})
    assert ai == [["Zzbir urun"], ["Zziki urun"]]                   # kişi başı günlük ad sınırı
    assert "Zzuc urun" in _queued()
    owner.get("/api/shopping-list")                                  # yeniden okuma da sınırı aşmaz
    assert len(ai) == 2
    monkeypatch.setattr(pg, "LIST_AI_NAMES_PER_USER_DAY", 20)
    system.put(pg._list_key(), str(pg.LIST_AI_CALLS_PER_DAY))        # sunucu geneli liste çağrısı tavanı
    owner.post("/api/shopping-list", json={"text": "Zzdort urun"})
    assert len(ai) == 2


def test_list_ai_respects_daily_gate(owner, ai, monkeypatch):  # noqa: F811
    from app import receipt

    def gate():
        raise receipt.DailyLimitError("sınır")

    monkeypatch.setattr(receipt, "gemini_text_json", lambda *a, **k: gate())
    owner.post("/api/shopping-list", json={"text": "Zzbes urun"})
    with system.connect() as c:
        assert c.execute("SELECT tries FROM product_group_queue WHERE name = 'Zzbes urun'").fetchone()[0] == 0


def test_no_ai_without_key(client, monkeypatch):
    called = []
    monkeypatch.setattr(pg, "_spawn", lambda fn: called.append(fn))
    client.post("/api/shopping-list", json={"text": "Zzaltı urun"})
    assert called == [] and "Zzaltı urun" in _queued()


# ---------------------------------------------------------------- 3. öneri

def test_suggest_own_product_names(client):
    _cache("ORNEKSAN SLIMS MAVI SIGARA", "sigara", "Sigara", "Tütün")
    _buy(client, "Örnekbüfe", "ORNEKSAN SLIMS MAVI SIGARA", 95)
    s = client.get("/api/shopping-list/suggest?q=ornek").json()["items"]
    p = next(x for x in s if x["source"] == "product")
    assert p == {"text": "Orneksan Slims Mavi Sigara", "group_key": None, "label": "Orneksan Slims Mavi Sigara · son ₺95",
                 "source": "product", "count": 1, "last_price": 95.0, "place": "Örnekbüfe"}
    assert client.get("/api/shopping-list/suggest?q=slim").json()["items"][0]["text"] == "Orneksan Slims Mavi Sigara"
    assert not [x for x in client.get("/api/shopping-list/suggest?q=zzyok").json()["items"] if x["source"] == "product"]


def test_suggest_is_cached_per_book(client, monkeypatch):
    from app import db

    _buy(client, "Örnekbüfe", "ORNEKSAN SLIMS MAVI SIGARA", 95)
    client.get("/api/shopping-list/suggest?q=ornek")
    calls = []
    real = db.priced_items
    monkeypatch.setattr(db, "priced_items", lambda *a, **k: calls.append(1) or real(*a, **k))
    for q in ("o", "or", "orn", "ornek"):
        client.get(f"/api/shopping-list/suggest?q={q}")
    assert calls == []                                               # her tuşta fişler yeniden okunmaz
    _buy(client, "Örnekbüfe", "ORNEKSAN KIRMIZI SIGARA", 90)        # yeni fiş: önbellek tazelenir
    assert "Orneksan Kirmizi Sigara" in [x["text"] for x in client.get("/api/shopping-list/suggest?q=ornek").json()["items"]]


# ---------------------------------------------------------------- 4. kafe yeri

def test_cafe_purchase_adds_own_place_keeps_market_match(client, resmi):
    _official([("ŞOK", "latte|250ml", 27.5)])
    _buy(client, "Örnekkahve", "LATTE", 120, category="Kafe & Restoran")
    row = client.post("/api/shopping-list", json={"text": "Latte"}).json()
    assert row["own_place"] == {"place": "Örnekkahve", "price": 120.0}
    [it] = client.get("/api/shopping-list").json()["items"]
    assert it["group"]["key"] == "latte|250ml" and it["best"]["chain"] == "ŞOK" and it["best"]["price"] == 27.5
    assert it["own_place"] == {"place": "Örnekkahve", "price": 120.0}
    opt = client.get("/api/shopping-list/optimize").json()
    assert "outside" not in opt
    line = opt["plans"][0]["stops"][0]["items"][0]
    assert opt["plans"][0]["stops"][0]["chain"] == "ŞOK" and line["own_place"] == {"place": "Örnekkahve", "price": 120.0}
    assert client.get("/api/shopping-list/plan").json()["items"][0]["own_place"]["place"] == "Örnekkahve"


def test_market_purchase_has_no_own_place(client, resmi):
    _official([("ŞOK", "latte|250ml", 27.5)])
    _buy(client, "BİM", "LATTE", 26)
    assert client.post("/api/shopping-list", json={"text": "Latte"}).json()["own_place"] is None


# ---------------------------------------------------------------- 5. plan satırında daha ucuz zincir

def test_cheaper_elsewhere_on_plan_line(client, resmi):
    _official([("BİM", "sut|1l", 30), ("BİM", "ekmek|250g", 10), ("BİM", "sivi-deterjan|2.5l", 44.9),
               ("Hakmar", "sivi-deterjan|2.5l", 41.5)])  # 2,5 L deterjan sıvıdır
    ids = {t: client.post("/api/shopping-list", json={"text": t}).json()["id"]
           for t in ("Süt 1 L", "Ekmek 250 g", "Deterjan 2,5 L")}
    opt = client.get("/api/shopping-list/optimize").json()
    assert opt["recommended_k"] == 1
    lines = {x["id"]: x for x in opt["plans"][0]["stops"][0]["items"]}
    assert lines[ids["Deterjan 2,5 L"]]["cheaper_elsewhere"] == {"chain": "Hakmar", "price": 41.5}
    assert "cheaper_elsewhere" not in lines[ids["Süt 1 L"]]
    two = opt["plans"][1]
    assert all("cheaper_elsewhere" not in x for s in two["stops"] for x in s["items"])  # en ucuzu zaten birleşimde
