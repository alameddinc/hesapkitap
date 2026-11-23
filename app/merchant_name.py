"""Fişin işyeri adı düzeltmesi (okumadan sonra, defter bağlamında).

1. Fişte geçerli VKN varsa ve bu defterde aynı tax_no ile kaydedilmiş gider varsa: en son kaydın açıklaması (kullanıcının
   düzelttiği ad korunur; aynı işyerinin her fişi aynı adla görünür).
2. Yoksa okunan ad resmi unvanın kesik bir parçası gibi duruyorsa (bölünmüş başlıktan yalnız son kelime: "Mamullerı" ←
   "ORNEK UNLU MAMULLERİ PASTACILIK SAN. TİC. LTD. ŞTİ."), ad unvandan türetilir: unvanın başından okunan adın son
   kelimesine kadar ("Ornek Unlu Mamulleri").
"""
from __future__ import annotations

import logging
import sqlite3

from app import receipt_templates as rt
from app import vat

log = logging.getLogger(__name__)


def known_name(c: sqlite3.Connection, tax_no: str | None) -> str | None:
    """Bu defterde aynı VKN'li en son giderin açıklaması (yoksa None). Yalnız geçerli 10 haneli VKN (TCKN değil)."""
    v = vat.vkn(tax_no)
    if not v:
        return None
    r = c.execute("SELECT description FROM entries WHERE tax_no = ? AND kind = 'gider' AND TRIM(description) != '' "
                  "ORDER BY entry_date DESC, id DESC LIMIT 1", (v,)).fetchone()
    return r[0].strip()[:80] if r else None


def from_legal(merchant: str | None, legal: str | None) -> str | None:
    """Okunan ad unvanın kesik bir parçasıysa unvandan tam ad; değilse None (okunan ad kalır)."""
    core = rt.core_name(legal)
    if not core:
        return None
    words = core.split()
    lw = [rt._i_key(w) for w in words]
    mw = [rt._i_key(w) for w in (merchant or "").split()]
    if not mw or len(mw) >= len(lw) or not set(mw) <= set(lw):
        return None  # ad zaten tam ya da unvanla ilgisiz (marka adı: "Ornek Cafe" / "XYZ GIDA LTD.")
    end = max(i for i, w in enumerate(lw) if w == mw[-1]) + 1
    if lw[:len(mw)] == mw and end <= len(mw):
        return None  # okunan ad unvanın başı ("Ornek Unlu"): kısaltılmış marka, kesik değil
    return rt._title(" ".join(words[:end]), legal)


def fix(doc: dict, c: sqlite3.Connection) -> None:
    """doc['merchant'] yerinde düzeltilir (kurallar modül açıklamasında). Hata okumayı bozmaz."""
    try:
        name = known_name(c, doc.get("tax_no")) or from_legal(doc.get("merchant"), doc.get("legal_name"))
        if name:
            doc["merchant"] = name
    except Exception:  # noqa: BLE001
        log.warning("fiş işyeri adı düzeltilemedi", exc_info=True)
