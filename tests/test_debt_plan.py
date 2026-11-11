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
          r"kredi\s*kartı\s*başvur", r"faizsiz\s*kredi", r"kampanya",
          # asgariyi strateji olarak önermek yasak; yalnız zararı azaltma cümlesi "en az asgari tutarı … yatır" geçer
          r"(?<!en az )asgari(yi)?\s+(tutar\w*\s+)?(\([^)]*\)\s+)?(öde|yatır)(?![a-zçğıöşü])"]
# Zararı azaltma sözleri: yalnız tutar kartın tamamına son ödeme gününe kadar yetmiyorsa (aksi halde tamamını öde)
HARM = [r"en\s+az\s+asgari", r"gecikme\s+faizi\s+işlemez", r"kapatamıyorsun"]


def _low(text: str) -> str:
    return text.replace("İ", "i").replace("I", "ı").lower()


def _banned_in(text: str) -> list[str]:
    return [p for p in BANNED if re.search(p, _low(text))]


def _harm_in(text: str) -> list[str]:
    return [p for p in HARM if re.search(p, _low(text))]


def _card(key, remaining, due, rate=0.035, title=None, min_payment=None):
    return {"key": key, "kind": "card", "title": title or key, "remaining": remaining, "due_date": due,
            "rate": {"monthly": rate, "source": "genel"}, "min_payment": min_payment, "total": remaining, "paid": 0.0}


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
    # orantılı pay küçüğe ₺200 düşerdi (< ₺250): küçük borç bütün olarak kapanır, kalan 1.000 ötekilere orantılı, ₺50 katı
    assert pays == {"borc:small": 1000.0, "borc:big": 650.0, "borc:soon": 350.0}
    assert {p["key"]: p["amount"] for p in faiz["months"][1]["payments"]} == {"borc:big": 1350.0, "borc:soon": 650.0}
    for sim in (kucuk, soz, faiz):
        assert sim["debt_free"] == "2027-03" and sum(m["total"] for m in sim["months"]) == 10000


def test_proportional_share_closes_small_person_whole():
    from app import debt_plan as dp

    people = [_person("a", 100, i=1), _person("b", 9900, i=2)]
    out = dp.allocate_people(people, {"a": 100, "b": 9900}, 5000, "faiz")
    assert out == {"a": 100.0, "b": 4900.0}  # a'nın payı ₺50 olurdu: bütün olarak kapanır


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
    assert first["interest_if_min"] == 351.0
    # 10.000 iki kartın 18.000'ine yetmiyor: uyarı zararı azaltma adımlarına döner (ayrıntı: test_harm_*)
    assert first["harm"] and first["warning"].startswith("Bu ay tamamını kapatamıyorsun.")
    assert first["payable"]["type"] == "statement" and first["payable"]["id"]
    assert p["excluded"] and "TL dışı" in p["excluded"][0]["text"]  # altın borcu planda yok
    assert all(d["title"] != "Örnek Kişi" or d["remaining"] == 5000 for d in p["debts"])  # alacak plana girmez
    # 10.000/ay: kasım 10.000 (kart 1 tamamı ... kart 2 kısmen), aralık kart 2 kalanı + elden borçlar
    assert p["shortfall"] is not None and p["shortfall"]["min_monthly"] == 18000
    assert p["shortfall"]["title"] == "Bu tutarla kart faizi önlenemiyor" and "₺18.000 yaparsan" in p["shortfall"]["text"]
    ok = dp.build(monthly=18000, today=TODAY)
    assert ok["shortfall"] is None and ok["totals"]["interest_projected"] == 0
    assert "yalnız asgariyi ödersen bu ay yaklaşık ₺351 faiz işler" in ok["debts"][0]["warning"]
    assert all(d.get("harm") is None for d in ok["debts"])
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
    harms = [d["harm"] for d in low["debts"] if d.get("harm")]
    assert harms and all(h["compare"] is None and h["interest_planned"] is None and "₺" not in h["steps"][1] for h in harms)
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
    files = [ROOT / "app" / "debt_plan.py", ROOT / "web" / "src" / "pages" / "DebtPlan.tsx",
             # ödeme planı olan borç: kayıt formu ve hesap (yalnız kayıt; yapılandırma vb. önerisi yok)
             ROOT / "app" / "debt_schedule.py", ROOT / "web" / "src" / "components" / "Debts.tsx",
             # sonradan eklenen ödeme planı (borcun alt kaydı): bölüm, form ve hesap
             ROOT / "web" / "src" / "components" / "DebtScheduleSection.tsx", ROOT / "web" / "src" / "lib" / "odemePlani.ts",
             # Alırsam ne olur? (mobil Alsam mı? aynı lib/alsamMi.ts): alım ekranı da faizli yolu önermez
             ROOT / "web" / "src" / "components" / "Simulator.tsx", ROOT / "web" / "src" / "lib" / "alsamMi.ts"]
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
    for s in ("Asgari tutarı yatır, yeter.", "Asgariyi yatır.", "Asgari tutarı (₺4.800) yatır."):
        assert _banned_in(s), s
    for s in ("Bu kartta yalnız asgariyi ödersen bu ay yaklaşık ₺351 faiz işler.", "yalnız asgari ödeme faizi durdurmaz",
              "Kredi kartı borcu", "Son ödeme gününden (18 Kasım) önce en az asgari tutarı (₺9.578) yatır — gecikme "
              "faizi işlemez."):
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


# ---------------------------------------------------------------- zararı azaltma (tutar kartın tamamına yetmiyorsa)

def test_shortfall_allocation_puts_every_minimum_first_then_highest_rate():
    from app import debt_plan as dp

    cards = [_card("kart:a", 10000, "2026-11-15", rate=0.03, min_payment=3000),
             _card("kart:b", 8000, "2026-11-25", rate=0.045, min_payment=2000)]
    people = [_person("borc:1", 1000, i=1)]
    sim = dp.simulate(cards, people, 9000, "faiz", "2026-11")
    pays = {p["key"]: p["amount"] for p in sim["months"][0]["payments"]}
    # asgariler (3.000 + 2.000), kalan 4.000 faizi yüksek karta (b); elden borca bu ay kalmaz
    assert pays == {"kart:a": 3000.0, "kart:b": 6000.0}
    # asgarilere bile yetmiyorsa: son ödemesi önce gelenin asgarisi önce
    low = {p["key"]: p["amount"] for p in dp.simulate(cards, people, 4000, "faiz", "2026-11")["months"][0]["payments"]}
    assert low == {"kart:a": 3000.0, "kart:b": 1000.0}
    # aynı oranda borcu büyük olan önce
    same = [_card("kart:a", 4000, "2026-11-15", min_payment=1000), _card("kart:b", 9000, "2026-11-25", min_payment=2000)]
    eq = {p["key"]: p["amount"] for p in dp.simulate(same, [], 5000, "faiz", "2026-11")["months"][0]["payments"]}
    assert eq == {"kart:a": 1000.0, "kart:b": 4000.0}
    # tutar yetiyorsa eski sıra: son ödeme gününe göre tamamı, sonra elden borç
    ok = {p["key"]: p["amount"] for p in dp.simulate(cards, people, 19000, "faiz", "2026-11")["months"][0]["payments"]}
    assert ok == {"kart:a": 10000.0, "kart:b": 8000.0, "borc:1": 1000.0}
    # gelecek ay son ödemesi olan kart bu ay "yetmiyor" sayılmaz: bu ay a'nın tamamı
    later = [_card("kart:a", 10000, "2026-11-15", min_payment=3000), _card("kart:b", 6000, "2026-12-20", min_payment=1500)]
    nov = {p["key"]: p["amount"] for p in dp.simulate(later, [], 10000, "faiz", "2026-11")["months"][0]["payments"]}
    assert nov == {"kart:a": 10000.0}


def test_harm_reduction_steps_and_comparison(client, tmpdb):
    from app import debt_plan as dp

    _seed_cards(tmpdb)
    _seed_people(client)
    p = dp.build(monthly=10000, today=TODAY)
    c1, c2 = p["debts"][0], p["debts"][1]
    # 10.000: asgariler 4.800 + 2.400, kalan 2.800 faizi yüksek (%3,75) karta → 7.600 / 2.400
    h = c1["harm"]
    assert h["title"] == "Bu ay tamamını kapatamıyorsun" and h["planned"] == 7600 and h["min_left"] == 4800
    assert h["steps"][0] == "Son ödeme gününden (12 Kasım) önce en az asgari tutarı (₺4.800) yatır — gecikme faizi işlemez."
    # (12.000 − 7.600) × %3,75 × 1,3 = 214,5 → ~₺215
    assert "planladığın ₺7.600 ile bu ay yaklaşık ₺215 faiz işler (tamamını ödersen ₺0)" in h["steps"][1]
    assert [(r["key"], r["amount"], r["interest"]) for r in h["compare"]] == [
        ("full", 12000.0, 0.0), ("plan", 7600.0, 215.0), ("asgari", 4800.0, 351.0)]  # Tamamı önce, asgari en son
    assert c2["harm"]["planned"] == 2400 and c2["harm"]["compare"][1]["interest"] == 164.0  # 3.600 × %3,5 × 1,3
    assert "gecikme faizi" in h["info"] and "akdi" in h["info"]
    # asgari borcun tamamıysa karşılaştırmada ayrı "Yalnız asgari" satırı yok
    from app import debt_plan as _dp
    whole = _dp._harm({**c1, "min_payment": 20000, "paid": 0.0, "kind": "card", "remaining": 12000.0}, {"months": []}, "2026-11", False)
    assert whole["min_left"] == 12000 and [r["key"] for r in whole["compare"]] == ["full", "plan"]
    assert "asgarisini" in p["shortfall"]["allocation"] and "faizi en yüksek" in p["shortfall"]["allocation"]
    item = next(i for i in p["this_month"]["items"] if i["key"] == c1["key"])
    assert item["amount"] == 7600 and "(asgari ₺4.800 dahil)" in item["text"]
    assert [w["text"] for w in p["warnings"] if w["key"] == f"faiz:{c1['key']}"] == [c1["warning"]]
    # asgarilere bile yetmiyor: planlanan asgarinin altında, gecikme faizi uyarısı
    low = dp.build(monthly=5000, today=TODAY)["debts"][1]["harm"]
    assert low["below_min"] and low["planned"] == 200 and low["compare"][1]["late"] and "asgarinin altında" in low["steps"][1]


def test_interest_plan_is_not_shown_as_success(client, tmpdb):
    """Tutar faizi önlemiyorsa: borçsuz ay cümlesi faizi söyler, "Planı başlat" ikincil ve faizsiz tutarı anar
    (istemci uyarıyı borçsuz ayın üstünde gösterir). Tutar yetiyorsa eski sözler."""
    from app import debt_plan as dp

    _seed_cards(tmpdb)
    _seed_people(client)
    p = dp.build(monthly=10000, today=TODAY)
    assert p["shortfall"] and p["debt_free"]["interest"] is True
    assert p["debt_free"]["text"].endswith(f" Bu tutarla kartlarda yaklaşık {dp._tl(p['shortfall']['interest'])} faiz işler.")
    offer = p["commit_offer"]
    assert offer["secondary"] is True and offer["title"] == "Bu tutar faizi önlemiyor"
    assert "₺18.000 yaparsan kartlarda faiz işlemez" in offer["text"] and offer["action"] == "Planı başlat"
    ok = dp.build(monthly=18000, today=TODAY)
    assert "interest" not in ok["debt_free"] and "faiz" not in ok["debt_free"]["text"]
    assert ok["commit_offer"]["title"] == "Bu planla başla" and not ok["commit_offer"].get("secondary")


def test_suggestion_never_rounds_below_interest_free(client, tmpdb, monkeypatch):
    """Öneri artanın 100'e aşağı yuvarlısı. Artan faizsiz tutara yetiyorsa öneri en az o tutar (yuvarlama faizli plan
    önermesin); yetmiyorsa öneri cümlesi faizsiz tutarı söyler. Kayıtlı ya da yazılan tutara dokunulmaz."""
    from app import debt_plan as dp

    _seed_cards(tmpdb)
    _seed_people(client)
    monkeypatch.setattr(dp, "suggestion", lambda today: {"amount": 18000.0, "raw": 18000.0, "months": 3, "text": "öneri."})
    need = dp.build(today=TODAY)["totals"]["min_monthly_no_interest"]
    assert need == 18000
    monkeypatch.setattr(dp, "suggestion", lambda today: {"amount": 17900.0, "raw": 18050.0, "months": 3, "text": "öneri."})
    r = dp.build(today=TODAY)
    assert r["monthly_source"] == "suggestion" and r["monthly"] == 18000 and r["suggestion"]["amount"] == 18000
    assert r["shortfall"] is None
    monkeypatch.setattr(dp, "suggestion", lambda today: {"amount": 9000.0, "raw": 9050.0, "months": 3, "text": "öneri."})
    r = dp.build(today=TODAY)
    assert r["monthly"] == 9000 and r["shortfall"]
    assert r["suggestion"]["text"] == "öneri. Faizsiz kapatmak için ayda ₺18.000 gerekiyor."
    assert dp.build(monthly=9000, today=TODAY)["suggestion"]["text"] == "öneri."  # yazılan tutarda öneri olduğu gibi


def test_no_harm_reduction_text_when_full_payment_possible(client, tmpdb):
    from app import debt_plan as dp

    _seed_cards(tmpdb)
    _seed_people(client)
    for monthly in (18000, 25000, 100000):
        for order in dp.ORDERS:
            p = dp.build(monthly=monthly, order=order, today=TODAY)
            assert p["shortfall"] is None and all(d.get("harm") is None for d in p["debts"])
            found = {s: _harm_in(s) for s in _all_strings(p) if _harm_in(s)}
            assert not found, found
            # tamamı ödenebiliyorken asgari bir seçenek değil: uyarı "Tamamını öde"
            assert all("Tamamını öde" in d["warning"] for d in p["debts"] if d["kind"] == "card" and d.get("warning"))
    short = dp.build(monthly=10000, today=TODAY)
    assert any(_harm_in(s) for s in _all_strings(short))


# ---------------------------------------------------------------- planı başlat (anlık görüntü)

def _people_only(client):
    a = client.post("/api/debts", json={"person": "Örnek Kişi", "direction": "borc", "amount": 5000,
                                        "debt_date": "2026-06-01"}).json()["id"]
    b = client.post("/api/debts", json={"person": "Komşu", "direction": "borc", "amount": 2000,
                                        "debt_date": "2026-08-01"}).json()["id"]
    return a, b


def test_commit_saves_snapshot_and_preview_does_not_change_it(client):
    from app import debt_plan as dp

    _people_only(client)
    p = dp.build(monthly=2000, order="kucuk", today=TODAY)
    assert p["commitment"] is None and p["commit_offer"]["action"] == "Planı başlat"
    p = dp.commit_plan(monthly=2000, order="kucuk", today=TODAY)
    c = p["commitment"]
    assert c["start_month"] == "2026-11" and c["monthly"] == 2000 and c["order"] == "kucuk" and not c["differs"]
    assert c["debt_free"]["month"] == "2027-02" and c["month_no"] == 1 and c["month_count"] == 4
    assert c["status"] == {"key": "ok", "title": "Plana uygun ✓", "text": "Bu ay planda ₺2.000 daha var."}
    assert c["changed"] is None and p["commit_offer"] is None and p["settings"] == {"monthly": 2000.0, "order": "kucuk"}
    prev = dp.build(monthly=3500, today=TODAY)
    assert prev["commitment"]["differs"] and prev["commitment"]["monthly"] == 2000
    assert prev["commitment"]["continue"]["action"] == "Bu planla devam"
    assert dp._store()["current"]["monthly"] == 2000  # önizleme kaydı değiştirmedi


def test_commit_progress_on_track_behind_ahead_and_done(client):
    from app import debt_plan as dp

    a, b = _people_only(client)
    dp.commit_plan(monthly=2000, order="kucuk", today=TODAY)  # Kas: Komşu 2.000 · Ara–Şub: Örnek Kişi
    dec = date(2026, 12, 10)
    # Kasım'da hiç ödenmedi: geride, yetişmek için bu ay 4.000; plan değişti uyarısı
    c = dp.build(today=dec)["commitment"]
    assert c["status"]["key"] == "behind" and c["status"]["title"] == "Planın ₺2.000 gerisindesin"
    assert c["status"]["text"].startswith("Bu ay ₺4.000 ödersen yetişirsin.")
    assert [r["key"] for r in c["changed"]["reasons"]] == ["missed"] and c["changed"]["text"] == "Kasım'da plandaki ödeme eksik kaldı"
    assert c["changed"]["action"] == "Planı yeniden oluştur"
    for s in _all_strings(c):
        assert not _banned_in(s) and not _harm_in(s), s  # kartta yetmeme yok: asgari sözü de yok
    client.post(f"/api/debts/{b}/pay", json={"amount": 2000, "paid_at": "2026-11-20"})
    c = dp.build(today=dec)["commitment"]
    assert c["status"] == {"key": "ok", "title": "Plana uygun ✓", "text": "Bu ay planda ₺2.000 daha var."}
    assert c["changed"] is None and c["month_no"] == 2
    client.post(f"/api/debts/{a}/pay", json={"amount": 3000, "paid_at": "2026-11-25"})
    c = dp.build(today=dec)["commitment"]
    assert c["status"]["key"] == "ahead"
    assert c["status"] == {"key": "ahead", "title": "Planın önündesin", "text": "Şubat 2027 yerine Aralık'ta bitebilir."}
    client.post(f"/api/debts/{a}/pay", json={"amount": 2000, "paid_at": "2026-12-05"})
    assert dp.build(today=dec)["commitment"]["status"]["key"] == "done"


def test_new_debt_marks_plan_changed_and_replan_keeps_history(client):
    from app import debt_plan as dp

    _people_only(client)
    dp.commit_plan(monthly=2000, order="kucuk", today=TODAY)
    client.post("/api/debts", json={"person": "Kuzen", "direction": "borc", "amount": 1500, "debt_date": "2026-11-20"})
    dec = date(2026, 12, 10)
    c = dp.build(today=dec)["commitment"]
    assert c["changed"]["title"] == "Plan değişti" and "Yeni borç eklendi: Kuzen" in c["changed"]["text"]
    assert "Önceki plan" in c["changed"]["confirm"]
    p = dp.commit_plan(today=dec)  # yeniden oluştur: kayıtlı tutar ve sıralamayla, bugünkü borçlar
    c = p["commitment"]
    assert c["start_month"] == "2026-12" and c["changed"] is None and c["status"]["key"] == "ok"
    assert {x["title"] for x in dp._store()["current"]["debts"]} == {"Örnek Kişi", "Komşu", "Kuzen"}
    assert [h["label"] for h in p["commit_history"]] == ["Önceki plan: Kasım'da başladı · ayda ₺2.000"]
    for i in range(7):
        dp.commit_plan(today=dec)
    assert len(dp._store()["history"]) == dp.HISTORY


def test_commit_tracks_card_payments(client, tmpdb):
    from app import debt_plan as dp

    _seed_cards(tmpdb)
    p = dp.commit_plan(monthly=18000, today=TODAY)
    sid = p["debts"][0]["id"]
    assert p["commitment"]["this_target"] == 18000
    assert client.post(f"/api/statements/{sid}/pay", json={"amount": 12000, "paid_at": "2026-11-11"}).status_code == 200
    c = dp.build(today=TODAY)["commitment"]
    assert c["paid"] == 12000 and c["status"]["text"] == "Bu ay planda ₺6.000 daha var."


def test_commit_endpoint_permissions_and_errors(owner, tmpdb):  # noqa: F811
    assert owner.post("/api/debt-plan/commit", json={"monthly": 1000}).status_code == 422  # açık borç yok
    _seed_cards(tmpdb)
    assert "debt_plan_commit" in owner.get("/api/meta").json()["capabilities"]
    r = owner.post("/api/debt-plan/commit", json={})
    assert r.status_code == 422 and "tutarı yaz" in r.json()["detail"]
    viewer = _member(owner, "goren", role="viewer")
    editor = _member(owner, "esim")
    assert viewer.post("/api/debt-plan/commit", json={"monthly": 5000}).status_code == 403
    r = editor.post("/api/debt-plan/commit", json={"monthly": 20000, "order": "soz"})
    assert r.status_code == 200 and r.json()["commitment"]["monthly"] == 20000
    assert owner.get("/api/debt-plan").json()["commitment"]["order"] == "soz"
    assert owner.get("/api/debt-plan", params={"monthly": 100}).json()["commitment"]["differs"] is True


def test_behind_on_card_advice_is_only_pay_more_or_minimum(client, tmpdb):
    from app import debt_plan as dp

    import json

    _seed_cards(tmpdb)
    dp.commit_plan(monthly=10000, today=TODAY)
    # plan bir ay önce başlamış gibi: ekim hedefi (5.000) ödenmedi
    store = dp._store()
    store["current"]["start_month"] = "2026-10"
    store["current"]["months"].insert(0, {"month": "2026-10", "total": 5000, "pays": {"card:4111***1111": 5000}})
    store["current"]["debts"][0]["planned"] += 5000
    tmpdb.set_setting(dp.COMMIT, json.dumps(store))
    p = dp.build(monthly=10000, today=TODAY)
    c = p["commitment"]
    assert c["status"]["key"] == "behind"
    assert "en az asgari tutarı yatır" in c["status"]["text"]  # kartta tutar yetmiyor: zararı azaltma
    bad = {s: _banned_in(s) for s in _all_strings(p) if _banned_in(s)}
    assert not bad, bad
