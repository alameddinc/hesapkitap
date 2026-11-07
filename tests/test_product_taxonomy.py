"""Ürün sözlüğü ve iki katlı kategori (app/product_taxonomy.py) + marka listesi (app/brands_tr.txt) ile kural
eşleştirmesi. Adlar uydurma fiş satırlarıdır (marka adları bilinen raf markaları; ürün / fiyat bilgisi yok)."""
from datetime import date

import pytest

from app import product_groups as pg
from app import product_taxonomy as tx


@pytest.mark.parametrize("name, key, sub", [
    # bilinen marka listesi (fiş yazılışı, Türkçe harfsiz) atılır
    ("SUTAS KASAR 400G", "kasar-peyniri|400g", "Peynir"),
    ("PINAR TAM YAG SUT 1L", "sut|1l", "Süt"),
    ("TARIM KREDI SIYAH ZEYTIN 1KG", "siyah-zeytin|1kg", "Zeytin"),          # çok kelimeli marka
    ("DR.OETKER KABARTMA TOZU 10G", "kabartma-tozu|10g", "Pasta Malzemeleri"),
    ("KELLOGGS MISIR GEVREGI 375G", "misir-gevregi|375g", "Kahvaltılık Gevrek Bar ve Granola"),
    # sözlük: alt kategori başına genel ürünler
    ("TORKU BASMATI PIRINC 2KG", "basmati-pirinc|2kg", "Bakliyat"),
    ("KIRMIZI MERCIMEK 1KG", "kirmizi-mercimek|1kg", "Bakliyat"),
    ("TAM YAGLI LABNE 200G", "labne|200g", "Peynir"),
    ("HELLIM PEYNIRI 250G", "hellim|250g", "Peynir"),
    ("SALAMURA SIYAH ZEYTIN XL 500G", "siyah-zeytin|500g", "Zeytin"),
    ("SIZMA ZEYTINYAGI 1LT", "zeytinyagi|1l", "Sıvı Yağlar"),
    ("BURGU MAKARNA 500G", "makarna|500g", "Mantı Makarna ve Erişte"),
    ("DANA KANGAL SUCUK 250G", "sucuk|250g", "Şarküteri"),
    # Türkçe tamlama: baş ad sondadır; niteleyen zayıf ad (tavuk, domates, çilek) ürünün kendisi değildir
    ("TAVUK CORBASI 65G", "corba|65g", "Hazır Gıda Karışımları"),
    ("DOMATES SALCASI 700G", "salca|700g", "Salça"),
    ("CILEK RECELI 380G", "recel|380g", "Bal ve Reçel"),
    ("YUMURTA GEZEN TAVUK 10LU", "yumurta|10ad", "Yumurta"),
    ("TAVUK CESNISI 60G", "baharat|60g", "Tuz Baharat ve Harçlar"),
    # daha belirli tür genel baş adın önüne geçer
    ("CECIL PEY 200GR", "cecil-peyniri|200g", "Peynir"),
    ("SUZME TAM YAGLI PEYNIR 500G", "suzme-peynir|500g", "Peynir"),
    ("HARDAL SOS 250G", "hardal|250g", "Ketçap Mayonez Sos ve Sirkeler"),
    # tavuk eti ve donuk sebze ayrı grup
    ("PILIC YAPRAK DONER 500G", "tavuk-doner|500g", "Beyaz Et"),
    ("DONDURULMUS BEZELYE 450G", "dondurulmus-bezelye|450g", "Dondurulmuş Gıda"),
    # "şeker ilavesiz" ürünü şeker yapmaz; sıfat ekli bilinmeyen kelime ürünü niteler
    ("SEKER ILAVESIZ VISNE RECELI 290G", "recel|290g", "Bal ve Reçel"),
    ("BIBERIYELI TAZE KASAR 200G", "kasar-peyniri|200g", "Peynir"),
    # fiş kısaltması (sözlükte tek anlamlı açılım)
    ("OLGUN. BEYAZ PEYNIR 500G", "beyaz-peynir|500g", "Peynir"),
    ("DILIMLI TOST PEYNI. 500GR", "tost-peyniri|500g", "Peynir"),
])
def test_catalog_style_names(name, key, sub):
    g = pg.rule_group(name)
    assert g and (g["key"], g["subcategory"]) == (key, sub), (name, g)


def test_variant_rule_still_separates_flavoured_milk():
    assert pg.rule_group("PINAR CILEKLI SUT 200ML")["key"] == "cilekli-sut|200ml"
    assert pg.rule_group("PROTEIN SUT KAHVELI 500ML")["key"] == "protein-icecegi|500ml"   # protein içeceği süt değil
    assert pg.rule_group("KAHVELI SUT 200ML")["key"] == "kahveli-sut|200ml"
    assert pg.rule_group("MEYVELI SUZME YOGURT 500G")["key"] == "suzme-yogurt|500g"


@pytest.mark.parametrize("name", [
    "ORNEKMARKA ZEYTIN 1KG",           # bilinmeyen kelime (tanınmayan marka): yapay zekâya kalır
    "DOND. PATATES 1KG",               # DOND: dondurma mı dondurulmuş mu belirsiz, açılmaz
    "ORNEKMARKA PEYNIRLI RAVIOLI 300G",
])
def test_unknown_or_ambiguous_names_are_left_to_ai(name):
    assert pg.rule_group(name) is None


def test_brand_words_that_are_products_are_kept():
    # kalemin markası "Örnek Sucuk": sözlükteki SUCUK atılmaz, yalnız ÖRNEK
    assert pg.rule_group("ORNEK SUCUK KANGAL 250G", brand="Örnek Sucuk")["key"] == "sucuk|250g"
    # sözlükte geçen tek kelimelik marka ("NAR") meyvenin kendisi olabilir
    assert "NAR" not in pg._BRAND_SINGLE and pg.rule_group("NAR 1KG")["key"] == "nar|1kg"
    assert "SUTAS" in pg._BRAND_SINGLE and "KELLOGGS" in pg._BRAND_SINGLE


def test_brand_file_has_no_product_data():
    from pathlib import Path

    lines = [ln for ln in (Path(pg.__file__).parent / "brands_tr.txt").read_text("utf-8").splitlines()
             if ln and not ln.startswith("#")]
    assert len(lines) > 300 and all(ln == ln.upper() and "|" not in ln and "₺" not in ln for ln in lines)


def test_taxonomy_is_consistent():
    assert pg.CATEGORIES[0] == "Süt ve Kahvaltılık" and len(pg.CATEGORIES) == 28     # ana adlar değişmedi
    assert set(tx.TAXONOMY) <= set(pg.CATEGORIES)
    for g, (top, sub) in pg.GENERIC_PATH.items():
        assert sub in pg.SUBCATEGORIES[top], g
    # eski genel adlar ve kategorileri korunur (kayıtlı gruplar ve istemciler bu adları taşır)
    old = {"Süt": "Süt ve Kahvaltılık", "Beyaz Peynir": "Süt ve Kahvaltılık", "Ekmek": "Fırın ve Unlu Mamul",
           "Tuz": "Temel Gıda", "Salça": "Temel Gıda", "Zeytinyağı": "Yağ", "Kıyma": "Et, Tavuk ve Balık",
           "Sucuk": "Şarküteri", "Su": "Su ve İçecek", "Latte": "Kafe", "Gofret": "Atıştırmalık",
           "Ketçap": "Sos ve Baharat", "Bulaşık Deterjanı": "Temizlik", "Peçete": "Kağıt Ürünleri",
           "Şampuan": "Kişisel Bakım", "Bebek Bezi": "Bebek", "Kedi Maması": "Evcil Hayvan", "Motorin": "Akaryakıt",
           "Protein İçeceği": "Su ve İçecek", "Çilekli Süt": "Süt ve Kahvaltılık", "Keçi Sütü": "Süt ve Kahvaltılık"}
    assert {g: pg.category_for(g) for g in old} == old
    assert pg.SYNONYMS["KASAR"] == "Kaşar Peyniri" and pg.SYNONYMS["DIZEL"] == "Motorin"
    assert pg.subcategory_for("Çilekli Yoğurt") == "Yoğurt" and pg.subcategory_for("Uydurma") is None
    assert pg.valid_subcategory("Temel Gıda", "Bakliyat") == "Bakliyat"
    assert pg.valid_subcategory("Temel Gıda", "Peynir") is None and pg.valid_subcategory(None, "Bakliyat") is None


def test_public_group_carries_subcategory():
    g = pg.group_for("SUTAS KASAR 400G", learn=False)
    assert g == {"key": "kasar-peyniri|400g", "label": "Kaşar Peyniri · 400 g", "category": "Süt ve Kahvaltılık",
                 "subcategory": "Peynir"}
    assert pg.paths(["kasar-peyniri|400g"]) == {"kasar-peyniri|400g": ("Süt ve Kahvaltılık", "Peynir")}
    assert pg.subcategories(["kasar-peyniri|400g"]) == {"kasar-peyniri|400g": "Peynir"}


def test_ai_subcategory_is_validated(monkeypatch):
    from app import receipt

    monkeypatch.setenv("GEMINI_API_KEY", "test")
    pg.groups_for([{"name": "ORNEKMARKA KURABIYE HAMURU 400G"}, {"name": "ORNEKMARKA SILGI"}])

    def fake(prompt, max_output_tokens=None, kind="kategori", model=None):
        assert "Temel Gıda: Bakliyat" in prompt and '"s": "Süt"' in prompt               # alt kategori listesi
        lines = [ln for ln in prompt.splitlines() if ln[:1].isdigit() and ". " in ln]
        return {"items": [{"i": int(ln.split(".")[0]), **(
            {"g": "Kurabiye Hamuru", "c": "Temel Gıda", "s": "Pasta Malzemeleri"} if "HAMURU" in ln
            else {"g": "Silgi", "c": "Kırtasiye", "s": "Peynir"})} for ln in lines]}, {}

    monkeypatch.setattr(receipt, "gemini_text_json", fake)
    assert pg.classify_pending() == 2
    assert pg.group_for("ORNEKMARKA KURABIYE HAMURU 400G")["subcategory"] == "Pasta Malzemeleri"
    g = pg.group_for("ORNEKMARKA SILGI")
    assert g["category"] == "Kırtasiye" and "subcategory" not in g                 # ana kategorinin altında değil


def test_regroup_promotes_rules_clears_queue_and_official_prices():
    from app import system

    with system.connect() as c:
        c.execute("INSERT OR REPLACE INTO product_groups (name_norm, group_key, label, source, created_at, category) "
                  "VALUES ('SUTAS KASAR 400 G', 'ornek-kasar|400g', 'Ornek Kasar · 400 g', 'ai', "
                  "'2026-11-01 00:00:00', 'Süt ve Kahvaltılık')")                  # eski yapay zekâ sonucu
        c.execute("INSERT OR REPLACE INTO product_groups (name_norm, group_key, label, source, created_at) "
                  "VALUES ('SUT 1 L', 'sut|1l', 'Süt · 1 L', 'rule', '2026-11-01 00:00:00')")   # alt kategorisiz
        c.execute("INSERT OR IGNORE INTO product_group_queue (name_norm, name, added_at) "
                  "VALUES ('TORKU BASMATI PIRINC 2 KG', 'TORKU BASMATI PIRINC 2KG', '2026-11-01 00:00:00')")
        c.execute("INSERT INTO community_prices (chain, group_key, name_norm, unit_price, day, contributor) "
                  "VALUES ('A101', 'ornek-kasar|400g', 'SUTAS KASAR 400 G', 90, '2026-11-05', 'x')")
        c.execute("INSERT INTO official_prices (chain, group_key, title, brand, price, day) "
                  "VALUES ('BİM', 'eski|1kg', 'Basmati Pirinç 2 Kg', 'Torku', 80, ?)", (date.today().isoformat(),))
        got = pg.regroup(c)
        assert got["changed"] >= 2
        rows = {r[0]: r[1:] for r in c.execute(
            "SELECT name_norm, group_key, source, subcategory FROM product_groups")}
        assert rows["SUTAS KASAR 400 G"] == ("kasar-peyniri|400g", "rule", "Peynir")
        assert rows["SUT 1 L"] == ("sut|1l", "rule", "Süt")
        assert rows["TORKU BASMATI PIRINC 2 KG"][:2] == ("basmati-pirinc|2kg", "rule")
        assert c.execute("SELECT COUNT(*) FROM product_group_queue WHERE name_norm = 'TORKU BASMATI PIRINC 2 KG'"
                         ).fetchone()[0] == 0
        assert c.execute("SELECT group_key FROM community_prices WHERE name_norm = 'SUTAS KASAR 400 G'"
                         ).fetchone()[0] == "kasar-peyniri|400g"
        assert c.execute("SELECT group_key FROM official_prices").fetchone()[0] == "basmati-pirinc|2kg"


def test_prices_api_exposes_subcategory(client):
    client.post("/api/entries", json={"entry_date": date.today().isoformat(), "description": "A101", "amount": 120,
                                      "kind": "gider", "category": "Market & Gıda", "payment": "nakit",
                                      "items": [{"name": "SUTAS KASAR 400G", "qty": 1, "amount": 120}]})
    p = client.get("/api/prices").json()["products"][0]
    assert (p["category"], p["subcategory"], p["group"]["subcategory"]) == ("Süt ve Kahvaltılık", "Peynir", "Peynir")
    g = client.get("/api/prices/groups", params={"q": "kaşar"}).json()[0]
    assert (g["key"], g["category"], g["subcategory"]) == ("kasar-peyniri|400g", "Süt ve Kahvaltılık", "Peynir")
    assert "price_subcategories" in client.get("/api/meta").json()["capabilities"]
