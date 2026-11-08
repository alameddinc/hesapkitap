"""Ürün biçimi (product_groups.form): kahve, çay, deterjan aynı genel adın altında ayrı gruplar; raf etiketi (Market
Fiyatı main_category + categories) adın söylemediği biçimi seçer; GROUPS_VERSION 5 yeniden gruplaması.

Adlar Market Fiyatı başlıklarının biçiminde ama markalar uydurmadır ("Örnekkahve", "Denemeçay"); fiyatlar uydurmadır.
08.11.2026: "Çekirdek kahve" satırında 250 ml soğuk kahve, 17 g 3ü1 arada ve "10 adet" kabul edilen boy aynı gruptaydı."""
import pytest

from app import product_groups as pg
from app import system

K, C, D, M = "Örnekkahve", "Denemeçay", "Örnekdet", "Örnekmarka"

FIXTURE = [  # (ad, marka, raf etiketi, beklenen anahtar)
    # kahve: addaki kelime
    ("Örnekkahve Espresso Çekirdek Kahve 1 Kg", K, None, "cekirdek-kahve|1kg"),
    ("Örnekkahve Çekirdek Kahve 250 Gr", K, None, "cekirdek-kahve|250g"),
    ("Örnekkahve Filtre Kahve 250 Gr", K, None, "filtre-kahve|250g"),
    ("Örnekkahve Espresso Kahve 250 Gr", K, None, "filtre-kahve|250g"),           # öğütülmüş espresso
    ("Örnekkahve Türk Kahvesi 100 Gr", K, None, "turk-kahvesi|100g"),
    ("Örnekkahve Hazır Sade Türk Kahvesi 7 Gr", K, None, "hazir-turk-kahvesi|7g"),
    ("Örnekkahve Gold Kavanoz Granül Kahve 100 Gr", K, None, "hazir-kahve|100g"),
    ("Örnekkahve Gold Espresso 80 Gr", K, None, "hazir-kahve|80g"),               # gold: granül
    ("Örnekkahve Klasik Kahve 2 Gr", K, None, "hazir-kahve|2g"),
    ("Örnekkahve 3'ü 1 Arada Kahve 18 Gr", K, None, "kahve-karisimi|18g"),
    ("Örnekkahve 2si1 Arada 10 Adet", K, None, "kahve-karisimi|10ad"),
    ("Örnekkahve Cappuccino 14 Gr", K, None, "kahve-karisimi|14g"),               # market cappuccinosu tozdur
    ("Örnekkahve Latte Sütlü Köpüklü 17 Gr", K, None, "kahve-karisimi|17g"),
    ("Örnekkahve Kapsül Kahve 10'lu 52 Gr", K, None, "kapsul-kahve|10ad"),
    ("Örnekkahve Vanilyalı Soğuk Kahve 250 Ml", K, None, "soguk-kahve|250ml"),
    ("Örnekkahve Latte Soğuk Kahve 250 Ml", K, None, "latte|250ml"),             # latte içeceği kendi grubu
    ("Örnekkahve Kahve Kreması 200 Gr", K, None, "kahve-kremasi|200g"),
    ("Örnekkahve Filtre Kahve Kağıdı No:4 80 Adet", K, None, "filtre-kagidi|80ad"),
    ("Örnekkahve Filtre Kahve 2x250 Gr Filtre Kağıdı Hediyeli", K, None, "filtre-kahve|2x250g"),  # hediye ürün değil
    # kahve: biçimi addan çıkmayan ürün raf etiketiyle
    ("Örnekkahve Sade Kahve 1 Kg", K, "Kahve|Kuru Gıdalar|Fi̇ltre Kahve|Çekirdek Kahveler", "cekirdek-kahve|1kg"),
    ("Örnekkahve Sade Kahve 200 Gr", K, "Kahve|Kuru Gıdalar|Granül Kahve|Öğütülmüş Kahveler", "filtre-kahve|200g"),
    ("Örnekkahve Sade Kahve 95 Gr", K, "Kahve|Granül Kahve|Granül Kahve Gold", "hazir-kahve|95g"),
    ("Örnekkahve Sade Kahve 200 Gr", K, None, "kahve|200g"),                     # etiket yoksa çeşidi belirsiz
    ("Örnekkahve Sade Kahve 50 Gr", K, "Bisküvi ve Kraker|Draje Çikolata|Sekerleme", None),  # kahve değil
    # kişinin yazdığı / fişteki adlar
    ("Çekirdek kahve", None, None, "cekirdek-kahve"),
    ("kahve çekirdeği 1 kg", None, None, "cekirdek-kahve|1kg"),
    ("Türk kahvesi", None, None, "turk-kahvesi"),
    ("3ü1 arada", None, None, "kahve-karisimi"),
    ("KAHVE", None, None, "kahve"),                                              # kafe fişi
    ("LATTE", None, None, "latte"),
    ("ESPRESSO", None, None, "espresso"),
    ("KAHVE 100 G", None, None, "kahve|100g"),
    # çay
    ("Denemeçay Siyah Çay 1 Kg", C, None, "cay|1kg"),
    ("Denemeçay Demlik Poşet Çay 48 Adet", C, None, "poset-cay|48ad"),
    ("Denemeçay Bardak Poşet Çay 25x2 Gr", C, None, "poset-cay|25x2g"),
    ("Denemeçay Şeftali Buzlu Çay 330 Ml", C, None, "soguk-cay|330ml"),
    ("Denemeçay Yeşil Çay 20 Adet", C, None, "bitki-cayi|20ad"),
    ("ÇAY", None, None, "cay"),
    # deterjan
    ("Örnekdet Toz Deterjan 4 Kg", D, None, "toz-deterjan|4kg"),
    ("Örnekdet Deterjan 4 Kg", D, None, "toz-deterjan|4kg"),                     # ağırlık: toz
    ("Örnekdet Sıvı Deterjan 2.5 Lt", D, None, "sivi-deterjan|2.5l"),
    ("Deterjan 2,5 L", None, None, "sivi-deterjan|2.5l"),
    ("Örnekdet Çamaşır Makinesi Tablet Deterjan 30 Adet", D, None, "kapsul-deterjan|30ad"),
    ("Örnekdet Bulaşık Makinesi Jel Deterjan 1 Lt", D, None, "bulasik-makinesi-deterjani|1l"),
    ("Örnekdet Limonlu Sıvı Bulaşık Deterjanı 4 Kg", D, None, "bulasik-deterjani|4kg"),  # sıvı da kiloyla satılır
    ("Örnekdet Bulaşık Makinesi Tableti 50 Adet", D, None, "bulasik-tableti|50ad"),
    # süt, içecek ve gıda dışı
    ("Örneksüt Yağlı Süt 1 Lt", "Örneksüt", None, "sut|1l"),
    ("Örneksüt Süt Mısır Aromalı Çerez 130 Gr", "Örneksüt", None, None),        # süt gramla satılmaz
    ("Örnekmarka Duş Jeli Vanilya Sütü 650 Ml", M, None, "dus-jeli|650ml"),       # gıda kelimesi koku
    ("Örnekmarka Kedi Maması Somon 1 Kg", M, None, "kedi-mamasi|1kg"),
    ("Örnekmarka Limonata 1 Lt", M, None, "limonata|1l"),
]


@pytest.mark.parametrize("name,brand,shelf,want", FIXTURE)
def test_forms_on_catalog_shaped_titles(name, brand, shelf, want):
    g = pg.rule_group(name, brand, shelf=shelf)
    assert (g or {}).get("key") == want


def test_one_form_per_group_no_mixed_units():
    """Aynı biçim grubunda yalnız bir boy türü kalır: çekirdek kahve ağırlık, soğuk kahve hacim (fixture'da)."""
    fams: dict[str, set] = {}
    for name, brand, shelf, want in FIXTURE:
        if want and "|" in want and (pu := pg.per_unit(want)):
            fams.setdefault(want.split("|")[0], set()).add(pu[1])
    assert fams["cekirdek-kahve"] == {"kg"} and fams["soguk-kahve"] == {"L"} and fams["filtre-kahve"] == {"kg"}
    assert fams["hazir-kahve"] == {"kg"} and fams["poset-cay"] == {"adet", "kg"} and fams["cay"] == {"kg"}


def test_refine_splits_ai_labels_and_categories():
    """Yapay zekâ "Kahve" dese de biçim adla seçilir (önbellek / kuyruk sonucu da aynı yoldan geçer)."""
    assert pg.refine("Kahve", "ORNEK 3 U 1 ARADA 18 G".split()) == "Kahve Karışımı"
    assert pg.refine("Kahve", "ORNEK CEKIRDEK 1 KG".split()) == "Çekirdek Kahve"
    assert pg.refine("Çay", "ORNEK BUZLU 330 ML".split()) == "Soğuk Çay"
    assert pg.refine("Kahve", "ORNEK HARMAN 200 G".split()) == "Kahve"
    assert pg.path_for("Çekirdek Kahve") == ("Çay ve Kahve", "Kahve")
    assert pg.path_for("Soğuk Kahve") == ("Su ve İçecek", "Diğer İçecekler")
    assert pg.form_parent("Kapsül Kahve") == "Kahve" and pg.form_parent("Poşet Çay") == "Çay"
    assert pg.child_bases("kahve")[:3] == ["turk-kahvesi", "hazir-kahve", "filtre-kahve"]
    assert pg.generic_of_key("turk-kahvesi|100g") == "Türk Kahvesi"


def test_name_only_brand_is_not_the_product(monkeypatch):
    """Adında ürün kelimesi geçen marka (kahveci zincirinin gofreti): markanın kelimesi ürün sayılmaz."""
    monkeypatch.setattr(pg, "_NAME_ONLY_BRANDS", {("KAHVE", "EVI")})
    assert pg.rule_group("Kahve Evi Gofret 33 Gr")["key"] == "gofret|33g"
    assert pg.rule_group("Kahve Evi Filtre Kahve 250 Gr")["key"] == "filtre-kahve|250g"
    assert pg.rule_group("Kahve Evi Ornekce 40 Gr") is None


def test_groups_for_shelf_updates_cache_and_receipt_follows():
    """Resmî satır raf etiketiyle biçime çözülür ve önbelleği düzeltir; aynı ad fişte (etiketsiz) o biçimi alır."""
    name = "Örnekkahve Sade Kahve 1 Kg"
    assert pg.groups_for([{"name": name, "brand": K}])[0]["key"] == "kahve|1kg"          # eski önbellek
    got = pg.groups_for([{"name": name, "brand": K, "shelf": "Kahve|Çekirdek Kahveler"}])[0]
    assert got["key"] == "cekirdek-kahve|1kg"
    with system.connect() as c:
        assert c.execute("SELECT group_key FROM product_groups WHERE name_norm = ?",
                         (pg.name_norm(name),)).fetchone()[0] == "cekirdek-kahve|1kg"
    assert pg.groups_for([{"name": name, "brand": K}])[0]["key"] == "cekirdek-kahve|1kg"  # fiş: önbellekteki biçim
    assert pg.groups_for([{"name": name, "brand": K, "unit": "kg"}])[0]["key"] == "cekirdek-kahve|kg"


def test_regroup_v5_moves_official_rows_by_shelf():
    """GROUPS_VERSION 5: arka planda (regroup_tick) resmî satırlar addan ve katalog raf etiketinden biçime taşınır;
    önbellekte raf etiketiyle çözülmüş biçim, addan çıkan üst ada (Kahve) geri dönmez."""
    from app import product_catalog

    product_catalog.reset()
    rows = [("Örnekkahve Espresso Çekirdek Kahve 1 Kg", "kahve|1kg"), ("Örnekkahve 3'ü 1 Arada Kahve 18 Gr", "kahve|18g"),
            ("Örnekkahve Vanilyalı Soğuk Kahve 250 Ml", "kahve|250ml"), ("Örnekkahve Sade Kahve 1 Kg", "kahve|1kg"),
            ("Denemeçay Şeftali Buzlu Çay 330 Ml", "cay|330ml"), ("Denemeçay Demlik Poşet Çay 48 Adet", "cay|48ad")]
    with system.connect() as c:
        c.executemany("INSERT INTO official_prices (chain, group_key, title, brand, price, day, region) "
                      "VALUES ('BİM', ?, ?, ?, 99.5, '2026-11-08', 'İstanbul')",
                      [(k, t, K if t.startswith("Ö") else C) for t, k in rows])
        c.execute("INSERT INTO catalog_products (product_id, title, brand, volume, main_category, categories, generic, "
                  "updated_at) VALUES ('X1', 'Örnekkahve Sade Kahve 1 Kg', ?, '1 KG', 'Kahve', "
                  "'Kuru Gıdalar|Çekirdek Kahveler', 'Çekirdek Kahve', '2026-11-08')", (K,))
        c.execute("INSERT OR REPLACE INTO sys_settings (key, value) VALUES ('product_groups:version', '4')")
        pg._store(c, pg.name_norm("Örnekkahve Sade Kahve 1 Kg"),
                  pg._compose("Çekirdek Kahve", {"value": 1000.0, "base": "g", "pack": 1}), "rule")
    assert pg.regroup_pending()
    while (got := pg.regroup_tick()) and not got["finished"]:
        pass
    assert not pg.regroup_pending()
    with system.connect() as c:
        keys = dict(c.execute("SELECT title, group_key FROM official_prices").fetchall())
        cached = c.execute("SELECT group_key FROM product_groups WHERE name_norm = ?",
                           (pg.name_norm("Örnekkahve Sade Kahve 1 Kg"),)).fetchone()[0]
    assert keys == {"Örnekkahve Espresso Çekirdek Kahve 1 Kg": "cekirdek-kahve|1kg",
                    "Örnekkahve 3'ü 1 Arada Kahve 18 Gr": "kahve-karisimi|18g",
                    "Örnekkahve Vanilyalı Soğuk Kahve 250 Ml": "soguk-kahve|250ml",
                    "Örnekkahve Sade Kahve 1 Kg": "cekirdek-kahve|1kg",                  # katalog raf etiketi
                    "Denemeçay Şeftali Buzlu Çay 330 Ml": "soguk-cay|330ml",
                    "Denemeçay Demlik Poşet Çay 48 Adet": "poset-cay|48ad"}
    assert cached == "cekirdek-kahve|1kg"
    product_catalog.reset()
