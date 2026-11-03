"""Paketler (yalnızca barındırılan sürümde, HESAPKITAP_HOSTED=1): ücretsiz / premium / aile.

Kendi sunucusuna kuranda paket yoktur: her şey açık, yapay zekâ kotası yöneticinin ayarı (system.ai_quota).
Hazır sürümde kota iki sayaçlı "okuma hakkı"dır: belge ve fiş (READS, app/doc_reads.py); ek okuma paketi (PACKS).
Hangi sayfa/özellik hangi pakette: CATALOG (tek tablo; yönetici panelinden değiştirilir, system.db'de saklanır).
Paket defter sahibinindir; ortak deftere davet edilen üye sahibin paketini kullanır (okuma sayacı kişi başı).
Abonelik mağazadan (App Store / Google Play) RevenueCat üzerinden gelir: plan + bitiş tarihi kullanıcıya yazılır.
"""
import os
from datetime import date, datetime, timezone

from app import system

FREE, PREMIUM, FAMILY, SELF = "free", "premium", "aile", "self"
TIERS = (FREE, PREMIUM, FAMILY)  # sayfa tablosunda bir satırın açık olduğu en düşük paket

# ---------------------------------------------------------------- sayfa / özellik tablosu (TEK KAYNAK)
# (anahtar, ad, tür, varsayılan paket, yönetici değiştirebilir mi, açıklama). Hazır sürümün yöneticisi paketi yönetici
# panelinden değiştirir (system.db → sys_settings "plan_table"; deploy gerekmez); varsayılan buradaki değerdir.
# tür: "ozellik" = sunucunun da uyguladığı kural (GATED, history_ok, plan_months, bütçe sınırı); "sayfa" = Diğer'deki
# sayfa: istemci kilit rozeti + bulanık önizleme gösterir. Kayıt ekleme/düzenleme hiçbir pakette kilitlenmez.
# Kendi sunucusunda (SELF) tablo uygulanmaz: her şey açık.
CATALOG: list[tuple[str, str, str, str, bool, str]] = [
    ("istatistik", "İstatistik: karşılaştırma ve yıl özeti", "ozellik", PREMIUM, True,
     "Geçmiş dönemlerle karşılaştırma, yıl özeti, faiz geçmişi (bu ayın özeti her pakette açık)"),
    ("gecmis", "Geçmiş dönemler", "ozellik", PREMIUM, True, "Bu dönem ve geçen dönemden eskisi"),
    ("uzun_plan", "1 aydan uzun planlama", "ozellik", PREMIUM, True, "Planlama ekranında 2–24 ay"),
    ("butce", "Sınırsız bütçe", "ozellik", PREMIUM, True, "Ücretsizde 2 bütçe"),
    ("kumbara", "Yeni kumbara açmak", "ozellik", PREMIUM, True, "Var olan kumbaralar her pakette görünür ve kullanılır"),
    ("fiyatlar", "Fiyat takibi", "sayfa", PREMIUM, True, "Ürün ürün fiyat geçmişi, en ucuz market"),
    ("alsam_mi", "Alsam mı?", "sayfa", PREMIUM, True, "Taksitli alımın aylara etkisi"),
    ("posta", "Deftere özel posta adresi", "sayfa", PREMIUM, True, "Ekstre, döküm, fatura postayla kendiliğinden"),
    ("ortak_defter", "Ortak defter, üye yetkileri, ortak kumbara", "sayfa", FAMILY, True,
     "4 kişiye kadar; her üye Premium'un tamamını kullanır"),
    ("hesaplar", "Hesaplar ve kumbara", "sayfa", FREE, True, "Banka hesapları, nakit, net varlık"),
    ("borclar", "Borç / Alacak", "sayfa", FREE, True, "Kime ne borcun var, kimden alacağın var"),
    ("transferler", "Transferler", "sayfa", FREE, True, "Havale, FAST ve kendi hesapların arası"),
    ("planlama", "Planlama", "sayfa", FREE, True, "Ücretsizde 1 ay (uzun planlama ayrı satır)"),
    ("odemeler", "Ödemeler", "sayfa", FREE, True, "Yaklaşan kart, fatura ve sabit ödemeler"),
    ("faturalar", "Faturalar", "sayfa", FREE, True, "Fatura takibi ve son ödeme tarihleri"),
    ("tasarruf", "Tasarruf", "sayfa", FREE, True, "Nereden kısabileceğin"),
    ("oneriler", "Sabit kalem önerileri", "sayfa", FREE, True, "Her ay tekrarlayan ödemeleri bulur"),
    ("tablo", "Tablo", "sayfa", FREE, True, "Aylar ve kategoriler yan yana"),
    ("kurallar", "Kategori kuralları", "sayfa", FREE, True, "İşyerine göre kendiliğinden kategori"),
    ("zekat", "Zekât ve Sadaka", "sayfa", FREE, False, "Her pakette ücretsiz (değiştirilemez)"),
]
_ROWS = {k: (label, kind, tier, editable, desc) for k, label, kind, tier, editable, desc in CATALOG}
DEFAULT_TIERS = {k: r[2] for k, r in _ROWS.items()}
TABLE_KEY = "plan_table"          # sys_settings: {"anahtar": "free|premium|aile"} — yalnız varsayılandan farklı olanlar
UPGRADE_YEARLY_KEY = "quota_upgrade_yearly"  # "1": hak dolunca aylık abonelere yıllığa geçişi öner (varsayılan kapalı)
_TTL = 10.0
_cache: tuple[float, dict, str] | None = None  # (zaman, ayar, system.db yolu)


def _overrides() -> dict:
    import json
    import time

    global _cache
    now, where = time.monotonic(), system._path()
    if _cache and now - _cache[0] < _TTL and _cache[2] == where:
        return _cache[1]
    try:
        raw = json.loads(system.get(TABLE_KEY) or "{}")
    except ValueError:
        raw = {}
    ov = {k: v for k, v in (raw if isinstance(raw, dict) else {}).items()
          if k in _ROWS and _ROWS[k][3] and v in TIERS}
    _cache = (now, ov, where)
    return ov


def tier(key: str) -> str | None:
    """Satırın açık olduğu en düşük paket (yöneticinin ayarı, yoksa varsayılan). Tabloda olmayan anahtar: None."""
    if key not in _ROWS:
        return None
    return _overrides().get(key, DEFAULT_TIERS[key])


def tiers() -> dict[str, str]:
    return {k: tier(k) for k in _ROWS}


def set_tiers(changes: dict[str, str], reset: bool = False) -> dict[str, str]:
    """Yönetici: satırların paketini değiştir. Bilinmeyen anahtar, değiştirilemez satır (zekât) ya da geçersiz paket
    ValueError. Varsayılana dönen satır kayıttan düşer."""
    import json

    global _cache
    for k, v in changes.items():
        if k not in _ROWS:
            raise ValueError(f"Bilinmeyen satır: {k}")
        if not _ROWS[k][3]:
            raise ValueError(f"{_ROWS[k][0]} değiştirilemez")
        if v not in TIERS:
            raise ValueError(f"Geçersiz paket: {v}")
    ov = {} if reset else dict(_overrides())
    ov.update(changes)
    ov = {k: v for k, v in ov.items() if v != DEFAULT_TIERS[k]}
    system.put(TABLE_KEY, json.dumps(ov, sort_keys=True) if ov else None)
    _cache = None
    return tiers()


def table() -> list[dict]:
    """Arayüz (paket karşılaştırması, yönetici ekranı) için tablo, CATALOG sırasıyla."""
    return [{"key": k, "label": label, "kind": kind, "default": d, "tier": tier(k), "editable": ed, "desc": desc}
            for k, label, kind, d, ed, desc in CATALOG]


def upgrade_yearly_enabled() -> bool:
    return system.get(UPGRADE_YEARLY_KEY) == "1"


def set_upgrade_yearly(on: bool) -> None:
    system.put(UPGRADE_YEARLY_KEY, "1" if on else None)


def _tierset(t: str) -> set[str]:
    return {FREE, PREMIUM, FAMILY} if t == FREE else {PREMIUM, FAMILY} if t == PREMIUM else {FAMILY}


# geriye uyum: özellik → hangi paketlerde var (VARSAYILAN tablo; güncel değer için allows/tier)
FEATURES = {k: _tierset(t) for k, t in DEFAULT_TIERS.items()}
FREE_HISTORY_MONTHS = 1  # ücretsiz: bu dönem + geçen dönem
FREE_PLAN_MONTHS = 1
FREE_BUDGETS = 2
# Verini indirmek (zip/Excel) ve hesabı silmek her pakette serbest: KVKK erişim ve taşınabilirlik hakkı.

# ---------------------------------------------------------------- okuma hakları (app/doc_reads.py)
# İki ayrı aylık sayaç: "belge" (kart ekstresi, hesap dökümü, fatura, posta belgesi, yapay zekâ kategori önerisi) ve
# "fis" (fiş fotoğrafı). Kişi başıdır: ortak defterde her üyenin kendi sayacı var (aile paketi kişi başı 200 / 300).
# Deneme süresinde özel sınır yok: paketin normal hakları.
BELGE, FIS = "belge", "fis"
READS = {FREE: {BELGE: 10, FIS: 30}, PREMIUM: {BELGE: 200, FIS: 300}, FAMILY: {BELGE: 200, FIS: 300}}
DOC_READS = {p: v[BELGE] for p, v in READS.items()}  # geriye uyum (eski istemci: plan.ai_docs)
KIND_LABELS = {BELGE: "belge", FIS: "fiş"}
# Ek okuma paketi (mağazada tüketilebilir ürün): bitene kadar geçerli, ay dönümünde silinmez, paket değişince de kalır.
# Önce aylık hak, o bitince ek hak düşer; belge ve fişte ortak kullanılır.
PACKS = {"okuma_100": 100, "okuma_300": 300}
LABELS = {FREE: "Ücretsiz", PREMIUM: "Premium", FAMILY: "Aile", SELF: "Kendi sunucun"}
FEATURE_LABELS = {k: r[0] for k, r in _ROWS.items()}
# (yöntem, API yolu öneki) → özellik (paket yoksa 402). Yöntem None: hepsi.
GATED = [(None, "/api/prices", "fiyatlar"), (None, "/api/book/invites", "ortak_defter"),
         (None, "/api/plan/simulate", "alsam_mi"), (None, "/api/year-summary", "istatistik"),
         (None, "/api/interest-history", "istatistik"), ("POST", "/api/jars", "kumbara")]


class PlanRequired(Exception):
    def __init__(self, feature: str):
        self.feature = feature
        super().__init__(f"{FEATURE_LABELS.get(feature, feature)} {_needs(feature)} pakette")


def _needs(feature: str) -> str:
    return "Aile" if tier(feature) == FAMILY else "Premium"


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
    if p == SELF:
        return True
    t = tier(feature)
    return t is None or p in _tierset(t)


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


FAMILY_SEATS = 4  # aile paketi: sahibi + 3 üye (4 kişiye kadar Premium)


def seats_left(owner_id: int) -> int | None:
    """Ortak defterde kalan yer (bekleyen davetler de yer tutar). None = sınırsız (kendi sunucusu)."""
    if not hosted():
        return None
    pending = [i for i in system.invites() if i.get("member_of") == owner_id and not i.get("used_at")
               and (not i.get("expires_at") or i["expires_at"] >= date.today().isoformat())]
    return max(0, FAMILY_SEATS - 1 - len(system.members(owner_id)) - len(pending))


def limits(u: dict | None) -> dict:
    """Arayüz için: None = sınırsız."""
    return {"history_months": None if allows(u, "gecmis") else FREE_HISTORY_MONTHS,
            "plan_months": None if allows(u, "uzun_plan") else FREE_PLAN_MONTHS,
            "budgets": None if allows(u, "butce") else FREE_BUDGETS, "family_seats": FAMILY_SEATS if hosted() else None}


# defter başına saklanan dosyaların (ekstre PDF'i, fatura, fiş fotoğrafı, hesap dökümü) toplam boyutu
STORAGE_BYTES = {FREE: 200 * 1024 * 1024, PREMIUM: 2 * 1024 ** 3, FAMILY: 2 * 1024 ** 3}


def storage_limit(u: dict | None) -> int | None:
    """Bayt; None = sınırsız (kendi sunucusu, yönetici). Ortak defterde sahibin paketi (defter onundur)."""
    if not hosted() or (u and u.get("role") == "admin"):
        return None
    return STORAGE_BYTES[plan_of(u)]


def read_limit(u: dict, kind: str = BELGE) -> int:
    """Barındırılan sürümde paketin aylık okuma hakkı (belge ya da fiş); yöneticinin elle verdiği kişisel kota
    (users.ai_quota) her zaman önce gelir ve iki sayaca da aynı sayı olarak uygulanır."""
    if u.get("ai_quota") is not None:
        return u["ai_quota"]
    return READS[plan_of(u)][kind]


def ai_limit(u: dict) -> int | None:
    """Geriye uyum: belge sayacının aylık hakkı."""
    return read_limit(u, BELGE)


def trial(u: dict | None) -> dict:
    """Deneme süresi (mağazanın tanıtım teklifi; RevenueCat period_type == TRIAL). Bilgi amaçlı: denemede sınırlar
    paketin normal haklarıdır. Ortak defter üyesi için sahibin aboneliği."""
    from math import ceil

    o = _owner(u) if u else None
    on = bool(hosted() and o and o.get("plan_trial") and plan_of(u) in (PREMIUM, FAMILY) and o.get("plan_until"))
    left = None
    if on:
        try:
            end = datetime.fromisoformat(o["plan_until"])
            end = end if end.tzinfo else end.replace(tzinfo=timezone.utc)
            left = max(0, ceil((end - datetime.now(timezone.utc)).total_seconds() / 86400))
        except ValueError:
            left = None
    return {"trial": on, "trial_days_left": left}


def upgrade_yearly(u: dict | None) -> bool:
    """Hak dolunca "yıllığa geç" önerilsin mi: yalnız yönetici açtıysa (varsayılan kapalı) ve kullanıcı aylık mağaza
    aboneliğini kendisi ödüyorsa. Yıllık plan daha fazla okuma hakkı VERMEZ (haklar aylık ve iki planda aynı); deneme
    sırasında aylıktan yıllığa geçişin mağazada ne zaman başladığı docs/magaza-abonelik.md'de (hesapkitap-cloud)."""
    if not (u and hosted() and upgrade_yearly_enabled()) or u.get("member_of"):
        return False
    prod = (u.get("plan_product") or "").split(":")[0]
    return bool(subscription(u)["active"] and prod.endswith("_monthly"))


def _source(o: dict) -> str | None:
    src = o.get("plan_source")
    return "store" if src == "revenuecat" else "manual" if src == "manual" else None


def info(u: dict | None) -> dict:
    p = plan_of(u)
    o = _owner(u) if u else None
    out = {"plan": p, "label": LABELS[p], "hosted": hosted(),
           "until": o.get("plan_until") if o and p in (PREMIUM, FAMILY) else None,
           # paketin kaynağı: "store" (mağaza aboneliği, kendisi yenilenir) | "manual" (yöneticinin verdiği, süresi
           # dolunca biter; istemci bitişe az kala haber verir) | None
           "source": _source(o) if o and p in (PREMIUM, FAMILY) and u.get("role") != "admin" else None,
           "features": {f: allows(u, f) for f in _ROWS}, "feature_labels": FEATURE_LABELS, "limits": limits(u),
           "ai_docs": None if p == SELF else ai_limit(u) if u else DOC_READS[FREE],
           "member": bool(u and u.get("member_of"))}
    if hosted():
        # sayfa tablosu ve haklar: paket ekranı satır satır bunları gösterir (istemci sabit değer yazmaz)
        out["tiers"] = tiers()
        out["table"] = table()
        out["reads_by_plan"] = READS
        # paket başı sınırlar (karşılaştırma tablosu; None = sınırsız), sayfa tablosuna göre
        out["limits_by_plan"] = {p_: {"history_months": None if p_ in _tierset(tier("gecmis")) else FREE_HISTORY_MONTHS,
                                      "plan_months": None if p_ in _tierset(tier("uzun_plan")) else FREE_PLAN_MONTHS,
                                      "budgets": None if p_ in _tierset(tier("butce")) else FREE_BUDGETS}
                                 for p_ in TIERS}
        out["packs"] = [{"id": k, "reads": n} for k, n in PACKS.items()]
        out["family_seats"] = FAMILY_SEATS
        out.update(trial(u))
    return out


# ---------------------------------------------------------------- RevenueCat (mağaza abonelikleri)

ENTITLEMENTS = {"aile": FAMILY, "premium": PREMIUM}  # RevenueCat entitlement kimliği → paket
ACTIVE_EVENTS = {"INITIAL_PURCHASE", "RENEWAL", "PRODUCT_CHANGE", "UNCANCELLATION", "NON_RENEWING_PURCHASE",
                 "SUBSCRIPTION_EXTENDED", "TEMPORARY_ENTITLEMENT_GRANT"}
# CANCELLATION: otomatik yenileme kapandı ama süre bitene kadar paket sürer → bitişi güncelle, planı bırak
END_EVENTS = {"EXPIRATION"}


STORES = {"APP_STORE": "app_store", "MAC_APP_STORE": "app_store", "PLAY_STORE": "play_store"}
# yenileme kesin kapalı: CANCELLATION (kullanıcı iptal etti), tek seferlik alım
NO_RENEW_EVENTS = {"CANCELLATION", "NON_RENEWING_PURCHASE"}


def subscription(u: dict | None) -> dict:
    """Kullanıcının KENDİ mağaza aboneliği (ortak defter üyesi sahibin paketini kullanır ama ödemez).
    will_renew: CANCELLATION geldiyse False; bilinmiyorsa (eski abonelik, olay gelmemiş) True say — silme akışında
    "hâlâ yenileniyor" uyarısı gereksiz çıksın, eksik çıkmasın. store: app_store | play_store | None."""
    active = bool(hosted() and u and u.get("plan_source") == "revenuecat" and u.get("plan") in (PREMIUM, FAMILY)
                  and _active(u))
    raw = (u or {}).get("plan_store")
    return {"active": active, "will_renew": (u.get("plan_renews") != 0) if active else False,
            "until": u.get("plan_until") if active else None,
            "store": STORES.get(raw, raw.lower() if raw else None) if active else None}


def apply_revenuecat(event: dict) -> dict:
    """RevenueCat webhook olayı → kullanıcının paketi. app_user_id = bizim kullanıcı kimliğimiz (uygulama giriş yapınca
    RevenueCat'e bunu verir). Dönen: ne yapıldığı (kayıt için). Silinmiş (ya da silinmek üzere bekleyen) hesaba gelen
    olay silme kaydına not düşer (app/deletion.py): ör. silinmiş hesapta yenileme = kullanıcı mağazada iptal etmemiş."""
    from app import deletion

    uid_raw = str(event.get("app_user_id") or "")
    if not uid_raw.isdigit():
        return {"ignored": "app_user_id"}
    u = system.user(int(uid_raw))
    noted = deletion.note_store_event(int(uid_raw), event, exists=bool(u))
    if not u:
        return {"ignored": "user", **({"deleted_account": int(uid_raw), **noted} if noted is not None else {})}
    kind = event.get("type")
    product = str(event.get("product_id") or "").split(":")[0]  # Google: "<abonelik>:<taban plan>"
    if product in PACKS:  # ek okuma paketi (tüketilebilir): abonelik alanlarına dokunulmaz
        return _apply_pack(u, kind, product, event)
    ents = [ENTITLEMENTS[e] for e in (event.get("entitlement_ids") or []) if e in ENTITLEMENTS]
    plan = FAMILY if FAMILY in ents else PREMIUM if PREMIUM in ents else None
    exp_ms = event.get("expiration_at_ms")
    until = datetime.fromtimestamp(exp_ms / 1000, timezone.utc).isoformat(timespec="seconds") if exp_ms else None
    store = event.get("store") or u.get("plan_store")
    if kind in END_EVENTS:
        if u.get("plan_source") == "manual" and _active(u):  # yöneticinin verdiği (test) paketi mağaza bitişi silmesin
            return {"user": u["id"], "ignored": "manual"}
        system.update_user(u["id"], plan=FREE, plan_until=None, plan_source="revenuecat", plan_renews=None, plan_trial=0)
        return {"user": u["id"], "plan": FREE}
    if kind in ACTIVE_EVENTS and plan:
        # deneme bilgisi ve ürün: PRODUCT_CHANGE yalnız bilgi amaçlıdır (yeni ürün yenilemede RENEWAL/INITIAL_PURCHASE ile
        # gelir), ürünü ve deneme durumunu o olay değiştirmez
        extra = {} if kind == "PRODUCT_CHANGE" else {"plan_trial": 1 if event.get("period_type") == "TRIAL" else 0,
                                                    **({"plan_product": product} if product else {})}
        system.update_user(u["id"], plan=plan, plan_until=until, plan_source="revenuecat", plan_store=store,
                           plan_renews=0 if kind in NO_RENEW_EVENTS else 1, **extra)
        return {"user": u["id"], "plan": plan, "until": until}
    if kind == "CANCELLATION":  # yenileme kapandı; süre bitene kadar paket sürer
        system.update_user(u["id"], plan_renews=0, plan_store=store, **({"plan_until": until} if until else {}))
        return {"user": u["id"], "until": until, "will_renew": False}
    return {"ignored": kind}


def _apply_pack(u: dict, kind: str | None, product: str, event: dict) -> dict:
    """Ek okuma paketi. NON_RENEWING_PURCHASE (ya da INITIAL_PURCHASE): bakiyeye eklenir; aynı mağaza işlemi
    (transaction_id, yoksa olay kimliği) ikinci kez gelirse yazılmaz. CANCELLATION (mağaza iadesi): o işlemin hakkı
    bakiyeden düşer (harcanmış olanı geri alınmaz)."""
    from app import doc_reads

    txn = str(event.get("transaction_id") or event.get("id") or "")
    if not txn:
        return {"user": u["id"], "ignored": "transaction_id"}
    if kind in ("NON_RENEWING_PURCHASE", "INITIAL_PURCHASE"):
        added = doc_reads.add_pack(u["id"], product, txn)
        return {"user": u["id"], "pack": product, "added": PACKS[product] if added else 0,
                **({} if added else {"duplicate": True})}
    if kind == "CANCELLATION":
        return {"user": u["id"], "pack": product, "refunded": doc_reads.refund_pack(u["id"], txn)}
    return {"user": u["id"], "ignored": kind}
