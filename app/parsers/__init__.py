"""Banka tespiti + parser registry. Yeni banka = yeni modül + DETECTORS'a bir satır."""
import io
import logging
import math
import re
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
                st.reader = f"parser:{getattr(parse, '__module__', '?').rsplit('.', 1)[-1]}"  # alarm: hangi okuyucu
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
    olmadıktan sonra), yapay zekânın okuduğu yazılır.

    Tutarlılık kapısı (app/parsers/gate.py): kural okuması (parser / şablon) denklemi, işlem toplamını, 0 işlem ve
    ölçek kaymasını geçemezse kayıttan önce yönetici alarmı ("parser_drift") ve yedek yol (_gate_fallback)."""
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
        if not allow_ai:  # iç yeniden okuma (tarih backfill'i): kapı/alarm yok, eski davranış
            return st
        from app.parsers import gate

        verdict = gate.check(st)
        if verdict.ok:
            return st
        return _gate_fallback(data, text, used_pw, st, verdict)
    from app import receipt

    if allow_ai and receipt.enabled():
        return _ai_read(data, text, used_pw, lambda: _template_miss(text))
    raise ValueError(f"Banka tanınamadı. Desteklenen: {SUPPORTED}. "
                     "Diğer bankalar için GEMINI_API_KEY tanımlarsan yapay zekâ ile okunur.")


def _ai_read(data: bytes, text: str, used_pw: str | None, miss) -> Statement:
    """Yapay zekâ okuması (önce okuma önbelleği). ReceiptError'ı kullanıcı metnine çevirir; NotAStatement geçer.
    miss(): şablon isabet ölçümünün nedeni (app/template_stats.py) — yalnız gerçek çağrıda, şablon öğrenilmeden önce."""
    from app import card_reader, doc_cache, receipt
    from app.parsers.ai import parse_pdf_with_ai

    cache = doc_cache.current()
    if cache is not None and (hit := cache.get()) is not None:  # aynı ekstre bu defterde yapay zekâyla okunmuştu
        try:
            st = Statement.from_dict(hit)
        except (TypeError, ValueError):
            st = None
        if st is not None:
            st.ai_parsed, st.source = True, "yapay_zeka"
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
    st.template_miss = miss()
    spec = card_reader.learn_from_ai(text, st)  # hata okumayı bozmaz
    # yapay zekânın işlemleri denklemi tutmuyor, aynı belgeden şablon tutuyor: kullanıcı düzeltilmiş okumayı görür
    st = card_reader.fixed_read(text, st, spec) or st
    if cache is not None:
        cache.put(st.to_dict())
    return st


def _gate_fallback(data: bytes, text: str, used_pw: str | None, st: Statement, verdict) -> Statement:
    """Kural okuması kapıdan geçmedi (app/parsers/gate.py): yönetici alarmı (incidents "parser_drift", banka başına
    günde bir), sonra sırayla
    1. bankaya özel parser patladıysa kart şablonları (kayıtlı ya da yapay zekâsız öğrenilen; ücretsiz),
    2. yapay zekâ okuması (GEMINI_API_KEY varsa; önbellek dahil),
    kapıyı geçen ilk okuma döner; hiçbiri geçmezse (ya da yapay zekâ yok / hata verdi) kural okuması aynen döner:
    bugünkü akış (kayıt + "Rakamlar tutmadı" doğrulaması, app/verification.py).
    Okuma hakkı: belge başına tek sayım (main.upload_statements charge) — yedek yol ek hak düşürmez; dönen okumada
    gate_fallback işaretlidir (yükleme ucu "zaten vardı"da da yedek yapay zekâ çağrısını kullanıcıya saymaz)."""
    from app import card_reader, receipt
    from app.parsers import gate

    reader = getattr(st, "reader", None) or f"{st.source}:?"
    _report_drift(st, reader, verdict)
    if st.source == "parser":
        try:
            alt = card_reader.read(text)
        except Exception:  # noqa: BLE001 - şablon denemesi okumayı bozmaz
            _log.warning("kapı yedeği: şablon denemesi hata verdi", exc_info=True)
            alt = None
        if alt is not None and gate.check(alt).ok:
            alt.gate_fallback = verdict.check
            _log.info("kapı yedeği: %s yerine şablon okuması (%s)", reader, getattr(alt, "reader", "sablon"))
            return alt
    if not receipt.enabled():
        return st
    try:
        ai = _ai_read(data, text, used_pw, lambda: "parser_drift")
    except Exception as e:  # noqa: BLE001 - yapay zekâ yoksa / hata verdiyse eski akış (kural okuması kaydedilir)
        _log.warning("kapı yedeği: yapay zekâ okuyamadı (%s): %s", reader, str(e)[:200])
        return st
    ai.gate_fallback = verdict.check
    ai_verdict = gate.check(ai)
    if ai_verdict.ok:
        _log.info("kapı yedeği: %s yerine yapay zekâ okuması", reader)
        return ai
    _log.warning("kapı yedeği: yapay zekâ okuması da tutmadı (%s, %s); kural okuması kalır", reader, ai_verdict.check)
    st.gate_fallback = verdict.check
    st.gate_ai_called = True  # yapay zekâ çağrıldı ama sonucu kullanılmadı (maliyet ölçümü)
    return st


def _report_drift(st: Statement, reader: str, verdict) -> None:
    """Yönetici alarmı: banka, okuyucu, kontrol ve sayılar — isim, kart no, işyeri/açıklama yok. Hata fırlatmaz."""
    try:
        from app import incidents

        ozet = drift_summary(reader, verdict)
        _log.warning("okuyucu kayması: banka=%s %s", st.bank, ozet)
        incidents.system_event("parser_drift", st.bank or "?", ozet)
    except Exception:  # noqa: BLE001 - alarm okumayı bozmaz
        _log.warning("okuyucu kayması kaydedilemedi", exc_info=True)


_READER_RE = re.compile(r"[^a-z0-9_:]")


def _whole_tl(v: float) -> int:
    """Tam TL (kuruş yok), yarım yukarı (sıfırdan uzağa): 17,50 → 18, −0,50 → −1."""
    return int(math.copysign(math.floor(abs(v) + 0.5), v))


def drift_summary(reader: str, verdict) -> str:
    """Alarmın sayısal özeti: sabit anahtarlar + sayılar (gate.numbers), tutarlar tam TL'ye yuvarlı (kuruş alarma
    girmez; oran/fark teşhisi için yeterli). Okuyucu adı küçük harf/rakam/":" ile sınırlı. Kapının kendisi kuruşlu
    sayılarla karar verir; yuvarlama yalnız alarm metninde."""
    reader = _READER_RE.sub("", str(reader).lower())[:40] or "?"
    nums = " · ".join(f"{k}={_whole_tl(v)}" for k, v in (verdict.numbers or {}).items()
                      if isinstance(v, (int, float)) and not isinstance(v, bool))
    return f"okuyucu={reader} · kontrol={verdict.check} · {nums}".strip(" ·")
