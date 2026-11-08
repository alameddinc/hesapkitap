"""Reklam filmi için iki hazır hesap: ortak defter (Aile paketi), ~3 aylık UYDURMA veri.

Kullanım (hazır sürümün veri klasöründe; e-posta ve şifre komut satırından, koda yazılmaz):
    python -m app.film_seed --data-dir /app/data --owner-email <sahip> --member-email <üye> --password-file <dosya | ->
        [--plan-days 90] [--zakat] [--dry-run]
    python -m app.film_seed --data-dir /app/data --reset-film --owner-email … --member-email … --password-file …
    python -m app.film_seed --data-dir /app/data --remove          # yalnız bu betiğin açtığı hesapları sil
    python -m app.film_seed --data-dir /app/data --refresh-prices  # uydurma fiyatları bugüne taşı (çekimden önceki gün)
Şifre dosyası: tek satır (iki hesap aynı şifre) ya da iki satır (sahip, üye); "-" = standart girdi.

Ne yapar (senaryo: hesapkitap-cloud docs/reklam-filmi-menu-rehberi.md 5.3 "Ay sonu, ikimiz"):
- İki doğrulanmış hesap: sahip (Aile paketi, yöneticinin elle verdiği gibi: plan_source "manual", --plan-days gün;
  mağaza satın alması değil) ve üye ("Görür ve ekler", Aile koltuğuyla; davet kodu açılıp hemen kullanılır).
  Görünen ad e-postanın @ öncesidir ("ayse@…" → "ayse"). İkisinde "Ortak defterde yeni kayıt" ve "Alışveriş listesi"
  bildirimleri açık, fiyat paylaşımı kapalı.
- Sahibin (ortak) defterine: iki uydurma kart ("Örnek Bank" …0000 sahibin, "Mavi Bank" …1111 üyenin; maskeli numara,
  tam kart numarası yok), son iki dönemin ekstresi (3/6, 2/3, 4/6 taksit), taksitli buzdolabı (elle), maaşlar ve kira
  (sabit kalem), faturalar (önceki 3 ayı ödenmiş), bütçeler, hesaplar ve kumbara; "Market A" … "Market E"
  fişleri (ürün ürün, iki kişi dönüşümlü: Kim ne harcadı), fişe bağlı tamamlanmış alışveriş (tasarruf), etkin ortak liste ve kayıtlı plan.
  --zakat: Zekât modülü (rızası "film_seed" eylemiyle yazılır) ve birkaç varlık.
- Uydurma piyasa verisi: yalnız community_prices, katkı kimliği "film-…" (app/film_scope.py). Bu satırları yalnız film
  hesapları görür; film hesapları da gerçek (Market Fiyatı / topluluk) veriyi görmez. official_prices'a yazılmaz.
Kayıtlar sys_settings film_scope.KEY (hesap id'leri) ve INFO_KEY ile işaretlenir; --remove / --reset-film yalnız
bunları siler (hesaplar, klasörleri, davetleri, "film-" fiyat satırları). Şema değişmez; her yazma kısa bir işlem.
Tarihler çalıştırıldığı güne göredir: çekimden bir gün önce --reset-film ile yeniden kurmak en temizi (ayın ilk
günlerinde "bu ay" az dolu olur).
"""
import argparse
import hashlib
import json
import os
import sys
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
from statistics import median

from app import film_scope

CHAINS = film_scope.CHAINS
FACTOR = {"Market A": 0.93, "Market B": 1.06, "Market C": 0.97, "Market D": 1.02, "Market E": 0.95}
BAND_DAYS = 92      # piyasa bandı (grafik 90 gün); community_prices 90 günden eskisini kendisi siler
DAILY_DAYS = 14     # son 14 gün her gün (teklif, fiş notu, tasarruf); öncesi DAILY_STEP günde bir (bant)
DAILY_STEP = 3
DRIFT = 0.0006
OWNER, MEMBER = "owner", "member"

# (fiş başlığı, birim ("kg": tartılı), bugünün ortanca fiyatı ₺, bulunmayan zincirler) — genel ürün adları, marka yok
PRODUCTS: list[tuple[str, str | None, float, tuple[str, ...]]] = [
    ("SÜT 1 L", None, 34.0, ()), ("YUMURTA 30LU", None, 146.0, ()), ("EKMEK 250 G", None, 15.0, ()),
    ("AYÇİÇEK YAĞI 5 L", None, 385.0, ()), ("TOZ DETERJAN 6 KG", None, 335.0, ()),
    ("BEYAZ PEYNİR 500 G", None, 138.0, ()), ("DOMATES", "kg", 39.0, ()), ("ELMA", "kg", 44.0, ()),
    ("MAKARNA 500 G", None, 22.0, ()), ("PİRİNÇ 1 KG", None, 72.0, ()), ("KOLA 2,5 L", None, 62.0, ()),
    ("ÇAY 1 KG", None, 280.0, ()), ("TOZ ŞEKER 1 KG", None, 48.0, ()), ("UN 5 KG", None, 158.0, ()),
    ("YOĞURT 1 KG", None, 68.0, ()), ("TEREYAĞI 500 G", None, 228.0, ()), ("SU 5 L", None, 29.0, ()),
    ("MUZ", "kg", 75.0, ()), ("PATATES", "kg", 25.0, ()), ("SOĞAN", "kg", 20.0, ()),
    ("KAŞAR PEYNİR 400 G", None, 178.0, ()), ("ZEYTİN 500 G", None, 118.0, ()),
    ("BULAŞIK DETERJANI 1,5 L", None, 88.0, ()), ("TUVALET KAĞIDI 32Lİ", None, 298.0, ()),
    ("KAHVE 100 G", None, 158.0, ("Market A",)), ("SALÇA 830 G", None, 78.0, ()),
    ("MERCİMEK 1 KG", None, 64.0, ()), ("AYRAN 1 L", None, 38.0, ()), ("TAVUK BUT", "kg", 148.0, ("Market C",)),
    ("SALATALIK", "kg", 35.0, ()),
]
_BY = {t: (u, m, miss) for t, u, m, miss in PRODUCTS}

# (kaç gün önce, zincir, kim, [(başlık, adet / kilo)]) — süt hep Market B'den: "Önerimiz: Market A'da daha ucuz"
RECEIPTS: list[tuple[int, str, str, list[tuple[str, float]]]] = [
    (82, "Market B", OWNER, [("SÜT 1 L", 2), ("YUMURTA 30LU", 1), ("BEYAZ PEYNİR 500 G", 1), ("EKMEK 250 G", 2),
                             ("DOMATES", 1.2), ("ÇAY 1 KG", 1)]),
    (75, "Market C", MEMBER, [("MAKARNA 500 G", 3), ("AYÇİÇEK YAĞI 5 L", 1), ("KOLA 2,5 L", 1), ("PİRİNÇ 1 KG", 1),
                              ("SÜT 1 L", 2)]),
    (67, "Market B", OWNER, [("SÜT 1 L", 2), ("YUMURTA 30LU", 1), ("TEREYAĞI 500 G", 1), ("ELMA", 1.6),
                             ("KAŞAR PEYNİR 400 G", 1)]),
    (59, "Market E", MEMBER, [("EKMEK 250 G", 2), ("ZEYTİN 500 G", 1), ("SALÇA 830 G", 1), ("SU 5 L", 2),
                              ("PATATES", 2.0)]),
    (50, "Market B", OWNER, [("SÜT 1 L", 2), ("YUMURTA 30LU", 1), ("BEYAZ PEYNİR 500 G", 1), ("DOMATES", 1.4),
                             ("MUZ", 1.1)]),
    (42, "Market D", OWNER, [("TOZ DETERJAN 6 KG", 1), ("BULAŞIK DETERJANI 1,5 L", 1), ("TUVALET KAĞIDI 32Lİ", 1),
                             ("SÜT 1 L", 2)]),
    (34, "Market C", MEMBER, [("SÜT 1 L", 2), ("KOLA 2,5 L", 2), ("MAKARNA 500 G", 2), ("SOĞAN", 1.5),
                              ("YOĞURT 1 KG", 1)]),
    (26, "Market B", OWNER, [("SÜT 1 L", 2), ("YUMURTA 30LU", 1), ("EKMEK 250 G", 2), ("KAŞAR PEYNİR 400 G", 1),
                             ("ELMA", 1.8)]),
    (18, "Market E", MEMBER, [("SÜT 1 L", 2), ("AYRAN 1 L", 2), ("SALATALIK", 1.0), ("DOMATES", 1.3),
                              ("EKMEK 250 G", 1)]),
    (11, "Market B", OWNER, [("SÜT 1 L", 2), ("YUMURTA 30LU", 1), ("TEREYAĞI 500 G", 1), ("BEYAZ PEYNİR 500 G", 1),
                             ("ÇAY 1 KG", 1)]),
    (4, "Market B", OWNER, [("SÜT 1 L", 2), ("YUMURTA 30LU", 1), ("EKMEK 250 G", 2), ("DOMATES", 1.4),
                            ("KAŞAR PEYNİR 400 G", 1)]),
]
LAST_PRICEY = {"SÜT 1 L": 1.14, "YUMURTA 30LU": 1.13}  # son fişte piyasa ortancasına göre: "2 ürün piyasadan pahalı"
TRIP_CHAIN = "Market A"
TRIP = [("Ayçiçek yağı 5 L", "AYÇİÇEK YAĞI 5 L", 1), ("4x Makarna 500 g", "MAKARNA 500 G", 4),
        ("Pirinç 1 kg", "PİRİNÇ 1 KG", 1), ("Toz şeker 1 kg", "TOZ ŞEKER 1 KG", 1),
        ("Mercimek 1 kg", "MERCİMEK 1 KG", 1), ("Un 5 kg", "UN 5 KG", 1)]
LIST = [("6x Süt", MEMBER), ("2,5 kg Elma", MEMBER), ("1,5 kg Domates", MEMBER), ("Yumurta 30'lu", OWNER),
        ("Beyaz peynir 500 g", OWNER), ("2x Kola 2,5 L", OWNER), ("Kahve 100 g", OWNER), ("1 kg Tavuk but", OWNER)]

# kartlar: (banka, maskeli ana kart, işlem satırındaki kart, kesim günü, limit, kim) — numaralar uydurma ve maskeli
CARDS = [("Örnek Bank", "0000********0000", "0000***0000", 12, 60000.0, OWNER),
         ("Mavi Bank", "0000********1111", "0000***1111", 22, 45000.0, MEMBER)]
# taksitli kart harcamaları: (kart sırası, açıklama, kategori, toplam ₺, taksit sayısı, son ekstrede kaçıncı taksit)
CARD_INSTALLMENTS = [(0, "ORNEK ELEKTRONIK TABLET", "Elektronik", 9000.0, 6, 3),
                     (0, "ORNEK OPTIK GOZLUK", "Sağlık", 4200.0, 3, 2),
                     (1, "ORNEK ILETISIM TELEFON", "Telefon", 18000.0, 6, 4)]
# tek çekim kart harcamaları: (kart sırası, açıklama, kategori, ₺, kesimden kaç gün önce) — her dönem
CARD_SPEND = [(0, "ORNEK ECZANE", "Sağlık", 340.0, 20), (0, "ORNEK GIYIM", "Giyim", 1850.0, 14),
              (0, "ORNEK AKARYAKIT", "Ulaşım & Araç", 2100.0, 9), (0, "ORNEK RESTORAN", "Kafe & Restoran", 1240.0, 5),
              (1, "ORNEK KAHVE", "Kafe & Restoran", 118.0, 25), (1, "ORNEK KAHVE", "Kafe & Restoran", 96.0, 17),
              (1, "ORNEK KAHVE", "Kafe & Restoran", 134.0, 9), (1, "ORNEK KAHVE", "Kafe & Restoran", 102.0, 3),
              (1, "ORNEK SPOR SALONU", "Sağlık", 1200.0, 21), (1, "ORNEK KITAPCI", "Kitap & Hobi", 420.0, 12)]


# ---------------------------------------------------------------- fiyatlar

def _h(*parts) -> float:
    d = hashlib.sha256("|".join(map(str, parts)).encode()).digest()
    return int.from_bytes(d[:4], "big") / 2 ** 32


def shelf(p: float) -> float:
    if p < 10:
        return round(p, 2)
    whole = int(p)
    return round(whole + (0.5 if p - whole < 0.75 else 0.95), 2)


def has(title: str, chain: str) -> bool:
    return chain not in _BY[title][2]


def price(title: str, chain: str, days_ago: int) -> float:
    """Zincirin o günkü uydurma raf fiyatı (kararlı): ortanca × zincir çarpanı × ürün-zincir sapması × gün dalgası."""
    _u, med, _m = _BY[title]
    p = med * FACTOR[chain] * (0.97 + 0.06 * _h("film", title, chain)) * (0.99 + 0.02 * _h("film", title, chain, days_ago // 4))
    return shelf(p * (1 - DRIFT * days_ago))


def market_median(title: str, days_ago: int) -> float:
    return median(price(title, ch, days_ago) for ch in CHAINS if has(title, ch))


_keys: dict[str, str] = {}


def key_of(title: str) -> str:
    if title not in _keys:
        from app import product_groups

        g = product_groups.rule_group(title, unit=_BY[title][0])
        if not g:
            raise ValueError(f"film ürünü gruba çözülemedi: {title}")
        _keys[title] = g["key"]
    return _keys[title]


def community_days(today: date) -> list[int]:
    return [d for d in range(BAND_DAYS + 1) if d <= DAILY_DAYS or d % DAILY_STEP == 0]


def community_rows(today: date):
    """(chain, group_key, name_norm, unit_price, day, contributor) — zincir × ürün × gün başına üç uydurma kişi (−0,5 /
    0 / +0,5 ₺): kişi ortancası tam raf fiyatı, en az 3 kişi eşiği geçer."""
    from app import product_groups

    for t, *_ in PRODUCTS:
        k, nn = key_of(t), product_groups.name_norm(t)
        for ch in CHAINS:
            if not has(t, ch):
                continue
            for d in community_days(today):
                p = price(t, ch, d)
                day = (today - timedelta(days=d)).isoformat()
                for i, off in enumerate((-0.5, 0.0, 0.5)):
                    yield ch, k, nn, round(p + off, 2), day, f"{film_scope.CONTRIB_PREFIX}{ch[-1].lower()}{i}"


def write_prices(today: date) -> int:
    """Film fiyat satırlarını sil ve bugüne göre yeniden yaz (ürün başına kısa bir işlem). Yazılan satır sayısı."""
    from app import system

    with system.connect() as c:
        c.execute("DELETE FROM community_prices WHERE contributor LIKE ?", (f"{film_scope.CONTRIB_PREFIX}%",))
    n, batch = 0, []
    rows = community_rows(today)

    def flush():
        with system.connect() as c:
            c.executemany("INSERT INTO community_prices (chain, group_key, name_norm, unit_price, day, contributor) "
                          "VALUES (?, ?, ?, ?, ?, ?)", batch)

    for r in rows:
        batch.append(r)
        if len(batch) >= 2000:
            flush()
            n += len(batch)
            batch = []
    if batch:
        flush()
        n += len(batch)
    return n


# ---------------------------------------------------------------- veri planı (rol: owner / member / None = Ortak)

def _months_back(today: date, n: int) -> str:
    y, m = divmod(today.year * 12 + today.month - 1 - n, 12)
    return f"{y:04d}-{m + 1:02d}"


def _bill_history(bills: list, today: date, months: int = 3) -> list:
    """Her faturanın önceki aylardaki ödenmiş kopyaları (Faturalar: aylık seri, ortalama, önceki aya göre değişim).
    Tutar mevsime göre (app/demo.BILLS tablosu, bu ayki tutara ölçekli); son ödeme günü aynı (en çok 28)."""
    from app import demo, money

    table = {p: a for p, _c, _d, a in demo.BILLS}
    out = []
    for b, _paid in bills:
        due0 = date.fromisoformat(b["due_date"])
        amounts = table.get(b["provider"])
        for back in range(1, months + 1):
            y, m = divmod(due0.year * 12 + due0.month - 1 - back, 12)
            due = date(y, m + 1, min(due0.day, 28))
            amt = (b["amount"] * amounts[m + 1] / amounts[due0.month]) if isinstance(amounts, dict) else b["amount"]
            out.append(({**b, "amount": money.money(amt), "due_date": due.isoformat()},
                        (due - timedelta(days=3)).isoformat()))
    return out


def _kesim_dates(today: date, day: int) -> list[date]:
    """Bugünden önceki son iki kesim günü (eskiden yeniye)."""
    y, m = today.year, today.month
    last = date(y, m, day)
    if last >= today:
        y, m = divmod(y * 12 + m - 2, 12)
        last = date(y, m + 1, day)
    y, m = divmod(last.year * 12 + last.month - 2, 12)
    return [date(y, m + 1, day), last]


def _receipt_lines(day_ago: int, chain: str, lines: list[tuple[str, float]], last: bool) -> list[dict]:
    from app import money

    out = []
    for t, q in lines:
        up = price(t, chain, day_ago)
        if last:  # son fiş: yalnız LAST_PRICEY piyasadan belirgin pahalı, gerisi ortancanın en çok %4 üstünde
            med = market_median(t, day_ago)
            up = shelf(med * LAST_PRICEY[t]) if t in LAST_PRICEY else min(up, shelf(med * 1.03))
        out.append({"name": t, "qty": q, "unit": "kg" if _BY[t][0] == "kg" else "adet", "amount": money.money(up * q)})
    return out


def build(today: date, zakat: bool = False) -> dict:
    """Yazılacak her şey (kimlikler rol adıyla). Yazmaz; --dry-run bunu özetler."""
    from app import money
    from app.parsers.base import PAYMENT, PURCHASE, Statement, Transaction

    m0 = _months_back(today, 0)
    months = [_months_back(today, i) for i in (3, 2, 1, 0)]
    plan = {"today": today.isoformat(), "month": m0, "entries": [], "recurring": [], "statements": [], "bills": [],
            "budgets": {"Market & Gıda": 9000, "Kafe & Restoran": 2500, "Yemek Siparişi": 1500, "Giyim": 3000},
            "accounts": [("Örnek Bank", 38420.50, "banka"), ("Cüzdan", 1250.0, "nakit")],
            "jar": {"name": "Tatil", "goal": 40000, "initial": 12500}, "zakat": zakat}
    E = plan["entries"]

    def entry(who, d: date, desc, amount, cat, payment="nakit", kind="gider", **kw):
        if d > today:
            return None
        e = {"who": who, "data": {"entry_date": d.isoformat(), "description": desc, "amount": money.money(amount),
                                  "kind": kind, "category": cat, "payment": payment if kind == "gider" else None, **kw}}
        E.append(e)
        return e

    # sabit kalemler: maaşlar (kişi başı) ve kira (Ortak)
    for who, desc, amt, day, kind, cat in ((OWNER, "Maaş", 58000, 1, "gelir", "Maaş"),
                                           (MEMBER, "Maaş", 52000, 15, "gelir", "Maaş"),
                                           (None, "Kira", 24000, 5, "gider", "Kira & Aidat")):
        plan["recurring"].append({"who": who, "data": {"description": desc, "amount": amt, "kind": kind, "category": cat,
                                                       "day_of_month": day, "start_month": months[0]}})
        for mo in months[1:]:
            y, m = map(int, mo.split("-"))
            entry(who, date(y, m, day), desc, amt, cat, payment="havale", kind=kind, _recurring=len(plan["recurring"]) - 1)
    # elle girilen taksitli kart harcaması: bu ay 5/6 → "Buzdolabının son iki taksiti kaldı"
    y, m = map(int, _months_back(today, 4).split("-"))
    entry(OWNER, date(y, m, 18), "Örnek Beyaz Eşya · Buzdolabı", 24000, "Elektronik", payment="kredi_karti",
          installments=6)
    # kahveler, yemek siparişi, eczane (Kim ne harcadı halkası)
    for back in range(1, 90, 4):
        entry(MEMBER, today - timedelta(days=back), "Örnek Kahve", 85 + 10 * int(_h("k", back) * 6), "Kafe & Restoran")
    for back in (6, 20, 37, 52, 68, 83):
        entry(MEMBER, today - timedelta(days=back), "Örnek Döner", 260 + 20 * int(_h("d", back) * 5), "Yemek Siparişi")
    for back in (9, 40, 71):
        entry(OWNER, today - timedelta(days=back), "Örnek Kırtasiye", 180 + 30 * int(_h("r", back) * 4), "Eğitim")
    # market fişleri (ürün ürün)
    for d, ch, who, lines in RECEIPTS:
        items = _receipt_lines(d, ch, lines, last=d == RECEIPTS[-1][0])
        e = entry(who, today - timedelta(days=d), ch, sum(i["amount"] for i in items), "Market & Gıda")
        if e:
            e.update(items=items, chain=ch, days_ago=d)
    # faturalar: ikisi yaklaşan, biri ödenmiş (Ortak)
    plan["bills"] = [({"provider": "Örnek Elektrik", "amount": 1120.40, "due_date": (today + timedelta(days=6)).isoformat(),
                       "category": "Elektrik"}, None),
                     ({"provider": "Örnek İnternet", "amount": 499.90, "due_date": (today + timedelta(days=9)).isoformat(),
                       "category": "İnternet & TV"}, None),
                     ({"provider": "Örnek Doğalgaz", "amount": 1685.00, "due_date": (today - timedelta(days=12)).isoformat(),
                       "category": "Doğalgaz"}, (today - timedelta(days=14)).isoformat())]
    plan["bills"] += _bill_history(plan["bills"], today)
    # ekstreler: kart başına son iki dönem; taksitler dönem dönem ilerler; son dönemde bir fiş kartla ödendi (eşleşir)
    w1, w2 = _kesim_dates(today, CARDS[0][3])
    matched = next((e for e in reversed(E) if e.get("chain") == "Market B" and e["who"] == OWNER
                    and w1 < date.fromisoformat(e["data"]["entry_date"]) <= w2), None)
    for ci, (bank, card_no, short, kday, limit, who) in enumerate(CARDS):
        k1, k2 = _kesim_dates(today, kday)
        prev_debt = 0.0
        for si, (start, end) in enumerate(((k1 - timedelta(days=30), k1), (k1, k2))):
            txns = []
            if prev_debt:
                txns.append(Transaction(txn_date=start + timedelta(days=8), description="ÖDEMENİZ İÇİN TEŞEKKÜRLER",
                                        amount=-prev_debt, txn_type=PAYMENT, card=short))
            cats = {}
            for c_, desc, cat, amt, before in CARD_SPEND:
                if c_ == ci:
                    txns.append(Transaction(txn_date=end - timedelta(days=before), description=desc, amount=amt,
                                            txn_type=PURCHASE, card=short))
                    cats[desc] = cat
            for c_, desc, cat, total, n, last_no in CARD_INSTALLMENTS:
                if c_ != ci:
                    continue
                no = last_no - (1 - si)
                if no < 1:
                    continue
                per = money.money(total / n)
                txns.append(Transaction(txn_date=end - timedelta(days=27), description=f"{desc} {no}. Taksit",
                                        amount=per, txn_type=PURCHASE, card=short, installment_no=no,
                                        installments_left=n - no, installment_left_amount=per if n - no else None))
                cats[f"{desc} {no}. Taksit"] = cat
            if ci == 0 and si == 1 and matched:
                matched["data"].update(payment="kredi_karti", card_last4=card_no[-4:])
                txns.append(Transaction(txn_date=date.fromisoformat(matched["data"]["entry_date"]),
                                        description=matched["chain"].upper(), amount=matched["data"]["amount"],
                                        txn_type=PURCHASE, card=short))
                cats[matched["chain"].upper()] = "Market & Gıda"
            spend = money.money(sum(t.amount for t in txns if t.txn_type == PURCHASE))
            st = Statement(bank=bank, card_no=card_no, period_end=end, due_date=end + timedelta(days=10),
                           total_debt=spend, min_payment=money.money(spend * 0.4), credit_limit=limit,
                           previous_balance=prev_debt, period_activity=spend, payments=prev_debt,
                           next_period_end=end + timedelta(days=30), next_due_date=end + timedelta(days=40),
                           available_limit=money.money(limit - spend), transactions=txns)
            plan["statements"].append({"who": who, "stmt": st, "categories": cats})
            prev_debt = spend
    return plan


def summary(plan: dict) -> list[str]:
    from collections import Counter

    by = Counter(e["who"] or "ortak" for e in plan["entries"])
    rc = [e for e in plan["entries"] if e.get("items")]
    n_rows = sum(1 for _ in community_rows(date.fromisoformat(plan["today"])))
    out = [f"Bugün {plan['today']} · dönem {plan['month']}",
           f"Kayıtlar: {len(plan['entries'])} (sahip {by[OWNER]}, üye {by[MEMBER]}, ortak {by['ortak']})",
           f"Fişler: {len(rc)} ({', '.join(sorted({e['chain'] for e in rc}))}), {sum(len(e['items']) for e in rc)} kalem",
           f"Sabit kalemler: {', '.join(r['data']['description'] for r in plan['recurring'])}",
           "Ekstreler: " + ", ".join(f"{s['stmt'].bank} {s['stmt'].period_end.isoformat()} "
                                     f"({len(s['stmt'].transactions)} işlem)" for s in plan["statements"]),
           f"Faturalar: {', '.join(dict.fromkeys(b['provider'] for b, _ in plan['bills']))} ({len(plan['bills'])})",
           f"Bütçeler: {', '.join(f'{k} ₺{v}' for k, v in plan['budgets'].items())}",
           f"Alışveriş: tamamlanmış ({TRIP_CHAIN}, {len(TRIP)} ürün, fişe bağlı) + etkin liste {len(LIST)} satır + kayıtlı plan",
           f"Uydurma piyasa satırı (community_prices, film-…): {n_rows}",
           f"Zekât: {'evet' if plan['zakat'] else 'hayır'}"]
    return out


# ---------------------------------------------------------------- yazma

@contextmanager
def _quiet():
    """Tohumlarken bildirim, arka plan işi ve dosya yazımı yok (kimseye haber gitmez; ekstrenin PDF'i yoktur)."""
    from app import db, product_groups, push

    saved = []

    def patch(mod, name, fn):
        saved.append((mod, name, getattr(mod, name)))
        setattr(mod, name, fn)

    try:
        for name in ("after_list_add", "after_trip_complete", "after_statement"):
            patch(push, name, lambda *a, **k: None)
        patch(push, "notify", lambda *a, **k: [])
        patch(db, "_store_quietly", lambda *a, **k: False)
        patch(product_groups, "kick", lambda *a, **k: None)
        patch(product_groups, "kick_names", lambda *a, **k: None)
        yield
    finally:
        for mod, name, fn in reversed(saved):
            setattr(mod, name, fn)


def _info() -> dict:
    from app import system

    try:
        return json.loads(system.get(film_scope.INFO_KEY) or "{}")
    except ValueError:
        return {}


def _put_ids(ids: list[int], info: dict) -> None:
    from app import system

    system.put(film_scope.KEY, json.dumps(ids) if ids else None)
    system.put(film_scope.INFO_KEY, json.dumps(info, ensure_ascii=False) if ids else None)
    film_scope.forget()


def remove(log=print) -> list[int]:
    """Yalnız bu betiğin açtığı hesapları (film_scope.KEY) ve "film-" fiyat satırlarını sil. Silinen id'ler."""
    from app import system

    ids = sorted(film_scope.user_ids() | set(_info().get("ids", [])))
    emails = {e.lower() for e in _info().get("emails", [])}
    gone = []
    for uid in sorted(ids, key=lambda i: i != _info().get("member")):  # önce üye
        u = system.user(uid)
        if not u:
            continue
        if u["role"] == "admin" or (emails and u["username"].lower() not in emails):
            log(f"atlandı: #{uid} film hesabı gibi görünmüyor")
            continue
        system.delete_user(uid)
        system.purge_user_data(u)
        with system.connect() as c:
            c.execute("DELETE FROM invites WHERE member_of = ? OR used_by = ?", (uid, uid))
            for k in ("seat_prio", "seat_queue", "email_unverified", "pw_unset", "consent", "invite_pending"):
                c.execute("DELETE FROM sys_settings WHERE key = ?", (f"{k}:{uid}",))
        gone.append(uid)
        log(f"silindi: #{uid} {u['username']}")
    with system.connect() as c:
        n = c.execute("DELETE FROM community_prices WHERE contributor LIKE ?",
                      (f"{film_scope.CONTRIB_PREFIX}%",)).rowcount
    log(f"uydurma fiyat satırı silindi: {n}")
    _put_ids([], {})
    return gone


def create_accounts(owner_email: str, member_email: str, pw_owner: str, pw_member: str, plan_days: int) -> tuple[dict, dict]:
    from app import auth, prefs, push, system

    owner = auth.create_user(owner_email, pw_owner)
    info = {"owner": owner["id"], "emails": [owner["username"]], "ids": [owner["id"]],
            "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "by": "app.film_seed"}
    _put_ids([owner["id"]], info)  # yarıda kalırsa --remove bunu da bulsun
    until = (datetime.now(timezone.utc) + timedelta(days=plan_days)).isoformat(timespec="seconds")
    system.update_user(owner["id"], plan="aile", plan_until=until, plan_source="manual")  # yöneticinin elle verdiği gibi
    inv = system.create_invite(note="film", days=1, member_of=owner["id"], book_role="editor")
    member = auth.create_user(member_email, pw_member, member_of=owner["id"], book_role="editor")
    info.update(member=member["id"], emails=[owner["username"], member["username"]], ids=[owner["id"], member["id"]],
                plan_until=until, plan="aile (elle, ücretsiz; film test hesabı)")
    _put_ids([owner["id"], member["id"]], info)
    if not system.use_invite(inv["code"], member["id"]):
        raise RuntimeError("davet kullanılamadı")
    for u in (owner, member):
        push.set_prefs(u["id"], {"types": {"ortak": True, "liste": True}})
        prefs.set_price_share(u["id"], False)
    return system.user(owner["id"]), system.user(member["id"])


def seed_book(plan: dict, owner: dict, member: dict, log=print) -> dict:
    """Sahibin defterine planı yaz (fiyat satırları önce yazılmış olmalı). {trip_id, entry_id}"""
    from app import db, prefs, shopping as sl, shopping_trips, system

    today = date.fromisoformat(plan["today"])
    uid = {OWNER: owner["id"], MEMBER: member["id"], None: None}
    book = system.user_db_path(owner)
    with db.use_space(book, owner):
        prefs.set_modules({})  # özellik seçimi yapılmış sayılsın (ilk açılış sorusu çıkmasın)
        db.set_setting("onboarding_dismissed", "1")
        rec_ids = [db.create_recurring(r["data"]) for r in plan["recurring"]]
        for r, rid in zip(plan["recurring"], rec_ids):
            if r["who"]:
                db.set_spender_override("recurring", rid, uid[r["who"]])
        for s in plan["statements"]:
            st = s["stmt"]
            sid = db.save_statement(f"film:{st.card_no}:{st.period_end}".encode(),
                                    f"{st.bank.lower().replace(' ', '-')}-{st.period_end.isoformat()[:7]}.pdf", st)
            with db.connect() as c:
                tx = c.execute("SELECT id, description FROM transactions WHERE statement_id = ?", (sid,)).fetchall()
            for t in tx:
                if t["description"] in s["categories"]:
                    db.update_card_transaction(t["id"], {"category": s["categories"][t["description"]]})
        for bank, card_no, _s, _d, limit, who in CARDS:
            db.set_card_spender(card_no, uid[who])
            db.set_card_limit(card_no, limit)
        receipts = []
        for e in plan["entries"]:
            data = dict(e["data"])
            ri = data.pop("_recurring", None)
            if ri is not None:
                data["recurring_id"] = rec_ids[ri]
            data["created_by"] = uid[e["who"]]
            eid = db.create_entry(data)
            if e.get("items"):
                db.save_receipt_items(eid, [dict(i) for i in e["items"]], e["chain"], data["entry_date"], data["amount"])
                receipts.append(eid)
        db.auto_link()
        for b, paid in plan["bills"]:
            bid = db.create_bill(b)
            if paid:
                db.pay_bill(bid, b["amount"], paid, "havale")
        for cat, lim in plan["budgets"].items():
            db.set_budget(cat, lim)
        for name, bal, kind in plan["accounts"]:
            db.set_account_balance(name, bal, (today - timedelta(days=2)).isoformat(), "elle", kind)
        acc = next(a["id"] for a in db.list_accounts() if a["name"] == "Örnek Bank")
        j = plan["jar"]
        db.create_jar({"name": j["name"], "goal": j["goal"], "account_id": acc, "monthly": 2500, "auto_day": 2},
                      initial=j["initial"])
        db.auto_mark_paid()
        for s in db.list_statements():  # son ödeme günü geçmiş ekstre ödenmiş olsun (geciken ödeme uyarısı çıkmasın)
            if not s.get("paid") and s.get("due_date") and s["due_date"] < today.isoformat():
                db.mark_statement_paid(s["id"], s["total_debt"], (date.fromisoformat(s["due_date"]) -
                                                                   timedelta(days=1)).isoformat())
        if plan["zakat"]:
            _zakat(owner, today)
    # alışveriş: üye tamamladı (fişe bağlı, tasarruf), sonra ortak liste ve kayıtlı plan
    back = min(3, today.day - 1)
    tday = today - timedelta(days=back)
    with db.use_space(book, member):
        ids = [sl.add(text, uid=member["id"])["id"] for text, _t, _q in TRIP]
        shopping_trips.save_plan(uid=member["id"])
        for i in ids:
            sl.update(i, {"done": True})
        trip = shopping_trips.complete(carry_over=False, uid=member["id"])
        if back:
            with db.connect() as c:
                c.execute("UPDATE shopping_trips SET day = ?, completed_at = ? WHERE id = ?",
                          (tday.isoformat(), f"{tday.isoformat()} 18:20:00", trip["id"]))
        items = _receipt_lines(back, TRIP_CHAIN, [(t, q) for _x, t, q in TRIP], last=False)
        total = sum(i["amount"] for i in items)
        eid = db.create_entry({"entry_date": tday.isoformat(), "description": TRIP_CHAIN, "amount": total, "kind": "gider",
                               "category": "Market & Gıda", "payment": "nakit", "created_by": member["id"]})
        db.save_receipt_items(eid, items, TRIP_CHAIN, tday.isoformat(), total)
        linked = shopping_trips.link_receipt(eid, "trip", trip["id"])
        for text, who in LIST:
            if who == MEMBER:
                sl.add(text, uid=member["id"])
    with db.use_space(book, owner):
        for text, who in LIST:
            if who == OWNER:
                sl.add(text, uid=owner["id"])
    with db.use_space(book, member):
        shopping_trips.save_plan(uid=member["id"])
    for u in (member,):  # üyenin kendi ("Benim") defteri: boş, ilk açılış sorusu çıkmasın
        with db.use_space(system.own_db_path(u), {**u, "own_book": True}):
            prefs.set_modules({})
            db.set_setting("onboarding_dismissed", "1")
    log(f"alışveriş: geçmiş #{trip['id']} fişe bağlı ({(linked or {}).get('count', 0)} ürün, "
        f"tasarruf ₺{(linked or {}).get('saving')})")
    return {"trip_id": trip["id"], "entry_id": eid, "receipts": receipts}


def _zakat(owner: dict, today: date) -> None:
    from app import db, zakat

    from app import prefs

    db.set_setting("modules", json.dumps({**prefs.modules(), "zekat": True}))
    db.set_setting("consent:zekat", json.dumps({"at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                                                "version": prefs.CONSENT_VERSION, "user": owner["id"],
                                                "action": "film_seed", "title": prefs.CONSENT_TITLE.get("zekat"),
                                                "text": prefs.CONSENT["zekat"]}, ensure_ascii=False))
    me = zakat.people()[0]["id"]
    es = zakat.add_person("Mehmet")
    start = (today - timedelta(days=300)).isoformat()
    zakat.add_asset(me, "CEYREK", 8, False, "kasada", (today - timedelta(days=500)).isoformat(), 68000)
    zakat.add_asset(me, "USD", 800, False, None, (today - timedelta(days=400)).isoformat(), 27000)
    zakat.add_asset(es, "ALTIN_22", 40, True, "takı", (today - timedelta(days=900)).isoformat(), None)
    zakat.update_person(me, {"hawl_start": start})
    zakat.update_person(es, {"hawl_start": start})


# ---------------------------------------------------------------- komut satırı

def _configure(data_dir: str) -> None:
    from app import system

    d = os.path.abspath(data_dir)
    if not os.path.isfile(os.path.join(d, "system.db")):
        raise SystemExit(f"{d}/system.db yok: hazır sürümün veri klasörünü ver (--data-dir)")
    system.SYSTEM_DB = os.path.join(d, "system.db")  # kullanıcı defterleri bunun yanındaki users/<id>/ (ana defter kullanılmaz)
    film_scope.forget()


def _passwords(path: str) -> tuple[str, str]:
    raw = sys.stdin.read() if path == "-" else open(path, encoding="utf-8").read()
    lines = [x.strip() for x in raw.splitlines() if x.strip()]
    if not lines:
        raise SystemExit("şifre dosyası boş")
    return lines[0], lines[1] if len(lines) > 1 else lines[0]


def main(argv: list[str] | None = None, today: date | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m app.film_seed", description="Reklam filmi için iki hazır hesap (uydurma veri)")
    ap.add_argument("--data-dir", required=True)
    ap.add_argument("--owner-email")
    ap.add_argument("--member-email")
    ap.add_argument("--password-file")
    ap.add_argument("--plan-days", type=int, default=90)
    ap.add_argument("--zakat", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--reset-film", action="store_true", help="önce bu betiğin açtığı hesapları sil, sonra yeniden kur")
    ap.add_argument("--remove", action="store_true", help="yalnız bu betiğin açtığı hesapları ve fiyat satırlarını sil")
    ap.add_argument("--refresh-prices", action="store_true", help="yalnız uydurma fiyatları bugüne göre yeniden yaz")
    a = ap.parse_args(argv)
    _configure(a.data_dir)
    from app import plans, system

    today = today or date.today()
    if a.remove or a.refresh_prices:
        if a.dry_run:
            print(f"(deneme) film hesapları: {sorted(film_scope.user_ids())}")
            return 0
        if a.remove:
            remove()
        else:
            if not film_scope.user_ids():
                raise SystemExit("film hesabı yok; önce kur")
            print(f"uydurma fiyat satırı: {write_prices(today)}")
        return 0
    if not (a.owner_email and a.member_email and a.password_file):
        ap.error("--owner-email, --member-email ve --password-file gerekli")
    owner_email, member_email = (system.check_username(x) for x in (a.owner_email, a.member_email))
    if "@" not in owner_email or "@" not in member_email or owner_email == member_email:
        raise SystemExit("iki ayrı e-posta adresi gerekli")
    if not plans.hosted():
        print("uyarı: HESAPKITAP_HOSTED=1 değil; paket ve koltuk kuralları bu sunucuda uygulanmaz")
    film = film_scope.user_ids()
    for e in (owner_email, member_email):
        u = system.user_by_name(e)
        if u and u["id"] not in film:
            raise SystemExit(f"{e} başka (film dışı) bir hesapta: dokunulmadı")
        if u and not a.reset_film:
            raise SystemExit(f"{e} zaten film hesabı; yeniden kurmak için --reset-film")
    plan = build(today, zakat=a.zakat)
    if a.dry_run:
        print("(deneme, hiçbir şey yazılmadı)")
        print(f"Hesaplar: sahip {owner_email} (Aile, elle, {a.plan_days} gün) · üye {member_email} (Görür ve ekler)")
        if a.reset_film and film:
            print(f"Önce silinecek film hesapları: {sorted(film)}")
        for line in summary(plan):
            print(line)
        return 0
    pw_owner, pw_member = _passwords(a.password_file)
    from app import auth

    for pw in (pw_owner, pw_member):  # silmeden önce: zayıf şifre yarıda bırakmasın
        try:
            auth._check_strength(pw)
        except ValueError as e:
            raise SystemExit(str(e)) from e
    if a.reset_film:
        remove()
    with _quiet():
        owner, member = create_accounts(owner_email, member_email, pw_owner, pw_member, a.plan_days)
        print(f"hesaplar: sahip #{owner['id']} {owner['username']} · üye #{member['id']} {member['username']}")
        print(f"uydurma fiyat satırı: {write_prices(today)}")
        seed_book(plan, owner, member)
    for line in summary(plan):
        print(line)
    return 0


if __name__ == "__main__":
    sys.exit(main())
