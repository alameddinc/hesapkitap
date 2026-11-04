"""Alt kategori: ana kategorinin altında kullanıcı tanımlı ad listesi. İşlemin `category` alanı ana kategori kalır,
alt kategori ayrı `subcategory` alanında (bütçe, istatistik, kurallar, eski istemci bozulmaz). Uydurma veri."""
import io

from tests.test_book import _member


def _entry(client, desc="Örnek Market", amount=300, cat="Market & Gıda", sub=None, date="2026-10-10", **kw):
    body = {"entry_date": date, "description": desc, "amount": amount, "kind": "gider", "category": cat,
            "payment": "nakit", **kw}
    if sub is not None:
        body["subcategory"] = sub
    r = client.post("/api/entries", json=body)
    assert r.status_code == 200, r.text
    return r.json()["id"]


def _row(client, eid):
    return next(r for r in client.get("/api/transactions").json() if r["uid"] == f"m{eid}")


def _add(client, cat, name):
    r = client.post("/api/subcategories", json={"category": cat, "name": name})
    assert r.status_code == 200, r.text
    return r.json()


def test_crud_and_same_name_under_other_parents(client):
    a = _add(client, "Market & Gıda", "Temizlik")
    assert a["created"] and a["category"] == "Market & Gıda" and a["name"] == "Temizlik"
    again = _add(client, "Market & Gıda", "  temizlik ")  # büyük/küçük harf ve boşluk farkı: aynı alt kategori
    assert again["id"] == a["id"] and again["created"] is False
    _add(client, "Market & Gıda", "Manav")
    _add(client, "Diğer", "Market")  # aynı ad başka ana kategorinin altında serbest
    _add(client, "Diğer", "Temizlik")
    _add(client, "Market & Gıda", "Market & Gıda")  # ana kategoriyle aynı adlı alt kategori de serbest
    out = client.get("/api/subcategories").json()
    assert out["can_edit"] is True
    assert out["subcategories"] == {"Diğer": ["Market", "Temizlik"], "Market & Gıda": ["Temizlik", "Manav", "Market & Gıda"]}
    meta = client.get("/api/meta").json()
    assert meta["subcategories"] == out["subcategories"] and meta["subcategories_editable"] is True
    assert "subcategories" in meta["capabilities"]

    # sırala
    ids = {i["name"]: i["id"] for i in out["items"] if i["category"] == "Market & Gıda"}
    r = client.put("/api/subcategories/order", json={"category": "Market & Gıda", "ids": [ids["Manav"], ids["Market & Gıda"]]})
    assert r.json()["subcategories"]["Market & Gıda"] == ["Manav", "Market & Gıda", "Temizlik"]

    # yeniden adlandır: aynı ana kategoride çakışma 409, olmayan 404
    assert client.patch(f"/api/subcategories/{ids['Manav']}", json={"name": "temizlik"}).status_code == 409
    assert client.patch("/api/subcategories/9999", json={"name": "X"}).status_code == 404
    assert client.delete("/api/subcategories/9999").status_code == 404
    assert client.post("/api/subcategories", json={"category": "Kategorisiz", "name": "X"}).status_code == 422
    assert client.post("/api/subcategories", json={"category": "Diğer", "name": "   "}).status_code == 422


def test_entry_create_patch_rename_delete(client):
    t = _add(client, "Market & Gıda", "Temizlik")
    other = _add(client, "Diğer", "Temizlik")
    e1 = _entry(client, sub="temizlik")  # listedeki yazılışa çevrilir
    e2 = _entry(client, "Deterjan", 120, cat="Diğer", sub="Temizlik")
    assert _row(client, e1)["subcategory"] == "Temizlik" and _row(client, e1)["category"] == "Market & Gıda"

    # yeni ad formda yazıldı: yetkili kişide listeye kendiliğinden eklenir
    e3 = _entry(client, "Manav", 80, sub="Meyve & Sebze")
    assert _row(client, e3)["subcategory"] == "Meyve & Sebze"
    assert "Meyve & Sebze" in client.get("/api/meta").json()["subcategories"]["Market & Gıda"]

    # süzgeç: yalnız ana kategori + alt kategori
    rows = client.get("/api/transactions", params={"category": "Market & Gıda", "subcategory": "Temizlik"}).json()
    assert [r["uid"] for r in rows] == [f"m{e1}"]
    assert {r["uid"] for r in client.get("/api/transactions", params={"subcategory": "Temizlik"}).json()} == {f"m{e1}", f"m{e2}"}

    # yeniden adlandır: yalnız o ana kategorinin kayıtlarına yansır
    r = client.patch(f"/api/subcategories/{t['id']}", json={"name": "Ev temizliği"}).json()
    assert r["updated"] == 1 and r["name"] == "Ev temizliği"
    assert _row(client, e1)["subcategory"] == "Ev temizliği"
    assert _row(client, e2)["subcategory"] == "Temizlik"  # "Diğer › Temizlik" değişmedi

    # sil: kayıttan kalkar, ana kategori kalır
    assert client.delete(f"/api/subcategories/{other['id']}").json()["cleared"] == 1
    assert _row(client, e2)["subcategory"] is None and _row(client, e2)["category"] == "Diğer"

    # düzenle: alt kategori değişir / kaldırılır
    assert client.patch(f"/api/entries/{e1}", json={"subcategory": "Meyve & Sebze"}).status_code == 200
    assert _row(client, e1)["subcategory"] == "Meyve & Sebze"
    client.patch(f"/api/entries/{e1}", json={"subcategory": ""})
    assert _row(client, e1)["subcategory"] is None


def test_old_client_without_subcategory(client):
    """Eski istemci alanı hiç göndermez: kayıt eskisi gibi; ana kategori değişince eski alt kategori düşer."""
    _add(client, "Market & Gıda", "Temizlik")
    plain = _entry(client)
    assert _row(client, plain)["subcategory"] is None
    eid = _entry(client, sub="Temizlik")
    client.patch(f"/api/entries/{eid}", json={"note": "not"})  # alt kategoriye dokunmaz
    assert _row(client, eid)["subcategory"] == "Temizlik"
    client.patch(f"/api/entries/{eid}", json={"category": "Market & Gıda"})  # aynı ana kategori: kalır
    assert _row(client, eid)["subcategory"] == "Temizlik"
    client.patch(f"/api/entries/{eid}", json={"category": "Sağlık"})  # başka ana kategori: alt kategori düşer
    row = _row(client, eid)
    assert row["category"] == "Sağlık" and row["subcategory"] is None
    # alt kategorisiz ana kategorilerde kırılım anahtarı yok (eski yanıt biçimi)
    cats = client.get("/api/summary", params={"month": "2026-10"}).json()["by_category"]
    assert all("children" not in c for c in cats)
    # kategorisi olmayan kayda alt kategori yazılmaz
    nocat = client.post("/api/entries", json={"entry_date": "2026-10-11", "description": "X", "amount": 5, "kind": "gider",
                                              "subcategory": "Temizlik"}).json()["id"]
    assert _row(client, nocat)["subcategory"] is None


def test_card_txn_and_rule_assign_subcategory(client, upload):
    upload(["vakifbank_a.txt"])
    _add(client, "Market & Gıda", "Temizlik")
    card = [r for r in client.get("/api/transactions").json() if r["source"] == "card" and r["kind"] == "harcama"]
    txn = card[0]
    r = client.patch(f"/api/card-transactions/{txn['id']}", json={"category": "Market & Gıda", "subcategory": "Temizlik",
                                                                  "create_rule": True}).json()
    assert r["rule"]
    rule = next(x for x in client.get("/api/rules").json() if x["pattern"] == r["rule"])
    assert rule["category"] == "Market & Gıda" and rule["subcategory"] == "Temizlik"
    row = next(x for x in client.get("/api/transactions").json() if x["uid"] == txn["uid"])
    assert (row["category"], row["subcategory"]) == ("Market & Gıda", "Temizlik")

    # kural doğrudan: aynı işyerinin diğer (elle verilmemiş) işlemleri ana + alt kategoriyi kuraldan alır
    other = next(x for x in card if x["uid"] != txn["uid"])
    rid = client.post("/api/rules", json={"pattern": __import__("re").escape(other["description"].upper()[:12]),
                                          "category": "Diğer", "subcategory": "Kırtasiye", "priority": 1}).json()["id"]
    assert "Kırtasiye" in client.get("/api/meta").json()["subcategories"]["Diğer"]  # kuralda yazılan ad listeye girer
    row = next(x for x in client.get("/api/transactions").json() if x["uid"] == other["uid"])
    assert (row["category"], row["subcategory"]) == ("Diğer", "Kırtasiye")
    # kuralın ana kategorisi değişince (alt kategori gönderilmeden) eski alt kategori kalkar
    client.patch(f"/api/rules/{rid}", json={"category": "Sağlık"})
    row = next(x for x in client.get("/api/transactions").json() if x["uid"] == other["uid"])
    assert (row["category"], row["subcategory"]) == ("Sağlık", None)
    # kart işleminde yalnız ana kategori değişirse alt kategori düşer
    client.patch(f"/api/card-transactions/{txn['id']}", json={"category": "Sağlık"})
    row = next(x for x in client.get("/api/transactions").json() if x["uid"] == txn["uid"])
    assert row["subcategory"] is None


def test_statistics_breakdown(client):
    _add(client, "Market & Gıda", "Temizlik")
    _add(client, "Market & Gıda", "Manav")
    _entry(client, "A", 300, sub="Temizlik")
    _entry(client, "B", 200, sub="Manav")
    _entry(client, "C", 100, sub="Temizlik")
    _entry(client, "D", 50)  # alt kategorisiz
    _entry(client, "Eczane", 70, cat="Sağlık")
    s = client.get("/api/summary", params={"month": "2026-10"}).json()
    m = next(c for c in s["by_category"] if c["name"] == "Market & Gıda")
    assert m["amount"] == 650  # ana kategori toplamı değişmez (bütçe/istatistik aynı)
    assert m["children"] == [{"name": "Temizlik", "amount": 400, "count": 2}, {"name": "Manav", "amount": 200, "count": 1},
                             {"name": None, "amount": 50, "count": 1}]
    assert "children" not in next(c for c in s["by_category"] if c["name"] == "Sağlık")
    y = client.get("/api/year-summary", params={"year": 2026}).json()
    ym = next(c for c in y["categories"] if c["name"] == "Market & Gıda")
    assert ym["children"][0] == {"name": "Temizlik", "amount": 400, "count": 2}


def test_split_child_does_not_inherit_subcategory(client):
    _add(client, "Market & Gıda", "Temizlik")
    eid = client.post("/api/entries", json={"entry_date": "2026-10-10", "description": "Örnek Market", "amount": 300,
                                            "kind": "gider", "category": "Market & Gıda", "subcategory": "Temizlik",
                                            "payment": "nakit", "splits": [{"amount": 100, "category": "Sigara"}]}).json()["id"]
    rows = [r for r in client.get("/api/transactions").json() if r["uid"].startswith(f"m{eid}")]
    main = next(r for r in rows if r["uid"] == f"m{eid}")
    child = next(r for r in rows if r.get("split_of"))
    assert main["subcategory"] == "Temizlik" and child["category"] == "Sigara" and child["subcategory"] is None


def test_export_has_subcategory_column(client):
    from openpyxl import load_workbook

    _add(client, "Market & Gıda", "Temizlik")
    _entry(client, "Deterjan", 90, sub="Temizlik")
    wb = load_workbook(io.BytesIO(client.get("/api/settings/export.xlsx").content))
    rows = list(wb["İşlemler"].values)
    head = list(rows[0])
    assert head[4] == "Kategori" and head[5] == "Tutar (₺)" and head[-1] == "Alt kategori"  # eski sütunlar yerinde
    assert rows[1][2] == "Deterjan" and rows[1][-1] == "Temizlik"


def test_migration_adds_columns_to_old_book(tmpdb):
    with tmpdb.connect() as c:
        c.execute("DROP TABLE subcategories")
        for t in ("transactions", "entries", "rules"):
            c.execute(f"ALTER TABLE {t} DROP COLUMN subcategory")
    tmpdb.init_db()
    with tmpdb.connect() as c:
        for t in ("transactions", "entries", "rules"):
            assert "subcategory" in {r["name"] for r in c.execute(f"PRAGMA table_info({t})")}
        assert c.execute("SELECT COUNT(*) FROM subcategories").fetchone()[0] == 0
    assert tmpdb.subcategories_map() == {}


def test_shared_book_permissions(client, monkeypatch):
    from app import auth

    monkeypatch.delenv("HESAPKITAP_NO_AUTH", raising=False)
    auth._fails.clear()
    auth.create_admin("admin", "yonetici-sifre-1")
    assert client.post("/api/auth/login", json={"username": "admin", "password": "yonetici-sifre-1"}).status_code == 200
    owner = client
    _add(owner, "Market & Gıda", "Temizlik")
    editor = _member(owner, "esim")
    full = _member(owner, "kardes", role="full")
    viewer = _member(owner, "misafir", role="viewer")

    # hepsi görür
    for m in (editor, full, viewer):
        assert m.get("/api/subcategories").json()["subcategories"] == {"Market & Gıda": ["Temizlik"]}
    assert editor.get("/api/meta").json()["subcategories_editable"] is False
    assert full.get("/api/meta").json()["subcategories_editable"] is True
    # görür + ekler: listedekini seçer, yenisini açamaz
    assert editor.post("/api/subcategories", json={"category": "Diğer", "name": "X"}).status_code == 403
    assert viewer.post("/api/subcategories", json={"category": "Diğer", "name": "X"}).status_code == 403
    eid = _entry(editor, sub="Temizlik")
    assert _row(editor, eid)["subcategory"] == "Temizlik"
    bad = editor.post("/api/entries", json={"entry_date": "2026-10-10", "description": "Y", "amount": 5, "kind": "gider",
                                            "category": "Market & Gıda", "subcategory": "Yeni ad", "payment": "nakit"})
    assert bad.status_code == 422
    # sahibin kaydında üye alt kategoriyi değiştirebilir (kategori gibi)
    own = _entry(owner, "Sahip kaydı", 40)
    assert editor.patch(f"/api/entries/{own}", json={"subcategory": "Temizlik"}).status_code == 200
    assert _row(owner, own)["subcategory"] == "Temizlik"
    # tam yetkili ekler, yeniden adlandırır
    sid = full.post("/api/subcategories", json={"category": "Diğer", "name": "Hediye"}).json()["id"]
    assert full.patch(f"/api/subcategories/{sid}", json={"name": "Hediyeler"}).status_code == 200
    assert owner.get("/api/subcategories").json()["subcategories"]["Diğer"] == ["Hediyeler"]
