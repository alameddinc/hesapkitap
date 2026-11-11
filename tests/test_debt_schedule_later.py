"""Ödeme planı borcun alt kaydı: elden borca sonradan plan ekle / düzenle / kaldır (PUT/DELETE /api/debts/{id}/schedule;
yetenek debt_schedule_edit). Kişiler, kurumlar ve tutarlar uydurmadır."""
import re
from datetime import date
from pathlib import Path

from tests.test_book import _member, owner  # noqa: F401  (fixture)
from tests.test_debt_plan import _all_strings, _banned_in
from tests.test_debt_schedule import _restructure_in

TODAY = date(2026, 11, 10)
PLAN = {"plan_installment": 2000, "plan_count": 6, "plan_start": "2026-11", "plan_day": 15, "creditor_type": "vergi_sgk"}


def _debt(client, did):
    return next(d for d in client.get("/api/debts").json() if d["id"] == did)


def _elden(client, amount=12000, person="Örnek Vergi Dairesi", day="2026-09-01"):
    r = client.post("/api/debts", json={"person": person, "direction": "borc", "amount": amount, "debt_date": day,
                                        "due_date": "2026-12-01"})
    assert r.status_code == 200, r.text
    return r.json()["id"]


def _plan_keys(d):
    s = d["schedule"]
    return {k: s[k] for k in ("installment", "count", "start_month", "end_month", "day", "creditor_type", "total",
                              "paid_count", "left_count", "next", "due_now", "text")}


def test_add_plan_later_equals_creating_with_plan(client):
    a = client.post("/api/debts", json={"person": "Örnek Vergi Dairesi", "direction": "borc", **PLAN}).json()["id"]
    b = _elden(client)
    r = client.put(f"/api/debts/{b}/schedule", json=PLAN)
    assert r.status_code == 200, r.text
    assert r.json()["debt"]["schedule"]["count"] == 6
    da, db_ = _debt(client, a), _debt(client, b)
    assert _plan_keys(da) == _plan_keys(db_)
    assert db_["amount"] == 12000.0 and db_["due_date"] is None  # vade taksitlerden; tutar korunur
    # ikisinden de planı kaldır: ikisi de aynı elden borç
    assert client.delete(f"/api/debts/{a}/schedule").status_code == 200
    assert client.patch(f"/api/debts/{b}", json={"plan_off": True}).status_code == 200
    da, db_ = _debt(client, a), _debt(client, b)
    for d in (da, db_):
        assert d["schedule"] is None and d["amount"] == 12000.0 and d["remaining"] == 12000.0
        assert all(d[k] is None for k in ("plan_installment", "plan_count", "plan_start", "plan_day", "plan_interest",
                                          "pay_terms"))
        # alacaklı türü borcun kendisinin: plan kalkınca da kalır (v4); ödeme koşulu boşalır, tarih türü kurumda kesin
        assert d["creditor_type"] == "vergi_sgk" and d["terms"] == "bilinmiyor" and d["strict"] is True


def test_payments_before_plan_kept_and_not_counted_as_installments(client):
    did = _elden(client, amount=10000)
    client.post(f"/api/debts/{did}/pay", json={"amount": 1000, "paid_at": "2026-10-05"})
    # öneri: kalan 9.000 ÷ 6 = 1.500
    from app import debt_schedule as ds

    assert ds.suggest(9000, 6) == 1500.0 and ds.suggest(11500, 6) == 1917.0
    r = client.put(f"/api/debts/{did}/schedule", json={**PLAN, "plan_installment": 1500})
    assert r.status_code == 200, r.text
    d = _debt(client, did)
    s = d["schedule"]
    assert d["paid_amount"] == 1000.0 and len(d["payments"]) == 1  # önceki ödeme duruyor
    assert s["total"] == 9000.0 and s["paid_before"] == 1000.0 and s["paid_count"] == 0 and s["left_count"] == 6
    assert s["next"]["no"] == 1 and s["next"]["left"] == 1500.0
    # plan başlangıcından sonraki ödeme 1. taksite düşer
    client.post(f"/api/debts/{did}/pay", json={"amount": None, "paid_at": "2026-11-12"})
    s = _debt(client, did)["schedule"]
    assert s["paid_count"] == 1 and s["next"]["no"] == 2 and _debt(client, did)["paid_amount"] == 2500.0
    # plan ekimde başlıyor desek ekim ödemesi 1. taksit olur
    client.put(f"/api/debts/{did}/schedule", json={**PLAN, "plan_installment": 1500, "plan_start": "2026-10"})
    s = _debt(client, did)["schedule"]
    assert s["paid_before"] == 0 and s["total"] == 10000.0 and s["paid_count"] == 1


def test_total_differs_from_remaining_is_allowed(client):
    did = _elden(client, amount=11500)
    # 2.000 × 6 = 12.000 ≠ 11.500: son taksit 1.500 sayılır (zorlama yok)
    s = client.put(f"/api/debts/{did}/schedule", json=PLAN).json()["debt"]["schedule"]
    assert s["count"] == 6 and s["last_amount"] == 1500.0 and _debt(client, did)["amount"] == 11500.0
    # taksitler kalandan çok fazla: plan erken biter, hata yok
    s = client.put(f"/api/debts/{did}/schedule", json={**PLAN, "plan_installment": 3000}).json()["debt"]["schedule"]
    assert s["count"] == 4 and s["last_amount"] == 2500.0 and "4 taksit" in s["text"]
    # istenirse borç toplamı taksit toplamına güncellenir
    r = client.put(f"/api/debts/{did}/schedule", json={**PLAN, "amount": 12000})
    assert r.status_code == 200 and r.json()["debt"]["amount"] == 12000.0
    assert r.json()["debt"]["schedule"]["last_amount"] == 2000.0


def test_edit_and_reject_rules(client):
    did = _elden(client)
    client.put(f"/api/debts/{did}/schedule", json=PLAN)
    r = client.put(f"/api/debts/{did}/schedule", json={**PLAN, "plan_installment": 3000, "plan_count": 4, "plan_day": 28,
                                                        "plan_interest": "yok"})
    s = r.json()["debt"]["schedule"]
    assert s["installment"] == 3000.0 and s["count"] == 4 and s["day"] == 28 and s["interest"] == "yok"
    assert _debt(client, did)["amount"] == 12000.0
    # sayı yerine son taksit ayı
    s = client.put(f"/api/debts/{did}/schedule", json={"plan_installment": 2000, "plan_start": "2026-11",
                                                        "plan_end": "2027-04"}).json()["debt"]["schedule"]
    assert s["count"] == 6 and s["end_month"] == "2027-04"
    # alacak, döviz, kapanmış borç: plan yok
    al = client.post("/api/debts", json={"person": "Örnek Kişi", "direction": "alacak", "amount": 500}).json()["id"]
    assert client.put(f"/api/debts/{al}/schedule", json=PLAN).status_code == 422
    fx = client.post("/api/debts", json={"person": "Örnek Kişi", "direction": "borc", "amount": 5, "asset": "GRAM_ALTIN"}).json()["id"]
    r = client.put(f"/api/debts/{fx}/schedule", json=PLAN)
    assert r.status_code == 422 and "TL borçta" in r.json()["detail"]
    closed = _elden(client, amount=300, person="Örnek Komşu")
    client.post(f"/api/debts/{closed}/pay", json={"amount": 300, "paid_at": "2026-10-01"})
    r = client.put(f"/api/debts/{closed}/schedule", json=PLAN)
    assert r.status_code == 422 and "kapanmış" in r.json()["detail"]
    # tutar ödenenden az olamaz; plan öncesi borcun tamamı ödenmişse ilk ay değişmeli
    part = _elden(client, amount=1000, person="Örnek Arkadaş")
    client.post(f"/api/debts/{part}/pay", json={"amount": 400, "paid_at": "2026-10-01"})
    assert "ödenmiş" in client.put(f"/api/debts/{part}/schedule", json={**PLAN, "amount": 300}).json()["detail"]
    assert client.put(f"/api/debts/{part}/schedule", json={"plan_installment": 100}).status_code == 200  # sayı kalandan
    assert _debt(client, part)["schedule"]["count"] == 6
    assert client.put("/api/debts/99999/schedule", json=PLAN).status_code == 404
    assert client.delete("/api/debts/99999/schedule").status_code == 404


def test_plan_added_later_feeds_debt_plan_and_push(client):
    from app import debt_plan as dp, finance, push

    did = _elden(client)
    before = dp.build(monthly=5000, today=TODAY)
    assert [d["kind"] for d in before["debts"] if d["id"] == did] == ["person"]
    client.put(f"/api/debts/{did}/schedule", json={**PLAN, "plan_day": 12})
    after = dp.build(monthly=5000, today=TODAY)
    assert [d["kind"] for d in after["debts"] if d["id"] == did] == ["fixed"]
    assert after["totals"]["fixed_this_month"] == 2000
    assert any("1. taksitin günü 2 gün sonra" in x["title"] for x in push._borc(TODAY))
    assert any(p["id"] == did and p["due_date"] == "2026-11-12" for p in finance.payables(TODAY) if p["type"] == "debt")
    client.delete(f"/api/debts/{did}/schedule")
    assert [d["kind"] for d in dp.build(monthly=5000, today=TODAY)["debts"] if d["id"] == did] == ["person"]
    assert not any("taksit" in x["title"] for x in push._borc(TODAY))


def test_texts_never_suggest_restructuring(client):
    did = _elden(client)
    client.post(f"/api/debts/{did}/pay", json={"amount": 500, "paid_at": "2026-10-01"})
    d = client.put(f"/api/debts/{did}/schedule", json=PLAN).json()["debt"]
    for s in _all_strings(d):
        assert not _banned_in(s) and not _restructure_in(s), s
    errs = [client.put(f"/api/debts/{did}/schedule", json={**PLAN, "amount": 100}).json()["detail"],
            client.put("/api/debts/99999/schedule", json=PLAN).json()["detail"]]
    for s in errs:
        assert not _banned_in(s) and not _restructure_in(s), s


def test_capability_listed(client):
    assert "debt_schedule_edit" in client.get("/api/meta").json()["capabilities"]


def test_viewer_and_editor_cannot_add_plan(owner):  # noqa: F811
    did = owner.post("/api/debts", json={"person": "Örnek Kişi", "direction": "borc", "amount": 1200,
                                         "debt_date": "2026-09-01"}).json()["id"]
    viewer = _member(owner, "izleyici", "viewer")
    r = viewer.put(f"/api/debts/{did}/schedule", json=PLAN)
    assert r.status_code == 403 and "görüntüleme" in r.json()["detail"]
    assert viewer.delete(f"/api/debts/{did}/schedule").status_code == 403
    editor = _member(owner, "ekleyen", "editor")
    assert editor.put(f"/api/debts/{did}/schedule", json=PLAN).status_code == 403
    full = _member(owner, "tam", "full")
    assert full.put(f"/api/debts/{did}/schedule", json=PLAN).status_code == 200
    assert owner.delete(f"/api/debts/{did}/schedule").status_code == 200


def test_sources_never_suggest_restructuring():
    """Plan bölümü / formu ve sunucu ucu: kullanıcıya giden dizgilerde yapılandırma, erteleme, kredi vb. önerisi yok."""
    root = Path(__file__).resolve().parent.parent
    files = [root / "web" / "src" / "components" / "DebtScheduleSection.tsx", root / "web" / "src" / "lib" / "odemePlani.ts",
             root / "web" / "src" / "components" / "Debts.tsx", root / "app" / "debt_schedule.py"]
    api = (root / "app" / "api" / "main.py").read_text(encoding="utf-8")
    start = api.index("class DebtScheduleIn")
    for name, text in [(f.name, f.read_text(encoding="utf-8")) for f in files] + \
            [("main.py schedule", api[start:api.index("@app.delete(\"/api/debts/{did}\")", start)])]:
        text = re.sub(r'"""[\s\S]*?"""', "", text)
        text = re.sub(r"/\*[\s\S]*?\*/", "", text)
        text = re.sub(r"(^|\s)(#|//)[^\n]*", "", text)
        flat = " | ".join(a or b for a, b in re.findall(r'"([^"\n]{3,})"|`([^`]{3,})`', text))
        assert not _banned_in(flat) and not _restructure_in(flat), (name, _banned_in(flat), _restructure_in(flat))
