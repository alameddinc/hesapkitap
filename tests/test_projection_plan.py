"""GET /api/stats/projection paket sınırı (yetenek projection_limited): uzun görünüm Premium ("uzun_plan" satırı,
plans.plan_months; Planlama'nın 1 ay kuralıyla aynı). Ücretsiz yalnız ilk dönemi görür, limited true.

Bugün sabit: 27 Kas 2026; maaş 15'i. Kişiler ve tutarlar uydurmadır."""
from datetime import date

from app import upcoming
from tests.test_plans import _rc, _user, hosted  # noqa: F401 - hosted fixture

TODAY = date(2026, 11, 27)
KEYS = {"start", "variable_avg", "basis_months", "periods", "limited"}
PERIOD_KEYS = {"label", "year", "salary_date", "end_date", "salary", "fixed", "end_balance"}


def _salary(c):
    r = c.post("/api/recurring", json={"description": "Maaş", "amount": 60000.0, "kind": "gelir", "category": "Maaş",
                                       "day_of_month": 15, "start_month": "2026-09"})
    assert r.status_code == 200, r.text


def _get(c, q=""):
    r = c.get(f"/api/stats/projection{q}")
    assert r.status_code == 200, r.text
    j = r.json()
    assert set(j) == KEYS and all(set(p) == PERIOD_KEYS for p in j["periods"])
    return j


def test_free_sees_only_first_period_then_premium_unlocks(hosted, monkeypatch):  # noqa: F811
    monkeypatch.setattr(upcoming, "_today", lambda: TODAY)
    c, u = _user(hosted, "zeynep")
    _salary(c)
    for q in ("?months=13", "?months=36", "", "?months=1"):  # months ne olursa olsun tek dönem
        j = _get(c, q)
        assert j["limited"] is True and len(j["periods"]) == 1, q
    first = j["periods"][0]
    assert first["salary_date"] == "2026-12-15" and first["salary"] == 60000.0
    assert c.get("/api/stats/projection?months=0").status_code == 422  # doğrulama paketten bağımsız
    assert c.get("/api/stats/projection?months=99").status_code == 422
    assert "projection_limited" in c.get("/api/meta").json()["capabilities"]
    # Premium: istenen kadar dönem; ilk dönem ücretsizin gördüğüyle aynı
    assert _rc(hosted, "INITIAL_PURCHASE", u["id"]).status_code == 200
    j = _get(c, "?months=13")
    assert j["limited"] is False and len(j["periods"]) == 13 and j["periods"][0] == first
    assert _get(c, "?months=1")["limited"] is False  # limited paketi söyler, dönem sayısını değil
    # abonelik bitince sınır geri gelir
    assert _rc(hosted, "EXPIRATION", u["id"], exp_ms=1000).status_code == 200
    j = _get(c, "?months=13")
    assert j["limited"] is True and len(j["periods"]) == 1


def test_free_without_salary_is_still_marked_limited(hosted, monkeypatch):  # noqa: F811
    monkeypatch.setattr(upcoming, "_today", lambda: TODAY)
    c, _ = _user(hosted, "zeynep")
    j = _get(c, "?months=13")
    assert j["periods"] == [] and j["limited"] is True


def test_admin_table_can_open_long_view_to_free(hosted, monkeypatch):  # noqa: F811
    """Sınır plan tablosundaki "uzun_plan" satırını izler: yönetici satırı ücretsize açarsa görünüm de açılır."""
    monkeypatch.setattr(upcoming, "_today", lambda: TODAY)
    c, _ = _user(hosted, "zeynep")
    _salary(c)
    assert _get(c, "?months=6")["limited"] is True
    assert hosted.put("/api/admin/plan-table", json={"tiers": {"uzun_plan": "free"}}).status_code == 200
    j = _get(c, "?months=6")
    assert j["limited"] is False and len(j["periods"]) == 6


def test_self_hosted_is_unlimited(client, monkeypatch):
    monkeypatch.setattr(upcoming, "_today", lambda: TODAY)
    _salary(client)
    j = _get(client, "?months=13")
    assert j["limited"] is False and len(j["periods"]) == 13
    assert len(_get(client, "?months=36")["periods"]) == 36
