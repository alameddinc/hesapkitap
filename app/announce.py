"""Yöneticinin duyurusu: bütün telefonlara (ya da süzülmüş bir kısmına) push ("1.0.5 sürümü geldi, güncelle").

- Hedef: push_devices'ta cihazı olan, kapatılmamış / silinmemiş hesaplar; platform (all | ios | android) ve
  below_version (yalnız app_version bu sürümün altındaki cihazlar; sürümü bilinmeyen cihaz dahil). Tercihinde
  "duyuru" türünü kapatan kişiye gitmez.
- test_only: yalnız gönderen yöneticinin kendi cihazlarına, hemen (sessiz saat ve tercih yok sayılır).
- Gönderim arka planda (istek beklemez). Sessiz saatteki kişiye kuyruğa girer (push_queue), bitince gider; kuyruktan
  gidenler de duyurunun sayılarına eklenir (push.flush → record).
- Aynı duyuru bir kez: push_sent (kişi, "duyuru:<kimlik>").
- Çift tıklama: aynı başlık + metin (ve aynı test/gerçek türü) 10 dakika içinde ikinci kez gönderilmez (force hariç).
- Kayıt: announcements tablosu (system db): kim, ne zaman, metin, hedef, hedef kişi/cihaz, gönderilen / başarısız /
  kuyrukta bekleyen.

route: "#/…" uygulama içi yol ya da "store" (mağaza sayfası). "store" telefona route "#/ozet" + action "store" olarak
gider: yeni uygulama mağazayı açar, eski uygulama (action'ı bilmez) Özet'e gider ("Yeni sürüm var" kartı orada).
"""
import logging
import re
import threading
from datetime import datetime, timedelta, timezone

from app import push, system

log = logging.getLogger("hesapkitap.announce")

KIND = "duyuru"
TITLE_MAX = 80
BODY_MAX = 300
DUP_WINDOW = 10 * 60          # saniye: aynı metin bu süre içinde ikinci kez gönderilmez (force hariç)
LIST_LIMIT = 50
ROUTE_RE = re.compile(r"^#/[\w\-/?=&%.~]*$")
VERSION_RE = re.compile(r"^\d+(\.\d+){0,3}$")
PLATFORMS = ("all", "ios", "android")


def _utc() -> datetime:
    return datetime.now(timezone.utc)


def _iso(d: datetime) -> str:
    return d.strftime("%Y-%m-%d %H:%M:%S")


def parse_version(v: str | None) -> tuple[int, ...] | None:
    """"1.0.4", "1.0.4 (36)", "1.0" → sayı parçaları; okunamazsa None."""
    m = re.match(r"^\s*v?(\d+(?:\.\d+){0,3})", v or "")
    if not m:
        return None
    parts = [int(x) for x in m.group(1).split(".")]
    return tuple(parts + [0] * (4 - len(parts)))


def device_matches(d: dict, platform: str = "all", below: str | None = None) -> bool:
    if platform in ("ios", "android") and d.get("platform") != platform:
        return False
    if below:
        have, lim = parse_version(d.get("app_version")), parse_version(below)
        if have is not None and lim is not None and have >= lim:
            return False  # sürümü bilinmeyen cihaz dahil
    return True


def validate(title: str, body: str, route: str | None, platform: str, below: str | None) -> dict:
    """Temizlenmiş alanlar; hatada ValueError (Türkçe ileti)."""
    t, b = push._clean(title or ""), push._clean(body or "")
    if not t:
        raise ValueError("Başlık boş olamaz")
    if not b:
        raise ValueError("Metin boş olamaz")
    if len(t) > TITLE_MAX:
        raise ValueError(f"Başlık en çok {TITLE_MAX} karakter olabilir")
    if len(b) > BODY_MAX:
        raise ValueError(f"Metin en çok {BODY_MAX} karakter olabilir")
    r = (route or "").strip() or None
    if r and r != "store" and not ROUTE_RE.match(r):
        raise ValueError('Yol "#/…" ya da "store" olmalı')
    if platform not in PLATFORMS:
        raise ValueError("Platform all, ios ya da android olmalı")
    v = (below or "").strip() or None
    if v and not VERSION_RE.match(v):
        raise ValueError("Sürüm 1.0.5 biçiminde olmalı")
    return {"title": t, "body": b, "route": r, "platform": platform, "below_version": v}


# ---------------------------------------------------------------- hedef

def _live_users() -> dict[int, dict]:
    return {u["id"]: u for u in system.users() if not u.get("disabled") and not u.get("deleted_at")}


def targets(platform: str = "all", below: str | None = None, only_uid: int | None = None,
            respect_prefs: bool = True) -> dict:
    """{users: {uid: [cihaz]}, opted_out (duyuruyu kapatan, cihazı eşleşen kişi)}."""
    users = _live_users()
    with system.connect() as c:
        rows = [dict(r) for r in c.execute("SELECT * FROM push_devices ORDER BY user_id, last_seen DESC")]
    by: dict[int, list[dict]] = {}
    for d in rows:
        if d["user_id"] not in users or (only_uid is not None and d["user_id"] != only_uid):
            continue
        if device_matches(d, platform, below):
            by.setdefault(d["user_id"], []).append(d)
    out, off = {}, 0
    for uid, devs in by.items():
        if respect_prefs and not push.get_prefs(uid)["types"].get(KIND, True):
            off += 1
            continue
        out[uid] = devs
    return {"users": out, "opted_out": off}


def _counts(t: dict) -> dict:
    devs = [d for ds in t["users"].values() for d in ds]
    return {"users": len(t["users"]), "devices": len(devs),
            "by_platform": {p: sum(1 for d in devs if d["platform"] == p) for p in ("ios", "android")},
            "opted_out": t["opted_out"]}


def preview(platform: str = "all", below: str | None = None, test_only: bool = False, admin_uid: int | None = None) -> dict:
    t = targets(platform, below, admin_uid if test_only else None, respect_prefs=not test_only)
    return {**_counts(t), "enabled": push.enabled()}


# ---------------------------------------------------------------- kayıt

def _row(r) -> dict:
    d = dict(r)
    d["test_only"] = bool(d["test_only"])
    return d


def get(aid: int) -> dict | None:
    with system.connect() as c:
        r = c.execute("SELECT * FROM announcements WHERE id = ?", (aid,)).fetchone()
    return _row(r) if r else None


def history(limit: int = LIST_LIMIT) -> list[dict]:
    with system.connect() as c:
        return [_row(r) for r in c.execute("SELECT * FROM announcements ORDER BY id DESC LIMIT ?", (limit,))]


def recent_duplicate(title: str, body: str, test_only: bool, now: datetime | None = None) -> dict | None:
    since = _iso((now or _utc()) - timedelta(seconds=DUP_WINDOW))
    with system.connect() as c:
        r = c.execute("SELECT * FROM announcements WHERE title = ? AND body = ? AND test_only = ? AND created_at >= ? "
                      "ORDER BY id DESC LIMIT 1", (title, body, int(test_only), since)).fetchone()
    return _row(r) if r else None


def record(aid: int, res: dict, final: bool = True) -> None:
    """Kuyruktan giden duyuru bildirimi (push.flush): sayılar eklenir; final (yeniden denenmeyecek) ise bekleyen düşer."""
    with system.connect() as c:
        c.execute("UPDATE announcements SET sent = sent + ?, failed = failed + ?, queued = MAX(0, queued - ?), "
                  "status = CASE WHEN status = 'queued' AND queued - ? <= 0 THEN 'done' ELSE status END WHERE id = ?",
                  (res.get("sent", 0), res.get("failed", 0) if final else 0, int(final), int(final), aid))


# ---------------------------------------------------------------- gönderim

def _spawn(fn) -> None:
    """Arka plan (testler aynı akışta çalıştırır)."""
    def go():
        try:
            fn()
        except Exception:  # noqa: BLE001
            log.exception("duyuru gönderimi")

    threading.Thread(target=go, name="duyuru", daemon=True).start()


def payload(aid: int, a: dict) -> dict:
    p = push.item(KIND, f"{KIND}:{aid}", a["title"], a["body"], push.R_OZET if a["route"] in (None, "store") else a["route"])
    if a["route"] == "store":
        p["action"] = "store"
    p["ann"] = aid  # push.flush sayıları buraya ekler
    p["only"] = {"platform": a["platform"], "below": a["below_version"]}  # push.deliver cihaz süzgeci
    return p


def create(admin: dict, data: dict, test_only: bool = False, force: bool = False) -> dict:
    """Doğrula, çift göndermeyi engelle, kaydı yaz, gönderimi arka planda başlat. Dönen: kayıt (+ duplicate)."""
    a = validate(data.get("title", ""), data.get("body", ""), data.get("route"), data.get("platform") or "all",
                 data.get("below_version"))
    if not force:
        dup = recent_duplicate(a["title"], a["body"], test_only)
        if dup:
            raise DuplicateError(dup)
    t = targets(a["platform"], a["below_version"], admin["id"] if test_only else None, respect_prefs=not test_only)
    n = _counts(t)
    with system.connect() as c:
        aid = c.execute(
            "INSERT INTO announcements (created_at, admin_id, admin_name, title, body, route, platform, below_version, "
            "test_only, target_users, target_devices, status) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'sending')",
            (_iso(_utc()), admin["id"], admin.get("username"), a["title"], a["body"], a["route"], a["platform"],
             a["below_version"], int(test_only), n["users"], n["devices"])).lastrowid
    uids = list(t["users"])
    _spawn(lambda: send(aid, uids, test_only))
    return get(aid)


class DuplicateError(Exception):
    def __init__(self, existing: dict):
        super().__init__("duplicate")
        self.existing = existing


def send(aid: int, uids: list[int], test_only: bool = False, now: datetime | None = None) -> dict:
    """Her kişi: daha önce gitmediyse (push_sent), sessiz saatteyse kuyruğa, değilse hemen cihazlarına."""
    a = get(aid)
    if not a:
        return {}
    p = payload(aid, a)
    now = now or push._now()
    sent = failed = queued = 0
    for uid in uids:
        if test_only:
            res = push.deliver(uid, p)
        else:
            prefs = push.get_prefs(uid)
            if not prefs["types"].get(KIND, True) or not push.claim(uid, p["id"], now.date()):
                continue
            if push.quiet_until(prefs, now):
                push.enqueue(uid, p, now, prefs)
                queued += 1
                continue
            res = push.deliver(uid, p)
        sent += res["sent"]
        failed += res["failed"]
    with system.connect() as c:
        c.execute("UPDATE announcements SET sent = sent + ?, failed = failed + ?, queued = queued + ?, status = ?, "
                  "finished_at = ? WHERE id = ?",
                  (sent, failed, queued, "queued" if queued else "done", _iso(_utc()), aid))
    return {"sent": sent, "failed": failed, "queued": queued}


def to_json(a: dict) -> dict:
    return {k: a.get(k) for k in ("id", "created_at", "finished_at", "admin_name", "title", "body", "route", "platform",
                                  "below_version", "test_only", "target_users", "target_devices", "sent", "failed",
                                  "queued", "status")}

