"""Canlı demo (HESAPKITAP_DEMO=1): şifresiz giriş, her ziyaretçiye örnek verinin kendi kopyası, 1 saat.

- Şablon: app.demo'nun uydurma verisi, bugünün ayına kaydırılmış (demo hiç eskimez); ayda bir yeniden üretilir.
- Sandbox: data/demo/sb/<oluşma zamanı>-<rastgele>/ekstre.db — çerezle bulunur, süresi dolunca yenisi açılır,
  eskiler kendiliğinden silinir. Kimse başkasının verisini görmez.
- Dosya yükleme, yapay zekâ, Telegram, e-posta, ayarlar, kullanıcı ve ortak defter işlemleri kapalı; yerine örnek
  ekstre / fiş düğmeleri var (yerel OCR ve genel kart okuyucu çalışır).
"""
import os
import re
import secrets
import shutil
import sqlite3
import time
from datetime import date

from app import db

TTL = int(os.environ.get("HESAPKITAP_DEMO_TTL", "3600"))  # saniye
MAX_SANDBOXES = 500
COOKIE = "hk_demo"
ANCHOR = date(2026, 10, 20)  # app.demo verisinin "bugünü": son ödemeler önümüzde, taksitler yaklaşan
CLOSED = ("/api/statements/upload", "/api/bank-import", "/api/receipts/parse", "/api/bills/parse", "/api/share",
          "/api/documents/parse",
          "/api/settings", "/api/admin", "/api/book", "/api/mail", "/api/templates")
OPEN = ("/api/book/me",)
SAMPLES = {
    "vakifbank": {"label": "Örnek VakıfBank ekstresi", "kind": "statement", "file": "vakifbank_b.txt"},
    "genel": {"label": "Örnek Garanti ekstresi (genel okuyucu)", "kind": "statement", "file": "garanti"},
    "fis-market": {"label": "Market fişi", "kind": "receipt", "file": "fis-market.jpg"},
    "fis-kahve": {"label": "Kahve fişi", "kind": "receipt", "file": "fis-kahve.jpg"},
    "fis-eczane": {"label": "Eczane fişi", "kind": "receipt", "file": "fis-eczane.jpg"},
}
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def enabled() -> bool:
    return os.environ.get("HESAPKITAP_DEMO") == "1"


def _base() -> str:
    return os.path.join(os.path.dirname(os.path.abspath(db.DB_PATH)), "demo")


def days_since_anchor(today: date | None = None) -> int:
    return ((today or date.today()) - ANCHOR).days


def _shift(v: str, days: int) -> str:
    """'2026-10-05…' → n gün sonrası; '2026-10' (dönem) → ayın ortası n gün kaydırılınca düştüğü ay."""
    from datetime import timedelta

    m = re.match(r"^(\d{4})-(\d{2})(?:-(\d{2}))?", v)
    if not m:
        return v
    try:
        if m.group(3):
            return (date(int(m.group(1)), int(m.group(2)), int(m.group(3))) + timedelta(days=days)).isoformat() + v[m.end():]
        return (date(int(m.group(1)), int(m.group(2)), 15) + timedelta(days=days)).isoformat()[:7] + v[m.end():]
    except ValueError:
        return v


def shift_db(path: str, days: int) -> None:
    """Veritabanındaki bütün tarih/ay değerlerini kaydır (demo verisi bugüne gelsin)."""
    if not days:
        return
    c = sqlite3.connect(path)
    try:
        for (t,) in c.execute("SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'").fetchall():
            cols = [r[1] for r in c.execute(f'PRAGMA table_info("{t}")')]
            for col in cols:
                rows = c.execute(f'SELECT rowid, "{col}" FROM "{t}" WHERE typeof("{col}") = \'text\' '
                                 f'AND "{col}" GLOB \'[12][0-9][0-9][0-9]-[01][0-9]*\'').fetchall()
                for rid, v in rows:
                    c.execute(f'UPDATE "{t}" SET "{col}" = ? WHERE rowid = ?', (_shift(v, days), rid))
        c.commit()
    finally:
        c.close()


def shift_text(text: str, days: int) -> str:
    """Örnek ekstre metnindeki gg.aa.yyyy / gg/aa/yyyy tarihlerini kaydır."""
    def rep(m):
        d, mo, y, sep = int(m.group(1)), int(m.group(3)), int(m.group(4)), m.group(2)
        iso = _shift(f"{y:04d}-{mo:02d}-{d:02d}", days)
        return f"{iso[8:10]}{sep}{iso[5:7]}{sep}{iso[0:4]}"
    return re.sub(r"\b(\d{2})([./])(\d{2})\2(\d{4})\b", rep, text) if days else text


def template() -> str:
    """Bugünün şablon veritabanı (yoksa üret; eski günlerinkini sil)."""
    days = days_since_anchor()
    path = os.path.join(_base(), f"template-{days}.db")
    if os.path.exists(path):
        return path
    os.makedirs(_base(), exist_ok=True)
    for n in os.listdir(_base()):
        if n.startswith("template-") and n.endswith(".db"):
            os.remove(os.path.join(_base(), n))
    tmp = path + f".{os.getpid()}.tmp"
    for p in (tmp,):
        if os.path.exists(p):
            os.remove(p)
    from app import demo, templates

    real_sync = templates.sync_community
    templates.sync_community = lambda force=False: 0  # şablon üretirken ağa çıkma
    try:
        with db.use_space(tmp, None):
            demo.seed(("vakifbank_a.txt", "kuveytturk.txt"))  # vakifbank_b "örnek ekstre yükle" için
    finally:
        templates.sync_community = real_sync
    shift_db(tmp, days)
    os.replace(tmp, path)
    return path


def _sandbox_dir() -> str:
    return os.path.join(_base(), "sb")


def _valid(sid: str | None) -> bool:
    return bool(sid and re.fullmatch(r"\d{10}-[0-9a-f]{16}", sid)
                and os.path.exists(os.path.join(_sandbox_dir(), sid, "ekstre.db")))


def expires_at(sid: str) -> int:
    return int(sid.split("-")[0]) + TTL


def cleanup() -> None:
    d = _sandbox_dir()
    if not os.path.isdir(d):
        return
    now = time.time()
    names = sorted(os.listdir(d))
    for i, n in enumerate(names):
        stale = not re.fullmatch(r"\d{10}-[0-9a-f]{16}", n) or int(n.split("-")[0]) + TTL + 600 < now
        if stale or len(names) - i > MAX_SANDBOXES:
            shutil.rmtree(os.path.join(d, n), ignore_errors=True)


def new_sandbox() -> str:
    cleanup()
    sid = f"{int(time.time())}-{secrets.token_hex(8)}"
    path = os.path.join(_sandbox_dir(), sid)
    os.makedirs(path, exist_ok=True)
    shutil.copy(template(), os.path.join(path, "ekstre.db"))
    return sid


def resolve(sid: str | None) -> tuple[str, bool]:
    """Çerezdeki sandbox geçerliyse o, değilse yenisi. (sid, yeni mi)"""
    if _valid(sid) and expires_at(sid) > time.time():
        return sid, False
    return new_sandbox(), True


def space(sid: str) -> tuple[str, dict]:
    return (os.path.join(_sandbox_dir(), sid, "ekstre.db"),
            {"id": 0, "username": "demo", "role": "user", "demo": True, "member_of": None, "disabled": 0,
             "must_change_password": 0, "expires_at": expires_at(sid)})


def closed(path: str) -> bool:
    if path in OPEN:
        return False
    return any(path == p.rstrip("/") or path.startswith(p.rstrip("/") + "/") for p in CLOSED)


def sample_text(key: str) -> str:
    s = SAMPLES[key]
    if s["file"] == "garanti":
        from tests.test_card_reader import GARANTI
        text = GARANTI
    else:
        with open(os.path.join(ROOT, "tests", "fixtures", s["file"]), encoding="utf-8") as f:
            text = f.read()
    return shift_text(text, days_since_anchor())


def sample_image(key: str) -> bytes:
    with open(os.path.join(ROOT, "docs", "demo", SAMPLES[key]["file"]), "rb") as f:
        return f.read()


# ---------------------------------------------------------------- istatistik (gizliliğe saygılı)
# Oturum = sandbox. IP saklanmaz: ziyaretçi, IP + tarayıcıdan GÜNLÜK değişen tuzla üretilen kısa bir parmak izidir
# (o gün kaç farklı kişi geldiği bilinir, kimse günler arasında izlenemez).

AREAS = {"/api/summary": "ozet", "/api/digest": "ozet", "/api/transactions": "islemler", "/api/plan": "planlama",
         "/api/compare": "istatistik", "/api/year-summary": "istatistik", "/api/zakat": "zekat", "/api/donations": "zekat",
         "/api/net-worth": "hesaplar", "/api/jars": "hesaplar", "/api/statements": "ekstreler", "/api/prices": "fiyatlar",
         "/api/savings": "tasarruf", "/api/debts": "borclar", "/api/transfers": "transferler", "/api/demo/sample": "ornek",
         "/api/entries": "kayit"}
BOT = re.compile(r"bot|crawl|spider|slurp|preview|facebookexternalhit|whatsapp|telegram|curl|python|headless", re.I)


def _stats_db() -> sqlite3.Connection:
    os.makedirs(_base(), exist_ok=True)
    c = sqlite3.connect(os.path.join(_base(), "stats.db"))
    c.execute("""CREATE TABLE IF NOT EXISTS sessions (sid TEXT PRIMARY KEY, created INTEGER, last_seen INTEGER,
                 requests INTEGER DEFAULT 0, visitor TEXT, mobile INTEGER, bot INTEGER, areas TEXT DEFAULT '',
                 samples INTEGER DEFAULT 0, writes INTEGER DEFAULT 0)""")
    c.execute("""CREATE TABLE IF NOT EXISTS waitlist (email TEXT PRIMARY KEY, created INTEGER, sid TEXT,
                 platform TEXT, note TEXT)""")
    if "tester" not in {r[1] for r in c.execute("PRAGMA table_info(waitlist)")}:  # Android kapalı testine gönüllü
        c.execute("ALTER TABLE waitlist ADD COLUMN tester INTEGER NOT NULL DEFAULT 0")
    return c


EMAIL = re.compile(r"^[^@\s]{1,64}@[^@\s]{1,190}\.[^@\s.]{2,24}$")
WAITLIST_MAX = 10000
PLATFORMS = ("telefon", "bilgisayar", "ikisi")


class WaitlistError(ValueError):
    pass


def join_waitlist(sid: str, email: str, platform: str | None = None, note: str | None = None, tester: bool = False) -> bool:
    """Kurulumsuz (hazır) sürüm açılınca haber verilecekler. True: yeni kayıt, False: zaten listedeydi."""
    email = (email or "").strip().lower()
    if not EMAIL.match(email):
        raise WaitlistError("Geçerli bir e-posta yaz")
    c = _stats_db()
    try:
        if c.execute("SELECT 1 FROM waitlist WHERE email = ?", (email,)).fetchone():
            if tester:  # önce sadece haber ver demişti, şimdi teste de katılıyor
                c.execute("UPDATE waitlist SET tester = 1 WHERE email = ?", (email,))
                c.commit()
            return False
        if c.execute("SELECT COUNT(*) FROM waitlist WHERE sid = ?", (sid,)).fetchone()[0] >= 3 or \
                c.execute("SELECT COUNT(*) FROM waitlist").fetchone()[0] >= WAITLIST_MAX:
            raise WaitlistError("Şu an kaydedilemedi, biraz sonra tekrar dene")
        c.execute("INSERT INTO waitlist (email, created, sid, platform, note, tester) VALUES (?, ?, ?, ?, ?, ?)",
                  (email, int(time.time()), sid, platform if platform in PLATFORMS else None, (note or "").strip()[:300] or None,
                   int(bool(tester))))
        c.commit()
        return True
    finally:
        c.close()


def _visitor(ip: str, ua: str) -> str:
    import hashlib

    salt = date.today().isoformat() + os.environ.get("SECRET_KEY", "")
    return hashlib.sha256(f"{salt}|{ip}|{ua}".encode()).hexdigest()[:12]


def track(sid: str, path: str, method: str, ip: str, ua: str) -> None:
    """Her demo isteğinde: oturumun son görülme zamanı, gezdiği bölümler, örnek/yazma sayısı."""
    try:
        now = int(time.time())
        area = next((a for p, a in AREAS.items() if path.startswith(p)), None)
        c = _stats_db()
        c.execute("INSERT OR IGNORE INTO sessions (sid, created, last_seen, visitor, mobile, bot) VALUES (?, ?, ?, ?, ?, ?)",
                  (sid, int(sid.split("-")[0]), now, _visitor(ip, ua), int(bool(re.search(r"Mobi|Android|iPhone", ua))),
                   int(bool(BOT.search(ua)))))
        c.execute("UPDATE sessions SET last_seen = ?, requests = requests + 1, "
                  "areas = CASE WHEN ? IS NULL OR instr(',' || areas || ',', ',' || ? || ',') > 0 THEN areas "
                  "ELSE trim(areas || ',' || ?, ',') END, samples = samples + ?, writes = writes + ? WHERE sid = ?",
                  (now, area, area, area, int(path.startswith("/api/demo/sample")),
                   int(method in ("POST", "PUT", "PATCH", "DELETE") and not path.startswith("/api/demo")), sid))
        c.commit()
        c.close()
    except Exception:  # noqa: BLE001 - istatistik demoyu asla bozmasın
        pass


def stats(days: int = 7) -> dict:
    since = int(time.time()) - days * 86400
    c = _stats_db()
    rows = c.execute("SELECT created, last_seen, requests, visitor, mobile, bot, areas, samples, writes FROM sessions "
                     "WHERE created >= ?", (since,)).fetchall()
    wl = c.execute("SELECT email, created, platform, note, tester FROM waitlist ORDER BY created DESC, rowid DESC").fetchall()
    c.close()
    human = [r for r in rows if not r[5]]
    durs = sorted(max(0, r[1] - r[0]) for r in human)
    from collections import Counter

    areas = Counter(a for r in human for a in (r[6] or "").split(",") if a)
    by_day = Counter(time.strftime("%Y-%m-%d", time.localtime(r[0])) for r in human)
    visitors_by_day = Counter()
    for d in by_day:
        visitors_by_day[d] = len({r[3] for r in human if time.strftime("%Y-%m-%d", time.localtime(r[0])) == d})
    med = durs[len(durs) // 2] if durs else 0
    return {
        "gün": days, "oturum": len(human), "bot_oturum": len(rows) - len(human),
        "tekil_ziyaretçi_günlük_toplam": sum(visitors_by_day.values()),
        "mobil_%": round(100 * sum(r[4] for r in human) / len(human)) if human else 0,
        "süre_medyan_sn": med, "süre_ort_sn": round(sum(durs) / len(durs)) if durs else 0,
        "1dk+_kalan": sum(d >= 60 for d in durs), "5dk+_kalan": sum(d >= 300 for d in durs),
        "örnek_deneyen": sum(1 for r in human if r[7]), "kayıt_ekleyen": sum(1 for r in human if r[8]),
        "bölümler": dict(areas.most_common()),
        "günlük": {d: {"oturum": by_day[d], "ziyaretçi": visitors_by_day[d]} for d in sorted(by_day)},
        "son_oturumlar": [{"başlangıç": time.strftime("%Y-%m-%d %H:%M", time.localtime(r[0])), "süre_sn": max(0, r[1] - r[0]),
                           "istek": r[2], "mobil": bool(r[4]), "bölümler": [a for a in (r[6] or "").split(",") if a],
                           "örnek": r[7], "kayıt": r[8]} for r in sorted(human, key=lambda r: -r[0])[:25]],
        # bekleme listesi dönemden bağımsız: hepsi
        "bekleme_listesi": len(wl),
        "bekleme_dönemde": sum(1 for w in wl if w[1] >= since),
        "testçi": sum(1 for w in wl if w[4]),
        "bekleyenler": [{"e-posta": w[0], "tarih": time.strftime("%Y-%m-%d %H:%M", time.localtime(w[1])), "platform": w[2],
                         "not": w[3], "testçi": bool(w[4])} for w in wl[:500]],
    }


if __name__ == "__main__":
    import json
    import sys

    if len(sys.argv) >= 2 and sys.argv[1] == "stats":
        print(json.dumps(stats(int(sys.argv[2]) if len(sys.argv) > 2 else 7), ensure_ascii=False, indent=2))
    else:
        print("Kullanım: python -m app.demo_mode stats [gün]")
