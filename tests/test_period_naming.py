"""Dönem adı ve Özet'teki "geçen döneme göre" kıyası.

Kural: dönem anahtarı (YYYY-MM, entries.month) dönemin BİTTİĞİ aydır ve değişmez; adı dönemin BAŞLADIĞI aydan verilir.
Ay dönümü 5 → anahtar 2026-12 = 5 Kas – 4 Ara = "Kasım dönemi" (9 Kasım'da "Aralık dönemi" görünmesin).
Kıyas: başlıktaki giderle aynı sayım (_totals) ve aynı dönem tanımı; içinde bulunulan dönemde geçen dönemin aynı günü.
"""
from datetime import date


def test_name_is_start_month(tmpdb):
    from app import finance

    assert tmpdb.period_name_month("2026-12", 1) == "2026-12"  # takvim ayı: ad = anahtar
    assert tmpdb.period_name_month("2026-12", 5) == "2026-11"
    assert tmpdb.period_name_month("2027-01", 17) == "2026-12"
    tmpdb.set_cycle_start_day(5)
    key = tmpdb.period_of("2026-11-09")
    assert key == "2026-12"  # anahtar değişmedi (veri aynı)
    assert finance.period_name(key) == "Kasım 2026" and finance.period_name(key, year=False) == "Kasım"
    assert finance.period_span(key) == "5 Kas – 4 Ara"
    assert finance.period_title(key, " dönemi bitti") == "Kasım 2026 dönemi bitti (5 Kas – 4 Ara)"
    # maaş ay dönümünde yatar: "maaş yattı → bu ayın dönemi"
    assert finance.period_name(tmpdb.period_of("2026-11-05")) == "Kasım 2026"
    assert finance.period_name(tmpdb.period_of("2026-11-04")) == "Ekim 2026"
    tmpdb.set_cycle_start_day(1)
    assert finance.period_title("2026-11", " dönemi bitti") == "Kasım 2026 dönemi bitti"


def test_meta_and_summary_carry_name(client, tmpdb):
    tmpdb.set_cycle_start_day(5)
    meta = client.get("/api/meta").json()
    cur = meta["current_month"]
    assert meta["period_naming"] == "start"
    assert meta["period"]["name"] == {"01": "Aralık", "02": "Ocak", "03": "Şubat", "04": "Mart", "05": "Nisan",
                                      "06": "Mayıs", "07": "Haziran", "08": "Temmuz", "09": "Ağustos", "10": "Eylül",
                                      "11": "Ekim", "12": "Kasım"}[cur[5:]]
    s = client.get(f"/api/summary?month={cur}").json()
    assert s["period"]["name"] == meta["period"]["name"] and s["period"]["start"] == meta["period"]["start"]


def _e(client, d, amount, desc="Market", kind="gider"):
    r = client.post("/api/entries", json={"entry_date": d, "description": desc, "amount": amount, "kind": kind,
                                          "category": "Market & Gıda" if kind == "gider" else "Maaş", "payment": "nakit"})
    assert r.status_code == 200


def test_vs_prev_same_day_same_rule(client, tmpdb):
    """Geçen dönemin aynı gününe kadar, aynı sayım: kira (sabit) da sayılır; başlıktaki rakamla aynı taban."""
    from app import finance

    tmpdb.set_cycle_start_day(5)
    # geçen dönem (Ekim: 5 Eki – 4 Kas): 3. güne kadar 300 + kira 1000, sonra 5000
    _e(client, "2026-10-06", 300)
    _e(client, "2026-10-07", 1000, "Kira")
    _e(client, "2026-10-20", 5000)
    # bu dönem (Kasım: 5 Kas – 4 Ara), bugün 9 Kas (5. gün)
    _e(client, "2026-11-06", 200)
    _e(client, "2026-11-08", 900, "Kira")
    s = finance.summary("2026-12", today=date(2026, 11, 9))
    v = s["vs_prev"]
    assert s["totals"]["expense"] == 1100 == v["current"]
    assert (v["basis"], v["day"], v["prev_month"], v["prev_name"]) == ("same_day", 5, "2026-11", "Ekim")
    assert v["prev"] == 1300 and v["diff"] == -200 and v["change"] == -15
    # biten dönem: iki dönemin tamamı
    v = finance.summary("2026-11", today=date(2026, 11, 9))["vs_prev"]
    assert v["basis"] == "full" and v["current"] == 6300 and v["prev"] == 0 and v["change"] is None


def test_digest_label_and_span(client, tmpdb):
    from app import finance

    tmpdb.set_cycle_start_day(5)
    _e(client, "2026-10-06", 300)
    d = finance.month_digest("2026-11", today=date(2026, 11, 9))
    assert (d["label"], d["span"], d["start"], d["end"], d["prev_label"]) == (
        "Ekim 2026", "5 Eki – 4 Kas", "2026-10-05", "2026-11-04", "Eylül 2026")


def test_search_spans_all_periods(client, tmpdb):
    _e(client, "2026-08-10", 120, "MIGROS Kadıköy")
    _e(client, "2026-10-10", 80.5, "Migros Jet")
    _e(client, "2026-10-11", 40, "Fırın")
    rows = client.get("/api/transactions?q=migros").json()
    assert sorted(r["month"] for r in rows) == ["2026-08", "2026-10"]
    assert [r["description"] for r in client.get("/api/transactions?q=FIRIN").json()] == ["Fırın"]  # Türkçe harf
    assert [r["amount"] for r in client.get("/api/transactions?q=80,50").json()] == [80.5]
    assert client.get("/api/transactions?month=2026-10&q=migros").json()[0]["description"] == "Migros Jet"
    assert client.get("/api/transactions?q=migros jet").json()[0]["month"] == "2026-10"  # her kelime
    page = client.get("/api/transactions?q=migros&limit=1").json()
    assert len(page["rows"]) == 1 and page["next"]
    from app import version

    assert {"period_naming", "txn_search"} <= set(version.CAPABILITIES)
