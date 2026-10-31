"""Günlük (log) ayarı: kişisel veri maskeleme ve döndürme (S8, KVKK).

- Erişim günlüğü (uvicorn.access) sorgu dizgesini YAZMAZ: '/api/admin/deleted?email=…' → '/api/admin/deleted?…'.
- Bütün günlük satırlarında e-posta, IBAN, kart numarası ve TC kimlik no biçimli diziler maskelenir (istisna metni dahil).
- Uygulama günlükleri stdout'a (docker json-file sürücüsü toplar; döndürme compose'da, bkz. deploy/logging.md). İsteğe
  bağlı dosya: HESAPKITAP_LOG_FILE (10 MB × 5 döndürmeli). Seviye: HESAPKITAP_LOG_LEVEL (varsayılan INFO).
- Kullanıcı günlükte adıyla/e-postasıyla değil id ile geçer (ör. 'job backup u12 ok').
"""
import logging
import logging.handlers
import os
import re

_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
_IBAN = re.compile(r"\bTR\s?\d{2}(?:\s?\d{4}){5}\s?\d{2}\b", re.I)
_CARD = re.compile(r"\b(?:\d[ -]?){15,18}\d\b")  # 16–19 hane (boşluklu/tireli)
_TCKN = re.compile(r"(?<![\d.,])[1-9]\d{10}(?![\d.,])")
_QUERY = re.compile(r"\?[^\s\"]*")

FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"


def mask(text: str) -> str:
    if not text:
        return text
    text = _EMAIL.sub("<e-posta>", text)
    text = _IBAN.sub("<iban>", text)
    text = _CARD.sub("<kart>", text)
    return _TCKN.sub("<no>", text)


def strip_query(path: str) -> str:
    return _QUERY.sub("?…", path) if isinstance(path, str) else path


def user_ref(user: dict | None) -> str:
    """Günlükte kullanıcı: 'u<id>' (ana defter: 'ana'); ad/e-posta yazılmaz."""
    return f"u{user['id']}" if user and user.get("id") is not None else "ana"


class MaskFilter(logging.Filter):
    """Kayıt mesajını maskeler; erişim günlüğünde sorgu dizgesini atar."""

    def filter(self, record: logging.LogRecord) -> bool:
        if record.name == "uvicorn.access" and isinstance(record.args, tuple) and len(record.args) == 5:
            a = list(record.args)
            a[2] = strip_query(a[2])
            record.args = tuple(a)
            return True
        try:
            msg = record.getMessage()
        except Exception:  # noqa: BLE001 - biçimlenemeyen kayıt: dokunma
            return True
        record.msg, record.args = mask(msg), ()
        return True


class MaskFormatter(logging.Formatter):
    """İstisna/yığın metni dahil tüm satırı maskeler."""

    def format(self, record: logging.LogRecord) -> str:
        return mask(super().format(record))


_installed = False


def install() -> None:
    """Bir kez: kök günlüğe maskeleyen stdout işleyicisi (yoksa), uvicorn günlüklerine süzgeç. Testlerde zararsız."""
    global _installed
    if _installed:
        return
    _installed = True
    flt = MaskFilter()
    for name in ("uvicorn.access", "uvicorn.error", "uvicorn"):
        logging.getLogger(name).addFilter(flt)
    root = logging.getLogger()
    level = getattr(logging, os.environ.get("HESAPKITAP_LOG_LEVEL", "INFO").upper(), logging.INFO)
    if not root.handlers:  # uvicorn kökü kurmaz: uygulama INFO satırları kaybolmasın
        h = logging.StreamHandler()
        h.setFormatter(MaskFormatter(FORMAT))
        root.addHandler(h)
        root.setLevel(level)
    if path := os.environ.get("HESAPKITAP_LOG_FILE"):
        fh = logging.handlers.RotatingFileHandler(path, maxBytes=10 * 1024 * 1024, backupCount=5, encoding="utf-8")
        fh.setFormatter(MaskFormatter(FORMAT))
        root.addHandler(fh)
    for h in root.handlers:
        h.addFilter(flt)
