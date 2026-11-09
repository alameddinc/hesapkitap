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


# ---------------------------------------------------------------- Akbank PDF (yazı tipi kodlu) benzeri
# Gerçek Akbank PDF'inin metni Type3 yazı tipiyle çizilir (tests/type3_pdf.py); düzen: İngiliz sayı biçimi (1,250.00),
# eğik çizgili tarih, ödeme / iade sondaki "(-)" ile, taksit satırı "(6,000.00 TL) 2/6.Taksit 1,000.00 1,000.00x4",
# "Önceki Dönem Hesap Özeti Bakiyesi", "En Az Ödeme Tutarı", faiz "Toplam Dönem Faizi" tarihli satırı

def us(x: float) -> str:
    return f"{x:,.2f}"


def akbank_pdf(cut="12/10/2026", due="22/10/2026", prev=4200.00, month=9, year=2026) -> Layout:
    mm = f"{month:02d}"
    rows = [Row(f"15/{mm}/{year}", "Odemeniz icin tesekkurler", prev, "odeme"),
            Row(f"16/{mm}/{year}", "ORNEK MARKET ISTANBUL", 450.00),
            Row(f"20/{month - 1:02d}/{year}", "ORNEK ELEKTRONIK", 1000.00, inst=(2, 6), inst_total=6000.00),
            Row(f"22/{mm}/{year}", "ORNEK KAFE", 180.00),
            Row(f"25/{mm}/{year}", "ORNEK GIYIM", 120.00, "iade"),
            Row(f"28/{mm}/{year}", "ORNEK AKARYAKIT", 1500.00),
            Row(f"02/{int(mm) + 1:02d}/{year}", "ORNEK ECZANE", 236.40),
            Row(cut, "Toplam Donem Faizi", 37.25, "faiz_ucret")]
    lay = Layout("Akbank T.A.Ş.", "557123******4410", cut, due, prev, rows, limit=30000.0, available=True)
    t = lay.total
    lines = ["AKBANK T.A.S.", "Kart No 5571 23** **** 4410", f"Donem Borcu {us(t)} TL", f"Son Odeme Tarihi {due}",
             f"En Az Odeme Tutari {us(round(t * 0.4, 2))} TL", f"Hesap Kesim Tarihi {cut}",
             "Kart Limiti 30,000.00 TL", f"Kullanilabilir Kart Limiti {us(30000 - t)} TL",
             f"Onceki Donem Hesap Ozeti Bakiyesi {us(prev)}", "Islem Tarihi Aciklama Tutar"]
    for r in rows:
        v = us(r.amount) + ("(-)" if r.kind in ("odeme", "iade") else "")
        if r.inst:
            lines.append(f"{r.date} {r.desc} ({us(r.inst_total)} TL) {r.inst[0]}/{r.inst[1]}.Taksit {v} "
                         f"{us(r.amount)}x{r.inst[1] - r.inst[0]}")
        else:
            lines.append(f"{r.date} {r.desc} {v}")
    lines += [f"Genel Toplam {us(t)}", "Akdi faiz orani aylik %3,50"]
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


# ---------------------------------------------------------------- İş Bankası benzeri, PDF metninde tarih bitişik
# tarih açıklamaya bitişik ("12/09/2026ORNEK MARKET"), ödeme satırında tarihten sonra referans numarası bitişik
# ("08/09/20261234-567890 …"), taksit satırında "3/6 taksidi (işlem toplamı)", puan sütunu. Önceki borç satırı tarihsiz.

def isbank_glued(cut="05.10.2026", due="15.10.2026", prev=2150.00) -> Layout:
    rows = [Row("08/09/2026", "HESAPTAN ODEME", prev, "odeme"),
            Row("12/09/2026", "ORNEK MARKET", 312.40),
            Row("14/09/2026", "ORNEK KAFE", 85.00),
            Row("15/07/2026", "ORNEK MOBILYA", 500.00, inst=(3, 6)),
            Row("19/09/2026", "ORNEK SINEMA IADE", 90.00, "iade"),
            Row("24/09/2026", "ORNEK RESTORAN", 640.00, points=6.40)]
    lay = Layout("Türkiye İş Bankası", "4506********2290", cut, due, prev, rows, limit=25000.0)
    t = lay.total
    lines = ["KREDİ KARTI HESAP ÖZETİ", "TÜRKİYE İŞ BANKASI A.Ş.", "Kart No: 4506 34** **** 2290",
             f"Hesap Kesim Tarihi: {cut}", f"Son Ödeme Tarihi: {due}", f"Hesap Özeti Borcu: {m(t)} TL",
             f"Ödenmesi Gereken Asgari Tutar: {m(round(t * 0.4, 2))} TL", "İşlem", "Tarihi Açıklama Tutar Taksit MaxiPuan",
             f"******** DÖNEM ÖNCEKİ HESAP ÖZETİ BAKİYENİZ *** {m(prev)}",
             f"08/09/20261234-567890 HESAPTAN ODEME -{m(prev)}",
             "12/09/2026ORNEK MARKET 312,40", "14/09/2026ORNEK KAFE 85,00",
             "15/07/2026ORNEK MOBILYA 500,00 3/6 taksidi (3.000,00)", "19/09/2026ORNEK SINEMA IADE -90,00",
             "24/09/2026ORNEK RESTORAN 640,00 6,40", "Toplam Kart Limiti: 25.000,00 TL"]
    lay.text = "\n".join(lines)
    return lay


# ---------------------------------------------------------------- Albaraka benzeri, PDF metninde kelime arası boşluk yok
# etiketler bitişik ("HesapKesimTarihi :08Ekim2026"), işlem tarihi ay adıyla bitişik ("16Eylül2026"), ödeme ve iade
# "+" önekli, taksitli iade "+137,40 2/+687,00", ek kart bölümü başlığı bitişik, sonda ekstre denklemi satırı.

def albaraka_glued(cut="08/10/2026", due="18/10/2026", prev=1000.00) -> Layout:
    ek = "5258***7720"
    rows = [Row("15/09/2026", "ODEMENIZICINTESEKKURLER", prev, "odeme"),
            Row("16/09/2026", "ORNEKMARKET", 450.00),
            Row("20/09/2026", "ORNEKONLINE", 120.00),
            Row("22/07/2026", "ORNEKMOBILYATAKSIT", 300.00, inst=(3, 6)),
            Row("25/08/2026", "ORNEKELEKTRONIKTAKSITLIIADE", 137.40, "iade"),
            Row("18/09/2026", "ORNEKKAFE", 80.00, card=ek),
            Row("21/09/2026", "ORNEKKITAPCIIADE", 25.50, "iade", card=ek)]
    lay = Layout("Albaraka Türk", "5258********6631", cut, due, prev, rows, limit=20000.0)
    t = lay.total
    act = round(sum(_sign(r.kind) * r.amount for r in rows if r.kind != "odeme"), 2)
    ay = {"07": "Temmuz", "08": "Ağustos", "09": "Eylül", "10": "Ekim", "11": "Kasım"}

    def d(s: str) -> str:
        dd, mm, yy = s.split("/")
        return f"{dd}{ay[mm]}{yy}"

    lines = ["ALBARAKA TÜRK KATILIM BANKASI A.Ş.", "KREDİKARTIEKSTRENİZ", "KartNumarası :5258********6631",
             "KartLimiti(TL) :20.000,00", f"HesapKesimTarihi :{d(cut)}", f"SonÖdemeTarihi :{d(due)}",
             f"DönemBorcu(TL) :{m(t)}", f"AsgariÖdemeTutarı(TL) :{m(round(t * 0.4, 2))}",
             "İşlemTarihi Açıklama Tutar(TL) KalanTaksit/Tutar", f"ÖncekiDönemBorcu {m(prev)}",
             f"{d('15/09/2026')} ODEMENIZICINTESEKKURLER +{m(prev)}", f"{d('16/09/2026')} ORNEKMARKET 450,00",
             f"{d('20/09/2026')} ORNEKONLINE 120,00", f"{d('22/07/2026')} ORNEKMOBILYATAKSIT 300,00 3/1.200,00",
             "1.800,00TL'nin3/6'sı", f"{d('25/08/2026')} ORNEKELEKTRONIKTAKSITLIIADE +137,40 2/+687,00",
             f"HarcamaToplamı {m(450 + 120 + 300 - 137.40)}", "5258********7720DENEMEEKKART",
             f"{d('18/09/2026')} ORNEKKAFE 80,00", f"{d('21/09/2026')} ORNEKKITAPCIIADE +25,50",
             f"HarcamaToplamı {m(80 - 25.5)}", "DevredenBakiye DönemHareketleri Ödemeler DönemBorcunuz",
             f"{m(prev)} + {m(act)} - {m(prev).replace('.', '')} = {m(t)}"]
    lay.text = "\n".join(lines)
    return lay


# ---------------------------------------------------------------- Halkbank (Paraf) kart ekstresi benzeri
# PDF metninde hücre içinde kaydırılmış açıklama: uzun açıklama tarih + tutar satırının ÜSTÜNE ve ALTINA taşar, tarih
# satırında açıklama kalmaz ("05/10/2026 1,234.50 12.35"). İngiliz sayı biçimi, eğik çizgili tarih, ödeme / iade "+"
# önekli, ParafPara sütunu. Kart no "5571-23##-" / "####-4410/ AD" iki satıra bölünmüş (ek kart bölümü de). Taksit
# satırı ilk alışveriş tarihini taşır; "3600.00/9-" üstte, "09.Taksit" altta. "Önceki Dönem Devreden" etiketinin değeri
# alt satırda; dönem borcu "Hesap Bakiyesi" ("Ekstre Borcu" etiketinin yanındaki değer dönem içi tutar). Aynı adlı
# bankanın hesap dökümü şablonuyla (kind "account") karışmamalı.

def halkbank(cut="01/11/2026", due="11/11/2026", prev=4200.00, month=10, year=2026, ek=True) -> Layout:
    mm = f"{month:02d}"
    ek_card = "5571***8812"
    # (Row, biçim): wrap = açıklama üst / alt satırda; inline = açıklama tarih satırında
    main = [(Row(f"03/{mm}/{year}", "OTOMATIK ODEME - TESEKKUR EDERIZ", round(prev - 1200, 2), "odeme"), "wrap"),
            (Row(f"05/{mm}/{year}", "ORNEK MARKET", 312.40, points=3.12), "inline"),
            (Row(f"07/{mm}/{year}", "ORNEK MAGAZACILIK ANONIM SIRKETI", 1234.50, points=12.35), "wrap"),
            (Row(f"09/{mm}/{year}", "ORNEK GIYIM IADE", 120.00, "iade"), "wrap"),
            (Row(f"14/02/{year}", "ORNEK ELEKTRONIK", 400.00, inst=(9, 9), inst_total=3600.00), "inst"),
            (Row(f"12/{mm}/{year}", "ORNEK ULASIM HIZMETLERI VE TICARET", 85.75, points=0.86), "wrap"),
            (Row(f"20/06/{year}", "ORNEK MOBILYA", 150.00, inst=(5, 9), inst_total=1350.00), "inst"),
            (Row(f"21/{mm}/{year}", "ORNEK KAFE", 64.90, points=0.65), "inline"),
            (Row(f"25/{mm}/{year}", "ORNEK SAGLIK URUNLERI DAGITIM", 450.00, points=4.50), "wrap")]
    ek_rows = [(Row(f"18/{mm}/{year}", "ORNEK KITAPCILIK YAYINCILIK", 210.00, card=ek_card, points=2.10), "wrap"),
               (Row(f"02/09/{year}", "ORNEK SPOR", 300.00, card=ek_card, inst=(2, 3), inst_total=900.00), "inst")] \
        if ek else []
    rows = [r for r, _ in main + ek_rows]
    lay = Layout("Türkiye Halk Bankası", "557123******4410", cut, due, prev, rows, limit=60000.0, available=True)
    t = lay.total
    act = round(sum(_sign(r.kind) * r.amount for r in rows if r.kind != "odeme"), 2)

    def block(r: Row, how: str) -> list[str]:
        v = ("+ " if r.kind in ("odeme", "iade") else "") + us(r.amount)
        pts = us(r.points or 0)
        if how == "inline":
            return [f"{r.date} {r.desc} {v} {pts}"]
        if how == "inst":
            return [f"{r.inst_total:.2f}/{r.inst[1]}-", f"{r.date} {r.desc} {v} {pts}", f"{r.inst[0]:02d}.Taksit"]
        ws = r.desc.split()
        return [" ".join(ws[:-1]), f"{r.date} {v} {pts}", ws[-1]]

    dd, mo, yy = cut.split("/")
    lines = ["T. HALKBANKASI A.Ş.", "Paraf Kredi Kartı Hesap Özeti", "Sayın DENEME KİŞİ",
             "Deneme Mah. Örnek Sok. No:7 Test/Ankara",
             f"Hesap Kesim Tarihi : {cut}", f"Son Ödeme Tarihi : {due}", f"Hesap Bakiyesi : {us(t)} TL",
             f"Asgari Ödeme Tutarı : {us(round(t * 0.4, 2))} TL", "Toplam Kredi Limiti : 60,000.00 TL",
             "Nakit Avans Limiti : 12,000.00 TL", f"Kullanılabilir Limit : {us(60000 - t)} TL",
             "ParafPara Önceki Dönem Devreden", f": {us(prev)} TL : 0.00",
             f"Ekstre Borcu Dönem İçi Borç Tutarı : {us(act)} TL Kazanılan ParafPara",
             "İşlem Tarihi Açıklama Tutar ParafPara", "Kart No :5571-23##-", "####-4410/ DENEME KİŞİ"]
    for r, how in main:
        lines += block(r, how)
    if ek:
        lines += ["Kart No :5571-23##-", "####-8812/ DENEME EKKART",
                  f"Toplam Harcama : {us(sum(r.amount for r, _ in ek_rows))}"]
        for r, how in ek_rows:
            lines += block(r, how)
    lines += ["Sonraki Hesap Kesim Tarihi", f"{int(dd):02d}/{int(mo) % 12 + 1:02d}/{yy} -", "Bilgilendirme"]
    lay.text = "\n".join(lines)
    return lay


# ---------------------------------------------------------------- Garanti BBVA (Bonus) kart ekstresi benzeri
# Tarih ay adıyla ("2 Ekim 2026"), Türk sayı biçimi; ödeme sondaki "+" ile; harcanan Bonus kendi tarihli satırında
# sondaki "-" ile ("BONUS KULLANIMI … 50,00 -") ve dönem borcunu DEĞİŞTİRMEZ (Bonus sütunu). Kazanılan Bonus tutardan
# ÖNCE ("0,07 734,60"), bazı satırlarda tutar TL karşılığıyla iki kez ("450,00 TL 450,00"); taksit satırı
# "1.000,00x6=6.000,00 3.Taksit 1.000,00"; dövizli satır; ek kart bölümü. Önceki borç etiketi sözlük dışı ("Önceki
# Dönemden Aktarılan Tutar"); önceki borç yalnız sondaki denklem satırından ("… TL + … TL + 0,00 TL - … TL = … TL").
# Yapay zekâ (canlıda görüldüğü gibi) ödeme satırını yazmamış (yalnız özette) ve harcanan Bonus satırını iade yazmış.

_AY = {"08": "Ağustos", "09": "Eylül", "10": "Ekim", "11": "Kasım", "12": "Aralık"}


def _ay(s: str) -> str:
    dd, mm, yy = s.split(".")
    return f"{int(dd)} {_AY[mm]} {yy}"


def garanti(cut="27.10.2026", due="06.11.2026", prev=4200.00, month=10, ai_bonus_refund=True) -> Layout:
    mm, y = f"{month:02d}", 2026
    ek = "5571***8812"
    pay = Row(f"02.{mm}.{y}", "ODEMENIZ ICIN TESEKKUR EDERIZ", prev, "odeme")
    bonus = Row(f"03.{mm}.{y}", "BONUS KULLANIMI ORNEK MARKET", 50.00, "iade")  # yapay zekânın yanlış satırı
    main = [Row(f"03.{mm}.{y}", "ORNEK MARKET", 312.40),
            Row(f"05.{mm}.{y}", "ORNEK 12-34-AB ONLINE", 734.60, points=0.07),
            Row(f"08.{mm}.{y}", "ORNEK KAFE", 64.90)]
    ek_rows = [Row(f"15.{month - 2:02d}.{y}", "ORNEK ELEKTRONIK", 1000.00, card=ek, inst=(3, 6), inst_total=6000.00),
               Row(f"10.{mm}.{y}", "ORNEK GIYIM", 450.00, card=ek),
               Row(f"12.{mm}.{y}", "ORNEK KITAPCI", 180.00, card=ek),
               Row(f"14.{mm}.{y}", "ORNEK DIJITAL", 857.50, card=ek, fx=("USD", 25.00)),
               Row(f"20.{mm}.{y}", "ORNEK AKARYAKIT", 1500.00, card=ek)]
    lay = Layout("Garanti BBVA", "557123******4410", cut, due, prev, [pay, *main, *ek_rows], limit=40000.0)
    t = lay.total
    act = round(sum(r.amount for r in main + ek_rows), 2)
    mn = round(t * 0.4, 2)
    if ai_bonus_refund:
        rows = [bonus, *main, *ek_rows]
        lay.extra = {"transactions": Layout("", "", cut, due, prev, rows).reply()["transactions"], "payments": prev,
                     "period_activity": act}

    def line(r: Row) -> str:
        if r.inst:
            return f"{_ay(r.date)} {r.desc} {m(r.amount)}x{r.inst[1]}={m(r.inst_total)} {r.inst[0]}.Taksit {m(r.amount)}"
        if r.fx:
            return f"{_ay(r.date)} {r.desc} {m(r.fx[1])} {r.fx[0]} {m(r.amount)}"
        if r.points:
            return f"{_ay(r.date)} {r.desc} {m(r.points)} {m(r.amount)} Bonus"
        if r.desc in ("ORNEK GIYIM", "ORNEK KITAPCI"):
            return f"{_ay(r.date)} {r.desc} {m(r.amount)} TL {m(r.amount)}"
        return f"{_ay(r.date)} {r.desc} {m(r.amount)}"

    lines = ["GARANTİ BBVA", "Bonus Kredi Kartı Hesap Özeti", "Sayın DENEME KİŞİ", "Deneme Mah. Örnek Sok. No: 9 Test/Bursa",
             "Kart Numarası 5571 23** **** 4410", f"Hesap Kesim Tarihi {_ay(cut)}", f"Son Ödeme Tarihi {_ay(due)}",
             f"Dönem Borcunuz {m(t)} TL", f"Asgari Ödeme Tutarı {m(mn)} TL", "Kart Limiti 40.000,00 TL",
             "Bu Dönem Harcanan Bonus -50,00 TL", "Bu Dönem Kazanılan Bonus 0,07 TL",
             "İşlem Tarihi Açıklama Kalan Borç Taksit Bonus Tutar (TL)", f"Önceki Dönemden Aktarılan Tutar {m(prev)}",
             f"{_ay(pay.date)} ÖDEMENİZ İÇİN TEŞEKKÜR EDERİZ {m(prev)} +",
             f"{_ay(bonus.date)} {bonus.desc} {m(bonus.amount)} -"]
    lines += [line(r) for r in main]
    lines += ["Kart No: Bonus Ek Kart / 5571 23** **** 8812"] + [line(r) for r in ek_rows]
    lines += [f"Toplam -50,00 {m(t)}",
              "Önceki Bakiye Dönem Harcamaları Faiz ve Ücretler Ödemeler Dönem Borcunuz Asgari Ödeme Tutarınız",
              f"{m(prev)} TL + {m(act)} TL + 0,00 TL - {m(prev)} TL = {m(t)} TL {m(mn)} TL",
              "Akdi faiz oranı aylık %3,50."]
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
