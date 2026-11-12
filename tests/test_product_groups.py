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
                                                         "size_value": 1.0, "size_unit": "kg",
                                                         "category": "Süt ve Kahvaltılık", "subcategory": "Peynir"}


def test_name_norm():
    assert pg.name_norm("SÜTAŞ GÜNLÜK SÜT 1L") == "SUTAS GUNLUK SUT 1 L"
    assert pg.name_norm("Su 1,5L.") == "SU 1,5 L"
    assert pg.name_norm("6X200ML") == "6 X 200 ML"


def test_groups_for_caches_rules_and_queues_unknown():
    from app import system

    out = pg.groups_for([{"name": "Torku Süt 1L"}, {"name": "ORNK CIK.GOFR 36G"}, {"name": ""}])
    assert out[0] == {"key": "sut|1l", "label": "Süt · 1 L", "category": "Süt ve Kahvaltılık",
                      "subcategory": "Süt"}
    assert out[1]["pending"] is True and out[1]["key"].endswith("|36g")   # geçici grup, boy yine okunur
    assert out[2] is None
    with system.connect() as c:
        assert c.execute("SELECT source FROM product_groups WHERE name_norm = 'TORKU SUT 1 L'").fetchone()[0] == "rule"
        assert [r[0] for r in c.execute("SELECT name FROM product_group_queue")] == ["ORNK CIK.GOFR 36G"]
    assert pg.pending() == 1


def test_classify_pending_batches_and_caps(monkeypatch):
    from app import ai_costs, receipt, system

    monkeypatch.setenv("GEMINI_API_KEY", "test")
    monkeypatch.setattr(pg, "BATCH", 50)  # parçalamayı küçük sayıyla sına (gerçek BATCH daha büyük)
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
    assert pg.group_for("ORNEKURUN7 1L") == {"key": "sut|1l", "label": "Süt · 1 L", "category": "Süt ve Kahvaltılık",
                      "subcategory": "Süt"}       # önbellekten (source ai)
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


@pytest.mark.parametrize("name, key, label", [
    ("ÇİKOLATALI GÜNLÜK SÜT 200 ML", "cikolatali-sut|200ml", "Çikolatalı Süt · 200 ml"),   # aromalı süt ayrı ürün
    ("Çilekli Süt 3x200ml", "cilekli-sut|3x200ml", "Çilekli Süt · 3×200 ml"),
    ("KAKAOLU SÜT 200ML", "kakaolu-sut|200ml", "Kakaolu Süt · 200 ml"),
    ("WHEY SÜT 330 ML", "protein-icecegi|330ml", "Protein İçeceği · 330 ml"),           # protein içeceği süt değil
    ("LAKTOZSUZ YARIM YAĞLI SÜT 1L", "laktozsuz-sut|1l", "Laktozsuz Süt · 1 L"),
    ("KEÇİ SÜTÜ 1 L", "keci-sutu|1l", "Keçi Sütü · 1 L"),
    ("YARIM YAĞLI SÜT 1 L", "sut|1l", "Süt · 1 L"),                                      # yağ oranı aynı grup
    ("LIGHT SÜT 1L", "sut|1l", "Süt · 1 L"),
    ("Meyveli Yoğurt 500 g", "meyveli-yogurt|500g", "Meyveli Yoğurt · 500 g"),
])
def test_variants_are_separate_groups(name, key, label):
    g = pg.rule_group(name)
    assert (g["key"], g["label"]) == (key, label)


def test_refine():
    assert pg.refine("Süt", ["WHEY", "SUT"]) == "Protein İçeceği"
    assert pg.refine("Süt", ["HARRAS", "SUT"]) == "Süt"
    assert pg.refine("Gofret", ["CIKOLATALI", "GOFRET"]) == "Gofret"       # atıştırmalıkta aroma grubu değiştirmez
    assert pg.refine("Laktozsuz Süt", ["LAKTOZSUZ", "CIKOLATALI"]) == "Çikolatalı Süt"
    assert pg.refine(None, ["SUT"]) is None


def test_query_parts():
    assert pg.query_parts("1l süt") == ("sut", "1l")
    assert pg.query_parts("süt 1 lt") == ("sut", "1l")
    assert pg.query_parts("Süt") == ("sut", None)
    assert pg.query_parts("süt 1") == ("sut", None)
    assert pg.query_parts("sut|500ml") == ("sut", "500ml")


def test_ai_generic_answer_is_refined(monkeypatch):
    from app import receipt

    monkeypatch.setenv("GEMINI_API_KEY", "test")
    pg.groups_for([{"name": "ORNEKMARKA WHEY SUT 330ML"}, {"name": "ORNEKMARKA SUT 1L"}])

    def fake(prompt, max_output_tokens=None, kind="kategori", model=None):
        assert "Protein İçeceği" in prompt and "Çikolatalı Süt" in prompt       # sıkılaştırılmış istem
        lines = [ln for ln in prompt.splitlines() if ln[:1].isdigit() and ". " in ln]
        return {"items": [{"i": int(ln.split(".")[0]), "g": "Süt"} for ln in lines]}, {}

    monkeypatch.setattr(receipt, "gemini_text_json", fake)
    assert pg.classify_pending() == 2
    assert pg.group_for("ORNEKMARKA WHEY SUT 330ML") == {"key": "protein-icecegi|330ml",
                                                         "label": "Protein İçeceği · 330 ml", "category": "Su ve İçecek",
                                                         "subcategory": "Diğer İçecekler"}
    assert pg.group_for("ORNEKMARKA SUT 1L") == {"key": "sut|1l", "label": "Süt · 1 L", "category": "Süt ve Kahvaltılık",
                      "subcategory": "Süt"}


def test_regroup_fixes_old_rows_and_moves_community_prices():
    from app import system

    with system.connect() as c:
        def put(nn, key, label, source):
            c.execute("INSERT OR REPLACE INTO product_groups (name_norm, group_key, label, source, created_at) "
                      "VALUES (?, ?, ?, ?, '2026-11-01 00:00:00')", (nn, key, label, source))
        put("CIKOLATALI GUNLUK SUT 200 ML", "sut|200ml", "Süt · 200 ml", "rule")       # eski kural sonucu
        put("ORNEKMARKA WHEY SUT 330 ML", "sut|330ml", "Süt · 330 ml", "ai")           # eski yapay zekâ sonucu
        put("ORNEKMARKA GUNLUK SUT 1 L", "sut|1l", "Süt · 1 L", "ai")                  # bilinmeyen kelime: yeniden sor
        put("TORKU SUT 1 L", "sut|1l", "Süt · 1 L", "rule")                            # değişmez
        put("POSET", "", "", "ai")
        for nn, key in (("CIKOLATALI GUNLUK SUT 200 ML", "sut|200ml"), ("ORNEKMARKA WHEY SUT 330 ML", "sut|330ml"),
                        ("TORKU SUT 1 L", "sut|1l")):
            c.execute("INSERT INTO community_prices (chain, group_key, name_norm, unit_price, day, contributor) "
                      "VALUES ('A101', ?, ?, 30, '2026-11-05', 'x')", (key, nn))
        got = pg.regroup(c)
        assert got == {"changed": 2, "queued": 1}
        groups = dict(c.execute("SELECT name_norm, group_key FROM product_groups").fetchall())
        assert groups["CIKOLATALI GUNLUK SUT 200 ML"] == "cikolatali-sut|200ml"
        assert groups["ORNEKMARKA WHEY SUT 330 ML"] == "protein-icecegi|330ml"
        assert groups["TORKU SUT 1 L"] == "sut|1l" and groups["POSET"] == ""
        assert dict(c.execute("SELECT name_norm, group_key FROM community_prices").fetchall()) == {
            "CIKOLATALI GUNLUK SUT 200 ML": "cikolatali-sut|200ml", "ORNEKMARKA WHEY SUT 330 ML": "protein-icecegi|330ml",
            "TORKU SUT 1 L": "sut|1l"}
        assert [r[0] for r in c.execute("SELECT name_norm FROM product_group_queue")] == ["ORNEKMARKA GUNLUK SUT 1 L"]
        assert pg.regroup(c) == {"changed": 0, "queued": 1}                              # tekrar: değişiklik yok


def test_regroup_runs_in_background_not_on_open():
    """Yeniden gruplama bağlantı açılışında değil, arka planda parça parça (07.11.2026 canlı kilitlenme): açılışta sürüm
    yazılmaz; regroup_tick bitirince yazılır, ikinci çağrıda iş yoktur."""
    from app import system

    assert pg.regroup_pending()
    with system.connect() as c:
        c.execute("INSERT OR REPLACE INTO product_groups (name_norm, group_key, label, source, created_at) "
                  "VALUES ('GUNLUK SUT 1 L', 'eski', 'Eski', 'ai', '2026-11-01')")
    got = pg.regroup_tick()
    assert got and got["finished"] and system.get("product_groups:version") == str(pg.GROUPS_VERSION)
    with system.connect() as c:
        assert c.execute("SELECT group_key FROM product_groups WHERE name_norm = 'GUNLUK SUT 1 L'").fetchone()[0] == "sut|1l"
    assert not pg.regroup_pending() and pg.regroup_tick() is None


def test_regroup_tick_resumes_in_chunks(monkeypatch):
    from app import system

    monkeypatch.setattr(pg, "REGROUP_CHUNK", 2)
    monkeypatch.setattr(pg, "REGROUP_BUDGET_S", 0.0)  # tek parça: her tick bir işlem
    with system.connect() as c:
        for i in range(5):
            c.execute("INSERT OR REPLACE INTO product_groups (name_norm, group_key, label, source, created_at) "
                      "VALUES (?, 'eski', 'Eski', 'ai', '2026-11-01')", (f"ORNEK SUT {i} 1 L",))
    steps = 0
    while pg.regroup_pending() and steps < 20:
        pg.regroup_tick()
        steps += 1
    assert not pg.regroup_pending() and steps >= 3


def test_per_unit_and_unit_price():
    assert pg.per_unit("sut|500ml") == (0.5, "L") and pg.per_unit("su|6x500ml") == (3.0, "L")
    assert pg.per_unit("peynir|250g") == (0.25, "kg") and pg.per_unit("domates|kg") == (1.0, "kg")
    assert pg.per_unit("yumurta|30ad") == (30, "adet") and pg.per_unit("ekmek") is None
    assert pg.per_unit("sut|1.5l") == (1.5, "L") and pg.per_unit("x|1ad") is None
    assert pg.unit_price(59, "sut|500ml") == {"unit_price_per": 118.0, "per_unit": "L"}
    assert pg.unit_price(None, "sut|1l") == {} and pg.unit_price(15, "ekmek") == {}


def test_categories():
    assert len(pg.CATEGORIES) >= 25 and len(set(pg.CATEGORIES)) == len(pg.CATEGORIES)
    assert set(pg.GENERIC_CATEGORY.values()) <= set(pg.CATEGORIES)
    assert all(g in pg.GENERIC_CATEGORY for g in pg.GENERICS)                  # her bilinen genel adın kategorisi var
    assert pg.rule_group("Çilekli Süt 200 ml")["category"] == "Süt ve Kahvaltılık"
    assert pg.rule_group("Bulaşık Deterjanı 1 L")["category"] == "Temizlik"
    assert pg.valid_category("Temizlik") == "Temizlik" and pg.valid_category("Uydurma") is None


def test_ai_category_is_stored(monkeypatch):
    from app import receipt

    monkeypatch.setenv("GEMINI_API_KEY", "test")
    pg.groups_for([{"name": "ORNEKMARKA MANTI 500G"}, {"name": "ORNEKMARKA KALEM"}])

    def fake(prompt, max_output_tokens=None, kind="kategori", model=None):
        assert "Konserve ve Hazır Gıda: Konserve" in prompt                     # kısa biçim: kategori listesi
        lines = [ln for ln in prompt.splitlines() if ln[:1].isdigit() and ". " in ln]
        return {"items": [{"i": int(ln.split(".")[0]), **({"g": "Mantı", "c": "Dondurulmuş Gıda"} if "MANTI" in ln
                                                           else {"g": "Kalem", "c": "Uydurma"})} for ln in lines]}, {}

    monkeypatch.setattr(receipt, "gemini_text_json", fake)
    assert pg.classify_pending() == 2
    assert pg.group_for("ORNEKMARKA MANTI 500G")["category"] == "Dondurulmuş Gıda"
    assert "category" not in pg.group_for("ORNEKMARKA KALEM")                    # listede olmayan kategori yazılmaz
    assert pg.categories(["manti|500g", "sut|1l"]) == {"manti|500g": "Dondurulmuş Gıda"}
