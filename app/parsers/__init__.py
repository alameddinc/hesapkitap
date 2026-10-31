"""Banka tespiti + parser registry. Yeni banka = yeni modül + DETECTORS'a bir satır."""
import io
import threading

import pdfplumber

from app.parsers import kuveytturk, vakifbank
from app.parsers.base import Statement

DETECTORS = [
    (vakifbank.is_vakifbank, vakifbank.parse_text),
    (kuveytturk.is_kuveytturk, kuveytturk.parse_text),
]


class PasswordRequired(ValueError):
    """PDF şifreli ve verilen şifre(ler) açmadı."""


def page_texts(pdf) -> list[str]:
    """Sayfa sayfa metin; her sayfadan sonra page.close(): pdfplumber açık belgede her sayfanın nesnelerini tutuyor,
    bellek sayfa sayısıyla doğrusal büyüyordu (60 sayfa ≈ +316 MB → ≈ +20 MB; metin birebir aynı)."""
    from app import limits

    pages = pdf.pages
    if len(pages) > limits.pdf_max_pages():
        raise limits.LimitError(f"PDF {len(pages)} sayfa; en fazla {limits.pdf_max_pages()} sayfa okunur")
    deadline = limits.Deadline()
    out = []
    for p in pages:
        deadline.check()
        try:
            out.append(p.extract_text() or "")
        finally:
            p.close()
    return out


# Aynı yükleme aynı PDF'i iki kez metne çeviriyordu (önce tür tahmini, sonra okuyucu): son birkaç belgenin metni
# (içerik özeti + şifre ile) tutulur. Metin küçük (60 sayfa ≈ 150 KB); belgenin kendisi tutulmaz.
_TEXTS: "dict[tuple[bytes, str], tuple[str, list[str]]]" = {}
_TEXTS_MAX = 4


def extract_text(data: bytes, password: str | None = None) -> str:
    import hashlib

    key = (hashlib.blake2b(data, digest_size=16).digest(), password or "")
    with _PAGES_LOCK:
        hit = _TEXTS.get(key)
    if hit:
        _remember_pages(*hit)
        return hit[0]
    text, pages = _extract(data, password)
    with _PAGES_LOCK:
        _TEXTS[key] = (text, pages)
        while len(_TEXTS) > _TEXTS_MAX:
            _TEXTS.pop(next(iter(_TEXTS)))
    _remember_pages(text, pages)
    return text


def _extract(data: bytes, password: str | None) -> tuple[str, list[str]]:
    from pdfminer.pdfdocument import PDFPasswordIncorrect

    try:
        with pdfplumber.open(io.BytesIO(data), password=password or "") as pdf:
            pages = page_texts(pdf)
    except Exception as e:  # noqa: BLE001
        from app.limits import LimitError

        if isinstance(e, LimitError):
            raise
        if e.args and isinstance(e.args[0], PDFPasswordIncorrect):
            raise PasswordRequired("PDF şifreli") from e
        raise
    return "\n".join(pages), pages


# Sayfa sınırları: metin tek parça döner (tüm okuyucular onu bekler); sayfa başlığı/altlığını ayıklamak isteyen
# (app/templates.py) aynı metnin sayfalarını buradan sorar. Küçük, kilitli önbellek — uçlar iş parçacığında çalışır.
_PAGES: dict[int, list[str]] = {}
_PAGES_LOCK = threading.Lock()


def _remember_pages(text: str, pages: list[str]) -> None:
    with _PAGES_LOCK:
        _PAGES[hash(text)] = pages
        while len(_PAGES) > 8:
            _PAGES.pop(next(iter(_PAGES)))


def pages_of(text: str) -> list[str] | None:
    """extract_text'in döndürdüğü metnin sayfaları (bilinmiyorsa None)."""
    with _PAGES_LOCK:
        pages = _PAGES.get(hash(text))
    return pages if pages is not None and "\n".join(pages) == text else None


def _text_with_passwords(data: bytes, passwords: list[str]) -> tuple[str, str | None]:
    """Şifresiz, sonra sırayla verilen şifrelerle dene. (metin, işe yarayan şifre) döner."""
    for pw in [None, *passwords]:
        try:
            return extract_text(data, pw), pw
        except PasswordRequired:
            continue
    raise PasswordRequired("PDF şifreli; şifre gerekli" if not passwords else "Şifre yanlış")


SUPPORTED = "VakıfBank, Kuveyt Türk; diğerleri ekstre denklemi tutarsa genel okuyucuyla"


def parse_text(text: str) -> Statement | None:
    """Yapay zekâsız okuma: bankaya özel okuyucu, yoksa genel okuyucu (ekstre denklemi tutmalı). Okunamazsa None."""
    for detect, parse in DETECTORS:
        if detect(text):
            return parse(text)
    from app import card_reader

    return card_reader.read(text)


def parse_statement(data: bytes, allow_ai: bool = True, passwords: list[str] | None = None) -> Statement:
    """Önce bankaya özel parser (kesin, ücretsiz); tanınmazsa ve GEMINI_API_KEY varsa yapay zekâ ile okuma.
    Şifreli PDF için verilen şifreler sırayla denenir; olmazsa PasswordRequired."""
    try:
        text, used_pw = _text_with_passwords(data, passwords or [])
    except PasswordRequired:
        raise
    except ValueError as e:
        from app.limits import LimitError

        if isinstance(e, LimitError):
            raise
        raise ValueError(f"PDF açılamadı: {e}") from e
    except Exception as e:  # noqa: BLE001 - bozuk PDF
        raise ValueError(f"PDF açılamadı: {e}") from e
    if st := parse_text(text):
        return st
    from app import receipt

    if allow_ai and receipt.enabled():
        from app.parsers.ai import parse_pdf_with_ai

        try:
            # şifreli PDF'i model okuyamaz: çözülmüş metni gönder
            st = parse_pdf_with_ai(data) if used_pw is None else parse_pdf_with_ai(text.encode(), mime="text/plain")
        except receipt.ReceiptError as e:
            raise ValueError(f"Banka tanınamadı ve yapay zekâ ile okunamadı: {e}") from e
        st.ai_parsed = True
        return st
    raise ValueError(f"Banka tanınamadı. Desteklenen: {SUPPORTED}. "
                     "Diğer bankalar için GEMINI_API_KEY tanımlarsan yapay zekâ ile okunur.")
