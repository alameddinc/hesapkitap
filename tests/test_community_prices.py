"""Anonim topluluk fiyatları: yalnız zincir, yalnız okunmuş fiş, günlük tuzlu katkı özeti, en az 3 kişi eşiği."""
import json
from datetime import date

from app import community_prices as cp
from app import system
from tests.test_book import owner  # noqa: F401  (fixture)

DAY = date(2026, 11, 6)
MILK = [{"name": "Torku Süt 1L", "qty": 1, "amount": 36}]


def _rows():
    with system.connect() as c:
        return [dict(r) for r in c.execute("SELECT * FROM community_prices ORDER BY id")]


def test_contributor_is_daily_and_unlinkable():
    a, b = cp.contributor(1, DAY), cp.contributor(2, DAY)
    assert a != b and a == cp.contributor(1, DAY) and len(a) == 24
    assert cp.contributor(1, date(2026, 11, 7)) != a          # ertesi gün yeni tuz
    assert cp.contributor(1, DAY) != a                         # eski günün tuzu geri gelmez
    assert set(json.loads(system.get(cp.SALT_KEY))) == {"day", "salt"}  # yalnız bugünün tuzu; eski gün yok


def test_contribute_only_known_chain_and_products():
    assert cp.contribute(1, "Örnek Bakkal", None, MILK, "2026-11-05", DAY) == 0            # zincir değil
    assert cp.contribute(None, "A101", None, MILK, "2026-11-05", DAY) == 0                 # kullanıcısız
    items = MILK + [{"name": "İNDİRİM", "qty": 1, "amount": -5}, {"name": "A101", "qty": 1, "amount": 10},
                    {"name": "Ekmek", "qty": 2, "amount": 30}, {"name": "Su", "qty": 1, "amount": None}]
    assert cp.contribute(1, "A101 YENİ MAĞAZACILIK", None, items, "2026-11-05", DAY) == 2
    rows = _rows()
    # sütün altındaki İNDİRİM satırı sütün indirimi: net fiyatla, indirimli diye (normal ortancaya girmez)
    assert [(r["chain"], r["group_key"], r["unit_price"], r["day"], r["discounted"]) for r in rows] == [
        ("A101", "sut|1l", 31.0, "2026-11-05", 1), ("A101", "ekmek", 15.0, "2026-11-05", 0)]
    assert set(rows[0]) == {"id", "chain", "group_key", "name_norm", "unit_price", "day", "contributor", "region",
                            "discounted"}  # şube/kişi yok
    assert rows[0]["region"] is None                                                         # şube yazısı yok: il yok


def test_pref_off_stops_contribution(client):
    from app import prefs

    assert prefs.price_share(5) is True                                                   # varsayılan açık
    prefs.set_price_share(5, False)
    assert cp.contribute(5, "A101", None, MILK, "2026-11-05", DAY) == 0
    prefs.set_price_share(5, True)
    assert cp.contribute(5, "A101", None, MILK, "2026-11-05", DAY) == 1


def test_median_needs_three_contributors():
    for uid, price in ((1, 36), (2, 38)):
        cp.contribute(uid, "A101", None, [{"name": "Süt 1 L", "qty": 1, "amount": price}], "2026-11-05", DAY)
    assert cp.medians(today=DAY) == {}                                                    # 2 kişi: gösterilmez
    for p in (40, 40, 40):  # aynı kişinin çok satırı tek kişi sayılır ve ortancayı çekmez
        cp.contribute(3, "A101", None, [{"name": "Süt 1 L", "qty": 1, "amount": p}], "2026-11-05", DAY)
    assert cp.medians(today=DAY) == {("A101", "sut|1l"): {"median": 38.0, "n": 3}}
    assert cp.median_for("A101", "sut|1l", DAY)["n"] == 3 and cp.median_for("BİM", "sut|1l", DAY) is None
    assert cp.medians(today=date(2026, 11, 30)) == {}                                     # 7 günden eski


def test_relabel_after_ai_learns(monkeypatch):
    from app import product_groups, receipt

    for uid in (1, 2, 3):
        cp.contribute(uid, "Migros", None, [{"name": "ORNEKMARKA SUT 1L", "qty": 1, "amount": 40}], "2026-11-05", DAY)
    assert {r["group_key"] for r in _rows()} == {"ornekmarka-sut|1l"}                     # geçici grup
    monkeypatch.setenv("GEMINI_API_KEY", "test")
    monkeypatch.setattr(receipt, "gemini_text_json", lambda *a, **k: ({"items": [{"i": 0, "g": "Süt"}]}, {}))
    assert product_groups.classify_pending() == 1
    assert {r["group_key"] for r in _rows()} == {"sut|1l"}
    assert cp.medians(today=DAY)[("Migros", "sut|1l")]["n"] == 3


def test_digit_check_uses_community_median():
    from app import receipt_check

    items = [{"name": "Süt 1 L", "qty": 1, "amount": 69.0}, {"name": "Ekmek", "qty": 1, "amount": 15.0}]
    without = receipt_check.digit_check(items, 104.0)
    out = receipt_check.digit_check(items, 104.0, community={0: 89.0})
    fix = out["digit_fix"][0]
    assert (fix["index"], fix["to"], fix["community_price"]) == (0, 89.0, 89.0) and "community" in fix["evidence"]
    assert fix["community_note"] == "başkaları ₺89,00 ödüyor"
    assert fix["score"] > next((f["score"] for f in without["digit_fix"] if f["index"] == 0), -99)


def test_saved_receipt_contributes_and_endpoint_toggles(owner):  # noqa: F811
    from app import db

    jpg = b"\xff\xd8\xff\xe0 ornek fis"
    name = f"{db.file_hash(jpg)}.jpg"
    assert db.store_file("receipts", name, jpg, "image/jpeg")
    body = {"entry_date": "2026-11-05", "description": "Migros", "amount": 36, "kind": "gider",
            "category": "Market & Gıda", "payment": "nakit", "items": MILK}
    assert owner.post("/api/entries", json=body).status_code == 200                       # elle: katılmaz
    assert _rows() == []
    assert owner.post("/api/entries", json={**body, "receipt_file": name}).status_code == 200
    assert len(_rows()) == 1
    r = owner.get("/api/me/price-share").json()
    assert r["on"] is True and "en az 3 kişiden" in r["text"]
    assert owner.get("/api/meta").json()["price_share"]["on"] is True
    assert owner.put("/api/me/price-share", json={"on": False}).json()["on"] is False
    assert owner.post("/api/entries", json={**body, "receipt_file": name}).status_code == 200
    assert len(_rows()) == 1


def test_price_share_via_ui_prefs(owner):  # noqa: F811
    """İstemci sözleşmesi: meta.ui.price_share ve PUT /api/me/ui {price_share} (öbür görünüm tercihleri gibi)."""
    assert owner.get("/api/meta").json()["ui"]["price_share"] is True
    r = owner.put("/api/me/ui", json={"price_share": False}).json()
    assert r["price_share"] is False and "price_share" not in owner.put("/api/me/ui", json={"simple": True}).json()
    assert owner.get("/api/meta").json()["ui"]["price_share"] is False
    assert owner.get("/api/me/price-share").json()["on"] is False                # aynı tercih
    assert owner.put("/api/me/ui", json={"price_share": True}).json()["price_share"] is True


def test_account_deletion_keeps_anonymous_rows():
    from app import prefs

    uid = system.create_user("ornek@example.com", None)["id"]
    prefs.set_price_share(uid, False)
    prefs.set_price_share(uid, True)
    cp.contribute(uid, "BİM", None, MILK, "2026-11-05", DAY)
    prefs.set_price_share(uid, False)
    system.delete_user(uid)
    assert len(_rows()) == 1 and system.get(f"price_share:{uid}") is None
