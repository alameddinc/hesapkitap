"""Excel'e aktarma: işlemler, sabit kalemler, hesaplar, borç/alacak. Başka bir programa taşımak ya da kendi tablonu
yapmak için; geri yükleme için değil (onun için zip yedek).
"""
import csv
import io
from datetime import date

from openpyxl import Workbook
from openpyxl.styles import Font
from openpyxl.utils import get_column_letter

from app import db, finance, vat

KIND = {"harcama": "Kart harcaması", "iade": "İade", "faiz_ucret": "Faiz/ücret", "odeme": "Kart ödemesi",
        "gelir": "Gelir", "gider": "Gider", "borc": "Borç hareketi"}


def _sheet(wb: Workbook, title: str, head: list[str], rows: list[list]) -> None:
    ws = wb.create_sheet(title)
    ws.append(head)
    for c in ws[1]:
        c.font = Font(bold=True)
    for r in rows:
        ws.append(r)
    ws.freeze_panes = "A2"
    for i, h in enumerate(head, 1):
        width = max([len(str(h))] + [len(str(r[i - 1] or "")) for r in rows[:500]])
        ws.column_dimensions[get_column_letter(i)].width = min(60, max(10, width + 2))
    for i, h in enumerate(head, 1):
        if h.startswith("Tutar") or h.startswith("KDV") or h in ("Bakiye", "Kalan", "Toplam (₺)"):
            for c in ws.iter_cols(min_col=i, max_col=i, min_row=2):
                for cell in c:
                    cell.number_format = "#,##0.00"


def workbook() -> bytes:
    wb = Workbook()
    wb.remove(wb.active)
    rows = [r for r in finance.transactions(None)
            if not r.get("pending") and not r.get("deleted") and not r.get("linked") and r.get("source") != "estimate"]
    rows.sort(key=lambda r: r["date"])
    out_kinds = {"harcama", "faiz_ucret", "gider"}
    # "Alt kategori" sonda: önceki sütunların yeri değişmesin (kullanıcının tablosu/formülleri sütun sırasına bağlı)
    _sheet(wb, "İşlemler", ["Tarih", "Dönem", "Açıklama", "Tür", "Kategori", "Tutar (₺)", "Kart", "Hesap", "Kişi", "Taksit", "Not",
                            *TAX_HEAD, "Alt kategori"],
           [[r["date"][:10], r["month"], r["description"], KIND.get(r["kind"], r["kind"]), r["category"],
             round(-abs(r["amount"]) if r["kind"] in out_kinds else r["amount"], 2), r.get("card"), r.get("account") or r.get("bank"),
             r.get("counterparty"), r.get("installment"), r.get("note"), *_tax_cells(r), r.get("subcategory")] for r in rows])
    _sheet(wb, "Sabit kalemler", ["Açıklama", "Tür", "Kategori", "Tutar (₺)", "Gün", "Başlangıç", "Bitiş"],
           [[r["description"], r["kind"], r["category"], r["amount"], r["day_of_month"], r["start_month"], r["end_month"]]
            for r in db.list_recurring()])
    _sheet(wb, "Hesaplar", ["Hesap", "Tür", "Bakiye", "Tarih"],
           [[a["name"], a["kind"], a["current"] if a["current"] is not None else a["balance"], a["as_of"]] for a in db.list_accounts()])
    _sheet(wb, "Borç-alacak", ["Kişi", "Yön", "Birim", "Tutar", "Kalan", "Vade", "Not"],
           [[d["person"], "Alacak" if d["direction"] == "alacak" else "Borç", d["asset"], d["amount"], d.get("remaining"),
             d.get("due_date"), d.get("note")] for d in db.list_debts()])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


# ---------------------------------------------------------------- fişin vergi bilgisi (app/vat.py)

TAX_HEAD = ["Unvan", "VKN", "Vergi dairesi", "KDV (₺)", "KDV %1", "KDV %10", "KDV %20", "KDV diğer"]


def _tax_cells(r: dict) -> list:
    """Unvan, VKN, vergi dairesi, KDV toplamı ve oran oran KDV (kart satırında boş)."""
    rates = vat.by_rate(r.get("vat_lines"))
    return [r.get("legal_name"), r.get("tax_no"), r.get("tax_office"), r.get("vat_total"),
            *(rates.get(k) for k in (*vat.EXPORT_RATES, 0))]


PAYMENT = {"kredi_karti": "Kredi kartı", "nakit": "Nakit", "havale": "Havale / EFT", "kart": "Ön ödemeli kart"}
RECEIPT_HEAD = ["Tarih", "İşyeri", *TAX_HEAD[:3], "Toplam (₺)", *TAX_HEAD[4:], "KDV toplam (₺)", "Ödeme", "Kategori"]


def receipts(frm: date | None, to: date | None) -> list[dict]:
    """Muhasebeci için fiş listesi: fişten gelen giderler (fotoğrafı, kalemi ya da vergi bilgisi olan), tarih sırasıyla.
    Ortak defter üyesine görebildiği kayıtlar (db.list_entries)."""
    from app import book

    with db.connect() as c:
        itemized = {r[0] for r in c.execute("SELECT DISTINCT entry_id FROM receipt_items")}
        names = {a["id"]: a["name"] for a in book.filter_accounts(
            db.rows(c.execute("SELECT id, name FROM accounts")))}
    out = []
    for e in db.list_entries():
        if e["kind"] != "gider" or e.get("debt_id"):
            continue
        if e.get("counterparty") and not (e.get("tax_no") or e.get("vat_total") or e.get("legal_name") or e["id"] in itemized):
            continue  # kişiye havale (dekont fotoğrafı receipt_file'da saklansa da) fiş değil
        if not (e.get("receipt_file") or e.get("tax_no") or e.get("vat_total") or e.get("legal_name") or e["id"] in itemized):
            continue
        d = e["entry_date"][:10]
        if (frm and d < frm.isoformat()) or (to and d > to.isoformat()):
            continue
        out.append({**e, "vat_lines": vat.lines_of(e.get("vat_lines") or []),
                    "pay_label": _pay(e, names)})
    out.sort(key=lambda e: (e["entry_date"], e["id"]))
    return out


def _pay(e: dict, names: dict) -> str:
    label = PAYMENT.get(e.get("payment") or "", "")
    where = names.get(e.get("wallet_id")) if e.get("payment") == "kart" else names.get(e.get("account_id"))
    if e.get("payment") == "kredi_karti" and e.get("card_last4"):
        where = f"•••• {e['card_last4']}"
    if e.get("payment") == "kredi_karti" and (e.get("installments") or 1) > 1:
        where = " · ".join(x for x in (where, f"{e['installments']} taksit") if x)
    return " · ".join(x for x in (label, where) if x)


def _receipt_row(e: dict) -> list:
    rates = vat.by_rate(e["vat_lines"])
    return [e["entry_date"][:10], e["description"], e.get("legal_name"), e.get("tax_no"), e.get("tax_office"),
            round(e["amount"], 2), *(rates.get(k) for k in (*vat.EXPORT_RATES, 0)), e.get("vat_total"),
            e["pay_label"], e.get("category")]


AYLAR = ["Ocak", "Şubat", "Mart", "Nisan", "Mayıs", "Haziran", "Temmuz", "Ağustos", "Eylül", "Ekim", "Kasım", "Aralık"]
SUMMARY_HEAD = ["Ay", "Fiş sayısı", "Toplam (₺)", "KDV %1", "KDV %10", "KDV %20", "KDV diğer", "KDV toplam (₺)"]


def period_range(period: str, value: str) -> tuple[date, date]:
    """Hazır dönem: period "month" + "2026-11" → ayın ilk/son günü; "year" + "2026" → yılın ilk/son günü."""
    import calendar
    import re

    if period == "month" and (m := re.fullmatch(r"(\d{4})-(\d{2})", value or "")) and 1 <= int(m.group(2)) <= 12:
        y, mo = int(m.group(1)), int(m.group(2))
        return date(y, mo, 1), date(y, mo, calendar.monthrange(y, mo)[1])
    if period == "year" and re.fullmatch(r"\d{4}", value or ""):
        return date(int(value), 1, 1), date(int(value), 12, 31)
    raise ValueError("Dönem 'month' (2026-11) ya da 'year' (2026) olmalı")


def _months(frm: date | None, to: date | None, rows: list[dict]) -> list[str]:
    """Özet satırları: aralık belliyse aradaki her ay (boş ay da; en çok 36), değilse fişi olan aylar."""
    if frm and to and (to.year - frm.year) * 12 + to.month - frm.month < 36:
        out, y, m = [], frm.year, frm.month
        while (y, m) <= (to.year, to.month):
            out.append(f"{y:04d}-{m:02d}")
            y, m = (y + 1, 1) if m == 12 else (y, m + 1)
        return out
    return sorted({e["entry_date"][:7] for e in rows})


def vat_summary(entries: list[dict], months: list[str]) -> list[list]:
    """KDV özeti: ay ay fiş sayısı, toplam, oran oran KDV ve KDV toplamı (her ay bir satır)."""
    out = []
    for ym in months:
        es = [e for e in entries if e["entry_date"][:7] == ym]
        rates = [vat.by_rate(e["vat_lines"]) for e in es]
        out.append([f"{AYLAR[int(ym[5:7]) - 1]} {ym[:4]}", len(es), round(sum(e["amount"] for e in es), 2),
                    *(round(sum(r.get(k, 0) for r in rates), 2) for k in (*vat.EXPORT_RATES, 0)),
                    round(sum(e.get("vat_total") or 0 for e in es), 2)])
    return out


def receipts_xlsx(frm: date | None, to: date | None, total_label: str = "Toplam") -> bytes:
    """İki sayfa: "Fiş listesi" (fiş başına satır + toplam) ve "KDV özeti" (ay ay; yıllık seçimde 12 ay + yıl toplamı)."""
    wb = Workbook()
    wb.remove(wb.active)
    entries = receipts(frm, to)
    rows = [_receipt_row(e) for e in entries]
    if rows:
        rows.append(["Toplam", None, None, None, None, *(round(sum(r[i] or 0 for r in rows), 2) for i in range(5, 11)), None, None])
    _sheet(wb, "Fiş listesi", RECEIPT_HEAD, rows)
    summary = vat_summary(entries, _months(frm, to, entries))
    if summary:
        summary.append([total_label, *(round(sum(r[i] for r in summary), 2) if i > 1 else sum(r[i] for r in summary)
                                      for i in range(1, len(SUMMARY_HEAD)))])
    _sheet(wb, "KDV özeti", SUMMARY_HEAD, summary)
    for name, data in (("Fiş listesi", rows), ("KDV özeti", summary)):
        if data:
            ws = wb[name]
            for cell in ws[ws.max_row]:
                cell.font = Font(bold=True)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def receipts_csv(frm: date | None, to: date | None) -> bytes:
    """Excel'in Türkçe ayarıyla açılsın diye: noktalı virgül ayraçlı, ondalık virgül, UTF-8 BOM."""
    buf = io.StringIO()
    w = csv.writer(buf, delimiter=";")
    w.writerow(RECEIPT_HEAD)
    for r in (_receipt_row(e) for e in receipts(frm, to)):
        w.writerow([f"{x:.2f}".replace(".", ",") if isinstance(x, float) else "" if x is None else x for x in r])
    return ("\ufeff" + buf.getvalue()).encode("utf-8")
