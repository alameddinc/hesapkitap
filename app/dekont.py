"""İşlem dekontu (tek bir havale/EFT/FAST'in belgesi): hesap dökümü DEĞİL.

Dekontta bakiye yoktur; döküm sanılırsa hesabın bakiyesi bozulur. Burada tek işlem olarak okunur (yapay zekâsız,
etiketlerden), hesaba gider/gelir olarak eklenir ve bakiyeye dokunulmaz. O ayın dökümü sonradan yüklenince aynı işlem
dekont kaydıyla birleşir (iki kez sayılmaz).
"""
import re
from datetime import date

from app import db


def _up(s: str) -> str:
    return s.replace("i", "İ").replace("ı", "I").upper()


def is_dekont(text: str) -> bool:
    head = _up(text[:400])
    return "DEKONT" in head and not re.search(r"HESAP HAREKET|HESAP EKSTRE|HESAP ÖZET|HESAP DÖKÜM", head)


def _field(text: str, *labels: str) -> str | None:
    for lab in labels:
        m = re.search(rf"(?im)^\s*{lab}\s*(?:\(TL\s*\))?\s*:\s*(.+?)(?:\s{{2,}}[A-ZÇĞİÖŞÜ][A-ZÇĞİÖŞÜ /()]+\s*:|$)", text)
        if m and m.group(1).strip() not in ("-", ""):
            return m.group(1).strip()
    return None


def _amount(v: str | None) -> float | None:
    if not v:
        return None
    s = re.sub(r"[^\d,.\-]", "", v)
    if "," in s and "." in s:  # 4,850.00 (İngilizce) ya da 4.850,00 (Türkçe): son ayraç ondalıktır
        s = s.replace(",", "") if s.rfind(".") > s.rfind(",") else s.replace(".", "").replace(",", ".")
    elif "," in s:
        s = s.replace(".", "").replace(",", ".")
    try:
        return abs(float(s))
    except ValueError:
        return None


def _iban(v: str | None) -> str:
    """Değerin içindeki IBAN (aynı satırdaki başka alanlar atlanır)."""
    m = re.search(r"TR\s?\d{2}(?:\s?\d){22}", (v or "").upper())
    return re.sub(r"\s", "", m.group(0)) if m else ""


def parse(text: str) -> dict:
    """{date, amount, kind (gider|gelir), counterparty, description, bank, ref} — okunamazsa ValueError."""
    from app.templates import bank_of

    m = re.search(r"İŞLEM TARİHİ\s*:\s*(\d{2})[./-](\d{2})[./-](\d{4})", _up(text)) or \
        re.search(r"(\d{2})[./](\d{2})[./](\d{4})", text)
    amount = _amount(_field(text, "İŞLEM TUTARI", "TUTAR", "TOPLAM"))
    if not m or not amount:
        raise ValueError("Dekont okunamadı (tarih ya da tutar yok). Hesap hareketleri listesini yükle.")
    d = date(int(m.group(3)), int(m.group(2)), int(m.group(1)))
    own = _iban(_field(text, "IBAN"))
    sender_iban = _iban(_field(text, "GÖNDEREN IBAN"))
    incoming = bool(own and sender_iban and own != sender_iban)
    who = _field(text, "GÖNDEREN" if incoming else "ALICI")
    purpose = _field(text, "ÖDEME AMACI", "AÇIKLAMA") or ""
    kind_txt = _field(text, "İŞLEM TÜRÜ") or ""
    desc = " · ".join(x for x in (who, purpose, kind_txt) if x)[:120] or "Dekont"
    ref = _field(text, "SORGU NO", "REFERANS", "İŞLEM NO") or ""
    return {"date": d.isoformat(), "amount": amount, "kind": "gelir" if incoming else "gider", "counterparty": who,
            "description": desc, "purpose": purpose, "bank": bank_of(text), "ref": re.sub(r"\W", "", ref)[:30]}


def _title(name: str) -> str:
    """'ELİF ŞAHİN' → 'Elif Şahin' (Türkçe i/ı doğru)."""
    if not name.isupper():
        return name
    return " ".join(w[:1] + w[1:].replace("I", "ı").replace("İ", "i").lower() for w in name.split())


def import_text(text: str) -> dict:
    """Dekontu tek işlem olarak ekle (bakiye değişmez). Aynı dekont tekrar yüklenirse eklenmez; aynı gün ve tutardaki
    eski/eksik dekont kaydı (ör. kişisi okunamamış) yeni kayıt açılmadan güncellenir."""
    p = parse(text)
    acc_id = db.ensure_account(p["bank"]) if p["bank"] else None
    h = f"dekont:{p['date']}:{p['amount']:.2f}:{p['ref']}"
    who = db.known_counterparty(_title(p["counterparty"])) if p["counterparty"] else None
    cp = db.get_counterparty(who) if who else None
    category = (cp or {}).get("category") or "Kategorisiz"
    desc = " · ".join(x for x in (who, _title(p["purpose"]) if p["purpose"] else None) if x) or p["description"]
    with db.connect() as c:
        old = c.execute("SELECT id, import_hash, counterparty, category FROM entries WHERE import_hash LIKE ? ORDER BY id LIMIT 1",
                        (f"dekont:{p['date']}:{p['amount']:.2f}:%",)).fetchone()
        if old and old["import_hash"] == h and old["counterparty"]:
            return {**p, "counterparty": who, "status": "duplicate"}
        if old:
            c.execute("UPDATE entries SET import_hash = ?, description = ?, counterparty = COALESCE(?, counterparty), "
                      "category = CASE WHEN category IS NULL OR category = 'Kategorisiz' THEN ? ELSE category END, "
                      "account_id = COALESCE(account_id, ?), payment = COALESCE(payment, ?) WHERE id = ?",
                      (h, desc[:120], who, category, acc_id, "havale" if p["kind"] == "gider" else None, old["id"]))
            return {**p, "counterparty": who, "status": "updated"}
    db.create_entry({"entry_date": p["date"], "description": desc[:120], "amount": p["amount"], "kind": p["kind"],
                     "category": category, "payment": "havale" if p["kind"] == "gider" else None,
                     "note": "Dekont (bakiye değişmedi; döküm gelince birleşir)", "import_hash": h,
                     "counterparty": who, "account_id": acc_id})
    return {**p, "counterparty": who, "status": "saved"}
