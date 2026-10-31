"""Kullanıcıya giden hata metinleri: Türkçe ve anlaşılır. Python'un iç mesajı (ValueError("could not convert..."),
openpyxl, httpx, Gemini'nin İngilizce yanıtı) kullanıcıya gitmez; günlüğe yazılır, yerine duruma göre genel metin.

- UserError: metni kullanıcıya gösterilmek için yazılmış hata (kendi kodumuz). user_text(e) bunun metnini olduğu gibi
  geçirir.
- Eski kodun Türkçe ValueError/KeyError metinleri (ör. "Alt kalemlerin toplamı … harcamayı geçemez") Türkçe olduğu
  anlaşılırsa geçer; İngilizce/teknik görünen metin genel metne döner.
"""
import logging
import re

log = logging.getLogger("hesapkitap.errors")


class UserError(ValueError):
    """Metni kullanıcıya gösterilir (Türkçe, ne yapacağını söyler)."""


GENERIC = {
    400: "İstek işlenemedi; girdiğin bilgiyi kontrol edip tekrar dene.",
    401: "Oturumun kapanmış; yeniden giriş yap.",
    403: "Bu işlem için yetkin yok.",
    404: "Bu kayıt silinmiş ya da başka bir cihazda değişmiş olabilir; listeyi yenile.",
    405: "Bu işlem bu adreste yapılamıyor; uygulamayı güncelle.",
    409: "Bu işlem şu an yapılamıyor; kayıt az önce değişmiş olabilir, listeyi yenile.",
    413: "Dosya ya da istek çok büyük.",
    422: "Girilen bilgi geçerli değil; kontrol edip tekrar dene.",
    429: "Çok fazla istek; biraz bekleyip tekrar dene.",
    500: "Beklenmeyen bir hata oldu; biraz sonra tekrar dene.",
    502: "Bağlı bir servise (yapay zekâ, fiyat, banka) şu an ulaşılamıyor; biraz sonra tekrar dene.",
    503: "Sunucu şu an meşgul; biraz sonra tekrar dene.",
}

_TR = re.compile(r"[çğıöşüÇĞİÖŞÜ₺]|\b(yok|değil|degil|için|olmalı|geçersiz|bulunamadı|yanlış|zaten|önce|sonra|şifre|kayıt|"
                 r"tutar|hesap|ekstre|dosya|belge|kart|borç|alacak|kişi|gün|ay|yıl|en fazla|en az|tekrar|gerekli)\b", re.I)
_TECH = re.compile(r"Traceback|Errno|NoneType|object has no|invalid literal|could not|Expecting|unexpected|"
                   r"line \d+|column \d+|not supported|is not|must be|argument|KeyError|TypeError|ValueError|"
                   r"https?://|\bat 0x|sqlite|zipfile|openpyxl|xlrd|pdfminer|httpx", re.I)


def is_user_text(msg: str) -> bool:
    """Metin kullanıcıya gösterilebilir mi: Türkçe ve teknik ayrıntı içermiyor."""
    msg = (msg or "").strip()
    return bool(msg) and len(msg) <= 400 and bool(_TR.search(msg)) and not _TECH.search(msg)


def user_text(e: BaseException | str | None, status: int = 422, fallback: str | None = None) -> str:
    """Hatanın kullanıcıya gösterilecek metni. Gösterilemiyorsa iç metin günlüğe, kullanıcıya genel metin."""
    if isinstance(e, KeyError) and e.args and not isinstance(e.args[0], str):
        e = None  # KeyError(42): kimlik; metin değil
    msg = e if isinstance(e, str) else (str(e.args[0]) if isinstance(e, KeyError) and e.args else str(e or ""))
    if isinstance(e, UserError) or is_user_text(msg):
        return msg
    if msg:
        log.info("kullanıcıya genel metin gösterildi (%s): %s", status, msg[:300])
    return fallback or GENERIC.get(status, GENERIC[400 if status < 500 else 500])


_FIELDS = {"amount": "Tutar", "entry_date": "Tarih", "description": "Açıklama", "kind": "Tür", "category": "Kategori",
           "payment": "Ödeme şekli", "username": "Kullanıcı adı", "password": "Şifre", "email": "E-posta",
           "person": "Kişi", "direction": "Yön", "debt_date": "Borç tarihi", "due_date": "Son ödeme tarihi",
           "paid_at": "Ödeme tarihi", "name": "Ad", "balance": "Bakiye", "month": "Ay", "file": "Dosya",
           "files": "Dosya", "monthly_limit": "Aylık sınır", "goal": "Hedef", "note": "Not", "asset": "Birim",
           "quantity": "Miktar", "start_month": "Başlangıç ayı", "day_of_month": "Ayın günü", "code": "Kod",
           "installments": "Taksit", "account_id": "Hesap", "from_month": "Başlangıç ayı", "gross": "Brüt tutar",
           "pattern": "Kural", "parts": "Alt kalemler", "items": "Kalemler", "other": "Eşleşme", "same": "Cevap"}


def _num(x) -> str:
    try:
        f = float(x)
    except (TypeError, ValueError):
        return str(x)
    s = f"{f:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return s[:-3] if s.endswith(",00") else s


def validation_text(errors: list[dict]) -> str:
    """Pydantic/FastAPI doğrulama hatalarından tek Türkçe cümle (ilk iki hata)."""
    out = []
    for err in errors[:2]:
        loc = [str(x) for x in err.get("loc", ()) if x not in ("body", "query", "path", "form")]
        field = loc[-1] if loc else ""
        name = _FIELDS.get(field, field.replace("_", " ") if field and not field.isdigit() else "Bir alan")
        t, ctx = err.get("type", ""), err.get("ctx") or {}
        if t == "missing":
            out.append(f"{name} eksik")
        elif t in ("greater_than", "greater_than_equal", "less_than", "less_than_equal"):
            op = {"greater_than": "büyük", "greater_than_equal": "en az", "less_than": "küçük",
                  "less_than_equal": "en fazla"}[t]
            lim = next(iter(ctx.values()), "")
            out.append(f"{name} {op} {_num(lim)} olmalı" if op in ("en az", "en fazla") else
                       f"{name} {_num(lim)} değerinden {op} olmalı")
        elif t in ("string_too_short", "too_short"):
            out.append(f"{name} boş olamaz" if ctx.get("min_length") in (1, None) else
                       f"{name} en az {ctx.get('min_length')} karakter olmalı")
        elif t in ("string_too_long", "too_long"):
            out.append(f"{name} en fazla {ctx.get('max_length')} karakter olabilir")
        elif t.startswith(("date", "datetime")):
            out.append(f"{name} geçerli bir tarih olmalı (YYYY-AA-GG)")
        elif t in ("float_parsing", "int_parsing", "float_type", "int_type", "decimal_parsing"):
            out.append(f"{name} sayı olmalı")
        elif t in ("literal_error", "enum"):
            out.append(f"{name} için geçersiz seçim")
        elif t == "string_pattern_mismatch":
            out.append(f"{name} biçimi geçersiz")
        elif t in ("json_invalid", "model_attributes_type", "dict_type", "list_type"):
            out.append("İstek gövdesi okunamadı")
        elif t == "value_error":
            msg = str(err.get("msg", "")).removeprefix("Value error, ")
            out.append(msg if is_user_text(msg) else f"{name} geçerli değil")
        else:
            out.append(f"{name} geçerli değil")
    return (". ".join(dict.fromkeys(out)) + ".") if out else GENERIC[422]
