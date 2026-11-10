"""Borç kapatma planı (app/debt_plan.py, GET/PUT /api/debt-plan): sıralamalar, takvim, faizi önleyen tutar, yetkiler,
yasak ifadeler. Kart numaraları, kişiler ve tutarlar uydurmadır."""
import re
from datetime import date
from pathlib import Path

import pytest

from tests.test_book import _member, owner  # noqa: F401  (owner: fixture)

ROOT = Path(__file__).resolve().parent.parent
TODAY = date(2026, 11, 10)

# Sahibin kuralı: plan hiçbir zaman faizli bir yol önermez. Metinlerde bu ifadeler geçmez (büyük/küçük harf fark etmez).
BANNED = [r"kredi\s*çek", r"kredi\s*al", r"kredi\s*kullan", r"ihtiyaç\s*kredi", r"kredi\s*teklif", r"taksitlendir",
          r"borç\s*transfer", r"nakit\s*avans", r"ertele", r"refinans", r"yapılandır", r"asgari(yi)?\s+öde(?![a-zçğıöşü])",
          r"kredi\s*kartı\s*başvur", r"faizsiz\s*kredi", r"kampanya"]


def _banned_in(text: str) -> list[str]:
    t = text.replace("İ", "i").replace("I", "ı").lower()
    return [p for p in BANNED if re.search(p, t)]


def _card(key, remaining, due, rate=0.035, title=None):
    return {"key": key, "kind": "card", "title": title or key, "remaining": remaining, "due_date": due,
            "rate": {"monthly": rate, "source": "genel"}}


def _person(key, remaining, due=None, debt_date="2026-01-01", i=0):
    return {"key": key, "kind": "person", "title": key, "remaining": remaining, "due_date": due, "debt_date": debt_date,
            "id": i}


# ---------------------------------------------------------------- takvim (birim)

def test_cards_always_first_in_every_order():
    from app import debt_plan as dp

    cards = [_card("kart:b", 3000, "2026-11-28"), _card("kart:a", 5000, "2026-11-20")]
    people = [_person("borc:1", 1000, i=1), _person("borc:2", 4000, "2026-12-01", i=2)]
    for order in dp.ORDERS:
        sim = dp.simulate(cards, people, 9000, order, "2026-11")
        first = sim["months"][0]["payments"]
        assert [p["key"] for p in first][:2] == ["kart:a", "kart:b"], order  # son ödemesi önce gelen önce
        assert sim["interest"] == 0 and sim["closes"]["kart:a"] == sim["closes"]["kart:b"] == "2026-11"


def test_orderings_differ_for_personal_debts():
    from app import debt_plan as dp

    people = [_person("borc:big", 6000, "2027-01-15", "2026-03-01", 1), _person("borc:small", 1000, None, "2026-01-01", 2),
              _person("borc:soon", 3000, "2026-12-05", "2026-05-01", 3)]
    kucuk = dp.simulate([], people, 2000, "kucuk", "2026-11")
    assert kucuk["months"][0]["payments"][0]["key"] == "borc:small" and kucuk["closes"]["borc:small"] == "2026-11"
    soz = dp.simulate([], people, 2000, "soz", "2026-11")
    assert soz["months"][0]["payments"][0]["key"] == "borc:soon"  # söz verilen tarihi en yakın
    assert list(soz["closes"]) == ["borc:soon", "borc:big", "borc:small"]  # tarihsiz en sonda
    faiz = dp.simulate([], people, 2000, "faiz", "2026-11")
    pays = {p["key"]: p["amount"] for p in faiz["months"][0]["payments"]}
    assert set(pays) == {"borc:big", "borc:small", "borc:soon"}  # orantılı: herkes alır
    assert pays == {"borc:big": 1200.0, "borc:small": 200.0, "borc:soon": 600.0}
    for sim in (kucuk, soz, faiz):
        assert sim["debt_free"] == "2027-03" and sum(m["total"] for m in sim["months"]) == 10000


def test_proportional_share_redistributes_capped_amounts():
    from app import debt_plan as dp

    people = [_person("a", 100, i=1), _person("b", 9900, i=2)]
    out = dp._share(people, {"a": 100, "b": 9900}, 5000)
    assert round(sum(out.values()), 2) == 5000 and out["a"] == 50


def test_too_small_amount_shows_needed_amount_and_interest_warning():
    from app import debt_plan as dp

    cards = [_card("kart:a", 10000, "2026-11-20"), _card("kart:b", 6000, "2026-12-20")]
    sim = dp.simulate(cards, [], 5000, "faiz", "2026-11")
    assert sim["interest"] > 0 and sim["interest_by"]["kart:a"] > 0
    # kasımda 10.000 gerekir; aralıkta toplam 16.000 → ayda 8.000
    assert dp.min_monthly(cards, "2026-11") == 10000
    assert dp.min_monthly([_card("kart:a", 4000, "2026-11-20"), _card("kart:b", 12000, "2026-12-20")], "2026-11") == 8000
    ok = dp.simulate(cards, [], 10000, "faiz", "2026-11")
    assert ok["interest"] == 0 and ok["debt_free"] == "2026-12"
    # bu ay zaten ödenen bu ayın tutarından düşer: gereken aylık tutar ona göre
    assert dp.min_monthly(cards, "2026-11", paid_now=2000) == 12000


def test_zero_amount_is_single_month_and_never_cleared():
    from app import debt_plan as dp

    sim = dp.simulate([_card("kart:a", 1000, "2026-11-20")], [], 0, "faiz", "2026-11")
    assert len(sim["months"]) == 1 and sim["debt_free"] is None and sim["interest"] > 0


# ---------------------------------------------------------------- uçtan uca

def _seed_cards(tmpdb):
    with tmpdb.connect() as c:
        for pe, due, total, mn, card in (("2026-11-01", "2026-11-12", 12000.0, 4800.0, "4111***1111"),
                                         ("2026-11-05", "2026-11-25", 6000.0, 2400.0, "5400***2222")):
            c.execute("INSERT INTO statements (file_hash, filename, bank, card_no, period_end, due_date, total_debt, "
                      "min_payment) VALUES (?, 'x.pdf', 'Örnek Bank', ?, ?, ?, ?, ?)", (f"h{pe}{card}", card, pe, due, total, mn))
        sid = c.execute("SELECT id FROM statements WHERE card_no = '4111***1111'").fetchone()[0]
        c.execute("INSERT INTO transactions (statement_id, txn_date, description, amount, txn_type, category) VALUES "
                  "(?, '2026-11-01', 'ALIŞVERİŞ FAİZİ (Oran:3.75)', 12.5, 'faiz_ucret', 'Banka Faiz & Ücret')", (sid,))


def _seed_people(client):
    a = client.post("/api/debts", json={"person": "Örnek Kişi", "direction": "borc", "amount": 5000,
                                        "debt_date": "2026-06-01", "due_date": "2026-12-15", "note": "Elden"}).json()["id"]
    b = client.post("/api/debts", json={"person": "Komşu", "direction": "borc", "amount": 2000,
                                        "debt_date": "2026-08-01"}).json()["id"]
    client.post("/api/debts", json={"person": "Örnek Kişi", "direction": "alacak", "amount": 900, "debt_date": "2026-08-01"})
    client.post("/api/debts", json={"person": "Kuzen", "direction": "borc", "amount": 10, "asset": "GRAM_ALTIN",
                                    "debt_date": "2026-08-01"})
    return a, b


def test_build_gathers_cards_and_personal_debts(client, tmpdb):
    from app import debt_plan as dp

    _seed_cards(tmpdb)
    a, b = _seed_people(client)
    p = dp.build(monthly=10000, order="faiz", today=TODAY)
    assert p["totals"] == {**p["totals"], "open": 25000.0, "cards": 18000.0, "people": 7000.0, "count": 4,
                           "card_count": 2, "person_count": 2}
    assert [d["kind"] for d in p["debts"]] == ["card", "card", "person", "person"]
    first = p["debts"][0]
    assert first["due_date"] == "2026-11-12" and first["rate"] == {"monthly": 0.0375, "source": "ekstre"}
    # yalnız asgariyi ödersen: (12.000 − 4.800) × %3,75 × 1,3 = 351
    assert first["interest_if_min"] == 351.0 and "yalnız asgariyi ödersen bu ay yaklaşık ₺351 faiz işler" in first["warning"]
    assert first["payable"]["type"] == "statement" and first["payable"]["id"]
    assert p["excluded"] and "TL dışı" in p["excluded"][0]["text"]  # altın borcu planda yok
    assert all(d["title"] != "Örnek Kişi" or d["remaining"] == 5000 for d in p["debts"])  # alacak plana girmez
    # 10.000/ay: kasım 10.000 (kart 1 tamamı ... kart 2 kısmen), aralık kart 2 kalanı + elden borçlar
    assert p["shortfall"] is not None and p["shortfall"]["min_monthly"] == 18000
    assert p["shortfall"]["title"] == "Bu tutarla kart faizi önlenemiyor" and "₺18.000 yaparsan" in p["shortfall"]["text"]
    ok = dp.build(monthly=18000, today=TODAY)
    assert ok["shortfall"] is None and ok["totals"]["interest_projected"] == 0
    assert ok["debt_free"]["month"] == "2026-12" and "Aralık 2026" in ok["debt_free"]["text"]
    items = ok["this_month"]["items"]
    assert [i["key"] for i in items if not i["done"]][:2] == [d["key"] for d in ok["debts"][:2]]
    assert all(i["payable"]["id"] for i in items)
    assert all(d["progress"] == 0 for d in ok["debts"])


def test_payment_this_month_reduces_budget_and_shows_done(client, tmpdb):
    from app import debt_plan as dp

    _seed_cards(tmpdb)
    a, _ = _seed_people(client)
    assert client.post(f"/api/debts/{a}/pay", json={"amount": 1000, "paid_at": "2026-11-03"}).status_code == 200
    p = dp.build(monthly=20000, today=TODAY)
    assert p["totals"]["paid_this_month"] == 1000 and p["this_month"]["budget"] == 19000
    done = [i for i in p["this_month"]["items"] if i["done"]]
    assert [(i["title"], i["amount"]) for i in done] == [("Örnek Kişi", 1000.0)]
    person = next(d for d in p["debts"] if d["key"] == f"borc:{a}")
    assert person["remaining"] == 4000 and person["progress"] == 0.2


def test_promise_missed_warning_is_respectful(client, tmpdb):
    from app import debt_plan as dp

    client.post("/api/debts", json={"person": "Örnek Kişi", "direction": "borc", "amount": 9000,
                                    "debt_date": "2026-06-01", "due_date": "2026-11-30"})
    p = dp.build(monthly=3000, order="kucuk", today=TODAY)
    [w] = [w for w in p["warnings"] if w["key"].startswith("soz:")]
    assert "Ocak 2027" in w["text"] and "haber" in w["text"]


def test_endpoint_preview_and_save(client, tmpdb):
    _seed_cards(tmpdb)
    _seed_people(client)
    assert "debt_plan" in client.get("/api/meta").json()["capabilities"]
    r = client.get("/api/debt-plan").json()
    assert r["monthly_source"] in ("suggestion", None) and r["settings"] == {"monthly": None, "order": "faiz"}
    assert r["suggestion"] is None or r["suggestion"]["amount"] >= 0  # öneri hiçbir zaman eksi değil
    prev = client.get("/api/debt-plan", params={"monthly": 5000, "order": "soz"}).json()
    assert prev["monthly"] == 5000 and prev["order"] == "soz" and prev["monthly_source"] == "param"
    assert client.get("/api/debt-plan").json()["settings"]["monthly"] is None  # önizleme kaydetmez
    assert client.put("/api/debt-plan", json={"monthly": 7500.555, "order": "kucuk"}).json() == {"monthly": 7500.56, "order": "kucuk"}
    r = client.get("/api/debt-plan").json()
    assert r["monthly"] == 7500.56 and r["order"] == "kucuk" and r["monthly_source"] == "saved"
    assert client.put("/api/debt-plan", json={"monthly": -1}).status_code == 422
    assert client.put("/api/debt-plan", json={"order": "kredi"}).status_code == 422
    assert client.get("/api/debt-plan", params={"order": "x"}).status_code == 422
    assert client.put("/api/debt-plan", json={"monthly": None}).json()["monthly"] is None


def test_free_in_every_plan():
    from app import plans

    assert plans.tier("borc_plani") == plans.FREE
    assert plans.allows({"plan": "free"}, "borc_plani")


def test_empty_plan(client):
    r = client.get("/api/debt-plan", params={"monthly": 1000}).json()
    assert r["totals"]["count"] == 0 and r["empty"]["title"] == "Açık borcun görünmüyor" and r["months"] == []
    assert r["summary_card"] == {"eligible": False}


# ---------------------------------------------------------------- ortak defter

def test_shared_book_permissions_and_visibility(owner, tmpdb):  # noqa: F811
    _seed_cards(tmpdb)
    _seed_people(owner)
    viewer = _member(owner, "goren", role="viewer")
    editor = _member(owner, "esim")
    assert viewer.get("/api/debt-plan").status_code == 200
    assert viewer.get("/api/debt-plan").json()["can_edit"] is False
    assert viewer.put("/api/debt-plan", json={"monthly": 1000}).status_code == 403
    assert editor.put("/api/debt-plan", json={"monthly": 12000, "order": "soz"}).status_code == 200  # Aile birlikte planlar
    assert owner.get("/api/debt-plan").json()["settings"] == {"monthly": 12000.0, "order": "soz"}
    assert editor.get("/api/debt-plan").json()["totals"]["person_count"] == 2

    owner.put("/api/book/rules", json={"hidden_pages": ["borclar"], "hide_fees": True,
                                       "hidden_cards": ["5400***2222"]})
    r = editor.get("/api/debt-plan").json()
    assert r["totals"]["person_count"] == 0 and r["hidden"] == {"fees": True, "people": True}
    assert [d["key"] for d in r["debts"]] and all(d["kind"] == "card" for d in r["debts"])
    assert r["totals"]["card_count"] == 1  # gizli kart yok
    assert all(d["interest_if_min"] is None for d in r["debts"]) and r["totals"]["interest_projected"] is None
    low = editor.get("/api/debt-plan", params={"monthly": 100}).json()
    assert low["shortfall"]["interest"] is None and "faiz ve vergi işler" not in low["shortfall"]["text"]
    assert owner.get("/api/debt-plan").json()["totals"]["person_count"] == 2  # sahip her şeyi görür


# ---------------------------------------------------------------- yasak ifadeler

def _all_strings(p) -> list[str]:
    if isinstance(p, dict):
        return [s for v in p.values() for s in _all_strings(v)]
    if isinstance(p, list):
        return [s for v in p for s in _all_strings(v)]
    return [p] if isinstance(p, str) else []


def test_no_interest_steering_words_in_any_output(client, tmpdb):
    from app import debt_plan as dp

    _seed_cards(tmpdb)
    _seed_people(client)
    for monthly in (0, 100, 5000, 18000, 100000):
        for order in dp.ORDERS:
            p = dp.build(monthly=monthly, order=order, today=TODAY)
            bad = {s: _banned_in(s) for s in _all_strings(p) if _banned_in(s)}
            assert not bad, bad


def test_no_interest_steering_words_in_sources():
    """Kaynaktaki kullanıcıya giden dizgiler (tırnak içi). Açıklamalar ve yorumlar taranmaz: modülün ilke notu yasak
    ifadeleri ne yapılmayacağını anlatmak için adıyla anar. Mobil ekran ayrı depoda: e2e testi ekrandaki metni tarar."""
    files = [ROOT / "app" / "debt_plan.py", ROOT / "web" / "src" / "pages" / "DebtPlan.tsx"]
    for f in files:
        assert f.exists(), f
        text = f.read_text(encoding="utf-8")
        text = re.sub(r'"""[\s\S]*?"""', "", text)
        text = re.sub(r"/\*[\s\S]*?\*/", "", text)
        text = re.sub(r"(^|\s)(#|//)[^\n]*", "", text)
        strings = re.findall(r'"([^"\n]{3,})"|`([^`]{3,})`', text)
        flat = " | ".join(a or b for a, b in strings)
        assert not _banned_in(flat), (f.name, _banned_in(flat))


def test_banned_scanner_catches_examples():
    for s in ("Kredi çekerek kapat", "Borcunu taksitlendir", "Borç transferi yap", "Nakit avans kullan",
              "Ödemeyi ertele", "Asgari öde, rahatla", "Asgariyi öde."):
        assert _banned_in(s), s
    for s in ("Bu kartta yalnız asgariyi ödersen bu ay yaklaşık ₺351 faiz işler.", "yalnız asgari ödeme faizi durdurmaz",
              "Kredi kartı borcu"):
        assert not _banned_in(s), s


# ---------------------------------------------------------------- bildirim

def test_push_reminds_promised_day(client):
    from app import push

    client.post("/api/debts", json={"person": "Örnek Kişi", "direction": "borc", "amount": 5000,
                                    "debt_date": "2026-06-01", "due_date": "2026-11-12"})
    two = push._borc(date(2026, 11, 10))
    assert [i["kind"] for i in two] == ["borc"] and "2 gün sonra" in two[0]["title"] and two[0]["route"] == "#/diger/borc-plani"
    assert "₺5.000" in two[0]["body"]
    assert push._borc(date(2026, 11, 11)) == []
    assert "bugün" in push._borc(date(2026, 11, 12))[0]["title"]
    assert push.TYPES["borc"][1] is True


@pytest.mark.parametrize("order", ["faiz", "kucuk", "soz"])
def test_order_labels_and_texts(order):
    from app import debt_plan as dp

    label, text = dp.ORDER_INFO[order]
    assert label and "kart" in text.lower()


# ---------------------------------------------------------------- canlı önizleme (yazarken)

def test_live_preview_changes_months_without_saving(client, tmpdb):
    """İstemci yazarken GET ?monthly=… ile önizler: borçsuz ay ve takvim tutarla değişir, kayıtlı ayar değişmez."""
    _seed_cards(tmpdb)
    _seed_people(client)
    client.put("/api/debt-plan", json={"monthly": 73600})
    big = client.get("/api/debt-plan", params={"monthly": 73600}).json()
    small = client.get("/api/debt-plan", params={"monthly": 2500}).json()
    assert big["debt_free"]["months"] == 1 and small["debt_free"]["months"] > big["debt_free"]["months"]
    assert len(small["months"]) == small["debt_free"]["months"] and small["debt_free"]["label"] != big["debt_free"]["label"]
    assert small["shortfall"]["title"] == "Bu tutarla kart faizi önlenemiyor" and big["shortfall"] is None
    assert small["pace"] is None  # kart varken faiz uyarısı konuşur
    assert client.get("/api/debt-plan").json()["settings"]["monthly"] == 73600  # önizleme kaydetmedi
    r = client.get("/api/debt-plan", params={"order": "kucuk"}).json()
    assert r["order"] == "kucuk" and r["monthly"] == 73600 and r["settings"]["order"] == "faiz"


def test_pace_line_only_personal_debts(client):
    from app import debt_plan as dp

    _seed_people(client)  # 5.000 + 2.000 elden borç, kart yok
    p = dp.build(monthly=2000, today=TODAY)
    assert p["shortfall"] is None and p["pace"] == {"months": 4, "text": "Bu tutarla elden borçlar 4 ayda biter."}
    assert "(4 ay)" in p["debt_free"]["text"]
    assert dp.build(monthly=7000, today=TODAY)["pace"]["text"] == "Bu tutarla elden borçlar bu ay biter."
    assert dp.build(monthly=0, today=TODAY)["pace"] is None
    assert dp.build(monthly=1, today=TODAY)["pace"] is None  # 10 yılda bitmiyor: stuck konuşur


@pytest.mark.parametrize("saved", [None, 25000])
def test_first_load_field_and_plan_use_same_amount(client, tmpdb, monkeypatch, saved):
    """İlk açılış: kutudaki tutar (yanıttaki monthly) ile planın hesaplandığı tutar aynı. Kayıt yoksa öneri (kaynak
    "suggestion"), kayıt varsa kayıtlı tutar; öneri ayrıca gelir. Sahadan: kutu ₺73.600 gösterip plan ₺25.000 ile
    hesaplanmış gibi görünmüştü."""
    from app import debt_plan as dp

    _seed_cards(tmpdb)
    _seed_people(client)
    monkeypatch.setattr(dp, "suggestion", lambda today: {"amount": 73600.0, "months": 3, "text": "öneri"})
    if saved:
        client.put("/api/debt-plan", json={"monthly": saved})
    r = client.get("/api/debt-plan").json()
    want = saved or 73600
    assert r["monthly"] == want and r["monthly_source"] == ("saved" if saved else "suggestion")
    assert r["suggestion"]["amount"] == 73600 and r["settings"]["monthly"] == saved
    same = client.get("/api/debt-plan", params={"monthly": want}).json()
    assert r["debt_free"] == same["debt_free"] and r["months"] == same["months"]
    assert r["this_month"]["budget"] == same["this_month"]["budget"]
