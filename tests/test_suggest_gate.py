"""Kalem adı önerisi (GET /api/prices/suggest, yetenek price_suggest) her pakette ve ortak defter üyesinde açık.

15.11.2026 kullanıcı bildirimi: fiş onayında / kayıtlı harcamada "Kalem ekle"de ad önerisi çıkmıyordu. Kök neden: uç
"/api/prices" önekinde olduğu için paket kapısı (plans.GATED "fiyatlar", Premium) ücretsiz pakette 402, ortak defterde
"fiyatlar" sayfası kapalı üyeye 403 dönüyordu; istemci (AdOneri) hatada sessizce hiçbir şey göstermiyor. Fiş kalemi
düzenlemek her pakette var: öneri açık, ama fiyat takibi kapalıyken yanıtta fiyat / mağaza yok (yalnız adlar).
Ürünler ve mağazalar uydurmadır."""
from tests.test_book import _member, owner  # noqa: F401 - fixture
from tests.test_plans import _rc, _user, hosted  # noqa: F401 - fixture


def _buy(c, store="Örnek Büfe", name="Zirve Slims Mavi Sigara", amount=100.0):
    r = c.post("/api/entries", json={"entry_date": "2026-11-01", "description": store, "amount": amount, "kind": "gider",
                                     "category": "Market & Gıda", "payment": "nakit",
                                     "items": [{"name": name, "qty": 1, "amount": amount}]})
    assert r.status_code == 200, r.text


def test_gate_for_skips_suggest_only():
    from app import plans

    assert plans.gate_for("/api/prices/suggest") is None
    assert plans.gate_for("/api/prices") == "fiyatlar" and plans.gate_for("/api/prices/groups") == "fiyatlar"


def test_free_user_gets_names_without_prices(hosted):  # noqa: F811
    c, u = _user(hosted)
    _buy(c)
    assert c.get("/api/prices").status_code == 402  # fiyat takibi yine Premium
    r = c.get("/api/prices/suggest", params={"q": "zirv"})
    assert r.status_code == 200, r.text
    it = r.json()["items"][0]
    assert it["name"] == "Zirve Slims Mavi Sigara" and it["last_price"] is None and it["last_store"] is None
    assert any(i["name"] == "Soğuk Çay" for i in c.get("/api/prices/suggest", params={"q": "soğuk"}).json()["items"])
    # Premium: fiyat ve mağaza da gelir
    assert _rc(hosted, "INITIAL_PURCHASE", u["id"]).status_code == 200
    it = c.get("/api/prices/suggest", params={"q": "zirv"}).json()["items"][0]
    assert it["last_price"] == 100.0 and it["last_store"] == "Örnek Büfe"


def test_member_with_prices_page_hidden_gets_names(owner):  # noqa: F811
    _buy(owner)
    m = _member(owner)
    assert m.get("/api/prices/suggest", params={"q": "zirv"}).json()["items"][0]["last_price"] == 100.0
    owner.put("/api/book/rules", json={"hidden_pages": ["fiyatlar"]})
    assert m.get("/api/prices").status_code == 403  # sayfa kapalı
    r = m.get("/api/prices/suggest", params={"q": "zirv"})
    assert r.status_code == 200, r.text
    it = r.json()["items"][0]
    assert it["name"] == "Zirve Slims Mavi Sigara" and it["last_price"] is None and it["last_store"] is None
    assert owner.get("/api/prices/suggest", params={"q": "zirv"}).json()["items"][0]["last_price"] == 100.0
