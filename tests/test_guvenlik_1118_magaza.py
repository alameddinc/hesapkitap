"""Güvenlik denetimi 18.11, Y3/O3: RevenueCat sandbox (TestFlight) satın alımı yalnız yönetici ve izinli test hesabına;
sırasız gelen eski olay yeni durumu ezmez. Kullanıcı kimlikleri ve işlem numaraları uydurmadır."""
import time

from tests.test_plans import _rc, _rc_event, _user, hosted  # noqa: F401 - fixture

NOW = int(time.time() * 1000)


def _plan(c) -> str:
    return c.get("/api/meta").json()["plan"]["plan"]


# ---------------------------------------------------------------- Y3

def test_sandbox_purchase_ignored_for_regular_user(hosted):  # noqa: F811
    """PoC C4: TestFlight (ücretsiz) satın alımı canlıda Aile paketi ve 300 okuma açıyordu."""
    from app import doc_reads

    c, u = _user(hosted)
    r = _rc_event({"type": "INITIAL_PURCHASE", "app_user_id": str(u["id"]), "entitlement_ids": ["aile"],
                   "environment": "SANDBOX", "expiration_at_ms": NOW + 3600_000})
    assert r.status_code == 200
    assert _plan(c) == "free"
    _rc_event({"type": "NON_RENEWING_PURCHASE", "app_user_id": str(u["id"]), "product_id": "okuma_300",
               "environment": "SANDBOX", "transaction_id": "sb-test-1"})
    assert doc_reads.credits(u["id"]) == 0


def test_sandbox_purchase_applies_to_admin(hosted):  # noqa: F811
    """Sahibin (yönetici) kendi TestFlight denemesi çalışır."""
    from app import plans, system

    admin = system.user_by_name("admin")
    assert plans.apply_revenuecat({"type": "INITIAL_PURCHASE", "app_user_id": str(admin["id"]), "entitlement_ids": ["aile"],
                                   "environment": "SANDBOX", "expiration_at_ms": NOW + 3600_000}).get("plan") == "aile"
    a = system.user(admin["id"])
    assert a["plan"] == "aile" and a["plan_env"] == "SANDBOX"


def test_sandbox_purchase_applies_to_listed_testers(hosted, monkeypatch):  # noqa: F811
    from app import doc_reads

    c1, u1 = _user(hosted, "testci1")
    c2, u2 = _user(hosted, "testci2")
    monkeypatch.setenv("HESAPKITAP_SANDBOX_USERS", f" {u1['id']} , TESTCI2@ornek.com")
    for u in (u1, u2):
        _rc_event({"type": "INITIAL_PURCHASE", "app_user_id": str(u["id"]), "entitlement_ids": ["premium"],
                   "environment": "SANDBOX", "expiration_at_ms": NOW + 3600_000})
    assert _plan(c1) == "premium" and _plan(c2) == "premium"
    _rc_event({"type": "NON_RENEWING_PURCHASE", "app_user_id": str(u1["id"]), "product_id": "okuma_300",
               "environment": "SANDBOX", "transaction_id": "sb-test-2"})
    assert doc_reads.credits(u1["id"]) >= 300


def test_sandbox_transfer_to_untrusted_account_ignored(hosted, monkeypatch):  # noqa: F811
    c1, u1 = _user(hosted, "testci3")
    c2, u2 = _user(hosted, "yabanci")
    monkeypatch.setenv("HESAPKITAP_SANDBOX_USERS", str(u1["id"]))
    _rc_event({"type": "INITIAL_PURCHASE", "app_user_id": str(u1["id"]), "entitlement_ids": ["aile"],
               "environment": "SANDBOX", "expiration_at_ms": NOW + 3600_000})
    _rc_event({"type": "TRANSFER", "transferred_from": [str(u1["id"])], "transferred_to": [str(u2["id"])],
               "entitlement_ids": ["aile"], "environment": "SANDBOX", "expiration_at_ms": NOW + 3600_000})
    assert _plan(c2) == "free" and _plan(c1) == "aile"


def test_production_events_unaffected(hosted):  # noqa: F811
    c, u = _user(hosted)
    _rc_event({"type": "INITIAL_PURCHASE", "app_user_id": str(u["id"]), "entitlement_ids": ["premium"],
               "environment": "PRODUCTION", "expiration_at_ms": NOW + 3600_000})
    assert _plan(c) == "premium"


# ---------------------------------------------------------------- O3

def test_late_expiration_of_previous_period_ignored(hosted):  # noqa: F811
    """PoC C3: RENEWAL'dan sonra gecikerek gelen önceki dönemin EXPIRATION'ı ödeyen kullanıcıyı ücretsize düşürüyordu."""
    c, u = _user(hosted)
    assert _rc(hosted, "RENEWAL", u["id"], exp_ms=NOW + 30 * 86400_000).status_code == 200
    assert _plan(c) == "premium"
    assert _rc_event({"type": "EXPIRATION", "app_user_id": str(u["id"]), "entitlement_ids": ["premium"],
                      "expiration_at_ms": NOW - 86400_000, "event_timestamp_ms": NOW - 86400_000}).status_code == 200
    assert _plan(c) == "premium"


def test_older_event_timestamp_ignored(hosted):  # noqa: F811
    """event_timestamp_ms: daha yeni olay uygulandıktan sonra gelen eski olay (ör. eski CANCELLATION) durumu ezmez."""
    from app import system

    c, u = _user(hosted)
    _rc_event({"type": "INITIAL_PURCHASE", "app_user_id": str(u["id"]), "entitlement_ids": ["premium"],
               "expiration_at_ms": NOW + 30 * 86400_000, "event_timestamp_ms": NOW})
    _rc_event({"type": "CANCELLATION", "app_user_id": str(u["id"]), "entitlement_ids": ["premium"],
               "expiration_at_ms": NOW + 86400_000, "event_timestamp_ms": NOW - 5000})
    v = system.user(u["id"])
    assert v["plan_renews"] == 1 and v["plan_until"].startswith(
        time.strftime("%Y-%m-%d", time.gmtime((NOW + 30 * 86400_000) / 1000)))
    # aynı ya da daha yeni olay uygulanır
    _rc_event({"type": "CANCELLATION", "app_user_id": str(u["id"]), "entitlement_ids": ["premium"],
               "event_timestamp_ms": NOW + 1000})
    assert system.user(u["id"])["plan_renews"] == 0


def test_real_expiration_still_downgrades(hosted):  # noqa: F811
    """Dönemin kendi bitişi (expiration_at_ms = paketin bitişi) ve daha yeni olay: ücretsize düşer."""
    c, u = _user(hosted)
    end = NOW + 86400_000
    _rc_event({"type": "RENEWAL", "app_user_id": str(u["id"]), "entitlement_ids": ["premium"], "expiration_at_ms": end,
               "event_timestamp_ms": NOW - 1000})
    assert _plan(c) == "premium"
    _rc_event({"type": "EXPIRATION", "app_user_id": str(u["id"]), "entitlement_ids": ["premium"], "expiration_at_ms": end,
               "event_timestamp_ms": NOW})
    assert _plan(c) == "free"
