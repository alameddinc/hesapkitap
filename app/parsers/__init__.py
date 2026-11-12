"""Banka tespiti + parser registry. Yeni banka = yeni modül + DETECTORS'a bir satır."""
import io
import logging
import threading

import pdfplumber

from app.parsers import kuveytturk, vakifbank
from app.parsers.base import Statement

DETECTORS = [
    (vakifbank.is_vakifbank, vakifbank.parse_text),
    (kuveytturk.is_kuveytturk, kuveytturk.parse_text),
]


_log = logging.getLogger("hesapkitap.parsers")

# PDF açılamadı (bozuk ya da PDF değil): kütüphanenin ayrıntısı ("No /Root object! - Is this really a PDF?") günlüğe
PDF_UNREADABLE = "PDF açılamadı: dosya bozuk ya da PDF değil. Bankadan indirdiğin PDF'i yeniden seç."


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
    text = "\n".join(pages)
    from app import glyphs

    # yazı tipi kodlu metin ("(cid:N)", Type3 yazı tipi): glif şekli → karakter eşlemesiyle (gerekirse yerel OCR) kurtar
    if got := glyphs.recover(data, password, text):
        text, pages, info = got
        with _PAGES_LOCK:
            _RECOVERED[hash(text)] = info
            while len(_RECOVERED) > 8:
                _RECOVERED.pop(next(iter(_RECOVERED)))
    return text, pages


# Glif eşlemesiyle kurtarılmış metnin bilgisi (app/glyphs.py recover: via, unknown, ocr_pages); geriye dönük öğrenme
# raporu için. Metin özetiyle, küçük.
_RECOVERED: dict[int, dict] = {}


def recovery_of(text: str) -> dict | None:
    with _PAGES_LOCK:
        return _RECOVERED.get(hash(text))


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
    """Yapay zekâsız okuma: bankaya özel okuyucu (source "parser"), yoksa kayıtlı/öğrenilen kart şablonu (source
    "sablon"; ekstre denklemi ve tarihler tutmalı). Okunamazsa None."""
    for detect, parse in DETECTORS:
        if detect(text):
            st = parse(text)
            if st is not None:
                st.source = "parser"
            return st
    from app import card_reader

    return card_reader.read(text)


def _template_miss(text: str) -> str:
    """Kart ekstresi neden yapay zekâya gitti (sabit kod, içerik yok): text_cid (PDF yazı tipi kodlu, metin yok),
    no_template (bu bankanın açık kart şablonu yok), unverified (şablon var ama okuması denklemi / tarihleri tutmadı)."""
    from app import card_reader, templates

    try:
        if card_reader._cid_text(text):
            return "text_cid"
        if not any(t["spec"].get("kind") == "card" and not t.get("disabled") and card_reader.matches(text, t["spec"])
                   for t in templates.all_templates()):
            return "no_template"
        return "unverified"
    except Exception:  # noqa: BLE001 - ölçüm okumayı bozmaz
        return "error"


def parse_statement(data: bytes, allow_ai: bool = True, passwords: list[str] | None = None) -> Statement:
    """Sıra: bankaya özel parser → kayıtlı kart şablonu (ikisi de ücretsiz, yapay zekâsız) → GEMINI_API_KEY varsa yapay
    zekâ. Yapay zekâ okuması doğrulanırsa aynı bankanın sonraki ekstreleri için şablon öğretir (card_reader.learn_from_ai;
    şifreli PDF'te şifre çözülmüş metinden). Şifreli PDF için verilen şifreler sırayla denenir; olmazsa PasswordRequired.
    Okuma önbelleği (doc_cache.reading ile açılmışsa): yapay zekâdan önce bakılır (PDF açıldıktan, yerel okuma
    olmadıktan sonra), yapay zekânın okuduğu yazılır."""
    try:
        text, used_pw = _text_with_passwords(data, passwords or [])
    except PasswordRequired:
        raise
    except ValueError as e:
        from app.limits import LimitError

        if isinstance(e, LimitError):
            raise
        _log.info("PDF açılamadı: %s", str(e)[:300])  # kütüphanenin İngilizce metni kullanıcıya gitmez
        raise ValueError(PDF_UNREADABLE) from e
    except Exception as e:  # noqa: BLE001 - bozuk PDF
        _log.info("PDF açılamadı: %s", str(e)[:300])
        raise ValueError(PDF_UNREADABLE) from e
    if st := parse_text(text):
        return st
    from app import receipt

    if allow_ai and receipt.enabled():
        from app import doc_cache
        from app.parsers.ai import parse_pdf_with_ai

        cache = doc_cache.current()
        if cache is not None and (hit := cache.get()) is not None:  # aynı ekstre bu defterde yapay zekâyla okunmuştu
            try:
                st = Statement.from_dict(hit)
            except (TypeError, ValueError):
                st = None
            if st is not None:
                st.ai_parsed, st.source = True, "yapay_zeka"
                from app import card_reader

                # ilk okumada öğrenilemediyse (eski sürüm) şimdi dene: yapay zekâ çağrısı yok, kayıtlı kural varsa no-op
                spec = card_reader.learn_from_ai(text, st)
                return card_reader.fixed_read(text, st, spec) or st
        try:
            # şifreli PDF'i model okuyamaz: çözülmüş metni gönder
            st = parse_pdf_with_ai(data) if used_pw is None else parse_pdf_with_ai(text.encode(), mime="text/plain")
        except receipt.ReceiptError as e:
            from app import errors

            raise ValueError(f"Banka tanınamadı ve yapay zekâ ile okunamadı: {errors.user_text(e, 502)}") from e
        st.ai_parsed, st.source = True, "yapay_zeka"
        from app import card_reader

        st.template_miss = _template_miss(text)  # şablon isabet ölçümü (app/template_stats.py)
        spec = card_reader.learn_from_ai(text, st)  # hata okumayı bozmaz
        # yapay zekânın işlemleri denklemi tutmuyor, aynı belgeden şablon tutuyor: kullanıcı düzeltilmiş okumayı görür
        st = card_reader.fixed_read(text, st, spec) or st
        if cache is not None:
            cache.put(st.to_dict())
        return st
    raise ValueError(f"Banka tanınamadı. Desteklenen: {SUPPORTED}. "
                     "Diğer bankalar için GEMINI_API_KEY tanımlarsan yapay zekâ ile okunur.")
