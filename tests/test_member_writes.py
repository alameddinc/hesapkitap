"""Ortak defter üyesinin (Görür ve ekler) belge okutma uçları: yeni bir okuma ucu eklenince üyeye kapalı kalmasın.
Canlı hata 06.11.2026: /api/documents/parse listede yoktu; üyenin fişi 403 aldı, uygulama "Demoda fiş yüklenmez" dedi."""
import re

from app import book


def _allowed(method: str, path: str) -> bool:
    return any(method == m and re.fullmatch(p, path) for m, p in book.MEMBER_WRITES)


def test_every_parse_endpoint_open_to_member():
    from app.api.main import app

    parse = sorted({r.path for r in app.routes if getattr(r, "methods", None) and "POST" in r.methods
                    and r.path.endswith("/parse") and r.path.startswith("/api/")})
    assert "/api/documents/parse" in parse
    # yalnız sahibin işi olan okumalar (ekstre / döküm içe aktarma) burada bilerek yok
    owner_only = {p for p in parse if p.startswith(("/api/statements", "/api/bank-import", "/api/imports"))}
    assert [p for p in parse if p not in owner_only and not _allowed("POST", p)] == []
