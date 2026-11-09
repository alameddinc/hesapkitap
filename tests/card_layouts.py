"""Uydurma kart ekstresi düzenleri (yapay zekâ okumasından şablon öğrenme testleri için). Banka adları gerçek, düzenler
herkese açık bilgiden esinlenilmiş taklittir; kişi, kart numarası, işyeri ve tutarların hepsi uydurmadır.

Her düzen (metin, yapay zekâ yanıtı) üretir; yanıt app/parsers/ai.py PROMPT biçimindedir."""
from dataclasses import dataclass, field


def m(x: float) -> str:
    """Türk sayı biçimi: 1.234,56"""
    return f"{x:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


@dataclass
class Row:
    date: str                 # gg.aa.yyyy
    desc: str
    amount: float             # pozitif
    kind: str = "harcama"     # harcama | iade | odeme | faiz_ucret
    card: str | None = None   # ek kart maskesi (yapay zekâ yanıtında); None: ana kart
    fx: tuple[str, float] | None = None
    inst: tuple[int, int] | None = None      # (kaçıncı, toplam)
    inst_total: float | None = None          # satırdaki taksitli işlem toplamı (tutardan önce)
    points: float | None = None              # bonus / chip-para / puan sütunu (tutardan sonra)


@dataclass
class Layout:
    bank: str                 # yapay zekânın yazdığı banka adı
    card_no: str              # yapay zekânın yazdığı ana kart
    cut: str                  # gg.aa.yyyy
    due: str
    prev: float
    rows: list[Row]
    text: str = ""
    limit: float | None = None            # metindeki kart limiti (yanıtta da)
    available: bool = False               # metinde "Kullanılabilir Limit" var mı
    extra: dict = field(default_factory=dict)  # yanıtta üzerine yazılacak alanlar

    @property
    def total(self) -> float:
        return round(self.prev + sum(_sign(r.kind) * r.amount for r in self.rows), 2)

    def reply(self, **kw) -> dict:
        def iso(d: str) -> str:
            dd, mm, yy = d.replace("/", ".").split(".")
            return f"{yy if len(yy) == 4 else '20' + yy}-{mm}-{dd}"

        txns = [{"date": iso(r.date), "description": r.desc, "amount": r.amount, "type": r.kind, "card": r.card,
                 "installment_no": r.inst[0] if r.inst else None, "installments_total": r.inst[1] if r.inst else None,
                 "orig_amount": r.fx[1] if r.fx else None, "orig_currency": r.fx[0] if r.fx else None}
                for r in self.rows]
        paid = round(sum(r.amount for r in self.rows if r.kind == "odeme"), 2)
        activity = round(sum(_sign(r.kind) * r.amount for r in self.rows if r.kind != "odeme"), 2)
        out = {"bank": self.bank, "card_no": self.card_no, "period_end": iso(self.cut), "due_date": iso(self.due),
               "next_period_end": None, "next_due_date": None, "total_debt": self.total,
               "min_payment": round(self.total * 0.4, 2), "credit_limit": self.limit,
               "available_limit": round(self.limit - self.total, 2) if self.available else None,
               "previous_balance": self.prev, "payments": paid, "period_activity": activity, "transactions": txns}
        return {**out, **self.extra, **kw}


def _sign(kind: str) -> int:
    return -1 if kind in ("odeme", "iade") else 1


# ---------------------------------------------------------------- Akbank (Axess) benzeri
# sütunlu başlık (etiket satırı, altında değerler), Chip-Para sütunu, taksit satırında işlem toplamı + taksit tutarı,
# dövizli satır, ödeme "-" önekli, EK KART bölümü, sonda "Gelecek dönem taksitleri" tablosu (kesimden sonraki tarihler)

def akbank(cut="12.10.2026", due="22.10.2026", prev=4200.00, ek=True, future=True, year=2026, month=9) -> Layout:
    mm = f"{month:02d}"
    main = [Row(f"15.{mm}.{year}", "ODEMENIZ ICIN TESEKKUR EDERIZ", prev, "odeme"),
            Row(f"16.{mm}.{year}", "ORNEK MARKET ISTANBUL", 450.00, points=4.50),
            Row(f"20.0{month - 1}.{year}", "ORNEK ELEKTRONIK TAKSIT 2/6", 1000.00, inst=(2, 6), inst_total=6000.00,
                points=10.00),
            Row(f"22.{mm}.{year}", "ORNEK DIJITAL HIZMET", 857.50, fx=("USD", 25.00), points=8.58),
            Row(f"25.{mm}.{year}", "ORNEK GIYIM IADE", 120.00, "iade"),
            Row(f"28.{mm}.{year}", "ORNEK AKARYAKIT", 1500.00, points=15.00),
            Row(f"02.{int(mm) + 1:02d}.{year}", "ORNEK ECZANE", 236.40)]
    ek_rows = [Row(f"18.{mm}.{year}", "ORNEK KAFE", 180.00, card="5571***8812", points=1.80),
               Row(f"30.{mm}.{year}", "ORNEK KITAPCI", 342.10, card="5571***8812", points=3.42)] if ek else []
    lay = Layout("Akbank T.A.Ş.", "557123******4410", cut, due, prev, main + ek_rows, limit=30000.0, available=True)
    t = lay.total
    lines = ["AKBANK T.A.Ş.", "Axess Kredi Kartı Hesap Özeti", "Sayın DENEME KİŞİ", "Deneme Mah. Örnek Sok. No: 7 Test/İzmir",
             "Kart Numarası 5571 23** **** 4410",
             "Hesap Kesim Tarihi Son Ödeme Tarihi Dönem Borcu Asgari Ödeme Tutarı",
             f"{cut} {due} {m(t)} TL {m(round(t * 0.4, 2))} TL",
             f"Önceki Dönem Borcu {m(prev)} TL",
             f"Kart Limiti 30.000,00 TL Kullanılabilir Limit {m(30000 - t)} TL", "Nakit Avans Limiti 10.000,00 TL",
             "İşlem Tarihi Açıklama Tutar (TL) Chip-Para"]
    for r in main:
        lines.append(_line(r, credit="minus"))
    lines.append(f"Ana Kart Toplamı {m(sum(_sign(r.kind) * r.amount for r in main))}")
    if ek:
        lines.append("EK KART 5571 23** **** 8812 DENEME EKKART")
        lines += [_line(r, credit="minus") for r in ek_rows]
        lines.append(f"Ek Kart Toplamı {m(sum(r.amount for r in ek_rows))}")
    if future:
        lines += ["Gelecek Dönem Taksitleriniz", f"12.11.{year} ORNEK ELEKTRONIK TAKSIT 3/6 1.000,00",
                  f"12.12.{year} ORNEK ELEKTRONIK TAKSIT 4/6 1.000,00"]
    lines += ["Akdi faiz oranı aylık %3,50; gecikme faizi aylık %3,80.", "Chip-Para bakiyeniz 41,30"]
    lay.text = "\n".join(lines)
    return lay


# ---------------------------------------------------------------- İş Bankası (Maximum) benzeri
# "Etiket : değer" başlık, "Önceki Hesap Özeti Borcu" dönem borcundan ÖNCE, ödeme/iade sondaki "+" ile, MaxiPuan sütunu,
# eski tarihli taksit satırı "3. Taksit", faiz/BSMV/KKDF satırları

def isbank(cut="05.10.2026", due="15.10.2026", prev=2150.00) -> Layout:
    rows = [Row("08.09.2026", "HESABINIZDAN YAPILAN ODEME", prev, "odeme"),
            Row("12.09.2026", "ORNEK MARKET", 312.40, points=3.12),
            Row("15.07.2026", "ORNEK MOBILYA 3. TAKSIT", 500.00, inst=(3, 6)),
            Row("19.09.2026", "ORNEK SINEMA IADE", 90.00, "iade"),
            Row("24.09.2026", "ORNEK RESTORAN", 640.00, points=6.40),
            Row("01.10.2026", "FAIZ", 45.00, "faiz_ucret"),
            Row("01.10.2026", "BSMV", 6.75, "faiz_ucret"),
            Row("01.10.2026", "KKDF", 6.75, "faiz_ucret")]
    lay = Layout("Türkiye İş Bankası", "4506********2290", cut, due, prev, rows, limit=25000.0)
    t = lay.total
    lines = ["TÜRKİYE İŞ BANKASI A.Ş.", "Maximum Kart Hesap Özeti", "DENEME KİŞİ", "Kart No: 4506 34** **** 2290",
             f"Hesap Özeti Kesim Tarihi : {cut}", f"Son Ödeme Tarihi : {due}",
             f"Önceki Hesap Özeti Borcu : {m(prev)}", f"Hesap Özeti Borcu : {m(t)}",
             f"Asgari Ödeme Tutarı : {m(round(t * 0.4, 2))}", "Toplam Kart Limiti : 25.000,00",
             "Nakit Kredi Limiti : 5.000,00", "İşlem Tarihi Açıklama Tutar MaxiPuan"]
    lines += [_line(r, credit="trailplus") for r in rows]
    lines += ["Dönem İçi Kazanılan MaxiPuan 9,52", "Sonraki Hesap Özeti Kesim Tarihi : 05.11.2026"]
    lay.text = "\n".join(lines)
    return lay


# ---------------------------------------------------------------- Albaraka benzeri
# eğik çizgili tarih, "Ekstre Borcu", önceki borç satırı yok (ilk ekstre), kâr payı satırı, "Taksit" sütunu (2/4)

def albaraka(cut="08/10/2026", due="18/10/2026") -> Layout:
    rows = [Row("10/09/2026", "ORNEK MARKET", 700.00),
            Row("11/08/2026", "ORNEK BEYAZ ESYA 2/4", 1250.00, inst=(2, 4)),
            Row("14/09/2026", "ORNEK ONLINE", 412.30, fx=("EUR", 11.50)),
            Row("20/09/2026", "ORNEK IADE", 50.00, "iade"),
            Row("05/10/2026", "KAR PAYI", 52.40, "faiz_ucret"),
            Row("05/10/2026", "BSMV", 7.86, "faiz_ucret")]
    lay = Layout("Albaraka Türk", "5258********6631", cut, due, 0.0, rows, limit=20000.0)
    t = lay.total
    lines = ["Albaraka Türk Katılım Bankası A.Ş.", "Kredi Kartı Ekstresi", "Sayın DENEME KİŞİ",
             "Kart No 5258 12** **** 6631", f"Ekstre Kesim Tarihi {cut}", f"Son Ödeme Tarihi {due}",
             f"Ekstre Borcu {m(t)} TL", f"Asgari Ödeme Tutarı {m(round(t * 0.4, 2))} TL", "Kart Limiti 20.000,00 TL",
             "Tarih Açıklama Tutar"]
    lines += [_line(r, credit="minus") for r in rows]
    lay.text = "\n".join(lines)
    return lay


def _line(r: Row, credit: str) -> str:
    cols = [r.date, r.desc]
    if r.fx:
        cols.append(f"{r.fx[0]} {m(r.fx[1])}")
    if r.inst_total:
        cols.append(m(r.inst_total))
    v = m(r.amount)
    if r.kind in ("odeme", "iade"):
        v = {"minus": "-" + v, "trail": v + "-", "trailplus": v + " +"}[credit]
    cols.append(v)
    if r.points:
        cols.append(m(r.points))
    return " ".join(cols)
