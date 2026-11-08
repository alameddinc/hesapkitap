"""Özet → "Beklenen" (summary.expected, yetenek summary_expected).

Kural: Gider/Gelir/Artan, kategori, bütçe, trend, İstatistik, yıl özeti ve dışa aktarım DEĞİŞMEZ; expected yalnız ek
bilgidir ve Planlama'nın o ayki "kesin" satırının kendisidir (finance.expected → finance.plan): iki ekran aynı rakamı
gösterir. Planlama'nın gerçekleşen kısmı Özet'in sayım kuralıyla kurulur; bekleyenler (ekstresi gelmemiş taksitler,
o kartların abonelikleri, girilmemiş sabit kalemler, ödenmemiş faturalar) üstüne eklenir, hiçbiri iki kez sayılmaz.
Geçmiş (kapanmış) dönemde expected null. Veriler uydurma (tests/fixtures/*.txt sahte ekstreler, tutarlar örnek).
"""
import io
from datetime import date

import openpyxl
import pytest

from app import finance, money

SEP, OCT, NOV, DEC = "2026-09", "2026-10", "2026-11", "2026-12"
TODAY = date(2026, 11, 8)  # kasım dönemi: ekim ekstreleri yüklü, kasım ekstresi henüz yok


def _seed(client, upload):
    assert all(r["status"] == "saved" for r in upload())
    # beklenen gelir (Gelir'in "bekliyor"u) ve girilmemiş sabit gider (kira)
    assert client.post("/api/recurring", json={"description": "Maaş", "amount": 85000, "kind": "gelir", "category": "Maaş",
                                               "day_of_month": 15, "start_month": SEP}).status_code == 200
    assert client.post("/api/recurring", json={"description": "Kira", "amount": 20000, "kind": "gider",
                                               "category": "Kira & Aidat", "day_of_month": 5, "start_month": SEP}).status_code == 200
    assert client.post("/api/entries", json={"entry_date": "2026-11-03", "description": "Örnek Market", "amount": 1250.5,
                                             "kind": "gider", "category": "Market & Gıda", "payment": "nakit"}).status_code == 200
    assert client.put("/api/budgets", json={"category": "Market & Gıda", "monthly_limit": 8000}).status_code == 200


@pytest.fixture()
def seeded(client, upload):
    _seed(client, upload)


def _plan_row(month):
    return next(m for m in finance.plan(6, today=TODAY)["months"] if m["month"] == month)


@pytest.mark.parametrize("month", [NOV, DEC])
def test_expected_is_planlama_month_and_reconciles_with_summary(seeded, month):
    s = finance.summary(month, today=TODAY)
    e, t, inst = s["expected"], s["totals"], s["installments"]
    p = _plan_row(month)
    # Planlama ile birebir (aynı kaynak)
    assert (e["expense"], e["income"], e["net"]) == (p["expense"], p["income"], p["net"])
    # Özet'in kendi rakamlarıyla mutabakat: gerçekleşen = Gider/Gelir, bekleyen = taksit (ekstre bekliyor) + sabit
    parts = e["parts"]
    assert parts["recorded_expense"] == t["expense"] and parts["recorded_income"] == t["income"]
    assert parts["pending_installments"] == inst["expected"] > 0  # yalnız "ekstre bekliyor" kısmı
    assert parts["pending_recurring_expense"] == s["pending"]["expense"] == 20000
    assert parts["pending_income"] == s["pending"]["income"] == 85000
    assert parts["pending_subscriptions"] > 0 and parts["pending_bills"] == 0
    assert money.eq(e["expense"], t["expense"] + parts["pending_expense"])
    assert money.eq(parts["pending_expense"], inst["expected"] + parts["pending_subscriptions"] + 20000)
    assert money.eq(e["income"], t["income"] + 85000) and money.eq(e["net"], e["income"] - e["expense"])
    # değişken (yeni harcama) tahmini eklenmez
    assert p["estimated"] > 0 and money.eq(e["expense"], p["expense"])
    # (i) metni yalnız olan bekleyenleri sayar
    assert s["definitions"]["expected"] == ("Ekstresi gelmemiş kart taksitleri ve abonelikleri, girilmemiş "
                                            "sabit giderler ve beklenen gelirler dahil tahmin; yeni harcamalar eklenmez.")


def test_past_period_has_no_expected(seeded):
    assert finance.summary(OCT, today=TODAY)["expected"] is None
    assert "expected" not in finance.summary(OCT, today=TODAY)["definitions"]
    assert finance.expected(SEP, today=TODAY) is None
    assert finance.expected("2029-01", today=TODAY) is None  # Planlama ufku (24 ay) dışı


def test_far_future_month_uses_longer_plan(seeded):
    e = finance.expected("2027-08", today=TODAY)  # 10. ay: plan(6) dışında, plan(10) satırı
    row = next(m for m in finance.plan(10, today=TODAY)["months"] if m["month"] == "2027-08")
    assert (e["expense"], e["income"]) == (row["expense"], row["income"])


def test_owner_text_when_only_installments_and_income(client, upload):
    assert all(r["status"] == "saved" for r in upload())
    client.post("/api/recurring", json={"description": "Maaş", "amount": 85000, "kind": "gelir", "category": "Maaş",
                                        "day_of_month": 15, "start_month": SEP})
    parts = {**finance.expected(NOV, today=TODAY)["parts"], "pending_subscriptions": 0}
    assert finance._expected_text(parts) == ("Ekstresi gelmemiş kart taksitleri ve beklenen gelirler dahil "
                                             "tahmin; yeni harcamalar eklenmez.")


def test_capability_and_api_shape(client, seeded):
    assert "summary_expected" in client.get("/api/meta").json()["capabilities"]
    cur = finance.current_period()
    s = client.get("/api/summary", params={"month": cur}).json()
    e = s["expected"]
    assert e["month"] == cur and e["source"] == "plan"
    p = next(m for m in client.get("/api/plan").json()["months"] if m["month"] == cur)
    assert (e["expense"], e["income"], e["net"]) == (p["expense"], p["income"], p["net"])
    assert client.get("/api/summary", params={"month": finance.add_months(cur, -1)}).json()["expected"] is None


# ---------------------------------------------------------------- toplamlar değişmedi (önce/sonra)

def _xlsx(client):
    wb = openpyxl.load_workbook(io.BytesIO(client.get("/api/settings/export.xlsx").content))
    return {ws.title: [list(r) for r in ws.iter_rows(values_only=True)] for ws in wb.worksheets}


def _snapshot(client):
    out = {}
    for m in (SEP, OCT, NOV, DEC):
        s = client.get("/api/summary", params={"month": m}).json()
        s.pop("expected", None)
        s["definitions"].pop("expected", None)
        out[f"summary {m}"] = s
        for name, path in (("compare", "/api/compare"), ("budgets", "/api/budgets"), ("tx", "/api/transactions"),
                           ("inst", "/api/installments")):
            out[f"{name} {m}"] = client.get(path, params={"month": m}).json()
    out["year"] = client.get("/api/year-summary", params={"year": 2026}).json()
    out["interest"] = client.get("/api/interest-history").json()
    out["xlsx"] = _xlsx(client)
    return out


def test_totals_and_statistics_identical_with_and_without_expected(client, seeded, monkeypatch):
    with_expected = _snapshot(client)
    finance._CACHE.clear()
    monkeypatch.setattr(finance, "expected", lambda month, today=None: None)  # özellik yokmuş gibi ("önce")
    without = _snapshot(client)
    finance._CACHE.clear()
    assert with_expected == without
    # rakamlar fikstürden (önceki sürümle alınmış değerler): Gider/Gelir/Artan aynı
    t = with_expected[f"summary {NOV}"]["totals"]
    assert (t["expense"], t["income"], t["left"]) == (1250.5, 0.0, -1250.5)
    t = with_expected[f"summary {OCT}"]["totals"]
    assert (t["expense"], t["income"]) == (42735.19, 85000.0)


# ---------------------------------------------------------------- Planlama: Özet'in sayım kuralı (düzeltmeler)

def test_planlama_counts_like_summary(client, seeded, tmpdb):
    # birikime ayrılan (altın alımı) gider değil; maaş dökümden geldiyse sabit maaş ikinci kez eklenmez
    client.post("/api/entries", json={"entry_date": "2026-11-04", "description": "Gram altın", "amount": 5000,
                                      "kind": "gider", "category": tmpdb.JAR_BUY_CATEGORY, "payment": "nakit"})
    client.post("/api/entries", json={"entry_date": "2026-11-06", "description": "MAAS 2026 KASIM ODEMESI", "amount": 84000,
                                      "kind": "gelir", "category": "Diğer Gelir", "payment": "havale"})
    s = finance.summary(NOV, today=TODAY)
    p = _plan_row(NOV)
    e = s["expected"]
    assert s["pending"]["income"] == 0  # Özet: maaş yattı, bekliyor yok
    assert e["parts"]["pending_income"] == 0 and money.eq(p["income"], 84000)
    assert e["parts"]["recorded_expense"] == s["totals"]["expense"] == 1250.5  # altın gider değil
    assert not any(ln["description"] == "Gram altın" for ln in p["lines"])
    assert (e["expense"], e["income"]) == (p["expense"], p["income"])
