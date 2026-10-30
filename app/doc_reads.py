"""Belge okuma hakkı (yalnız hazır sürüm, plans.hosted()).

Okunan her BELGE 1 hak: kart ekstresi (PDF/Excel), hesap dökümü, fiş fotoğrafı, fatura (dosya ya da mail metni),
deftere özel posta adresine gelen belge/bildirim. Okuma yöntemi (yerel ayrıştırıcı, OCR, Gemini) ve bir belgenin içindeki
yapay zekâ çağrısı sayısı fark etmez.

- Kontrol işten ÖNCE: check(key) hak doluysa QuotaError (receipt.ReceiptError) atar; belge okunmaz.
- Sayım işten SONRA ve yalnız yeni belge okunduysa: charge(key). Hata çıkan, "zaten vardı" sonucu veren okumada
  charge çağrılmaz.
- charge idempotenttir: aynı içerik (key = dosya baytları ya da "mail:<Message-ID>") aynı defterde ikinci kez sayılmaz
  (system.doc_reads kaydı). Bu yüzden aynı belgeyi hem dış uç (endpoint) hem iç işleyici (mailbox.handle_pdf) sayabilir;
  çift sayım olmaz. Hak doluyken hiçbir belge okunmaz (istisna yok).
- Sayaç system.ai_usage (hazır sürümde anlamı: okunan belge); aile/ortak defterde havuz system.ai_used_pool.
- Hazır sürümde receipt._check_quota/_count_usage (yapay zekâ çağrısı başına) kapalıdır; tek sayaç burasıdır.

Kendi sunucusunda bu modül hiçbir şey yapmaz (kota yapay zekâ çağrısı başına, receipt.py). Yönetici sınırsız.
"""
import hashlib
from datetime import date

from app.receipt import ReceiptError


class QuotaError(ReceiptError):
    """Hak doldu. İstemci elle girişe yönlendirir: fields() hata yanıtına eklenir."""

    def __init__(self, msg: str, limit: int, upgrade: bool):
        super().__init__(msg)
        self.limit, self.upgrade = limit, upgrade

    def fields(self) -> dict:
        return {"manual": True, "quota_full": True, "limit": self.limit, "upgrade": self.upgrade}


def _subject() -> dict | None:
    """Hakkı sayılan kullanıcı: hazır sürümde açık defterin yönetici-olmayan kullanıcısı."""
    from app import db, plans

    if not plans.hosted():
        return None
    u = db.current_user()
    if not u or u.get("role") == "admin" or not u.get("id"):
        return None
    return u


def _key(key: bytes | str) -> str:
    raw = key.encode() if isinstance(key, str) else key
    return hashlib.sha256(raw).hexdigest()


def _book(u: dict) -> int:
    """Tekrar kaydının defteri: ortak defter = sahibin kimliği; üyenin "kendi defteri" = üyenin kimliği.
    (Hak havuzu ikisinde de ailenin ortak havuzudur: system.ai_used_pool.)"""
    return u["id"] if u.get("own_book") else (u.get("member_of") or u["id"])


def message(u: dict, quota: int) -> str:
    from app import plans

    msg = f"Bu ayki {quota} belge okuma hakkın doldu. Ay başında yenilenir. Bu arada harcamanı elle ekleyebilirsin."
    if _upgrade(u):
        msg += f" Premium'da ayda {plans.DOC_READS[plans.PREMIUM]}."
    return msg


def _upgrade(u: dict) -> bool:
    from app import plans

    return plans.plan_of(u) == plans.FREE


def check(key: bytes | str | None = None) -> None:
    """Hak doluysa QuotaError: hiçbir belge okunmaz (bilinen bankanın yerel ekstresi, daha önce okunmuş dosya dahil).
    key şimdilik yalnız imza uyumu için; tekrar muafiyeti sayımdadır (charge), kontrolde değil."""
    from app import system

    u = _subject()
    if not u:
        return
    quota = system.ai_quota(u)
    if quota is None:
        return
    if system.ai_used_pool(u) >= quota:
        raise QuotaError(message(u, quota), quota, _upgrade(u))


def allowed(key: bytes | str | None = None) -> bool:
    try:
        check(key)
    except QuotaError:
        return False
    return True


def charge(key: bytes | str) -> bool:
    """Başarıyla okunmuş YENİ belge: 1 hak. Aynı içerik bu defterde daha önce sayıldıysa False (hak düşmez)."""
    u = _subject()
    if not u:
        return False
    from app import system

    month = date.today().strftime("%Y-%m")
    with system.connect() as c:  # kayıt + sayaç tek işlemde: yarışta iki istek aynı belgeyi iki kez sayamaz
        if not c.execute("INSERT OR IGNORE INTO doc_reads (book_id, hash, month) VALUES (?, ?, ?)",
                         (_book(u), _key(key), month)).rowcount:
            return False
        c.execute("INSERT INTO ai_usage (user_id, month, count) VALUES (?, ?, 1) "
                  "ON CONFLICT(user_id, month) DO UPDATE SET count = count + 1", (u["id"], month))
    return True
