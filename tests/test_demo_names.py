"""Demoda gerçek marka yok (app/demo_names.py, app/demo_prices.py): uydurma verinin yanında gerçek banka, zincir, işyeri
ya da kurum adı görünmez; kurgu zincirler yalnız demoda ulusal zincir gibi davranır."""
import re
import sqlite3

import pytest
from fastapi.testclient import TestClient

from app import demo_mode, demo_names, demo_prices, price_sources, system
from app.receipt_templates import chain_of
from app.textnorm import normalize

# demoda görünmemesi gereken gerçek adlar (normalize: büyük harf, Türkçe harfsiz; kelime sınırıyla aranır)
REAL = ["BİM", "A101", "ŞOK", "Migros", "CarrefourSA", "Carrefour", "Macrocenter", "Hakmar", "VakıfBank", "Vakıf Bank",
        "Vakıfkart", "Kuveyt Türk", "Garanti", "Bonus", "Ziraat", "Akbank", "Yapı Kredi", "Halkbank", "İş Bankası",
        "Spotify", "Netflix", "YouTube", "Scribd", "OpenAI", "Anthropic", "Claude", "Hetzner", "Uber", "Yemeksepeti",
        "Getir", "Trendyol", "Hepsiburada", "Hepsipay", "Hepsibu", "iyzico", "TKPAY", "HalkÖde", "H&M", "Hennes",
        "LC Waikiki", "LCWaikiki", "Colins", "Koton", "DeFacto", "Zara", "D&R", "Opet", "Shell", "POAŞ",
        "Petrol Ofisi", "İSPARK", "Otoyol Yatırım", "Kızılay", "AFAD", "LÖSEV", "Enerjisa", "Turkcell", "Vodafone",
        "Türk Telekom"]
_RX = re.compile(r"(?<![A-Z0-9])(" + "|".join(re.escape(normalize(n)) for n in REAL) + r")(?![A-Z0-9])")
# uygulamanın kendi varsayılan kategori / kalem kuralları (marka anahtar kelimeleri içerir; demo verisi değil)
APP_TABLES = {"rules", "item_rules", "categories"}
DEMO_SYSTEM_TABLES = {"official_prices", "official_daily", "community_prices", "official_imports"}


def _hits(path: str, tables: set[str] | None = None) -> list[str]:
    out = []
    c = sqlite3.connect(path)
    try:
        for (t,) in c.execute("SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'").fetchall():
            if t in APP_TABLES or (tables is not None and t not in tables):
                continue
            for col in [r[1] for r in c.execute(f'PRAGMA table_info("{t}")')]:
                for (v,) in c.execute(f'SELECT DISTINCT "{col}" FROM "{t}" WHERE typeof("{col}") = \'text\''):
                    if m := _RX.search(normalize(v)):
                        out.append(f"{t}.{col}: {m.group(1)} ← {v[:80]}")
    finally:
        c.close()
    return out


@pytest.fixture()
def demo(tmp_path, monkeypatch, tmpdb):
    monkeypatch.setenv("HESAPKITAP_DEMO", "1")
    monkeypatch.delenv("HESAPKITAP_NO_AUTH", raising=False)
    monkeypatch.delenv("HESAPKITAP_PRICE_SOURCES", raising=False)
    from app.api.main import app

    return TestClient(app)


def test_brand_scan_is_turkish_aware():
    assert _RX.search(normalize("iyzico/örnek")) and _RX.search(normalize("MİGROS ORNEK")) and _RX.search("SOK")
    assert _RX.search(normalize("şok-1234")) and _RX.search(normalize("Kızılay"))
    assert not _RX.search(normalize("Market A · Örnek Vakıf · Mavi Bank · ÖRNEK GİYİM AVM · Sokak"))


def test_demo_template_has_no_real_brand_names(demo):
    assert _hits(demo_mode.template()) == []
    assert _hits(system.SYSTEM_DB, DEMO_SYSTEM_TABLES) == []  # demo piyasa verisi (zincir adları, katkıcılar)


def test_demo_samples_have_no_real_brand_names(demo):
    for key in ("vakifbank", "genel"):
        r = demo.post(f"/api/demo/sample/{key}").json()[0]
        assert r["status"] == "saved" and r["bank"] in demo_names.FICTIONAL_BANKS
        assert not _RX.search(normalize(demo_mode.SAMPLES[key]["label"]))
    sid = demo.cookies.get(demo_mode.COOKIE)
    assert _hits(demo_mode.space(sid)[0]) == []
    # kurgu adlar eski kategorilerine düşer (demo kuralları)
    cats = {t["description"]: t["category"] for t in demo.get("/api/transactions").json()}
    assert cats["ÖRNEK MÜZİK ABONELİĞİ/İSTANBUL"] == "Dijital Abonelik"
    assert cats["ÖRNEK YAPAY ZEKA/SAN FRANCISCO"] == "AI & Yazılım Abonelik"
    assert cats["ÖRNEK GİYİM AVM"] == "Giyim" and cats["ÖRNEK AKARYAKIT"] == "Ulaşım & Araç"
    assert cats["MARKET D"] == "Market & Gıda" and cats["ÖRNEK YEMEK SİPARİŞİ"] == "Yemek Siparişi"
    assert cats["ÖRNEK BULUT SUNUCU"] == "Yazılım & Bulut" and cats["ÖRNEK OTOPARK/İSTANBUL"] == "Ulaşım & Araç"
    assert cats["ÖRNEK MOBİLYA"] == "Ev & Tamirat" and cats["9916 I065 MARKET B ORNEK"] == "Market & Gıda"


def test_scrub_statement_keeps_installment_lines_together():
    assert demo_names.description("Vade Farki - H&M ORNEK AVM") == "Vade Farki - ÖRNEK GİYİM AVM"
    assert demo_names.description("9916 I065 A101 ORNEK 5400*3456") == "9916 I065 MARKET B ORNEK 5400*3456"
    assert demo_names.description("ORNEK CAFE") == "ORNEK CAFE"
    assert demo_names.bank("Garanti") == "Yeşil Bank" and demo_names.bank("Akbank") == demo_names.DEFAULT_BANK
    assert demo_names.bank("Mavi Bank") == "Mavi Bank" and demo_names.bank(None) is None


def test_fictional_chains_are_national_only_in_demo(monkeypatch):
    monkeypatch.delenv("HESAPKITAP_DEMO", raising=False)
    assert price_sources.national_chains() == frozenset(price_sources.NATIONAL_CHAINS)
    assert chain_of("Market A") is None and chain_of("MIGROS TICARET") == "Migros"
    monkeypatch.setenv("HESAPKITAP_DEMO", "1")
    nat = price_sources.national_chains()
    assert nat >= price_sources.NATIONAL_CHAINS and nat >= demo_prices.NATIONAL and "Market E" not in nat
    assert chain_of("MARKET D") == "Market D" and chain_of("Market A") == "Market A"
    assert chain_of("MARKET A.Ş.") is None  # yalnız adın tamamı
    assert set(demo_prices.CHAINS) == set(demo_prices.FACTOR) and demo_prices.NATIONAL < set(demo_prices.CHAINS)


def test_demo_where_to_buy_regions_and_receipt_note(demo):
    kahve = demo.get("/api/prices/groups", params={"q": "kahve"}).json()[0]
    regions = {o["chain"]: o.get("region") for o in kahve["best"] if o["source"] == "resmi"}
    assert regions and all(r == ("İstanbul" if ch == "Market E" else "ulusal") for ch, r in regions.items())
    ents = [e for e in demo.get("/api/transactions").json() if e.get("description") == "Market D"]
    last = max(ents, key=lambda e: e["date"])
    notes = demo.get(f"/api/entries/{last['id']}/receipt-items").json()["check"]["market_notes"]
    egg = next(n for n in notes if n["group"] == "yumurta|30ad")
    assert egg["region"] == "ulusal" and egg["cheapest_chain"] in demo_prices.CHAINS
    out = demo.get("/api/prices").json()
    sut = next(p for p in out["products"] if p["group"] and p["group"]["key"] == "sut|1l")
    assert sut["market"] and sut["advice"]["chain"] in demo_prices.CHAINS
