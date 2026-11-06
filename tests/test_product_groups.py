"""Ürün grupları: markadan bağımsız ürün + boy (kurallar, önbellek, toplu yapay zekâ)."""
import pytest

from app import product_groups as pg


@pytest.mark.parametrize("name, key, label", [
    ("Torku Süt 1L", "sut|1l", "Süt · 1 L"),
    ("SÜTAŞ GÜNLÜK SÜT 1L", "sut|1l", "Süt · 1 L"),
    ("PINAR SÜT 1000 ML", "sut|1l", "Süt · 1 L"),
    ("Erikli Su 1,5 L", "su|1.5l", "Su · 1,5 L"),
    ("SU 6X500ML", "su|6x500ml", "Su · 6×500 ml"),
    ("AYRAN 200 ML X6", "ayran|6x200ml", "Ayran · 6×200 ml"),
    ("ÜLKER ÇİKOLATALI GOFRET 36GR", "gofret|36g", "Gofret · 36 g"),
    ("YUMURTA 10'LU", "yumurta|10ad", "Yumurta · 10 adet"),
    ("TOZ ŞEKER 5 KG", "toz-seker|5kg", "Toz Şeker · 5 kg"),
    ("Kaşar 500 gr", "kasar-peyniri|500g", "Kaşar Peyniri · 500 g"),
    ("Ekmek", "ekmek", "Ekmek"),
])
def test_rule_groups(name, key, label):
    g = pg.rule_group(name)
    assert (g["key"], g["label"]) == (key, label)


def test_rule_group_unknown_brand_and_weighed():
    assert pg.rule_group("Örnekmarka Süt 1L") is None                    # bilinmeyen kelime: yapay zekâya kalır
    assert pg.rule_group("Örnekmarka Süt 1L", brand="Örnekmarka")["key"] == "sut|1l"  # kalemin markası atılır
    assert pg.rule_group("BEYAZ PEYNİR", unit="kg") == {"key": "beyaz-peynir|kg", "label": "Beyaz Peynir · kg",
                                                         "size_value": 1.0, "size_unit": "kg"}


def test_name_norm():
    assert pg.name_norm("SÜTAŞ GÜNLÜK SÜT 1L") == "SUTAS GUNLUK SUT 1 L"
    assert pg.name_norm("Su 1,5L.") == "SU 1,5 L"
    assert pg.name_norm("6X200ML") == "6 X 200 ML"


def test_groups_for_caches_rules_and_queues_unknown():
    from app import system

    out = pg.groups_for([{"name": "Torku Süt 1L"}, {"name": "ORNK CIK.GOFR 36G"}, {"name": ""}])
    assert out[0] == {"key": "sut|1l", "label": "Süt · 1 L"}
    assert out[1]["pending"] is True and out[1]["key"].endswith("|36g")   # geçici grup, boy yine okunur
    assert out[2] is None
    with system.connect() as c:
        assert c.execute("SELECT source FROM product_groups WHERE name_norm = 'TORKU SUT 1 L'").fetchone()[0] == "rule"
        assert [r[0] for r in c.execute("SELECT name FROM product_group_queue")] == ["ORNK CIK.GOFR 36G"]
    assert pg.pending() == 1


def test_classify_pending_batches_and_caps(monkeypatch):
    from app import ai_costs, receipt, system

    monkeypatch.setenv("GEMINI_API_KEY", "test")
    names = [f"ORNEKURUN{i} 1L" for i in range(60)] + ["POSET"]
    pg.groups_for([{"name": n} for n in names])
    calls = []

    def fake(prompt, max_output_tokens=None, kind="kategori", model=None):
        calls.append((kind, model, prompt.count("\n")))
        lines = [ln for ln in prompt.splitlines() if ln[:1].isdigit()]
        items = [{"i": int(ln.split(".")[0]), "g": None if "POSET" in ln else "Süt"} for ln in lines]
        ai_costs.record(None, kind, model, {"promptTokenCount": 900, "candidatesTokenCount": 600})
        return {"items": items}, {}

    monkeypatch.setattr(receipt, "gemini_text_json", fake)
    assert pg.classify_pending(max_calls=5) == 61
    assert len(calls) == 2 and all(k == "grup" and m == receipt.DEFAULT_MODEL for k, m, _ in calls)  # ≤50 ad / çağrı
    assert pg.pending() == 0
    assert pg.group_for("ORNEKURUN7 1L") == {"key": "sut|1l", "label": "Süt · 1 L"}       # önbellekten (source ai)
    assert pg.group_for("POSET") is None                                                 # ürün değil
    with system.connect() as c:
        assert c.execute("SELECT COUNT(*) FROM ai_calls WHERE kind = 'grup'").fetchone()[0] == 2
    # günlük tavan
    pg.groups_for([{"name": "ORNEKYENI 1L"}])
    system.put(pg._day_key(), str(pg.AI_CALLS_PER_DAY))
    assert pg.classify_pending(max_calls=1) == 0 and pg.pending() == 1


def test_classify_pending_without_key_does_nothing():
    pg.groups_for([{"name": "ORNEKURUN 1L"}])
    assert pg.classify_pending() == 0 and pg.pending() == 1


def test_products_have_group(client):
    for store, name, price in (("A101", "Torku Süt 1L", 36), ("BİM", "SÜTAŞ GÜNLÜK SÜT 1L", 38)):
        client.post("/api/entries", json={"entry_date": "2026-11-01", "description": store, "amount": price,
                                          "kind": "gider", "category": "Market & Gıda", "payment": "nakit",
                                          "items": [{"name": name, "qty": 1, "amount": price}]})
    ps = client.get("/api/prices").json()["products"]
    assert {p["group"]["key"] for p in ps} == {"sut|1l"} and len(ps) == 2
