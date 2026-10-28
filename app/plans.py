"""Paketler (yalnızca barındırılan sürümde, HESAPKITAP_HOSTED=1): ücretsiz / premium / aile.

Kendi sunucusuna kuranda paket yoktur: her şey açık, yapay zekâ kotası yöneticinin ayarı (system.ai_quota).
Paket defter sahibinindir; ortak deftere davet edilen üye sahibin paketini kullanır.
Abonelik mağazadan (App Store / Google Play) RevenueCat üzerinden gelir: plan + bitiş tarihi kullanıcıya yazılır.
"""
import os
from datetime import datetime, timezone

from app import system

FREE, PREMIUM, FAMILY, SELF = "free", "premium", "aile", "self"
# özellik → hangi paketlerde var
FEATURES = {
    "fiyatlar": {PREMIUM, FAMILY},       # fiyat takibi, en ucuz market
    "istatistik": {PREMIUM, FAMILY},     # geçmiş dönemlerle karşılaştırma, yıl özeti, faiz geçmişi (bu ayın özeti serbest)
    "alsam_mi": {PREMIUM, FAMILY},       # alım simülasyonu
    "kumbara": {PREMIUM, FAMILY},        # yeni kumbara açmak (var olanlar görünür)
    "gecmis": {PREMIUM, FAMILY},         # HISTORY_MONTHS'tan eski dönemler
    "uzun_plan": {PREMIUM, FAMILY},      # 1 aydan uzun planlama
    "butce": {PREMIUM, FAMILY},          # FREE_BUDGETS'tan fazla bütçe
    "ortak_defter": {FAMILY},            # eş / aile üyesi davet etmek
}
FREE_HISTORY_MONTHS = 1  # ücretsiz: bu dönem + geçen dönem
FREE_PLAN_MONTHS = 1
FREE_BUDGETS = 2
# Verini indirmek (zip/Excel) ve hesabı silmek her pakette serbest: KVKK erişim ve taşınabilirlik hakkı.
# ayda yapay zekâ ile belge okuma. Premium/Aile: adil kullanım (normal kullanıcı ~30-50 okur; kötüye kullanım kapanır).
# Ortak defterde sahip + üyeler aynı havuzdan düşer (system.ai_used_pool).
AI_DOCS = {FREE: 10, PREMIUM: 300, FAMILY: 300}
LABELS = {FREE: "Ücretsiz", PREMIUM: "Premium", FAMILY: "Aile", SELF: "Kendi sunucun"}
FEATURE_LABELS = {"fiyatlar": "Fiyat takibi", "istatistik": "Geçmişle karşılaştırma", "alsam_mi": "Alsam mı?",
                  "kumbara": "Kumbara", "gecmis": "Geçmiş aylar", "uzun_plan": "Uzun planlama", "butce": "Sınırsız bütçe",
                  "ortak_defter": "Ortak defter (aile)"}
# (yöntem, API yolu öneki) → özellik (paket yoksa 402). Yöntem None: hepsi.
GATED = [(None, "/api/prices", "fiyatlar"), (None, "/api/book/invites", "ortak_defter"),
         (None, "/api/plan/simulate", "alsam_mi"), (None, "/api/year-summary", "istatistik"),
         (None, "/api/interest-history", "istatistik"), ("POST", "/api/jars", "kumbara")]


class PlanRequired(Exception):
    def __init__(self, feature: str):
        self.feature = feature
        super().__init__(f"{FEATURE_LABELS.get(feature, feature)} {_needs(feature)} pakette")


def _needs(feature: str) -> str:
    return "Aile" if FEATURES.get(feature) == {FAMILY} else "Premium"


def hosted() -> bool:
    return os.environ.get("HESAPKITAP_HOSTED") == "1"


def open_signup() -> bool:
    """Barındırılan sürümde herkes davet kodu olmadan hesap açabilir."""
    return hosted() and os.environ.get("HESAPKITAP_OPEN_SIGNUP") == "1"


def _owner(u: dict) -> dict:
    """Ortak defter üyesi: paket sahibinkidir."""
    if u.get("member_of"):
        o = system.user(u["member_of"])
        if o:
            return o
    return u


def _active(u: dict, now: datetime | None = None) -> bool:
    until = u.get("plan_until")
    if not until:
        return True  # süresiz (elle verilmiş)
    try:
        end = datetime.fromisoformat(until)
    except ValueError:
        return False
    if end.tzinfo is None:
        end = end.replace(tzinfo=timezone.utc)
    return end > (now or datetime.now(timezone.utc))


def plan_of(u: dict | None) -> str:
    if not hosted():
        return SELF
    if not u:
        return FREE
    if u.get("role") == "admin":
        return FAMILY
    o = _owner(u)
    p = o.get("plan") or FREE
    return p if p in (PREMIUM, FAMILY) and _active(o) else FREE


def allows(u: dict | None, feature: str) -> bool:
    p = plan_of(u)
    return p == SELF or p in FEATURES.get(feature, {p})


def require(u: dict | None, feature: str) -> None:
    if not allows(u, feature):
        raise PlanRequired(feature)


def gate_for(path: str, method: str = "GET") -> str | None:
    for m, prefix, f in GATED:
        if (m is None or m == method) and (path == prefix or path.startswith(prefix + "/") or path.startswith(prefix + "?")):
            # POST /api/jars/{id}/... (mevcut kumbaraya alım/ayırma) serbest; sadece yeni kumbara açmak kilitli
            if f == "kumbara" and path != prefix:
                continue
            return f
    return None


def history_ok(u: dict | None, month: str | None) -> bool:
    """Ücretsiz pakette bu dönem + son FREE_HISTORY_MONTHS dönem görünür."""
    if not month or allows(u, "gecmis"):
        return True
    from app import finance

    return month >= finance.add_months(finance.current_period(), -FREE_HISTORY_MONTHS)


def plan_months(u: dict | None, months: int) -> int:
    return months if allows(u, "uzun_plan") else min(months, FREE_PLAN_MONTHS)


def limits(u: dict | None) -> dict:
    """Arayüz için: None = sınırsız."""
    p = plan_of(u)
    full = p in (SELF, PREMIUM, FAMILY)
    return {"history_months": None if full else FREE_HISTORY_MONTHS, "plan_months": None if full else FREE_PLAN_MONTHS,
            "budgets": None if full else FREE_BUDGETS}


def ai_limit(u: dict) -> int | None:
    """Barındırılan sürümde paketin kotası; elle verilmiş kişisel kota her zaman önce gelir."""
    if u.get("ai_quota") is not None:
        return u["ai_quota"]
    return AI_DOCS[plan_of(u)]


def info(u: dict | None) -> dict:
    p = plan_of(u)
    o = _owner(u) if u else None
    return {"plan": p, "label": LABELS[p], "hosted": hosted(),
            "until": o.get("plan_until") if o and p in (PREMIUM, FAMILY) else None,
            "features": {f: allows(u, f) for f in FEATURES}, "feature_labels": FEATURE_LABELS, "limits": limits(u),
            "ai_docs": None if p == SELF else ai_limit(u) if u else AI_DOCS[FREE],
            "member": bool(u and u.get("member_of"))}


# ---------------------------------------------------------------- RevenueCat (mağaza abonelikleri)

ENTITLEMENTS = {"aile": FAMILY, "premium": PREMIUM}  # RevenueCat entitlement kimliği → paket
ACTIVE_EVENTS = {"INITIAL_PURCHASE", "RENEWAL", "PRODUCT_CHANGE", "UNCANCELLATION", "NON_RENEWING_PURCHASE",
                 "SUBSCRIPTION_EXTENDED", "TEMPORARY_ENTITLEMENT_GRANT"}
# CANCELLATION: otomatik yenileme kapandı ama süre bitene kadar paket sürer → bitişi güncelle, planı bırak
END_EVENTS = {"EXPIRATION"}


def apply_revenuecat(event: dict) -> dict:
    """RevenueCat webhook olayı → kullanıcının paketi. app_user_id = bizim kullanıcı kimliğimiz (uygulama giriş yapınca
    RevenueCat'e bunu verir). Dönen: ne yapıldığı (kayıt için)."""
    uid_raw = str(event.get("app_user_id") or "")
    if not uid_raw.isdigit():
        return {"ignored": "app_user_id"}
    u = system.user(int(uid_raw))
    if not u:
        return {"ignored": "user"}
    kind = event.get("type")
    ents = [ENTITLEMENTS[e] for e in (event.get("entitlement_ids") or []) if e in ENTITLEMENTS]
    plan = FAMILY if FAMILY in ents else PREMIUM if PREMIUM in ents else None
    exp_ms = event.get("expiration_at_ms")
    until = datetime.fromtimestamp(exp_ms / 1000, timezone.utc).isoformat(timespec="seconds") if exp_ms else None
    if kind in END_EVENTS:
        system.update_user(u["id"], plan=FREE, plan_until=None, plan_source="revenuecat")
        return {"user": u["id"], "plan": FREE}
    if kind in ACTIVE_EVENTS and plan:
        system.update_user(u["id"], plan=plan, plan_until=until, plan_source="revenuecat")
        return {"user": u["id"], "plan": plan, "until": until}
    if kind == "CANCELLATION" and until:
        system.update_user(u["id"], plan_until=until)
        return {"user": u["id"], "until": until}
    return {"ignored": kind}
