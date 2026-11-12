"""Ölçüm (dönüşüm hunisi): hesap açma → aktivasyon → tutunma → deneme/abonelik → paket ekranı. Yalnız yönetici görür.

Gizlilik (KVKK): üçüncü taraf analitik yok, IP / cihaz kimliği / sayfa adresi / saat saklanmaz.
- user_activity: kişi başı yalnız GÜN (o gün uygulamayı kullandı mı, hangi platformdan, en çok 5'e kadar kaç kayıt
  ekledi). Kohort (hafta hafta tutunma) için gereken tek kişiye bağlı satır; ACTIVITY_KEEP_DAYS (400) günden eskisi gece
  bakımında silinir, hesap silinince hemen silinir.
- funnel_events: istemci olayları (paket ekranı, satın alma…) yalnız TOPLAM: gün × olay × platform × paket durumu.
  Kişi kimliği yazılmaz; aynı kişinin aynı gün aynı olayı süreç belleğinde tekilleştirilir ("kişi" sütunu).
- store_events: mağaza (RevenueCat) olaylarının toplamı: gün × tür × paket × dönem × mağaza. Kişi yok.
- Demo ziyaretleri demo sunucusunun kendi istatistiğinden gelir (app/demo_mode.py, günlük değişen parmak izi).

"Aktif gün": oturum açmış kullanıcının ana ekranlardan birini okuyan (CORE_READS) ya da kayıt yazan başarılı isteği.
Arka plan uçları (oturum, bildirim kaydı, olay gönderme, sorun bildir) sayılmaz.
Haftalar Türkiye saatiyle pazartesi başlar.
"""
import json
import logging
import os
import re
import threading
import time
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from app import system

TZ = ZoneInfo("Europe/Istanbul")
log = logging.getLogger("hesapkitap.funnel")

ACTIVITY_KEEP_DAYS = 400   # kişi başı gün satırı (kohort için 12 hafta + 4 hafta yeter; yıl karşılaştırması için 400)
AGG_KEEP_DAYS = 1100       # toplamlar (kişisiz): 3 yıl
SEEN_KEEP_DAYS = 45        # webhook tekrar kimliği
WRITE_CAP = 5              # "≥5 kayıt" eşiği için gün başı en çok 5 sayılır (daha fazlası yazılmaz)
EVENT_DAILY_CAP = 20       # kişi/gün/olay başı en çok sayılan olay
EVENT_HOURLY = 60          # kişi başı saatte en çok olay isteği (üstü 429)
STARTED_KEY = "funnel_started"  # sys_settings: ölçümün başladığı gün (öncesinin kohortu eksik sayılır)

# Özet görünümü (Minimal / Ayrıntılı / Sade): hangi görünümde açıldı ve elle değiştirme (nereden → nereye). Yalnız toplam.
GORUNUMLER = ("minimal", "ayrintili", "sade")
GORUNUM_OLAY = tuple(f"ozet_gorunum_{g}" for g in GORUNUMLER)
GORUNUM_DEGISTI = tuple(f"ozet_degisti_{a}_{b}" for a in GORUNUMLER for b in GORUNUMLER if a != b)
FUNNEL_EVENTS = ("paywall_view", "plans_open", "purchase_start", "purchase_success", "purchase_cancel", "signup_demo")
EVENTS = FUNNEL_EVENTS + GORUNUM_OLAY + GORUNUM_DEGISTI
PLATFORMS = ("web", "ios", "android")
STORE_PLATFORM = {"app_store": "ios", "play_store": "android"}

# aktif gün: bu önekli okuma uçları (ana ekranlar) ya da herhangi bir yazma (PASSIVE hariç)
CORE_READS = ("/api/summary", "/api/transactions", "/api/plan", "/api/statements", "/api/bills", "/api/prices",
              "/api/shopping-list", "/api/shopping-trips", "/api/accounts", "/api/net-worth", "/api/payables",
              "/api/digest", "/api/debts", "/api/transfers", "/api/budgets", "/api/zakat", "/api/cards",
              "/api/compare", "/api/year-summary", "/api/entries", "/api/inbox", "/api/savings", "/api/jars",
              "/api/installments", "/api/interest", "/api/holdings", "/api/donations", "/api/recurring")
PASSIVE = ("/api/auth/", "/api/events", "/api/push/", "/api/feedback", "/api/me/", "/api/onboarding",
           "/api/insights/", "/api/digest/dismiss", "/api/admin/")
# "kayıt" (aktivasyon ≥5): tek kayıt oluşturan POST uçları. Toplu içe aktarma (ekstre, banka dökümü) belge sayılır.
RECORD_POSTS = frozenset({"/api/entries", "/api/debts", "/api/bills", "/api/dekont/save", "/api/recurring",
                          "/api/donations", "/api/share", "/share-target"})
WRITE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}

# Mağaza fiyatı (Türkiye, KDV dahil): MRR yaklaşık hesabı. Kaynak: hesapkitap-cloud docs/magaza-abonelik.md (02.11.2026).
# Fiyat değişince HESAPKITAP_STORE_PRICES='{"premium_monthly": 79.99, ...}' ile ezilir (deploy gerekmez).
DEFAULT_PRICES = {"premium_monthly": 79.99, "premium_yearly": 599.99, "aile_monthly": 149.99, "aile_yearly": 1199.99,
                  "okuma_100": 49.99, "okuma_300": 119.99}
VAT = 0.20          # fiyatın içindeki KDV
STORE_CUT = 0.15    # küçük işletme programı mağaza payı (Apple / Google, yıllık 1 M$ altı)


def enabled() -> bool:
    """Hazır sürümde açık; kendi sunucusunda istenirse HESAPKITAP_FUNNEL=1 (varsayılan kapalı: hiç satır yazılmaz)."""
    from app import plans

    return plans.hosted() or os.environ.get("HESAPKITAP_FUNNEL") == "1"


def today() -> date:
    return datetime.now(TZ).date()


def prices() -> dict[str, float]:
    out = dict(DEFAULT_PRICES)
    try:
        raw = json.loads(os.environ.get("HESAPKITAP_STORE_PRICES") or "{}")
        out.update({k: float(v) for k, v in raw.items() if k in DEFAULT_PRICES and float(v) >= 0})
    except (ValueError, TypeError, AttributeError):
        log.warning("HESAPKITAP_STORE_PRICES okunamadı; varsayılan fiyatlar")
    return out


def platform_of(headers) -> str:
    """İsteğin platformu: mobil uygulama (X-HK-Client: mobile) iOS / Android (tarayıcı kimliğinden), yoksa web.
    Kimlik yazılmaz; yalnız bu üç (bilinemezse "mobil") değerden biri."""
    ua = headers.get("user-agent") or ""
    if (headers.get("x-hk-client") or "").lower() == "mobile" or "HesapKitapApp/" in ua:
        return "ios" if re.search(r"iPhone|iPad|iPod|iOS", ua) else "android" if "Android" in ua else "mobil"
    return "web"


_started_in: set[str] = set()  # başlangıç günü yazılmış sistem veritabanları (her istekte sorgu olmasın)


def _ensure_started() -> None:
    where = system._path()
    if where in _started_in:
        return
    if not system.get(STARTED_KEY):
        system.put(STARTED_KEY, today().isoformat())
    _started_in.add(where)


# ---------------------------------------------------------------- aktif gün (middleware)

_seen: dict[tuple[int, str], int] = {}  # (kişi, gün) → bu süreçte sayılan kayıt; yalnız bellek (kimlik yazılmaz)
_seen_day = ""
_lock = threading.Lock()


def _reset_memory() -> None:
    global _seen_day
    with _lock:
        _seen.clear()
        _seen_day = ""
        _events_seen.clear()
        _rate.clear()


def touch(u: dict | None, method: str, path: str, status: int, headers) -> None:
    """Başarılı, oturumlu isteğin ardından: kişinin bugünü (bir kez) ve kayıt sayısı (en çok WRITE_CAP). Hata hiçbir
    isteği bozmaz. Aynı gün ikinci istekten sonrası (kayıt değilse) veritabanına hiç gitmez."""
    global _seen_day
    try:
        if status >= 400 or not u or u.get("demo") or not enabled():
            return
        uid = u.get("id")
        if not isinstance(uid, int) or uid <= 0:
            return
        write = method in WRITE_METHODS and not path.startswith(PASSIVE)
        if not (write or (method == "GET" and path.startswith(CORE_READS))):
            return
        record = method == "POST" and path in RECORD_POSTS
        day = today().isoformat()
        with _lock:
            if day != _seen_day:  # gün döndü: dünkü bellek gereksiz
                _seen.clear()
                _seen_day = day
            cur = _seen.get((uid, day))
            first = cur is None
            add = record and (cur or 0) < WRITE_CAP
            if first or add:
                _seen[(uid, day)] = (cur or 0) + (1 if add else 0)
        if not (first or add):
            return
        with system.connect() as c:
            if first:
                c.execute("INSERT OR IGNORE INTO user_activity (user_id, day, platform) VALUES (?, ?, ?)",
                          (uid, day, platform_of(headers)))
            if add:
                c.execute("UPDATE user_activity SET writes = MIN(writes + 1, ?) WHERE user_id = ? AND day = ?",
                          (WRITE_CAP, uid, day))
        if first:
            _ensure_started()
    except Exception:  # noqa: BLE001 - ölçüm uygulamayı asla bozmasın
        log.exception("aktif gün yazılamadı")


# ---------------------------------------------------------------- istemci olayları (POST /api/events)

_events_seen: set[tuple[int, str, str]] = set()   # (kişi, olay, gün): "kişi" sütunu bir kez artsın
_events_n: dict[tuple[int, str, str], int] = {}    # (kişi, olay, gün) → sayılan olay (EVENT_DAILY_CAP)
_rate: dict[int, list[float]] = {}


class RateLimited(Exception):
    pass


def plan_state(u: dict | None) -> str:
    """Olay anındaki paket durumu: free | trial | premium | aile | self (kendi sunucusu)."""
    from app import plans

    p = plans.plan_of(u)
    if p in (plans.PREMIUM, plans.FAMILY) and plans.trial(u).get("trial"):
        return "trial"
    return p


def record_event(u: dict | None, name: str, platform: str, now: float | None = None) -> bool:
    """Beyaz listedeki olayın günlük toplamını artır. True: sayıldı. ValueError: liste dışı ad. RateLimited: kişi
    saatte EVENT_HOURLY'yi aştı. Demo ve ölçümün kapalı olduğu sunucu: sayılmaz (False)."""
    if name not in EVENTS:
        raise ValueError("Bilinmeyen olay")
    if not u or u.get("demo") or not enabled() or not isinstance(u.get("id"), int):
        return False
    uid, now = u["id"], now or time.time()
    with _lock:
        if len(_rate) > 20000:
            for k in [k for k, q in _rate.items() if not q or q[-1] < now - 3600]:
                _rate.pop(k, None)
        q = [t for t in _rate.get(uid, []) if t > now - 3600]
        if len(q) >= EVENT_HOURLY:
            _rate[uid] = q
            raise RateLimited()
        _rate[uid] = q + [now]
        day = datetime.fromtimestamp(now, TZ).date().isoformat()
        if len(_events_n) > 50000:  # bellek sınırı: eski günler düşer
            for k in [k for k in _events_n if k[2] != day]:
                _events_n.pop(k, None)
            _events_seen.intersection_update({k for k in _events_seen if k[2] == day})
        key = (uid, name, day)
        if _events_n.get(key, 0) >= EVENT_DAILY_CAP:
            return False
        _events_n[key] = _events_n.get(key, 0) + 1
        new_user = key not in _events_seen
        _events_seen.add(key)
    ps = plan_state(u)
    plat = platform if platform in PLATFORMS + ("mobil",) else "mobil"
    with system.connect() as c:
        c.execute("INSERT INTO funnel_events (day, event, platform, plan_state, n, users) VALUES (?, ?, ?, ?, 1, ?) "
                  "ON CONFLICT (day, event, platform, plan_state) DO UPDATE SET n = n + 1, users = users + excluded.users",
                  (day, name, plat, ps, int(new_user)))
    return True


# ---------------------------------------------------------------- mağaza olayları (RevenueCat webhook)

def _product(event: dict) -> str:
    return str(event.get("product_id") or "").split(":")[0]


def store_kind(event: dict) -> str | None:
    """RevenueCat olayı → ölçüm türü. None: sayılmaz."""
    t = event.get("type")
    trial = event.get("period_type") == "TRIAL"
    prod = _product(event)
    from app import plans

    if prod in plans.PACKS:
        return {"NON_RENEWING_PURCHASE": "pack", "INITIAL_PURCHASE": "pack", "CANCELLATION": "pack_refund"}.get(t)
    if t == "INITIAL_PURCHASE":
        return "trial_start" if trial else "purchase"
    if t == "RENEWAL":
        return "trial_convert" if event.get("is_trial_conversion") else "renewal"
    if t == "CANCELLATION":
        if event.get("cancel_reason") == "CUSTOMER_SUPPORT":  # RevenueCat: mağaza iadesi
            return "refund"
        return "trial_cancel" if trial else "cancel"
    return {"EXPIRATION": "expire", "PRODUCT_CHANGE": "product_change", "UNCANCELLATION": "uncancel",
            "BILLING_ISSUE": "billing_issue"}.get(t)


def _plan_period(event: dict) -> tuple[str, str]:
    prod = _product(event)
    from app import plans

    if prod in plans.PACKS:
        return prod, "tek"
    plan = "aile" if prod.startswith("aile") or "aile" in (event.get("entitlement_ids") or []) else \
        "premium" if prod.startswith("premium") or "premium" in (event.get("entitlement_ids") or []) else "?"
    period = "monthly" if prod.endswith("_monthly") else "yearly" if prod.endswith("_yearly") else "?"
    return plan, period


def _store(event: dict) -> str:
    if str(event.get("environment") or "").upper() == "SANDBOX":
        return "sandbox"  # test satın alımı: sayılarda ayrı durur, toplamlara girmez
    raw = str(event.get("store") or "")
    return {"APP_STORE": "app_store", "MAC_APP_STORE": "app_store", "PLAY_STORE": "play_store"}.get(raw, raw.lower() or "?")


def record_store_event(event: dict, at: float | None = None) -> str | None:
    """Webhook olayının toplamını artır (aynı olay kimliği ikinci kez sayılmaz). Dönen: sayılan tür ya da None."""
    try:
        kind = store_kind(event)
        if not kind:
            return None
        ms = event.get("event_timestamp_ms") or event.get("purchased_at_ms")
        ts = (ms / 1000) if isinstance(ms, (int, float)) and ms > 0 else (at or time.time())
        day = datetime.fromtimestamp(ts, TZ).date().isoformat()
        plan, period = _plan_period(event)
        eid = str(event.get("id") or "")
        with system.connect() as c:
            if eid:
                if c.execute("SELECT 1 FROM store_event_seen WHERE id = ?", (eid,)).fetchone():
                    return None
                c.execute("INSERT INTO store_event_seen (id, at) VALUES (?, ?)", (eid, today().isoformat()))
            c.execute("INSERT INTO store_events (day, kind, plan, period, store, n) VALUES (?, ?, ?, ?, ?, 1) "
                      "ON CONFLICT (day, kind, plan, period, store) DO UPDATE SET n = n + 1",
                      (day, kind, plan, period, _store(event)))
        return kind
    except Exception:  # noqa: BLE001 - ölçüm webhook'u bozmasın
        log.exception("mağaza olayı sayılamadı")
        return None


# ---------------------------------------------------------------- bakım

def prune(now: date | None = None) -> dict:
    d = now or today()
    with system.connect() as c:
        a = c.execute("DELETE FROM user_activity WHERE day < ?", ((d - timedelta(days=ACTIVITY_KEEP_DAYS)).isoformat(),)).rowcount
        cut = (d - timedelta(days=AGG_KEEP_DAYS)).isoformat()
        e = c.execute("DELETE FROM funnel_events WHERE day < ?", (cut,)).rowcount
        s = c.execute("DELETE FROM store_events WHERE day < ?", (cut,)).rowcount
        c.execute("DELETE FROM store_event_seen WHERE at < ?", ((d - timedelta(days=SEEN_KEEP_DAYS)).isoformat(),))
    return {"activity": a, "events": e, "store": s}


# ---------------------------------------------------------------- rapor

def _monday(d: date) -> date:
    return d - timedelta(days=d.weekday())


def _local_day(created_at: str | None) -> date | None:
    """users.created_at (UTC, 'YYYY-MM-DD HH:MM:SS' ya da ISO) → Türkiye günü."""
    if not created_at:
        return None
    try:
        dt = datetime.fromisoformat(str(created_at).replace(" ", "T"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(TZ).date()


def _rate_of(k: int | None, n: int | None) -> float | None:
    return round(100 * k / n, 1) if k is not None and n else None


def report(weeks: int = 12, platform: str | None = None, now: date | None = None, demo: dict | None = None) -> dict:
    """Yönetici "Ölçüm" sayfası. platform: None (hepsi) | web | ios | android — kohortta hesabın ilk aktif günündeki
    platform, olaylarda isteğin platformu, mağazada App Store = ios, Google Play = android (web'de mağaza yok).
    demo: demo sunucusunun günlük istatistiği (app.demo_mode.stats "günlük"), yoksa None."""
    from app import plans

    weeks = max(1, min(int(weeks), 52))
    plat = platform if platform in PLATFORMS else None
    t = now or today()
    first = _monday(t) - timedelta(weeks=weeks - 1)
    starts = [first + timedelta(weeks=i) for i in range(weeks)]
    started_s = system.get(STARTED_KEY)
    started = date.fromisoformat(started_s) if started_s else None

    def widx(d: date) -> int | None:
        if d < first or d > t:
            return None
        return (d - first).days // 7

    with system.connect() as c:
        users = [dict(r) for r in c.execute(
            "SELECT id, created_at, role, member_of, deleted_at FROM users WHERE role <> 'admin'")]
        act: dict[int, dict[date, tuple[str, int]]] = defaultdict(dict)
        for uid, day, p, w in c.execute("SELECT user_id, day, platform, writes FROM user_activity"):
            act[uid][date.fromisoformat(day)] = (p, w)
        reads: dict[int, set[date]] = defaultdict(set)
        for uid, day in c.execute("SELECT user_id, day FROM read_daily WHERE count > 0"):
            try:
                reads[uid].add(date.fromisoformat(day))
            except ValueError:
                pass
        for uid, at in c.execute("SELECT user_id, at FROM ai_calls WHERE user_id IS NOT NULL AND kind <> 'kategori'"):
            if d := _local_day(at):
                reads[uid].add(d)
        ev_rows = c.execute("SELECT day, event, platform, plan_state, n, users FROM funnel_events WHERE day >= ?",
                            (first.isoformat(),)).fetchall()
        ev_gorunum = c.execute("SELECT day, event, platform, plan_state, n, users FROM funnel_events WHERE day >= ? AND "
                               "event LIKE 'ozet_%'", ((t - timedelta(days=GORUNUM_GUN - 1)).isoformat(),)).fetchall()
        ev_all = c.execute("SELECT event, platform, SUM(n), SUM(users) FROM funnel_events GROUP BY event, platform").fetchall()
        st_rows = c.execute("SELECT day, kind, plan, period, store, n FROM store_events").fetchall()
        subs_rows = [dict(r) for r in c.execute(
            "SELECT id, plan, plan_until, plan_source, plan_store, plan_trial, plan_product, plan_renews, plan_env, "
            "member_of, role FROM users WHERE plan_source = 'revenuecat' AND plan IN ('premium', 'aile')")]

    # ---- kohortlar: hesap açma haftası, platform = ilk aktif gün
    def signup_platform(uid: int) -> str:
        a = act.get(uid)
        if not a:
            return "bilinmiyor"
        return a[min(a)][0]

    blank = lambda: {"signups": 0, "web": 0, "ios": 0, "android": 0, "mobil": 0, "bilinmiyor": 0,  # noqa: E731
                     "act_n": 0, "act_doc": 0, "act_rec": 0, "act_any": 0,
                     "w2_n": 0, "w2": 0, "w4_n": 0, "w4": 0}
    rows = [blank() for _ in starts]
    tot = blank()
    for u in users:
        d0 = _local_day(u["created_at"])
        if not d0:
            continue
        sp = signup_platform(u["id"])
        if plat and sp != plat:
            continue
        targets = [tot] + ([rows[i]] if (i := widx(d0)) is not None else [])
        a = act.get(u["id"], {})
        measured = bool(started and d0 >= started)
        days = sorted(a)
        for r in targets:
            r["signups"] += 1
            r[sp if sp in r else "bilinmiyor"] += 1
        # aktivasyon: ilk 7 gün (d0..d0+6) dolmuş ve ölçüm başladıktan sonra açılmış hesap
        if measured and d0 + timedelta(days=6) < t:
            win = [d0 + timedelta(days=k) for k in range(7)]
            doc = any(x in reads.get(u["id"], ()) for x in win)
            rec = sum(a[x][1] for x in win if x in a) >= WRITE_CAP
            for r in targets:
                r["act_n"] += 1
                r["act_doc"] += doc
                r["act_rec"] += rec
                r["act_any"] += doc or rec
        # tutunma: 2. hafta (7–13. gün), 4. hafta (21–27. gün) en az bir aktif gün; pencere bitmiş olmalı
        for key, lo, hi in (("w2", 7, 13), ("w4", 21, 27)):
            if measured and d0 + timedelta(days=hi) < t:
                back = any(lo <= (x - d0).days <= hi for x in days)
                for r in targets:
                    r[f"{key}_n"] += 1
                    r[key] += back

    def finish(r: dict) -> dict:
        return {**r, "act_any_pct": _rate_of(r["act_any"], r["act_n"]), "act_doc_pct": _rate_of(r["act_doc"], r["act_n"]),
                "act_rec_pct": _rate_of(r["act_rec"], r["act_n"]), "w2_pct": _rate_of(r["w2"], r["w2_n"]),
                "w4_pct": _rate_of(r["w4"], r["w4_n"])}

    # ---- demo → hesap
    sd = [0] * weeks
    sd_tot = 0
    for day, ev, p, _ps, _n, us in ev_rows:
        if ev == "signup_demo" and (not plat or p == plat) and (i := widx(date.fromisoformat(day))) is not None:
            sd[i] += us
    for ev, p, _n, us in ev_all:
        if ev == "signup_demo" and (not plat or p == plat):
            sd_tot += us or 0
    demo_weeks = None
    if demo is not None:
        demo_weeks = [{"sessions": 0, "visitors": 0} for _ in starts]
        for day, v in (demo or {}).items():
            try:
                i = widx(date.fromisoformat(day))
            except ValueError:
                continue
            if i is None:
                continue
            pv = v.get("platform") if isinstance(v, dict) else None
            if plat:
                if not isinstance(pv, dict):
                    demo_weeks = None  # eski demo sunucusu platform ayırmıyor
                    break
                demo_weeks[i]["sessions"] += int(pv.get(plat) or 0)
            else:
                demo_weeks[i]["sessions"] += int(v.get("oturum") or 0)
                demo_weeks[i]["visitors"] += int(v.get("ziyaretçi") or 0)

    # ---- paket ekranı hunisi
    pw = [{e: {"users": 0, "n": 0} for e in FUNNEL_EVENTS if e != "signup_demo"} for _ in starts]
    pw_by_state: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for day, ev, p, ps, n, us in ev_rows:
        if ev == "signup_demo" or ev not in FUNNEL_EVENTS or (plat and p != plat):
            continue
        if (i := widx(date.fromisoformat(day))) is not None:
            pw[i][ev]["users"] += us
            pw[i][ev]["n"] += n
            pw_by_state[ps][ev] += us
    pw_tot = {e: {"users": 0, "n": 0} for e in FUNNEL_EVENTS if e != "signup_demo"}
    for ev, p, n, us in ev_all:
        if ev in pw_tot and (not plat or p == plat):
            pw_tot[ev]["users"] += us or 0
            pw_tot[ev]["n"] += n or 0

    # ---- mağaza olayları (sandbox ayrı)
    KINDS = ("trial_start", "purchase", "trial_convert", "renewal", "trial_cancel", "cancel", "refund", "expire",
             "uncancel", "product_change", "billing_issue", "pack", "pack_refund")
    st = [{k: 0 for k in KINDS} for _ in starts]
    st_tot = {k: 0 for k in KINDS}
    split: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))  # tür → "premium·monthly" → n
    sandbox = 0
    for day, kind, plan, period, store, n in st_rows:
        if store == "sandbox":
            sandbox += n
            continue
        if plat and STORE_PLATFORM.get(store) != plat:
            continue
        if kind in st_tot:
            st_tot[kind] += n
            split[kind][f"{plan}·{period}"] += n
            if (i := widx(date.fromisoformat(day))) is not None:
                st[i][kind] += n

    # ---- şu anki abonelikler ve yaklaşık MRR (kişinin KENDİ mağaza aboneliği: ortak defter üyeleri sahibin paketini
    # kullanır ama ödemez, iki kez sayılmaz; yöneticinin elle verdiği paket ve sandbox hariç)
    pr = prices()
    active = {k: 0 for k in ("premium_monthly", "premium_yearly", "aile_monthly", "aile_yearly")}
    trials = {"premium": 0, "aile": 0}
    ending = 0
    unknown = 0
    for u in subs_rows:
        if u["role"] == "admin" or not plans._active(u) or (u.get("plan_env") or "").upper() == "SANDBOX":
            continue
        if plat and STORE_PLATFORM.get(plans.STORES.get(u.get("plan_store") or "", (u.get("plan_store") or "").lower())) != plat:
            continue
        if u.get("plan_trial"):
            trials[u["plan"]] = trials.get(u["plan"], 0) + 1
            continue
        prod = (u.get("plan_product") or "").split(":")[0]
        if prod in active:
            active[prod] += 1
        else:
            unknown += 1
        if u.get("plan_renews") == 0:
            ending += 1
    mrr = sum(n * (pr[k] if k.endswith("_monthly") else pr[k] / 12) for k, n in active.items())
    net = mrr / (1 + VAT) * (1 - STORE_CUT)

    gorunum = _gorunum(ev_gorunum, plat, t)

    week_list = []
    for i, s in enumerate(starts):
        week_list.append({"start": s.isoformat(), "end": min(s + timedelta(days=6), t).isoformat(),
                          "partial": s + timedelta(days=6) > t, "cohort": finish(rows[i]), "signup_demo": sd[i],
                          "demo": demo_weeks[i] if demo_weeks else None, "paywall": pw[i], "store": st[i]})
    window = {"signups": sum(r["signups"] for r in rows), "web": sum(r["web"] for r in rows),
              "ios": sum(r["ios"] for r in rows), "android": sum(r["android"] for r in rows),
              "other": sum(r["bilinmiyor"] + r["mobil"] for r in rows), "signup_demo": sum(sd),
              "demo_sessions": sum(w["sessions"] for w in demo_weeks) if demo_weeks else None}
    return {
        "platform": plat or "hepsi", "today": t.isoformat(), "started": started_s,
        "weeks": week_list, "window": window,
        "total": {"cohort": finish(tot), "signup_demo": sd_tot, "paywall": pw_tot, "store": st_tot,
                  "store_split": {k: dict(v) for k, v in split.items()},
                  "paywall_by_state": {k: dict(v) for k, v in pw_by_state.items()}},
        "demo_available": demo_weeks is not None,
        "gorunum": gorunum,
        "store_sandbox": sandbox,
        "subs": {"active": active, "unknown_product": unknown, "trials": trials, "ending": ending,
                 "paying": sum(active.values()) + unknown, "mrr_gross": round(mrr, 2), "mrr_net": round(net, 2),
                 "prices": pr, "vat": VAT, "store_cut": STORE_CUT},
        "definitions": DEFINITIONS,
    }


GORUNUM_GUN = 7  # "Görünüm" kartı: son kaç gün


def _gorunum(ev_rows, plat: str | None, t: date) -> dict:
    """Admin Ölçüm "Görünüm" kartı: son GORUNUM_GUN günde Özet'i hangi görünümde açan kişi-gün (günde tekil; kişi
    kimliği yok) ve günlük ortalama; elle değiştirme sayıları (nereden → nereye). Yalnız funnel_events toplamları."""
    lo = (t - timedelta(days=GORUNUM_GUN - 1)).isoformat()
    acan = {g: 0 for g in GORUNUMLER}
    degisti = {f"{a}>{b}": 0 for a in GORUNUMLER for b in GORUNUMLER if a != b}
    for day, ev, p, _ps, n, us in ev_rows:
        if day < lo or (plat and p != plat):
            continue
        if ev in GORUNUM_OLAY:
            acan[ev.removeprefix("ozet_gorunum_")] += us
        elif ev in GORUNUM_DEGISTI:
            a, b = ev.removeprefix("ozet_degisti_").split("_")
            degisti[f"{a}>{b}"] += n
    return {"days": GORUNUM_GUN, "from": lo,
            "views": {g: {"user_days": v, "daily_avg": round(v / GORUNUM_GUN, 1)} for g, v in acan.items()},
            "changes": degisti, "changes_total": sum(degisti.values())}


DEFINITIONS = {
    "hafta": "Pazartesi başlar (Türkiye saati). Son hafta bugüne kadar (eksik).",
    "hesap": "Yönetici dışındaki hesaplar, açıldığı haftaya göre. Platform: hesabın ilk aktif günündeki platform.",
    "demo": "Demo oturumu: demo sunucusunun 1 saatlik kopyası (botlar hariç). Demodan gelen hesap: hesap açtıktan sonra "
            "uygulamanın 'önce demoyu denedi' diye bir kez gönderdiği olay (kimlik yok, yalnız toplam).",
    "aktivasyon": "Hesap açtığı gün dahil ilk 7 günde en az 1 belge okutan (fiş, fatura, ekstre…) ya da en az 5 kayıt "
                  "giren. Yalnız 7 günü dolmuş ve ölçüm başladıktan sonra açılmış hesaplar.",
    "tutunma": "2. hafta: hesap açtıktan sonraki 7–13. günlerde, 4. hafta: 21–27. günlerde en az bir aktif gün. Aktif gün: "
               "ana ekranlardan birini açan ya da kayıt yazan istek. Penceresi bitmemiş hesaplar paydaya girmez.",
    "mağaza": "RevenueCat olayları: deneme başlatma, doğrudan satın alma, denemeden ücretliye geçiş, yenileme, iptal "
              "(yenileme kapatıldı), iade (mağaza müşteri hizmeti), bitiş. Test (sandbox) olayları ayrı.",
    "mrr": "Şu an süren ücretli (deneme dışı) mağaza abonelikleri × mağaza fiyatı (yıllık / 12). Brüt KDV dahil; net: "
           "KDV %20 ve mağaza payı %15 düşülmüş tahmin. Ortak defter üyeleri sayılmaz (sahibin aboneliği).",
    "görünüm": "Özet görünümü: Minimal (tek rakam + 3 satır), Ayrıntılı (bütün bölümler açık), Sade (büyük yazı). Son 7 "
                "günde Özet'i o görünümde açan kişi-gün (günde tekil; kimlik yok) ve elle değiştirme sayısı (nereden → "
                "nereye).",
    "paket ekranı": "Kilitli sayfa görüntüleme → paket ekranı → satın alma başlatma → başarılı / vazgeçti. 'Kişi': o gün "
                    "olayı yapan kişi (gün içinde tekil), haftalık toplamı kişi-gündür.",
}
