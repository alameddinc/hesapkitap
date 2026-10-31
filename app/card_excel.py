"""Kredi kartı işlem dökümü Excel'i (ör. Kuveyt Türk "Toplu Ekstre": İşlem Tarihi · Açıklama · Tutar · Sektör).

Bankalar internet şubesinden geçmiş aylara ait kart hareketlerini çoğunlukla PDF ekstre olarak değil, böyle toplu bir
Excel olarak verir. Tutarlar işaretsizdir (harcama da ödeme de artı): hesap dökümü sanılırsa harcamalar GELİR olur.
Burada satırlar kart işlemine çevrilir ve kartın kesim gününe göre aylık ekstrelere bölünür. PDF'i zaten yüklenmiş
dönemler atlanır (iki kez sayılmaz).
"""
import re
from collections import defaultdict
from datetime import date, timedelta

from app.bank_import import parse_amount, parse_date, short_bank
from app.parsers.base import FEE, PAYMENT, PURCHASE, REFUND, Statement, Transaction

_CARD = re.compile(r"(\d{4})\d{0,2}\*{2,}(\d{4})")
# sektör → uygulama kategorisi (kural tabanlı kategori bulamazsa)
SECTOR_CATEGORY = [
    (r"RESTORAN|YEMEK YER|FAST FOOD|KAFE|CAFE|TATLI|PASTANE|FIRIN", "Kafe & Restoran"),
    (r"BAKKAL|S[ÜU]PERMARKET|MARKET|G[İI]DA|YIYECEK|YİYECEK", "Market & Gıda"),
    (r"SERV[İI]S [İI]STASYON|PETROL|AKARYAKIT|OTOPARK|GARAJ|ULA[ŞS]IM|TAKS[İI]", "Ulaşım & Araç"),
    (r"ECZANE|HASTANE|SA[ĞG]LIK|DOKTOR", "Sağlık"),
    (r"G[İI]Y[İI]M|AYAKKABI|TEKST[İI]L", "Giyim"),
    (r"REKLAM", "Reklam"),
    (r"SEYAHAT|HAVAYOLU|OTEL|KONAKLAMA", "Seyahat & Konaklama"),
    (r"[ÇC]OK KATLI MA[ĞG]AZA|ELEKTRON[İI]K", "Online Alışveriş"),
    (r"E[ĞG][İI]T[İI]M|OKUL|KURS", "Eğitim"),
]


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", str(s or "")).strip()


def detect(rows: list[list[str]]) -> dict | None:
    """Kart dökümü mü? {header_idx, cols, card_no, bank} ya da None."""
    head = " ".join(_norm(c) for r in rows[:8] for c in r)
    card = _CARD.search(head)
    for i, r in enumerate(rows[:15]):
        low = [_norm(c).lower() for c in r]
        if any("tarih" in c for c in low) and any(c.startswith("tutar") for c in low) and \
                (any("sektör" in c or "sektor" in c for c in low) or any("taksit" in c for c in low)
                 or any("puan" in c for c in low) or (card and "toplu ekstre" in head.lower())):
            if not card:
                return None
            cols = {"date": next(j for j, c in enumerate(low) if "tarih" in c),
                    "desc": next((j for j, c in enumerate(low) if "açıklama" in c or "aciklama" in c), 1),
                    "amount": next(j for j, c in enumerate(low) if c.startswith("tutar")),
                    "sector": next((j for j, c in enumerate(low) if "sektör" in c or "sektor" in c), None)}
            bank = short_bank(head)
            known = bank not in ("Banka hesabı", head.strip()) and len(bank) <= 25  # başlıkta tanınan banka adı yoksa boş
            return {"header_idx": i, "cols": cols, "card_no": f"{card.group(1)}***{card.group(2)}",
                    "bank": bank if known else ""}
    return None


def _kind(desc: str, sector: str) -> str:
    u = desc.upper()
    if not sector and re.search(r"ÖDEME|ODEME|PAYMENT", u):
        return PAYMENT
    if re.search(r"\bİADE\b|\bIADE\b|İPTAL|IPTAL", u):
        return REFUND
    if re.search(r"FA[İI]Z|BSMV|KKDF|AİDAT|AIDAT|ÜCRET|UCRET|VADE FARK", u) and not sector:
        return FEE
    return PURCHASE


def _period_end(d: date, cut_day: int) -> date:
    """Kesim günü: işlem o ayın kesimine kadarsa o ay, sonrasıysa bir sonraki ay."""
    if d.day <= cut_day:
        return d.replace(day=min(cut_day, 28))
    nxt = (d.replace(day=1) + timedelta(days=32)).replace(day=1)
    return nxt.replace(day=min(cut_day, 28))


def to_statements(rows: list[list[str]], info: dict, cut_day: int, bank: str, existing: set[str]) -> list[Statement]:
    """existing: kartın kayıtlı kesim tarihleri; kesimi ±5 gün içinde olan dönem (PDF ekstresi ya da önceki Excel)
    üretilmez (kesim hafta sonuna kaymış olabilir)."""
    c = info["cols"]
    groups: dict[date, list[Transaction]] = defaultdict(list)
    # Taksit satırı alışverişin İLK tarihiyle gelir ama dosya faturalanma sırasına göre dizilidir: taksit satırı,
    # sıradaki (taksitsiz) komşusunun dönemine yazılır.
    parsed = []
    for r in rows[info["header_idx"] + 1:]:
        if len(r) <= max(c["date"], c["amount"]):
            continue
        d = parse_date(_norm(r[c["date"]]))
        amt = parse_amount(_norm(r[c["amount"]]))
        if not d or amt is None:
            continue
        desc = _norm(r[c["desc"]]) if c["desc"] < len(r) else ""
        sector = _norm(r[c["sector"]]) if c["sector"] is not None and c["sector"] < len(r) else ""
        kind = _kind(desc, sector)
        inst = re.search(r"\b(\d{1,2})/(\d{1,2})\b", desc)
        n, total = (int(inst.group(1)), int(inst.group(2))) if inst else (None, None)
        signed = -abs(amt) if kind in (PAYMENT, REFUND) else abs(amt)
        parsed.append((d, bool(inst), Transaction(
            txn_date=d, description=desc[:120] or "?", amount=signed, txn_type=kind, card=info["card_no"],
            installment_no=n if n and total and n <= total else None,
            installments_left=(total - n) if n and total and n <= total else None,
            note=sector or None)))
    plain = [i for i, (_, is_inst, _) in enumerate(parsed) if not is_inst]
    for i, (d, is_inst, t) in enumerate(parsed):
        if is_inst and plain:
            near = min(plain, key=lambda j: (abs(j - i), j))  # en yakın taksitsiz satırın dönemi
            d = parsed[near][0]
        groups[_period_end(d, cut_day)].append(t)
    out = []
    known = [date.fromisoformat(x) for x in existing]
    for pe, txns in sorted(groups.items()):
        if any(abs((pe - k).days) <= 5 for k in known):  # o dönemin ekstresi zaten var
            continue
        charges = round(sum(t.amount for t in txns if t.txn_type != PAYMENT), 2)
        paid = round(-sum(t.amount for t in txns if t.txn_type == PAYMENT), 2)
        out.append(Statement(bank=bank, card_no=info["card_no"], period_end=pe, due_date=pe + timedelta(days=10),
                             total_debt=charges, min_payment=None, credit_limit=None, previous_balance=paid,
                             period_activity=charges, payments=paid, transactions=sorted(txns, key=lambda t: t.txn_date)))
    return out


def sector_category(sector: str) -> str | None:
    u = (sector or "").upper()
    for pat, cat in SECTOR_CATEGORY:
        if re.search(pat, u):
            return cat
    return None


def import_file(data: bytes, filename: str) -> dict | None:
    """Kart Excel'i ise içe aktar ve özet döndür; kart dökümü değilse None (hesap hareketi akışına gider)."""
    from app import bank_import, db

    rows = bank_import.read_rows(data, filename)
    info = detect(rows)
    if not info:
        return None
    last4 = info["card_no"][-4:]
    with db.connect() as c:
        sts = db.rows(c.execute("SELECT bank, card_no, period_end FROM statements WHERE substr(card_no, -4) = ?", (last4,)))
    bank = info["bank"] or (sts[0]["bank"] if sts else "") or "Kredi kartı"
    days = [int(s["period_end"][8:10]) for s in sts]
    cut = max(set(days), key=days.count) if days else 15  # kartın bilinen kesim günü; yoksa 15
    card_no = sts[0]["card_no"] if sts else info["card_no"]
    info = {**info, "card_no": card_no}
    stmts = to_statements(rows, info, cut, bank, {s["period_end"] for s in sts})
    saved = 0
    for st in stmts:
        key = f"kart-excel:{st.card_no}:{st.period_end}".encode()  # dönem başına tekil; tekrar yükleme eklemez
        if db.statement_exists(key, st, source="excel"):
            continue
        db.save_statement(key, filename, st, source="excel")
        saved += 1
    # kural tabanlı kategori bulunamayanlara sektörden kategori
    with db.connect() as c:
        for r in db.rows(c.execute("SELECT id, note FROM transactions WHERE category = 'Kategorisiz' AND note IS NOT NULL")):
            if cat := sector_category(r["note"]):
                c.execute("UPDATE transactions SET category = ? WHERE id = ?", (cat, r["id"]))
    db.auto_link()
    db.auto_mark_paid()
    n = sum(len(s.transactions) for s in stmts)
    return {"bank": bank, "card_no": card_no, "statements": saved, "transactions": n,
            "skipped_periods": len({s["period_end"] for s in sts}), "cut_day": cut,
            "range": [min(s.period_end for s in stmts).isoformat(), max(s.period_end for s in stmts).isoformat()] if stmts else None}
