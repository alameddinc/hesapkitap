"""Hesap silme: yumuşak silme, geri alma, bekleme süresi sonunda kalıcı silme, anonim silme kaydı.

Akış
- POST /api/me/delete → hesap HEMEN kapanır (users.deleted_at, delete_after = şimdi + bekleme süresi): oturumlar düşer
  (session_version artar; auth.token_user silinmek üzere bekleyeni hiç kabul etmez), erişim anahtarları çalışmaz
  (system.token_owner), gelen posta adresi mail kabul etmez (inbound.resolve), gece işleri o defterde çalışmaz.
  Ortak defter sahibi silerse üyeleri de aynı akışa girer (deleted_with = sahip); üye silerse yalnız kendisi.
- Bekleme süresinde giriş (şifre / Google / Apple / şifre sıfırlama): 409 account_pending_deletion + kısa ömürlü geri
  alma belgesi → POST /api/me/restore → hesap (ve sahiple birlikte silinen üyeler) aynen geri gelir.
- Süre dolunca (app/jobs.py, her dakikalık turda): veri klasörü + nesne deposu önekleri (R2), ai_usage, doc_reads,
  api_tokens, onay / şifresiz ayarları gider; RevenueCat müşteri kaydı silinir (anahtar varsa).
- HESAPKITAP_DELETE_GRACE_DAYS (varsayılan 7; 0 = hemen kalıcı sil). Kendi sunucusunda da aynı.

Silme kaydı (system.deleted_accounts): kullanıcı id'si, tarihler, paket ve mağaza durumu, e-postanın HMAC özeti
(sunucu gizli anahtarıyla; düz e-posta yok). Fatura / itiraz takibi içindir; RECORD_KEEP_DAYS sonra düşer.

Mağaza: hesabı silmek App Store aboneliğini iptal edemez (Apple buna izin vermez) — kullanıcıya silme akışının ilk
adımında söylenir. REVENUECAT_SECRET_KEY ayarlıysa Google Play aboneliğinin yenilemesi silme anında durdurulmaya
çalışılır ve kalıcı silmede RevenueCat müşteri kaydı silinir. Bu çağrıların hatası silmeyi hiçbir zaman engellemez.
"""
import hashlib
import hmac
import json
import logging
import os
from datetime import datetime, timedelta, timezone
from urllib.parse import quote

from app import system

log = logging.getLogger("hesapkitap.deletion")

RECORD_KEEP_DAYS = 365
RC_API = "https://api.revenuecat.com/v1"
HTTP_CLIENT = None  # testler httpx.MockTransport'lu istemci koyar
# silinmiş hesaba gelince uyarı: kullanıcı mağazada iptal etmemiş, ücret alınmaya devam ediyor olabilir
CHARGE_EVENTS = {"INITIAL_PURCHASE", "RENEWAL", "PRODUCT_CHANGE", "UNCANCELLATION", "NON_RENEWING_PURCHASE"}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(d: datetime) -> str:
    return d.astimezone(timezone.utc).isoformat(timespec="seconds")


def grace_days() -> int:
    v = os.environ.get("HESAPKITAP_DELETE_GRACE_DAYS", "7").strip()
    return int(v) if v.isdigit() else 7


def pending(u: dict | None) -> bool:
    return bool(u and u.get("deleted_at"))


def email_hash(name: str) -> str:
    """Geri çevrilemez özet: aynı e-posta aynı sunucuda hep aynı değeri verir (yönetici e-postayla arayabilir)."""
    from app import auth

    return hmac.new(auth._secret(), (name or "").strip().lower().encode(), hashlib.sha256).hexdigest()


# ---------------------------------------------------------------- silme kaydı

def _record(u: dict, reason: str, now: datetime, delete_after: str | None, purged: bool) -> None:
    from app import plans

    sub = plans.subscription(u)
    with system.connect() as c:
        row = c.execute("SELECT notes FROM deleted_accounts WHERE user_id = ?", (u["id"],)).fetchone()
        if row:  # sahip silerken zaten bekleyen üye / yönetici bekleyeni sildi: tarihi koru, kalıcı silmeyi işle
            if purged:
                c.execute("UPDATE deleted_accounts SET purged_at = ? WHERE user_id = ?", (_iso(now), u["id"]))
            return
        c.execute("INSERT INTO deleted_accounts (user_id, deleted_at, delete_after, purged_at, reason, plan, plan_until, "
                  "plan_source, plan_store, will_renew, store_customer, email_hash, notes) "
                  "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, '[]')",
                  (u["id"], _iso(now), delete_after, _iso(now) if purged else None, reason, u.get("plan") or "free",
                   u.get("plan_until"), u.get("plan_source"), sub["store"] or u.get("plan_store"),
                   int(sub["will_renew"]) if sub["active"] else None, str(u["id"]), email_hash(u["username"])))


def _note(uid: int, note: dict) -> None:
    with system.connect() as c:
        row = c.execute("SELECT notes FROM deleted_accounts WHERE user_id = ?", (uid,)).fetchone()
        if not row:
            return
        notes = json.loads(row[0] or "[]")[-49:]  # son 50 not yeter
        notes.append({"at": _iso(_now()), **note})
        c.execute("UPDATE deleted_accounts SET notes = ? WHERE user_id = ?", (json.dumps(notes, ensure_ascii=False), uid))


def record(uid: int) -> dict | None:
    with system.connect() as c:
        r = c.execute("SELECT * FROM deleted_accounts WHERE user_id = ?", (uid,)).fetchone()
    return _row(dict(r)) if r else None


def _row(r: dict) -> dict:
    notes = json.loads(r.get("notes") or "[]")
    return {**r, "notes": notes, "warnings": [n for n in notes if n.get("warning")]}


def records(limit: int = 50, email: str | None = None) -> list[dict]:
    """Yönetici paneli: son silinenler ya da e-postayla arama (özet hesaplanıp eşleştirilir)."""
    with system.connect() as c:
        if email:
            rows = c.execute("SELECT * FROM deleted_accounts WHERE email_hash = ? ORDER BY deleted_at DESC",
                             (email_hash(email),)).fetchall()
        else:
            rows = c.execute("SELECT * FROM deleted_accounts ORDER BY deleted_at DESC LIMIT ?", (limit,)).fetchall()
    return [_row(dict(r)) for r in rows]


def note_store_event(uid: int, event: dict, exists: bool) -> dict | None:
    """RevenueCat olayı silinmiş ya da silinmek üzere bekleyen hesaba geldi: kayda not; ücretli olaysa uyarı logu.
    Kayıt yoksa (silinmemiş hesap) None."""
    rec = record(uid)
    if not rec:
        return None
    kind = event.get("type") or "?"
    note: dict = {"event": kind, "store": event.get("store")}
    if kind in CHARGE_EVENTS:
        note["warning"] = ("silinmek üzere bekleyen hesapta" if exists and not rec["purged_at"] else "silinmiş hesapta") \
            + f" mağaza olayı: {kind} (abonelik mağazada iptal edilmemiş olabilir)"
        log.warning("revenuecat: kullanıcı %s %s", uid, note["warning"])
    _note(uid, note)
    return {"note": kind, **({"warning": note["warning"]} if "warning" in note else {})}


def expire_records(now: datetime | None = None) -> int:
    """Kalıcı silinmesinin üzerinden RECORD_KEEP_DAYS geçen kayıtlar da düşer."""
    cutoff = _iso((now or _now()) - timedelta(days=RECORD_KEEP_DAYS))
    with system.connect() as c:
        return c.execute("DELETE FROM deleted_accounts WHERE purged_at IS NOT NULL AND purged_at <= ?", (cutoff,)).rowcount


# ---------------------------------------------------------------- RevenueCat (isteğe bağlı: REVENUECAT_SECRET_KEY)

def _rc_key() -> str:
    return os.environ.get("REVENUECAT_SECRET_KEY", "").strip()


def _http():
    import httpx

    return HTTP_CLIENT or httpx.Client(timeout=10)


def rc_stop_android_renewal(uid: int) -> dict:
    """Google Play aboneliğinin yenilemesini durdur (dönem sonuna kadar sürer, para iadesi yok). Apple'da mümkün değil.
    Hata yutulur ve kayda yazılır; silmeyi engellemez."""
    key = _rc_key()
    if not key:
        log.info("revenuecat: gizli anahtar yok, Google Play yenilemesi durdurulmadı (kullanıcı %s)", uid)
        return {"skipped": "no_key"}
    out: dict = {"cancelled": [], "errors": []}
    try:
        h = {"Authorization": f"Bearer {key}"}
        r = _http().get(f"{RC_API}/subscribers/{quote(str(uid))}", headers=h)
        if r.status_code == 404:
            return {"skipped": "no_customer"}
        r.raise_for_status()
        now = _now()
        for pid, s in ((r.json().get("subscriber") or {}).get("subscriptions") or {}).items():
            exp = s.get("expires_date")
            live = not exp or datetime.fromisoformat(exp.replace("Z", "+00:00")) > now
            if s.get("store") != "play_store" or not live or s.get("unsubscribe_detected_at") or not s.get("store_transaction_id"):
                continue
            tx = s["store_transaction_id"]
            c = _http().post(f"{RC_API}/subscribers/{quote(str(uid))}/subscriptions/{quote(tx, safe='')}/cancel", headers=h)
            (out["cancelled"] if c.status_code < 300 else out["errors"]).append(pid if c.status_code < 300 else f"{pid}: {c.status_code}")
    except Exception as e:  # noqa: BLE001 - ağ / anahtar hatası silmeyi engellemesin
        log.warning("revenuecat: Google Play yenilemesi durdurulamadı (kullanıcı %s): %s", uid, e)
        out["errors"].append(str(e)[:200])
    _note(uid, {"action": "android_cancel", **out})
    return out


def rc_delete_customer(uid: int) -> dict:
    """RevenueCat müşteri kaydını sil (kalıcı silmede). Mağaza aboneliğini değiştirmez. 404 = zaten yok."""
    key = _rc_key()
    if not key:
        log.info("revenuecat: gizli anahtar yok, müşteri kaydı silinmedi (kullanıcı %s)", uid)
        return {"skipped": "no_key"}
    try:
        r = _http().delete(f"{RC_API}/subscribers/{quote(str(uid))}", headers={"Authorization": f"Bearer {key}"})
        res = {"deleted": r.status_code in (200, 204, 404), "status": r.status_code}
    except Exception as e:  # noqa: BLE001
        log.warning("revenuecat: müşteri kaydı silinemedi (kullanıcı %s): %s", uid, e)
        res = {"deleted": False, "error": str(e)[:200]}
    _note(uid, {"action": "rc_delete", **res})
    return res


# ---------------------------------------------------------------- silme, geri alma, kalıcı silme

def request_delete(u: dict, now: datetime | None = None) -> dict:
    """Kullanıcı kendi hesabını sildi (onay uçta alındı). Dönen: {"delete_after": ISO | None, "purged": bool}."""
    now = now or _now()
    days = grace_days()
    after = _iso(now + timedelta(days=days)) if days else None
    members = [] if u.get("member_of") else [m for m in system.members(u["id"]) if not pending(m)]
    _record(u, "self", now, after, purged=False)
    for m in members:
        _record(m, "owner", now, after, purged=False)
    if u.get("plan_source") == "revenuecat":  # mağazadan almış: Google Play ise yenilemeyi hemen durdurmayı dene
        rc_stop_android_renewal(u["id"])
    if not days:
        for m in members:
            purge(m, now)
        purge(u, now)
        return {"delete_after": None, "purged": True}
    for m in members:
        system.update_user(m["id"], deleted_at=_iso(now), delete_after=after, deleted_with=u["id"])
        system.bump_session(m["id"])
    system.update_user(u["id"], deleted_at=_iso(now), delete_after=after, deleted_with=None)
    system.bump_session(u["id"])
    log.info("hesap silinmek üzere: %s (%d üye), kalıcı silme %s", u["id"], len(members), after)
    return {"delete_after": after, "purged": False}


def restorable(u: dict) -> bool:
    """Sahibi defteri silince birlikte silinen üye kendi başına geri alamaz (defter sahibinin)."""
    return pending(u) and not u.get("deleted_with")


def restore(u: dict) -> list[int]:
    """Hesabı (ve sahiple birlikte silinen üyeleri) geri getir; silme kaydı düşer. Dönen: geri gelen id'ler."""
    ids = [u["id"]] + [m["id"] for m in system.members(u["id"]) if m.get("deleted_with") == u["id"]]
    for i in ids:
        system.update_user(i, deleted_at=None, delete_after=None, deleted_with=None)
        with system.connect() as c:
            c.execute("DELETE FROM deleted_accounts WHERE user_id = ? AND purged_at IS NULL", (i,))
    log.info("hesap geri alındı: %s", ids)
    return ids


def purge(u: dict, now: datetime | None = None) -> None:
    """Kalıcı sil: önce veri (klasör + nesne deposu), sonra hesap satırı. Veri silinemezse satır kalır, sonraki turda
    yeniden denenir (yarım kalan silme kaybolmasın)."""
    now = now or _now()
    if not record(u["id"]):  # eski (kayıtsız) bekleyen hesap ya da bekleme süresi 0
        _record(u, "owner" if u.get("deleted_with") else "self", now, u.get("delete_after"), purged=False)
    system.purge_user_data(u)
    rc_delete_customer(u["id"])
    system.delete_user(u["id"])
    for k in ("consent", "pw_unset"):  # KVKK: onay kaydı (IP içerir) da gider
        system.put(f"{k}:{u['id']}", None)
    _record(u, "self", now, None, purged=True)
    log.info("hesap kalıcı olarak silindi: %s", u["id"])


def purge_due(now: datetime | None = None) -> list[int]:
    """Bekleme süresi dolan hesapları kalıcı sil (gece işi her turda çağırır; sorgu ucuz)."""
    now = now or _now()
    with system.connect() as c:
        rows = [dict(r) for r in c.execute(
            "SELECT * FROM users WHERE deleted_at IS NOT NULL AND delete_after IS NOT NULL AND delete_after <= ? "
            "AND role <> 'admin' ORDER BY deleted_with IS NULL, id", (_iso(now),))]  # önce üyeler, sonra sahip
    done = []
    for u in rows:
        try:
            purge(u, now)
            done.append(u["id"])
        except Exception:  # noqa: BLE001 - bir hesap patlarsa diğerleri sürsün; bir sonraki turda yeniden
            log.exception("kalıcı silme başarısız: %s", u["id"])
    return done


def admin_deleted(u: dict, now: datetime | None = None) -> None:
    """Yönetici panelinden silme (veri users/_silinen'e taşınır, ayrı saklama kuralı): yalnız anonim kayıt."""
    now = now or _now()
    _record(u, "admin", now, None, purged=False)
    with system.connect() as c:
        c.execute("UPDATE deleted_accounts SET purged_at = ?, reason = COALESCE(reason, 'admin') WHERE user_id = ?",
                  (_iso(now), u["id"]))
