"""Banka tespiti + parser registry. Yeni banka = yeni modül + DETECTORS'a bir satır."""
import io

import pdfplumber

from app.parsers import kuveytturk, vakifbank
from app.parsers.base import Statement

DETECTORS = [
    (vakifbank.is_vakifbank, vakifbank.parse_text),
    (kuveytturk.is_kuveytturk, kuveytturk.parse_text),
]


def extract_text(data: bytes) -> str:
    with pdfplumber.open(io.BytesIO(data)) as pdf:
        return "\n".join(p.extract_text() or "" for p in pdf.pages)


SUPPORTED = "VakıfBank, Kuveyt Türk"


def parse_statement(data: bytes, allow_ai: bool = True) -> Statement:
    """Önce bankaya özel parser (kesin, ücretsiz); tanınmazsa ve GEMINI_API_KEY varsa yapay zekâ ile okuma."""
    try:
        text = extract_text(data)
    except Exception as e:  # noqa: BLE001 - bozuk/şifreli PDF
        raise ValueError(f"PDF açılamadı (şifreli olabilir): {e}") from e
    for detect, parse in DETECTORS:
        if detect(text):
            return parse(text)
    from app import receipt

    if allow_ai and receipt.enabled():
        from app.parsers.ai import parse_pdf_with_ai

        try:
            st = parse_pdf_with_ai(data)
        except receipt.ReceiptError as e:
            raise ValueError(f"Banka tanınamadı ve yapay zekâ ile okunamadı: {e}") from e
        st.ai_parsed = True
        return st
    raise ValueError(f"Banka tanınamadı. Desteklenen: {SUPPORTED}. "
                     "Diğer bankalar için GEMINI_API_KEY tanımlarsan yapay zekâ ile okunur.")
