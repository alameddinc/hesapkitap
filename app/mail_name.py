"""Kişisel ekstre adresi: <ad>@<HESAPKITAP_MAIL_DOMAIN> (ör. ayse@ornek.app).

Özellik HESAPKITAP_MAIL_DOMAIN boşken kapalıdır; o zaman yalnız rastgele kodlu fatura-<kod>@… adresi vardır (app/inbound.py).
Kodlu adres açıkken de çalışmaya devam eder.

Kurallar (normalize(), check()):
- 3–30 karakter; a-z 0-9 . _ - ; Türkçe harfler ascii'ye çevrilir, küçük harfe indirilir
- başta/sonda nokta yok, ardışık nokta yok; ayrılmış adlar ve "fatura-" ile başlayanlar alınamaz
- benzersiz (büyük/küçük harf farkı yok)
Değiştirme: ömür boyu bir kez. Eski ad RELEASE_DAYS gün başkasına verilmez ve o süre boyunca hâlâ aynı deftere teslim
edilir (banka ayarını güncellemeye zaman kalsın).

Ad tahmin edilebilir olduğu için bu adrese gelen banka belgesi, defterde ilk kez görülen kart/hesaba aitse önce onay
bekler (app/mail_approval.py). Kodlu adreste bu adım yoktur.
"""
import os
import re
import secrets
from datetime import datetime, timedelta, timezone

from app import system

MIN_LEN, MAX_LEN = 3, 30
RELEASE_DAYS = 90
MAX_CHANGES = 1
_VALID = re.compile(r"^[a-z0-9](?:[a-z0-9_-]|\.(?!\.))*[a-z0-9_-]$")
_TR = str.maketrans({"ç": "c", "ğ": "g", "ı": "i", "ö": "o", "ş": "s", "ü": "u",
                     "Ç": "c", "Ğ": "g", "İ": "i", "I": "i", "Ö": "o", "Ş": "s", "Ü": "u"})
RESERVED = frozenset({
    "admin", "administrator", "destek", "info", "bilgi", "noreply", "no-reply", "no_reply", "donotreply", "support",
    "help", "yardim", "abuse", "postmaster", "hostmaster", "webmaster", "security", "guvenlik", "billing", "odeme",
    "fatura", "ekstre", "ekstreler", "cuzi", "hesapkitap", "root", "mail", "email", "eposta", "posta", "test", "api",
    "www", "hello", "merhaba", "iletisim", "contact", "kvkk", "legal", "hukuk", "privacy", "gizlilik", "sales",
    "satis", "team", "ekip", "staff", "system", "sistem", "null", "undefined", "mailer-daemon", "daemon", "bounce",
    "bounces", "news", "newsletter", "bulten", "app", "uygulama", "bank", "banka", "official", "resmi", "yonetici",
    "moderator", "owner", "sahip", "user", "kullanici", "demo", "status", "durum", "ftp", "smtp", "imap", "pop",
    "dmarc", "dkim", "spf", "mx", "ns", "autoconfig", "autodiscover", "account", "accounts", "hesap", "hesaplar",
    "kayit", "signup", "login", "giris", "press", "basin", "jobs", "kariyer", "career", "marketing", "pazarlama",
})
RESERVED_PREFIXES = ("fatura-",)
_REASONS = {
    "short": f"Ad en az {MIN_LEN} karakter olmalı",
    "long": f"Ad en çok {MAX_LEN} karakter olabilir",
    "chars": "Yalnız harf, rakam, nokta, alt çizgi ve tire kullanılabilir; nokta başta, sonda ya da art arda olamaz",
    "reserved": "Bu ad ayrılmış, başka bir ad dene",
    "taken": "Bu ad alınmış",
    "same": "Bu zaten senin adın",
}


class NameError_(ValueError):
    """Kullanıcıya gösterilecek Türkçe neden; code: short | long | chars | reserved | taken | same | limit | off."""

    def __init__(self, code: str, message: str | None = None):
        super().__init__(message or _REASONS.get(code, code))
        self.code = code


def domain() -> str:
    """Kişisel adres alan adı; boşsa özellik kapalı."""
    return os.environ.get("HESAPKITAP_MAIL_DOMAIN", "").strip().lower().lstrip("@")


def enabled() -> bool:
    return bool(domain())


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(d: datetime) -> str:
    return d.strftime("%Y-%m-%dT%H:%M:%SZ")


def _init(c) -> None:
    c.execute("""CREATE TABLE IF NOT EXISTS mail_names (
                 name TEXT PRIMARY KEY COLLATE NOCASE,
                 user_id INTEGER NOT NULL,           -- defter (app/inbound._book_user)
                 created_at TEXT NOT NULL,
                 released_at TEXT)                   -- değiştirildiği an; RELEASE_DAYS boyunca hâlâ teslim edilir""")
    c.execute("CREATE INDEX IF NOT EXISTS mail_names_user ON mail_names (user_id)")


def normalize(raw: str) -> str:
    """Yazılan adı kurala yaklaştır: Türkçe harf → ascii, küçük harf, boşluk → nokta, izinsiz karakter atılır."""
    s = (raw or "").strip().translate(_TR).lower()
    s = re.sub(r"\s+", ".", s)
    return re.sub(r"[^a-z0-9._-]", "", s)


def check_rules(name: str) -> None:
    """Biçim kuralları (benzersizlik hariç). Uymazsa NameError_."""
    if len(name) < MIN_LEN:
        raise NameError_("short")
    if len(name) > MAX_LEN:
        raise NameError_("long")
    if not _VALID.match(name):
        raise NameError_("chars")
    if name in RESERVED or name.startswith(RESERVED_PREFIXES):
        raise NameError_("reserved")


def _cutoff() -> str:
    return _iso(_now() - timedelta(days=RELEASE_DAYS))


def _holder(c, name: str) -> int | None:
    """Adı tutan defter: etkin ad ya da RELEASE_DAYS içinde bırakılmış ad. Yoksa None."""
    r = c.execute("SELECT user_id FROM mail_names WHERE name = ? AND (released_at IS NULL OR released_at > ?)",
                  (name, _cutoff())).fetchone()
    return r[0] if r else None


def available(name: str, book_id: int | None = None) -> tuple[bool, str | None, str | None]:
    """(uygun mu, normalize ad, neden kodu). Kendi etkin adın "same"; kendi bıraktığın (90 gün içindeki) ad geri alınabilir."""
    n = normalize(name)
    try:
        check_rules(n)
    except NameError_ as e:
        return False, n, e.code
    with system.connect() as c:
        _init(c)
        h = _holder(c, n)
        if h is None:
            return True, n, None
        if book_id is not None and h == book_id:
            active = c.execute("SELECT 1 FROM mail_names WHERE name = ? AND released_at IS NULL", (n,)).fetchone()
            return (False, n, "same") if active else (True, n, None)
    return False, n, "taken"


def current(book_id: int) -> str | None:
    with system.connect() as c:
        _init(c)
        r = c.execute("SELECT name FROM mail_names WHERE user_id = ? AND released_at IS NULL", (book_id,)).fetchone()
    return r[0] if r else None


def changes_used(book_id: int) -> int:
    with system.connect() as c:
        _init(c)
        return c.execute("SELECT COUNT(*) FROM mail_names WHERE user_id = ? AND released_at IS NOT NULL",
                         (book_id,)).fetchone()[0]


def claim(book_id: int, raw: str) -> str:
    """Adı al ya da (ömür boyu bir kez) değiştir. Normalize adı döner; olmazsa NameError_."""
    if not enabled():
        raise NameError_("off", "Kişisel adres şu an kapalı")
    n = normalize(raw)
    check_rules(n)
    with system.connect() as c:
        _init(c)
        c.execute("BEGIN IMMEDIATE")  # iki kişi aynı adı aynı anda alamasın
        try:
            mine = c.execute("SELECT name FROM mail_names WHERE user_id = ? AND released_at IS NULL",
                             (book_id,)).fetchone()
            if mine and mine[0].lower() == n:
                raise NameError_("same")
            h = _holder(c, n)
            if h is not None and h != book_id:
                raise NameError_("taken")
            if mine:
                used = c.execute("SELECT COUNT(*) FROM mail_names WHERE user_id = ? AND released_at IS NOT NULL",
                                 (book_id,)).fetchone()[0]
                if used >= MAX_CHANGES:
                    raise NameError_("limit", "Adını yalnız bir kez değiştirebilirsin; bu hakkını kullandın")
                c.execute("UPDATE mail_names SET released_at = ? WHERE name = ?", (_iso(_now()), mine[0]))
            # kendi bıraktığı adı geri alıyorsa ya da süresi dolmuş eski kayıt varsa üzerine yaz
            c.execute("DELETE FROM mail_names WHERE name = ?", (n,))
            c.execute("INSERT INTO mail_names (name, user_id, created_at) VALUES (?, ?, ?)", (n, book_id, _iso(_now())))
            c.execute("COMMIT")
        except BaseException:
            c.execute("ROLLBACK")
            raise
    return n


def lookup(local: str) -> int | None:
    """Gelen postanın yerel kısmı → defter id (etkin ya da 90 gün içinde bırakılmış ad). Yoksa None."""
    n = (local or "").strip().lower()
    if not n or n.startswith(RESERVED_PREFIXES):
        return None
    with system.connect() as c:
        _init(c)
        return _holder(c, n)


def address_of(name: str | None) -> str | None:
    return f"{name}@{domain()}" if name and enabled() else None


def _parts(text: str) -> list[str]:
    return [p for p in re.split(r"[^a-z0-9]+", normalize(text).replace(".", " ").replace("_", " ").replace("-", " "))
            if p and not p.isdigit()]


def suggestions(username: str, display: str | None = None, book_id: int | None = None, limit: int = 5) -> list[str]:
    """Kullanıcı adı / e-postasından (ve varsa görünen addan) uygun 3–5 öneri: ad, ad.soyadharfi, adsoyad, ad+2 rakam."""
    src = display or (username.split("@", 1)[0] if "@" in (username or "") else username or "")
    parts = _parts(src)
    first = parts[0] if parts else ""
    last = parts[-1] if len(parts) > 1 else ""
    cands: list[str] = []
    if first:
        cands.append(first)
        if last:
            cands += [f"{first}.{last[0]}", f"{first}{last}", f"{first}.{last}"]
        cands += [f"{first}{secrets.randbelow(90) + 10}" for _ in range(6)]
    else:
        cands += [f"ekstrem{secrets.randbelow(9000) + 1000}" for _ in range(6)]
    out: list[str] = []
    for c in cands:
        if c in out:
            continue
        ok, n, _ = available(c, book_id)
        if ok and n == c:
            out.append(c)
        if len(out) >= limit:
            break
    return out


def auto_claim(book_id: int, username: str) -> str | None:
    """Kayıtta: önerilerden ilk uygun adı ver (özellik kapalıysa ya da zaten adı varsa hiçbir şey). Hata fırlatmaz."""
    import logging

    if not enabled():
        return None
    try:
        if current(book_id):
            return None
        for s in suggestions(username or "", book_id=book_id):
            try:
                return claim(book_id, s)
            except NameError_:
                continue
    except Exception:  # noqa: BLE001 - kayıt bu yüzden düşmesin
        logging.getLogger("hesapkitap.mail_name").exception("kayıtta ekstre adresi verilemedi")
    return None
