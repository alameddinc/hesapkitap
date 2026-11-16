"""Sentetik belgeler (C: belge okuma uçtan uca). Gerçek kullanıcı belgesi YOK: her metin burada üretilir.

- kart ekstresi: VakıfBank biçiminde metin (app/parsers/vakifbank.py okur). /api/statements/upload'a b"SIMDOC:<anahtar>"
  baytları gider; testin kurduğu sahte extract_text (conftest'teki FIXTURE: yöntemiyle aynı) metni döndürür.
- hesap dökümü: noktalı virgüllü CSV (app/bank_import.read_rows/detect/parse okur); /api/bank-import/preview → içe aktar.
- dekont: tests/test_dekont.py biçiminde metin (app/dekont.py, app/dekont_parse.py okur); /api/dekont/parse.

Kişi adları, kart numaraları, IBAN'lar, şirket adları UYDURMADIR.
"""
from __future__ import annotations

import itertools
from datetime import date

_DOCS: dict[str, str] = {}
_seq = itertools.count(1)


def register(text: str) -> bytes:
    """Metni kaydet, yüklenecek sahte PDF baytlarını döndür."""
    key = f"d{next(_seq)}"
    _DOCS[key] = text
    return f"SIMDOC:{key}".encode()


def install(monkeypatch) -> None:
    """parsers.extract_text'i SIMDOC: baytlarını tanıyacak şekilde sar (conftest'in FIXTURE: sahtesinin üstüne)."""
    import app.parsers as parsers

    prev = parsers.extract_text

    def fake(data: bytes, password: str | None = None) -> str:
        if data.startswith(b"SIMDOC:"):
            return _DOCS[data[7:].decode()]
        return prev(data, password)

    monkeypatch.setattr(parsers, "extract_text", fake)
    # dekont/fatura okuması ayrı bir PDF metin yolundan geçer (main._try_text → parsers.extract_text)


def tr_amount(x: float) -> str:
    """6240.5 → '6.240,50' (Kasım 2026+ VakıfBank biçimi)."""
    s = f"{abs(x):,.2f}".replace(",", "_").replace(".", ",").replace("_", ".")
    return s


def d(x: date) -> str:
    return x.strftime("%d.%m.%Y")


def card_statement(owner: str, card_no: str, cut: date, due: date, next_cut: date, next_due: date,
                   prev_balance: float, payments: list[tuple[date, float]], purchases: list[tuple[date, str, float]],
                   limit: float = 150000.0) -> str:
    """VakıfBank kart ekstresi metni. card_no '5400***0001' biçiminde; metinde 5400********0001."""
    masked = card_no.replace("***", "********")
    activity = round(sum(a for _, _, a in purchases), 2)
    paid = round(sum(a for _, a in payments), 2)
    total = round(prev_balance + activity - paid, 2)
    minimum = round(max(total, 0) * 0.4, 2)
    lines = [
        "Kredi Kartı Hesap Özeti (TL)",
        "HESAP BİLGİLERİNİZ",
        f"Dönem Borcunuz : {tr_amount(total)} TL 0000000000000000",
        f"Asgari Ödeme Tutarı : {tr_amount(minimum)} TL",
        f"Son Ödeme Tarihi : {d(due)} Son Ödeme Tarihi: {d(due)}",
        "Şube Adı : ORNEK (0000)",
        f"VB Müşteri No : 000000000000 Sayın {owner}",
        f"Kart No : {masked}",
        f"Limitiniz : {tr_amount(limit)} TL",
        f"Hesap Kesim Tarihi : {d(cut)}",
        f"Bir Sonraki Hesap Kesim Tarihi : {d(next_cut)}",
        f"Bir Sonraki Son Ödeme Tarihi : {d(next_due)}",
        "Detaylı bilgi için www.vakifkart.com.tr adresini ziyaret edebilirsiniz.",
        "İŞLEM TARİHİ AÇIKLAMA TUTAR (TL) KALAN TAKSİT WORLDPUAN",
        f"{d(cut)} ÖNCEKİ DÖNEM HESAP ÖZETİ BAKİYENİZ {tr_amount(prev_balance)}",
    ]
    rows = [(pd, f"ÖDEMENİZ İÇİN TEŞEKKÜRLER +{tr_amount(a)}") for pd, a in payments]
    rows += [(pd, f"{desc} {tr_amount(a)}") for pd, desc, a in purchases]
    rows.sort(key=lambda r: r[0])
    lines += [f"{d(x)} {txt}" for x, txt in rows]
    lines += ["HESAP ÖZETİ",
              f"Önceki Hesap Bakiyesi {tr_amount(prev_balance)}",
              f"Dönem Içi Islemler {tr_amount(activity)}",
              "Toplam Faiz ve Ücretler 0,00",
              f"Ödemeler +{tr_amount(paid)}",
              f"Dönem Borcunuz {tr_amount(total)}"]
    return "\n".join(lines) + "\n"


def bank_csv(owner: str, rows: list[tuple[date, str, float, float]], reverse: bool = False) -> bytes:
    """Hesap dökümü CSV'si: (tarih, açıklama, tutar ±, bakiye). reverse: yeniden eskiye (bazı bankalar)."""
    head = ["Hesap Hareketleri;;;;", f"Müşteri: {owner};;;;", "Tarih;Açıklama;Tutar;Bakiye;Kanal"]
    body = []
    for x, desc, amt, bal in (reversed(rows) if reverse else rows):
        sign = "-" if amt < 0 else ""
        body.append(f"{d(x)};{desc};{sign}{tr_amount(amt)};{'-' if bal < 0 else ''}{tr_amount(bal)};MOBIL")
    return ("\n".join(head + body) + "\n").encode("utf-8")


def dekont(sender: str, sender_iban: str, receiver: str, receiver_iban: str, receiver_bank: str, on: date,
           amount: float, ref: str, purpose: str = "Aktarım", kind: str = "Giden FAST") -> str:
    def sp(iban: str) -> str:
        return " ".join(iban[i:i + 4] for i in range(0, len(iban), 4))

    return "\n".join([
        "DEKONT",
        f"MÜŞTERİ NO : 12345678 İŞLEM TARİHİ : {on.strftime('%d/%m/%Y')} - 10:15",
        f"TCKN/VKN/YKN : 1234567**** VALÖR TARİHİ : {on.strftime('%d/%m/%Y')}",
        f"IBAN : {sp(sender_iban)} ETTN NO : -",
        f"İŞLEM TÜRÜ : {kind}",
        f"GÖNDEREN : {sender}",
        f"GÖNDEREN IBAN : {sp(sender_iban)}",
        f"ALICI : {receiver}",
        f"ALICI IBAN : {sp(receiver_iban)}",
        f"ALICI BANKA : {receiver_bank}",
        f"ÖDEME AMACI : {purpose}",
        f"SORGU NO : {ref}",
        f"İŞLEM TUTARI (TL) : {amount:,.2f}",
    ]) + "\n"
