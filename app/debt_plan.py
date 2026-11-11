"""Borç kapatma planı (GET/PUT /api/debt-plan, yetenek "debt_plan"): açık borçlar tek listede, ay ay ödeme takvimi.

İlke (sahibin kararı, değişmez): plan borçtan çıkmayı ve FAİZ ÖDEMEMEYİ hedefler (niyet: insanları faizden
kurtarmak). Hiçbir metin kredi, borç transferi, taksitlendirme, nakit avans, yapılandırma, erteleme ya da faizli bir
ürün önermez. Var olan faizli bir kredinin ERKEN KAPATILMASI faizden çıkıştır, önerilebilir (aşağıda 2b). Tamamı ödenebiliyorken "asgariyi öde" bir seçenek değildir, yalnız uyarıdır (kalan borca ne kadar faiz
işleyeceği). Banka/ortaklık teklifi yoktur. Söz tarihine yetişmeyen borçta yalnız "haber ver" ve kullanıcı basarsa
"Yeni tarihte anlaştık" (söz tarihini kaydeder) vardır; kesin tarihte tarih değiştirme önerilmez, gereken aylık tutar
söylenir.

Borcun koşulları (Borç / Alacak "borcum"; app/debt_schedule.py terms_of / strict_of; sütun boşsa eski davranış):
  alacaklı türü  kisi | vergi_sgk | banka | magaza ("Mağaza / senet") | diger
  ödeme koşulu   taksitli (ödeme planı) | parca (parça parça ödenebilir) | tek (tek seferde ödenecek) | bilinmiyor
                 (parca gibi)
  tarih türü     kesin (tarihinde ödenmesi şart: gecikirse bedeli/yaptırımı var) | söz (esnek). Varsayılan: planlı
                 borçta ve kurumda kesin, kişide söz.

Sıra (her ay; app/debt_plan.simulate):
  1. Yükümlülükler: kart borçları, taksitler (o ayın taksiti, geçmişte eksik kalan dahil) ve vadesi gelen kesin borçlar.
     Para yetiyorsa hepsi tamamen (kartın tamamı: faiz işlemesin), sonra son ödemesi sonraki aylarda olan kartlar.
     Yetmiyorsa önce her yükümlülüğün gecikmesi önlenir, tarih sırasıyla: kartta ekstredeki asgari, taksitte o ayın
     taksiti, kesin borçta vadesi bu aysa tutarı; kalan son ödemesi gelen kartlara (faizi en yüksek önce: faiz azalsın).
  2. Kesin tarihi ileride olan borçlar: tarihine yetişmek için bu ay ayrılması GEREKEN kadar (sonraki ayların tutarı,
     taksitleri ve kart borçları düşülerek hesaplanır; esnek borçların payı yalnız gerekirse kayar).
  2b. Faizli kredinin erken kapatılması (ödeme planlı borçta faiz "var" ve "bugün kapatma tutarı" girildiyse;
     app/debt_schedule.py payoff_*). Artan para BİRİKİM olarak ayrılır, yetince kredi tek seferde kapanır (satır
     anahtarı borc:<id>:kapat; kapanınca sonraki taksitler yok). Kısmi erken ödeme modellenmez: banka kısmi ödemeyle
     taksiti ya da vadeyi kendi hesabıyla değiştirir, kaydedilen plan artık doğru olmaz; tek seferde kapatma kaydedilen
     planla tutarlı ve basittir (metinde: kısmi ödeme yaparsan planı bankanın yeni planına göre güncelle).
       - Kartta kalan borç her zaman önce: kart 1. adımda kapanır; birikimdeki para kapatana kadar faizi azaltmaz,
         karta yatan her lira hemen azaltır (kredinin oranı yüksek olsa da). İki kredide oranı bilinen ve yüksek olan
         önce, sonra kalan faizi (kalan taksitlere oranla) büyük olan; sıradaki kredi öncekinin kapandığı ay başlar.
       - Kapatma tutarı her ödenen taksitle azalır (payoff_at): oran girildiyse anapara payı orandan, yoksa kalan faiz
         kalan taksitlerle orantılı azalır (yaklaşık). Erken kapatma kazandırmıyorsa (tutar ≥ kalan taksitler) ya da
         bu ayki artanla kapatma tutarı taksitler bitmeden birikmeyecekse birikim ayrılmaz; taksitler biterse birikim
         diğer borçlara döner.
       - Söz tarihi gelmiş (esnek) borç varken kredi bu ay kapanamıyorsa birikim kalan paranın en çok yarısını alır
         (DUE_SHARE); öbür yarısı söz tarihi gelmiş borçlara (biri tamamen beklemesin). Bu ay kapanabiliyorsa önce kredi.
       - Gösterim debts[].payoff: "Krediyi Mart 2027'de kapatırsan yaklaşık ₺… faizden kurtulursun (kalan taksitler
         ₺…, kapatma tutarı ₺…)"; tutar girilmemişse nötr ipucu "Bankana bugün kapatma tutarını sor; …". Kapatma ayında
         "Kapattım" (POST /api/debts/{id}/pay close_early): borç ödenen toplamla kapanır.
  3. Kalan para tarih sırasıyla (sıralama "tarih"): söz/vade tarihi aynı aya düşen borçlar bir grup; tarihsizler en
     sonda, en eskiden. Grupta para hepsine yetiyorsa hepsi; yetmiyorsa kapanabilenler kapanır (önce "tek seferde"
     olan, sonra altın/döviz borcu, sonra küçük), kalan öbürlerine: parça ödenebilene anlamlı pay, tek seferde
     ödenecek olana birikim. Hiçbirine yetmiyorsa paylar orantılı ama anlamlı (MIN_PAY / ROUND_TO; pay küçük
     kalırsa en büyük borç o ay bekler: ufalama yok). Grubun artanı sonraki gruba. "kucuk": borç borç, en küçük önce.
     Orantılı bölme YALNIZ aynı gruptaki hiçbir borca yetmeyen durumda olur (eski "faizi önle"deki gibi bütün
     borçlara yayılmaz: faizsiz borçta sıra bitiş tarihini değiştirmez, yalnız kimin bekleyeceğini belirler).
  4. "Tek seferde" borç: ayrılan para birikimdir (months[].payments[].saving); borcun kalanı ödeme yapılana kadar
     düşmez; ödeme ayı birikimin yettiği ay ("X için biriktir: Şubat'ta ₺… birikmiş olur, tek seferde öde").
  5. Altın/döviz borcu kendi biriminde gösterilir, TL karşılığı güncel fiyattan (app/zakat.prices; elle girilen gram
     fiyatı dahil): "TL karşılığı fiyata göre değişir". Fiyat yoksa plana girmez (excluded).
  6. Ay içi gün (yalnız not): kesin tarihli taksit/borcun günü Planlama'daki maaş gününden önceyse "bu ödemeyi önceki
     aydan ayır" notu. Gün gün nakit akışı kurulmaz.

Yetişmeyen tarih (debts[].late): söz/vade, o tarihte açık kalan, tahmini ödeme ayı. Sözde "Yeni tarihte anlaştık"
(istemci yalnız kullanıcı basınca PATCH /api/debts/{id} {due_date}); kesinde "Bu tarih kesin; yetişmek için ayda ₺…
gerekir." (en az aylık tutar, plan yeniden hesaplanarak bulunur).

Zararı azaltma (yalnız tutar bir kartın tamamına son ödeme gününe kadar yetmiyorsa; kart satırında harm): önce son
ödeme gününden önce en az ekstredeki asgari tutar (gecikme faizi işlemesin), asgarinin üstündeki her lira kalan borcun
akdi faizini azaltır; karşılaştırma: yalnız asgari / planlanan / tamamı (₺0). Yasal asgari oranları kodda yok: tutar
ekstreden okunur, sonraki aylar için ekstrenin kendi asgari/borç oranı kullanılır (yaklaşık).

Planı başlat (POST /api/debt-plan/commit, ayar "debt_plan_commit"): o günkü borçlarla ay ay hedefler kaydedilir.
Sonraki aylarda ödenen (ekstre ve borç ödemeleri, plandaki borç başına hedefle sınırlı) birikmiş hedefle karşılaştırılır:
plana uygun / gerisinde (bu ay ne kadar ödersen yetişirsin) / önünde (daha erken bitebilir). Geçmiş aylardaki birikim
hedefleri ödenene kadar "biriktirildi" sayılır (commitment.saved). Plana yeni borç girdiyse ya da geçen ayların hedefi
eksik kaldıysa "Plan değişti" uyarısı; yeniden oluşturmak eski planı geçmişe taşır. Önizleme (?monthly, ?order)
kaydedilen planı değiştirmez.

Borçlar kendiliğinden toplanır:
  kart      her kartın son ekstresinin ödenmemiş kısmı (finance.payables: kısmi ödeme düşülmüş; eski ekstreler yeni
            ekstrenin toplamında olduğu için ayrıca sayılmaz). Gelecek taksitler bilgi olarak (her ay ekstreye eklenir).
  diğer     Borç / Alacak'taki açık "borcum" kayıtları (kind "person": kişi ya da kurum; TL, fiyatı varsa altın/döviz).
  taksit    Ödeme planı olan borç (kind "fixed"; app/debt_schedule.py): plan bunu kaydedilmiş bir yükümlülük olarak
            okur; taksitler tek tek öne çekilmez. Faizliyse ve kapatma tutarı girildiyse erken kapatma (2b).

Tutarlar: diğer borç ödemeleri kişi başına toplanır ve ROUND_TO katlarına yuvarlanır; kişi başı en az MIN_PAY (kalan
daha azsa kalanın tamamı); FOLD'dan küçük kalan bırakılmaz. Kart ve taksit tutarları tam (kuruşuyla) kalır. Yanıtta
this_month.groups ve months[].groups aynı kişinin borçlarını tek satırda toplar (items / payments borç borç kalır).

Aylık tutar: her zamanki harcamaların (taksitler dahil) karşılandıktan sonra borçlara ayırabileceğin para. Önerisi
Planlama'nın "her zamanki harcamanla" artanı (months[].net_with_estimate ortalaması), hiçbir zaman eksi değil.

Sıralama seçenekleri: "tarih" (Söz tarihine göre, varsayılan) ve "kucuk" (En küçükten başla). Eski kayıtlı "faiz" ve
"soz" "tarih" sayılır (API eski değerleri kabul eder). Her seçenekte kartlar ve kesin tarihler önce.

Takvim takvim ayıdır (bu ay = 0). Bu ay borçlara zaten ödenen, bu ayın tutarından düşülür. Kart borcu son ödeme
gününün ayında kapanmazsa kalanına aylık faiz (ekstredeki "Oran:%x" biliniyorsa o, yoksa genel varsayım) + vergiler
eklenir: bu yalnız "bu tutarla ne olur" uyarısıdır; plan böyle bir durumu çözüm olarak sunmaz, kapatacak tutarı söyler.
Aynı hesap (monthly_interest) Kartlar ekranındaki faiz satırını da verir (card_interest; /api/cards/summary ve
/api/payables, sunucu hesaplar): son ödeme günü geçmiş kalan "bu ay yaklaşık ₺Y faiz ve vergi eklenir; ödemedikçe
her ay büyür", gün gelmemiş kısmi ödemede "kalanı son ödeme gününe kadar öde: yaklaşık ₺Y faizden kurtulursun",
yoksa "tamamını ödersen faiz işlemez".

Borca bağlı kumbara (yetenek "debt_jar"; Premium "kumbara" kuralı, plan ücretsiz kalır): "tek seferde" borç ve faizli
kredinin erken kapatılması birikimi bir kumbarada tutulabilir (jars.debt_id; bir borca en çok bir açık kumbara). Kumbara
varsa plan birikimi kumbaranın GERÇEK bakiyesinden başlatır (simulate: tek seferde borçta saved, kredide pot; kalan
aylık birikim buna göre yeniden hesaplanır), bu ay kumbaraya ayrılan bu ayın tutarından düşer (ödenen gibi) ve plan
takibi (commitment) varsayılan birikim yerine kumbaradaki bakiyeyi sayar. Kumbara kapanır/silinirse ya da bağı
kaldırılırsa plan eski davranışa döner. debts[].jar: offer ("Kumbara aç") | locked (Premium değil: nötr öneri) |
linked (bakiye, "Kumbaradan öde" pay); this_month.items[] jar_action ("Kumbaraya ayır": POST /api/jars/{id}/moves) ve
jar_pay ("Kumbaradan öde": POST /api/jars/{id}/pay-debt). Değer ilkesi aynı: metinler faize yönlendirmez.

Ortak defter: kartlar ve hesaplar db süzgeçlerinden geçer (gizli kart yok); Borç / Alacak sayfası üyeye kapalıysa
diğer borçlar plana girmez; faiz gizliyse faiz tutarı yazılmaz. Ayar (aylık tutar, sıralama) defterindir: "Görür ve
ekler" ya da tam yetkili üye değiştirir (Aile birlikte planlar), "Sadece görür" yalnız önizler.
"""
import json
import math
import re
from datetime import date

from app import db, debt_schedule, money

ORDERS = ("tarih", "kucuk")
LEGACY_ORDERS = {"faiz": "tarih", "soz": "tarih"}  # eski kayıtlı ayarlar ve eski istemciler
ORDER_INFO = {
    "tarih": ("Söz tarihine göre", "Önce kart borçları ve tarihi kesin olanlar: gecikmesin, faiz işlemesin. Sonra söz "
                                   "verdiğin tarihi en yakın olan; aynı aya düşenlerde kapanabilen önce kapanır. Tarihi "
                                   "olmayanlar en sonda, en eskiden."),
    "kucuk": ("En küçükten başla", "Önce kart borçları ve tarihi kesin olanlar. Sonra en küçük borç; her kapanan borç "
                                   "bir sonrakine güç verir."),
}
GENERIC_RATE = 0.035   # aylık akdi faiz varsayımı (ekstrede oran yoksa); metinde "yaklaşık" yazılır
TAX = 0.30             # faize eklenen vergiler: BSMV %15 + KKDF %15
HORIZON = 120          # en çok 10 yıl
SETTING = "debt_plan"  # defter ayarı: {"monthly": float | null, "order": "tarih"}
COMMIT = "debt_plan_commit"  # defter ayarı: {"current": anlık görüntü | null, "history": [{started_at, ...}]}
HISTORY = 5
TOL = 1.0              # plan takibinde 1 TL'lik fark yok sayılır
ROUND_TO = 50          # diğer borç ödemeleri bu katlara yuvarlanır (kart ve taksit tutarı tam kalır)
MIN_PAY = 250          # kişi başı en az anlamlı ödeme (kalan daha azsa kalanın tamamı)
FOLD = 100             # bundan küçük kalan bırakılmaz: aynı ayın ödemesine eklenir (aylık tutarı en çok bu kadar aşar)
PK = ":kapat"          # erken kapatma satırının anahtar eki (borc:<id>:kapat): taksit satırından ayrı
DUE_SHARE = 0.5        # söz tarihi gelmiş borç varken erken kapatma birikimi kalan paranın en çok bu kadarını alır
# "Ocak'ta" gibi bulunma eki (ayın son sesine göre)
_AY_EK = ("'ta", "'ta", "'ta", "'da", "'ta", "'da", "'da", "'ta", "'de", "'de", "'da", "'ta")
_YIL_EK = {"0": "'da", "1": "'de", "2": "'de", "3": "'te", "4": "'te", "5": "'te", "6": "'da", "7": "'de", "8": "'de",
           "9": "'da"}
FAIZ_INFO = ("Faiz neden işler: kartta son ödeme gününe kadar ödenmeyen kalan borca akdi (alışveriş) faizi işler. "
             "Asgariyi de ödemezsen üstüne gecikme faizi eklenir. Faiz kalan borca işlediği için ne kadar çok "
             "yatırırsan o kadar az faiz çıkar; tamamını ödersen hiç çıkmaz.")
PRICE_NOTE = "TL karşılığı fiyata göre değişir."
AYLAR = ("Ocak", "Şubat", "Mart", "Nisan", "Mayıs", "Haziran", "Temmuz", "Ağustos", "Eylül", "Ekim", "Kasım", "Aralık")
_RATE_RE = re.compile(r"ORAN\s*[:=]?\s*%?\s*(\d{1,2}[.,]\d{1,3})")


def _tl(x: float) -> str:
    return money.tl(x, trim=True)


def _r(x: float) -> float:
    return money.money(x)


def _ym(d: date) -> str:
    return f"{d.year:04d}-{d.month:02d}"


def _add(month: str, n: int) -> str:
    y, m = map(int, month.split("-"))
    y, m = divmod(y * 12 + m - 1 + n, 12)
    return f"{y:04d}-{m + 1:02d}"


def month_label(month: str) -> str:
    y, m = month.split("-")
    return f"{AYLAR[int(m) - 1]} {y}"


def _day_label(iso: str) -> str:
    d = date.fromisoformat(iso[:10])
    return f"{d.day} {AYLAR[d.month - 1]}"


def month_in(month: str, today_month: str | None = None) -> str:
    """"Ocak'ta" / "Ocak 2028'de" (yıl bu yıl değilse)."""
    y, m = month.split("-")
    if today_month and today_month[:4] == y:
        return AYLAR[int(m) - 1] + _AY_EK[int(m) - 1]
    return f"{AYLAR[int(m) - 1]} {y}{_YIL_EK[y[-1]]}"


def _months_between(a: str, b: str) -> int:
    ya, ma = map(int, a.split("-"))
    yb, mb = map(int, b.split("-"))
    return (yb - ya) * 12 + (mb - ma)


# ---------------------------------------------------------------- ayar



def _loc(n: int) -> str:
    """Gün bulunma eki: 5'inde, 2'sinde, 3'ünde, 6'sında, 9'unda, 10'unda, 20'sinde."""
    ek = debt_schedule._ek(n)
    return f"{n}'{ek}{'nda' if ek[-1] in 'ıu' else 'nde'}"


# ---------------------------------------------------------------- ayar

def norm_order(order: str | None) -> str | None:
    """Sıralama anahtarı: "tarih" | "kucuk"; eski "faiz" / "soz" → "tarih"; bilinmeyen → None."""
    if order in ORDERS:
        return order
    return LEGACY_ORDERS.get(order or "")


def settings() -> dict:
    try:
        raw = json.loads(db.get_setting(SETTING) or "{}")
    except ValueError:
        raw = {}
    monthly = raw.get("monthly")
    return {"monthly": _r(monthly) if isinstance(monthly, (int, float)) and monthly >= 0 else None,
            "order": norm_order(raw.get("order")) or "tarih"}


def save_settings(data: dict) -> dict:
    cur = settings()
    if "monthly" in data:
        v = data["monthly"]
        cur["monthly"] = None if v is None else _r(max(0.0, float(v)))
    if norm_order(data.get("order")):
        cur["order"] = norm_order(data["order"])
    db.set_setting(SETTING, json.dumps(cur))
    return cur

# ---------------------------------------------------------------- girdiler

def _hidden_people() -> bool:
    from app import book

    r = book.active()
    return bool(r and "borclar" in r["hidden_pages"])


def _plan_hidden() -> bool:
    from app import book

    r = book.active()
    return bool(r and "plan" in r["hidden_pages"])


def _learned_rates() -> dict[str, float]:
    """Kart → ekstredeki son akdi (alışveriş) faiz oranı (aylık, ör. 0.0375). Açıklamada "Oran:3.75" yazıyorsa."""
    from app.finance import _fold

    out: dict[str, float] = {}
    with db.connect() as c:
        rows = c.execute("SELECT t.description, s.card_no FROM transactions t JOIN statements s ON s.id = t.statement_id "
                         "WHERE t.txn_type = 'faiz_ucret' AND t.is_deleted = 0 ORDER BY s.period_end DESC").fetchall()
    for desc, card in rows:
        if card in out:
            continue
        k = _fold(desc)
        if not any(w in k for w in ("ALISVERIS", "AKDI")):
            continue
        m = _RATE_RE.search(desc.upper().replace("İ", "I"))
        if m:
            pct = float(m.group(1).replace(",", "."))  # yüzde (3.75)
            if 0.5 <= pct <= 15:
                out[card] = pct / 100
    return out


def _cards(today: date) -> tuple[list[dict], list[dict]]:
    """(açık kart borçları, bu ay tamamı ödenmiş kart ekstreleri)."""
    from app import finance

    stmts = {s["id"]: s for s in db.list_statements()}
    proj = finance.card_projection()
    rates = _learned_rates()
    this_month = _ym(today)
    open_, done = [], []
    for p in finance.payables(today):
        if p["type"] != "statement":
            continue
        st = stmts.get(p["id"]) or {}
        card_no = st.get("card_no") or ""
        paid = p["paid_amount"] or 0.0
        paid_now = paid if (p.get("paid_at") or "")[:7] == this_month else 0.0
        if p["status"] == "odendi":
            if paid_now and not p.get("rolled_into"):
                done.append({"key": f"kart:{p['id']}", "kind": "card", "id": p["id"], "title": _card_title(st, p),
                             "paid": _r(paid_now), "payable": p})
            continue
        if not p.get("in_total"):
            continue  # ödenmemiş eski ekstre: kalanı kartın son ekstresinde
        remaining = _r(max(0.0, p["amount"] - paid))
        if remaining <= 0:
            continue
        inst = [i for i in (proj.get(card_no) or {}).get("installments", []) if i["amount"] > 0]
        future = None
        if inst:
            last = max(i["month"] for i in inst)
            total = _r(sum(i["amount"] for i in inst))
            future = {"total": total, "last_month": last, "last_label": month_label(last),
                      "text": f"İleride {_tl(total)} taksit daha var; her ay ekstrene eklenir, son taksit "
                              f"{month_label(last)}. Aylık tutarın bunların dışında."}
        rate = rates.get(card_no)
        open_.append({
            "key": f"kart:{p['id']}", "kind": "card", "id": p["id"], "title": _card_title(st, p), "card_no": card_no,
            "bank": st.get("bank"), "period_end": st.get("period_end"), "total": _r(p["amount"]), "paid": _r(paid),
            "paid_now": _r(paid_now),
            "remaining": remaining, "due_date": p["due_date"], "min_payment": p["min_payment"],
            "overdue": p["due_date"] < today.isoformat(),
            "rate": {"monthly": rate or GENERIC_RATE, "source": "ekstre" if rate else "genel"},
            "future_installments": future, "payable": p,
        })
    return open_, done


def _card_title(st: dict, p: dict) -> str:
    """Kısa kart adı ("Örnek Bank ••1234"; hatırlatmalarla aynı): uzun kart numarası dar ekranda bölünmesin."""
    card = st.get("card_no") or ""
    return f"{st.get('bank')} ••{card[-4:]}" if st.get("bank") and len(card) >= 4 else p["title"]




def _unit_prices() -> dict[str, float]:
    """Altın/döviz birimi → güncel TL fiyatı (zekât ile aynı kaynak; elle girilen gram fiyatı dahil). Alınamazsa boş."""
    from app import zakat

    try:
        px = zakat.prices()["prices"]
    except Exception:  # noqa: BLE001 - fiyat yoksa altın/döviz borcu eskisi gibi plan dışı kalır
        return {}
    out = {}
    for asset, key in zakat.DEBT_ASSET_KEYS.items():
        p = px.get(key)
        if isinstance(p, (int, float)) and p > 0:
            out[asset] = float(p)
    return out


def _people(today: date) -> tuple[list[dict], list[dict], list[dict], list[dict]]:
    """(açık diğer borçlarım: TL ve fiyatı bilinen altın/döviz, plana girmeyenler, bu ay tamamen ödenen borçlar, ödeme
    planı olan açık borçlar)."""
    from app import finance

    if _hidden_people():
        return [], [], [], []
    this_month = _ym(today)
    payables = {p["id"]: p for p in finance.payables(today) if p["type"] == "debt"}
    debts = [d for d in db.list_debts() if d["direction"] == "borc" and not d["written_off_at"]]
    prices = _unit_prices() if any(d["asset"] != "TRY" and not d["settled_at"] and d["remaining"] > 0
                                   for d in debts) else {}
    open_, other, done, fixed = [], [], [], []
    for d in debts:
        paid_now = sum(x["amount"] for x in d["payments"] if (x["paid_at"] or "")[:7] == this_month)
        gold = d["asset"] != "TRY"
        price = prices.get(d["asset"]) if gold else 1.0
        if gold:
            if d["settled_at"] or d["remaining"] <= 1e-9:
                continue
            if not price:
                other.append({"key": f"borc:{d['id']}", "title": d["person"],
                              "text": f"{finance.fmt_asset(d['remaining'], d['asset'])}: TL dışı borç; güncel fiyat "
                                      "alınamadığı için plana girmedi. Borç / Alacak'ta takip edilir."})
                continue
        payable = payables.get(d["id"]) or _debt_payable(d, today)
        if not gold and (d["settled_at"] or money.is_zero(d["remaining"]) or d["remaining"] < 0):
            if paid_now and (d["settled_at"] or "")[:7] == this_month:
                done.append({"key": f"borc:{d['id']}", "kind": "person", "id": d["id"], "title": d["person"],
                             "paid": _r(paid_now), "payable": payable})
            continue
        if d.get("schedule"):
            fixed.append(_fixed_item(d, payable, paid_now, today))
            continue
        ctype = d.get("creditor_type")
        item = {
            "key": f"borc:{d['id']}", "kind": "person", "id": d["id"], "title": d["person"],
            "note": d["note"] or None, "total": _r(d["amount"] * price), "paid": _r((d["paid_amount"] or 0) * price),
            "paid_now": _r(paid_now * price), "remaining": _r(d["remaining"] * price), "due_date": d["due_date"],
            "debt_date": d["debt_date"], "overdue": bool(d["due_date"] and d["due_date"] < today.isoformat()),
            "payable": payable, "terms": d.get("terms") or "bilinmiyor", "strict": bool(d.get("strict")),
            "creditor_type": ctype, "creditor_label": debt_schedule.CREDITORS.get(ctype) if ctype else None,
            "asset": d["asset"],
        }
        if gold:
            item.update({"qty": d["remaining"], "price": price, "qty_text": finance.fmt_asset(d["remaining"], d["asset"])})
        open_.append(item)
    _attach_jars([*open_, *fixed], today)
    return open_, other, done, fixed


def _debt_jars() -> dict[int, dict]:
    """Borç id → açık, borca bağlı TL kumbarası. Hesaplar (kumbara) sayfası üyeye kapalıysa yok sayılır."""
    from app import book

    r = book.active()
    if r and "hesaplar" in r["hidden_pages"]:
        return {}
    out: dict[int, dict] = {}
    for j in db.debt_jar_rows():
        out.setdefault(j["debt_id"], j)
    return out


def _jar_usable(d: dict) -> bool:
    """Plan bu borcun birikimini kumbaradan sayar mı: tek seferde ödenecek TL borç ya da erken kapatma planı olan kredi."""
    if d["kind"] == "fixed":
        return bool(d.get("payoff_base"))
    return _is_tek(d) and (d.get("asset") or "TRY") == "TRY"


def _attach_jars(debts: list[dict], today: date) -> None:
    """Bağlı kumbara: jar (list_jars satırı), jar_saved (plan birikimi buradan başlar), jar_now (bu ay ayrılan net)."""
    if not debts:
        return
    jars = _debt_jars()
    month = _ym(today)
    for d in debts:
        j = jars.get(d["id"])
        if not j:
            continue
        d["jar"] = j
        if _jar_usable(d):
            d["jar_used"] = True
            d["jar_saved"] = _r(max(0.0, j["balance"]))
            d["jar_now"] = _r(max(0.0, sum(m["amount"] for m in j["moves"]
                                           if m["move_date"][:7] == month and not m.get("debt_payment_id"))))


def _jar_now(debts) -> float:
    """Bu ay bağlı kumbaralara ayrılan (gerçek)."""
    return _r(sum(d.get("jar_now") or 0.0 for d in debts))


def _jar_deduct(cards: list[dict], people: list[dict], fixed: list[dict], monthly: float | None, order: str, start: str,
                paid_now: float) -> float:
    """Bu ay kumbaraya ayrılandan aylık tutardan sayılan kısım: her borçta en çok planın bu ay o borca ayırdığı kadar
    (kumbara yokmuş gibi, ayın başındaki bakiyeyle hesaplanır). Fazlası aylık tutarın dışından gelmiş sayılır (ör.
    birikmiş para): kumbarada birikim olarak durur ama bu ayın taksitlerini ve kart ödemesini kısmaz."""
    jn = [d for d in (*people, *fixed) if d.get("jar_now")]
    if not jn or not monthly:
        return 0.0

    def pre(ds):
        return [{**d, "jar_saved": _r(max(0.0, (d.get("jar_saved") or 0.0) - d["jar_now"]))} if d.get("jar_now") else d
                for d in ds]

    sim = simulate(cards, pre(people), monthly, order, start, first_budget=_r(max(0.0, monthly - paid_now)),
                   fixed=pre(fixed))
    m0 = sim["months"][0] if sim["months"] and sim["months"][0]["month"] == start else None
    if not m0:
        return 0.0
    out = 0.0
    for d in jn:
        key = d["key"] + (PK if d["kind"] == "fixed" else "")
        planned = sum(p["amount"] for p in m0["payments"] if p["key"] == key)
        out += min(d["jar_now"], planned)
    return _r(out)


def _salary_day(today: date) -> int | None:
    """Planlama'daki beklenen ana gelirin (maaş) günü: maaş sayılan en büyük gelir kalemi (yoksa en büyük gelir)."""
    from app import salary

    if _plan_hidden():
        return None
    month = _ym(today)
    try:
        recs = [r for r in db.list_recurring() if r["kind"] == "gelir" and (r["start_month"] or "") <= month
                and (not r.get("end_month") or r["end_month"] >= month)]
        sal = [r for r in recs if salary.is_salary(r)] or recs
    except Exception:  # noqa: BLE001 - not yalnız bilgi; hesaplanamazsa yok
        return None
    if not sal:
        return None
    return int(max(sal, key=lambda r: r["amount"] or 0)["day_of_month"] or 1)


def _fixed_item(d: dict, payable: dict, paid_now: float, today: date) -> dict:
    """Ödeme planı olan borç: kalan taksitler ay ay (ödenen toplam en eski taksitten düşer; geçmişte eksik kalan bu aya).
    Taksitli borç her zaman yükümlülüktür (o ayın taksiti tarihinde)."""
    sch = d["schedule"]
    start = _ym(today)
    left_paid = debt_schedule.plan_paid(d)  # ilk taksit ayından önce ödenen plana girmez
    dues: dict[str, float] = {}
    due_nos: list[int] = []  # bu aya kadar vadesi gelip ödenmemiş taksitlerin numaraları
    for r in debt_schedule.installments(d):
        take = min(left_paid, r["amount"])
        left_paid = _r(left_paid - take)
        rest = _r(r["amount"] - take)
        if rest > 0:
            m = max(r["month"], start)
            dues[m] = _r(dues.get(m, 0.0) + rest)
            if r["month"] <= start:
                due_nos.append(r["no"])
    nx = sch["next"]
    return {
        "key": f"borc:{d['id']}", "kind": "fixed", "id": d["id"], "title": d["person"], "note": d["note"] or None,
        "total": _r(d["amount"]), "paid": _r(d["paid_amount"] or 0), "paid_now": _r(paid_now),
        "remaining": _r(d["remaining"]), "due_date": nx["date"] if nx else None, "debt_date": d["debt_date"],
        "overdue": bool(sch["overdue"]), "dues": dues, "due_nos": due_nos, "day": sch["day"], "schedule": sch,
        "creditor_label": sch["creditor_label"], "creditor_type": sch["creditor_type"], "payable": payable,
        "terms": "taksitli", "terms_label": debt_schedule.TERMS["taksitli"], "strict": True,
        # erken kapatma (faizli planda kapatma tutarı girildiyse): simulate bunu birikimle kapatır
        "payoff_base": debt_schedule.payoff_base(d, today),
    }


def _debt_payable(d: dict, today: date) -> dict:
    """Ödemeler listesinde olmayan (vadesiz) borcun "Ödedim" penceresi için Payable biçimi (finance.payables ile aynı)."""
    due = d["due_date"] or today.isoformat()
    paid = d["paid_amount"] or None
    status = "odendi" if d["settled_at"] else "kismi" if paid else "bekliyor"
    return {"type": "debt", "id": d["id"], "key": f"debt:{d['id']}", "title": f"{d['person']} (borç)",
            "subtitle": " · ".join(x for x in ("Borç", d["note"]) if x), "amount": d["amount"], "min_payment": None,
            "due_date": due, "days_left": (date.fromisoformat(due) - today).days, "paid_amount": paid,
            "paid_at": d["settled_at"], "status": status, "in_total": status != "odendi"}


def suggestion(today: date) -> dict | None:
    """Planlama'nın "her zamanki harcamanla" artanının ortalaması (en çok 3 ay; ücretsizde 1), 100'e aşağı yuvarlı."""
    from app import finance, plans

    if _plan_hidden():
        return None
    try:
        months = finance.plan(plans.plan_months(db.current_user(), 3), today=today)["months"]
    except Exception:  # noqa: BLE001 - öneri hesaplanamazsa plan yine çalışsın
        return None
    if not months:
        return None
    nets = [m["net_with_estimate"] for m in months]
    avg = sum(nets) / len(nets)
    amount = float(max(0, math.floor(avg / 100) * 100))
    text = (f"Planlama'ya göre her zamanki harcamanla ayda yaklaşık {_tl(amount)} artıyor." if amount > 0 else
            "Planlama'ya göre her zamanki harcamanla bu ay artan para görünmüyor; ayırabileceğin tutarı sen yaz.")
    return {"amount": amount, "raw": _r(max(0.0, avg)), "months": len(nets), "text": text}


# ---------------------------------------------------------------- takvim

def _eff(rate: float) -> float:
    return rate * (1 + TAX)


def monthly_interest(remaining: float, rate: float) -> float:
    """Kalan kart borcuna bir ayda eklenen faiz ve vergi (BSMV + KKDF): kalan × aylık oran × (1 + TAX). Plan
    (simulate) ve Kartlar ekranındaki faiz satırı (card_interest) aynı hesabı kullanır."""
    return _r(max(0.0, remaining) * _eff(rate))


def card_interest(remaining: float, due_date: str | None, paid: float | None, rate: float | None, today: date,
                  fees_hidden: bool = False) -> dict | None:
    """Kartlar ekranında son ekstrenin faiz satırı (sunucu hesaplar, istemci yalnız gösterir; yetenek card_interest).
    state: after_due (son ödeme günü geçti, kalan var: bu ay eklenecek faiz ve vergi) | partial (gün gelmedi, kısmi
    ödeme var: kalanı öde, faizden kurtul) | before_due (gün gelmedi: tamamını ödersen faiz işlemez). Oran ekstreden
    (yoksa GENERIC_RATE, metinde "yaklaşık"). Ortak defterde faiz gizliyse tutar yazılmaz (amount None)."""
    remaining = _r(remaining or 0.0)
    if remaining <= 0 or not due_date:
        return None
    source = "ekstre" if rate else "genel"
    rate = rate or GENERIC_RATE
    y = monthly_interest(remaining, rate)
    pct = f"%{rate * 100:.2f}".replace(".", ",")
    note = (f"Hesap: ekstrendeki aylık {pct} faiz oranı ve vergiler (BSMV, KKDF)." if source == "ekstre"
            else f"Hesap yaklaşık: aylık {pct} faiz ve vergiler (BSMV, KKDF); bankanın oranı ekstrende yazar.")
    out = {"remaining": remaining, "amount": None if fees_hidden else y, "rate": rate, "rate_source": source,
           "rate_note": None if fees_hidden else note, "due_date": due_date}
    if due_date < today.isoformat():
        text = (f"Kalan {_tl(remaining)} borca faiz ve vergi eklenir; ödemedikçe her ay büyür." if fees_hidden else
                f"Kalan {_tl(remaining)} borca bu ay yaklaşık {_tl(y)} faiz ve vergi eklenir; ödemedikçe her ay büyür.")
        return {**out, "state": "after_due", "level": "bad", "text": text}
    if paid and paid > 0:
        text = ("Kalanı son ödeme gününe kadar ödersen faiz işlemez." if fees_hidden else
                f"Kalanı son ödeme gününe kadar öde: yaklaşık {_tl(y)} faizden kurtulursun.")
        return {**out, "state": "partial", "level": "warn", "text": text}
    return {**out, "state": "before_due", "level": "info", "text": "Tamamını ödersen faiz işlemez."}


def _sort_cards(cards: list[dict]) -> list[dict]:
    # gecikmiş önce (son ödemesi en eski), sonra son ödeme gününe göre; aynı günde büyük borç önce (faiz riski büyük)
    return sorted(cards, key=lambda d: (d["due_date"] or "9999", -d["remaining"]))



def person_key(title: str) -> str:
    """Aynı kişinin borçları tek satırda toplansın: "Nermin Teyze" = "nermin teyze " (Türkçe büyük/küçük harf)."""
    return " ".join(title.replace("İ", "i").replace("I", "ı").lower().split())


def _floor(x: float) -> float:
    """ROUND_TO katına aşağı (ROUND_TO'dan küçükse tam liraya)."""
    if x >= ROUND_TO:
        return float(math.floor(x / ROUND_TO + 1e-9) * ROUND_TO)
    return float(math.floor(max(0.0, x) + 1e-9))


def _part(rem: float, budget: float, any_paid: bool) -> float:
    """Bir kişiye (ya da borca) bütçeden ne kadar: yetiyorsa tamamı; FOLD'dan küçük kalan bırakmaz (tamamı); değilse
    ROUND_TO katına yuvarlı kısmi ve sonraya en az MIN_PAY bırakır. Bu ay başka ödeme varken MIN_PAY'den küçük kısmi
    ödeme yapılmaz (0)."""
    if budget <= 0 or rem <= 0:
        return 0.0
    if money.ge(budget, rem) or rem - budget < FOLD:
        return rem
    amt = _floor(budget)
    if rem - amt < MIN_PAY:
        amt = _floor(rem - MIN_PAY)
    if amt < MIN_PAY and any_paid:
        return 0.0
    return max(0.0, amt)


def _round_shares(shares: dict[str, float], budget: float) -> dict[str, float]:
    """Payları ROUND_TO katlarına: toplam bütçenin yuvarlısı (en büyük küsurat önce)."""
    base = {k: _floor(v) if v >= ROUND_TO else 0.0 for k, v in shares.items()}
    left = _floor(budget) - sum(base.values())
    for k in sorted(shares, key=lambda k: -(shares[k] - base[k])):
        if left < ROUND_TO:
            break
        base[k] += ROUND_TO
        left -= ROUND_TO
    return {k: v for k, v in base.items() if v > 0}




def _sort_people(people: list[dict], order: str) -> list[dict]:
    if norm_order(order) == "kucuk":
        return sorted(people, key=lambda d: (d["remaining"], d["due_date"] or "9999", d["id"]))
    # tarih: söz/vade tarihi önce, tarihsizler en eskiden
    return sorted(people, key=lambda d: (d["due_date"] is None, d["due_date"] or "", d["debt_date"] or "", d["id"]))


def _due_m(d: dict) -> str | None:
    return (d.get("due_date") or "")[:7] or None


def _is_tek(d: dict) -> bool:
    return d.get("terms") == "tek"


def _ceil_to(x: float) -> float:
    return float(math.ceil(x / ROUND_TO - 1e-9) * ROUND_TO) if x > 0 else 0.0


def _within(debts: list[dict], need: dict[str, float], amount: float) -> dict[str, float]:
    """Bir birime (aynı kişi, aynı koşul) ayrılan tutar borçlarına: söz tarihi yakın, sonra küçük borç önce; önce
    bütün olarak kapanır."""
    out: dict[str, float] = {}
    for d in sorted(debts, key=lambda d: (d["due_date"] is None, d["due_date"] or "", need[d["key"]], d["id"])):
        if amount <= 0:
            break
        amt = _r(min(need[d["key"]], amount))
        if amt > 0:
            out[d["key"]] = amt
            amount = _r(amount - amt)
    return out


def _split(units: list[dict], budget: float, any_paid: bool) -> dict[str, float]:
    """Hiçbirine yetmeyen birimler arasında anlamlı paylar (birimler öncelik sırasında). Orantılı pay MIN_PAY'in
    altına düşerse en son sıradaki (en büyük) birim bu ay bekler; tek birim kalırsa ona _part (parça) ya da birikim
    (tek seferde). Tutarlar ROUND_TO katı."""
    live = list(units)
    while len(live) > 1:
        total = sum(u["need"] for u in live)
        shares = {u["key"]: budget * u["need"] / total for u in live}
        if min(shares.values()) >= MIN_PAY:
            return _round_shares(shares, budget)
        live.pop()
    if not live:
        return {}
    u = live[0]
    amt = _floor(min(budget, u["need"])) if u["tek"] else _part(u["need"], budget, any_paid)
    if u["tek"] and amt < MIN_PAY and any_paid:
        amt = 0.0
    return {u["key"]: amt} if amt > 0 else {}


def _alloc_group(units: list[dict], budget: float, any_paid: bool) -> dict[str, float]:
    """Aynı gruptaki (aynı ay ya da sıradaki tek borç) birimlere bütçe: hepsine yetiyorsa hepsi; yetmiyorsa kapanabilenler
    kapanır (önce tek seferde olan, sonra altın/döviz, sonra küçük), kalan öbürlerine anlamlı paylarla."""
    out: dict[str, float] = {}
    if budget <= 0 or not units:
        return out
    if money.ge(budget, sum(u["need"] for u in units)):
        return {u["key"]: u["need"] for u in units}
    pri = sorted(units, key=lambda u: (not u["tek"], not u["gold"], u["need"], u["idx"]))
    for u in pri:
        if money.le(u["need"], budget):
            out[u["key"]] = u["need"]
            budget = _r(budget - u["need"])
    rest = [u for u in pri if u["key"] not in out]
    if budget > 0 and rest:
        out.update(_split(rest, budget, any_paid or bool(out)))
    return out


def allocate_people(people_s: list[dict], rem: dict[str, float], budget: float, order: str,
                    saved: dict[str, float] | None = None, any_paid: bool = False) -> dict[str, float]:
    """Yükümlülüklerden kalan bütçe diğer borçlara (borç anahtarı → bu ay ayrılan yeni para; tek seferde borçta birikim
    dahil). tarih: aynı aya düşen borçlar bir grup (tarihsizler borç borç, en eskiden); kucuk: borç borç, küçük önce.
    Grupta aynı kişinin aynı koşuldaki borçları tek birim."""
    saved = saved or {}
    budget = _r(budget)
    out: dict[str, float] = {}
    if budget <= 0:
        return out
    need = {d["key"]: _r(rem[d["key"]] - saved.get(d["key"], 0.0)) for d in people_s}
    live = [d for d in people_s if need[d["key"]] > 0]
    groups: list[list[dict]] = []
    if norm_order(order) == "kucuk":
        groups = [[d] for d in live]
    else:
        by: dict[str, list[dict]] = {}
        for d in live:
            m = _due_m(d)
            if m is None:
                groups.append([d])
            else:
                if m not in by:
                    by[m] = []
                    groups.append(by[m])
                by[m].append(d)
    for g in groups:
        if budget <= 0:
            break
        units: dict[tuple, dict] = {}
        for d in g:
            uk = (person_key(d["title"]), _is_tek(d), d.get("asset", "TRY") != "TRY")
            u = units.get(uk)
            if u is None:
                u = units[uk] = {"key": "|".join(map(str, uk)), "need": 0.0, "tek": uk[1], "gold": uk[2],
                                 "idx": len(units), "debts": []}
            u["need"] = _r(u["need"] + need[d["key"]])
            u["debts"].append(d)
        got = _alloc_group(list(units.values()), budget, any_paid or bool(out))
        for u in units.values():
            amt = got.get(u["key"], 0.0)
            if amt > 0:
                for k, a in _within(u["debts"], need, amt).items():
                    out[k] = _r(out.get(k, 0.0) + a)
                budget = _r(budget - amt)
    return out


def _norm_person(d: dict) -> dict:
    """Eski çağrılar (testler, eski anlık görüntüler): koşulu olmayan borç parça parça ödenebilir ve söz."""
    return {**d, "terms": d.get("terms") or "bilinmiyor", "strict": bool(d.get("strict")),
            "asset": d.get("asset") or "TRY"}


def simulate(cards: list[dict], people: list[dict], monthly: float, order: str, start: str,
             first_budget: float | None = None, fixed: list[dict] | None = None) -> dict:
    """Ay ay ödeme. cards/people: {key, title, remaining, due_date, ...} (kartta rate.monthly; diğer borçta terms,
    strict, asset); fixed: ödeme planı olan borçlar {key, title, remaining, dues {ay: tutar}, day, payoff_base?}. Sıra
    modül açıklamasında. Dönen: months, closes {key: ay}, interest (toplam, varsayım), interest_by {key: tutar}, debt_free
    (ay | None), saved_by {key: son birikim}, payoff_by {key: {month, amount, installments, saved}} (erken kapatma)."""
    order = norm_order(order) or "tarih"
    fixed = fixed or []
    people = [_norm_person(d) for d in people]
    rem = {d["key"]: d["remaining"] for d in (*fixed, *cards, *people)}
    # bağlı kumbara: tek seferde borcun birikimi kumbaradaki gerçek bakiyeden başlar
    saved = {d["key"]: (_r(min(d.get("jar_saved") or 0.0, d["remaining"])) if _is_tek(d) else 0.0) for d in people}
    cards_s = _sort_cards(cards)
    people_s = _sort_people(people, order)
    fixed_s = sorted(fixed, key=lambda d: (d.get("day") or 1, d["id"]))
    carry = {d["key"]: 0.0 for d in fixed_s}
    titles = {d["key"]: d["title"] for d in (*fixed_s, *cards_s, *people_s)}
    canon: dict[str, str] = {}  # aynı kişinin borçları tek ad altında ("nermin teyze" → "Nermin Teyze")
    for d in sorted(people_s, key=lambda d: d["id"]):
        titles[d["key"]] = canon.setdefault(person_key(d["title"]), d["title"])
    kinds = {d["key"]: d["kind"] for d in (*fixed_s, *cards_s, *people_s)}
    pinfo = {d["key"]: d for d in people_s}
    closes: dict[str, str] = {}
    interest_by: dict[str, float] = {}
    months: list[dict] = []
    debt_free = None
    # erken kapatma: faizli planda kapatma tutarı girilmiş krediler; oranı bilinen ve yüksek olan önce, sonra kalan faizi
    # (kalan taksitlere göre) büyük olan. Para birikim olarak ayrılır, yetince kredi tek seferde kapanır.
    payoffs = sorted((d for d in fixed_s if d.get("payoff_base") and d["remaining"] > 0),
                     key=lambda d: (-(d["payoff_base"].get("rate") or 0),
                                    debt_schedule.payoff_at(d["payoff_base"], d["remaining"]) / d["remaining"], d["id"]))
    pot = {d["key"]: _r(d.get("jar_saved") or 0.0) for d in payoffs}  # bağlı kumbara: birikim oradan başlar
    pot_jar = dict(pot)  # kumbaradaki para aylık tutara geri dönmez (yalnız plandan ayrılan döner)
    payoff_by: dict[str, dict] = {}
    for d in payoffs:
        titles[d["key"] + PK] = d["title"]
        kinds[d["key"] + PK] = "fixed"
    if all(v <= 0 for v in rem.values()):
        return {"months": [], "closes": {}, "interest": 0.0, "interest_by": {}, "debt_free": start, "saved_by": {},
                "payoff_by": {}}

    def need(key: str) -> float:
        return _r(rem[key] - saved.get(key, 0.0))

    for k in range(HORIZON):
        m = _add(start, k)
        budget = _r(first_budget if k == 0 and first_budget is not None else monthly)
        pays: dict[str, float] = {}     # yeni para: ödeme
        saves: dict[str, float] = {}    # yeni para: birikim (tek seferde borç)
        from_saved: dict[str, float] = {}
        for d in people_s:  # kumbaradaki birikim borcun tamamına yetiyor: tek seferde öde (yeni para gerekmez)
            key = d["key"]
            if rem[key] > 0 and saved[key] > 0 and money.ge(saved[key], rem[key]):
                from_saved[key] = saved[key]
                saved[key] = 0.0
                pays[key] = 0.0
                rem[key] = 0.0

        def give(key: str, amt: float, cap: bool = True) -> float:
            """Bütçeden bir borca ver (tek seferde borçta birikime; birikim yetince tamamı ödenir). Dönen: verilen.
            cap=False: diğer borç dağıtımı küçük kalanı bırakmamak için bütçeyi en çok FOLD kadar aşabilir."""
            nonlocal budget
            amt = _r(min(amt, budget if cap else amt, need(key) if key in pinfo else rem[key]))
            if amt <= 0:
                return 0.0
            budget = _r(budget - amt)
            if key in pinfo and _is_tek(pinfo[key]):
                s = _r(saved[key] + amt)
                if money.ge(s, rem[key]):  # birikim yetti: tek seferde öde
                    from_saved[key] = saved[key]
                    saved[key] = 0.0
                    pays[key] = _r(pays.get(key, 0.0) + amt)
                    saves.pop(key, None)
                    rem[key] = 0.0
                else:
                    saved[key] = s
                    saves[key] = _r(saves.get(key, 0.0) + amt)
                return amt
            pays[key] = _r(pays.get(key, 0.0) + amt)
            rem[key] = _r(rem[key] - amt)
            return amt

        # 1) yükümlülükler: bu ayın taksitleri, son ödemesi gelen kartlar, vadesi gelen kesin borçlar
        fixed_due = fixed_short = 0.0
        fdue: dict[str, float] = {}
        for d in fixed_s:
            due = _r(min(rem[d["key"]], carry[d["key"]] + d["dues"].get(m, 0.0)))
            if due > 0:
                fdue[d["key"]] = due
                fixed_due = _r(fixed_due + due)
        due_cards = [d for d in cards_s if rem[d["key"]] > 0 and (d["due_date"] or "9999")[:7] <= m]
        sdue = [d for d in people_s if d["strict"] and need(d["key"]) > 0 and _due_m(d) and _due_m(d) <= m]
        total_ob = _r(sum(fdue.values()) + sum(rem[d["key"]] for d in due_cards) + sum(need(d["key"]) for d in sdue))
        fixed_paid: dict[str, float] = {}
        if money.ge(budget, total_ob):
            for d in fixed_s:
                if d["key"] in fdue:
                    fixed_paid[d["key"]] = give(d["key"], fdue[d["key"]])
            for d in due_cards:
                give(d["key"], rem[d["key"]])
            for d in sdue:
                give(d["key"], need(d["key"]))
            for d in cards_s:  # son ödemesi sonraki aylarda olan kartlar da (kartlar diğer borçlardan önce)
                give(d["key"], rem[d["key"]])
        else:
            # tutar yetmiyor: önce her yükümlülüğün gecikmesi önlenir, tarih sırasıyla (taksit, kart asgarisi, vadesi
            # gelen kesin borç); kalan son ödemesi gelen kartlara, faizi en yüksek önce (eşitse borcu büyük)
            obs = []
            for d in fixed_s:
                if d["key"] in fdue:
                    obs.append((debt_schedule.day_in(m, d.get("day") or 1), 0, d["key"], fdue[d["key"]]))
            for d in due_cards:
                obs.append(((d["due_date"] or f"{m}-28"), 1, d["key"], _asgari(d, rem[d["key"]], m, k == 0)))
            for d in sdue:
                obs.append((d["due_date"], 2, d["key"], need(d["key"])))
            for _, _, key, amt in sorted(obs, key=lambda o: (o[0], o[1])):
                got = give(key, amt)
                if key in fdue:
                    fixed_paid[key] = got
            for d in sorted(due_cards, key=lambda d: (-d["rate"]["monthly"], -rem[d["key"]])):
                if budget <= 0:
                    break
                give(d["key"], rem[d["key"]])
        for d in fixed_s:
            if d["key"] in fdue:
                paid = fixed_paid.get(d["key"], 0.0)
                carry[d["key"]] = _r(fdue[d["key"]] - paid)
                fixed_short = _r(fixed_short + fdue[d["key"]] - paid)
        # 2) kesin tarihi ileride olan borçlar: tarihine yetişmek için bu ay gereken kadar (sonraki aylar yetmiyorsa)
        fut = sorted((d for d in people_s if d["strict"] and need(d["key"]) > 0 and _due_m(d) and _due_m(d) > m),
                     key=lambda d: (d["due_date"], d["id"]))
        if fut and budget > 0:
            cards_left = _r(sum(rem[d["key"]] for d in cards_s if rem[d["key"]] > 0))
            reserve = 0.0
            for D in sorted({_due_m(d) for d in fut}):
                n = _months_between(m, D)
                cum = sum(need(d["key"]) for d in fut if _due_m(d) <= D)
                later_fixed = sum(f["dues"].get(_add(m, j), 0.0) for f in fixed_s for j in range(1, n + 1))
                cap = n * max(0.0, monthly) - later_fixed - cards_left
                reserve = max(reserve, cum - cap)
            reserve = min(budget, _ceil_to(reserve))
            for d in fut:
                if reserve <= 0:
                    break
                reserve = _r(reserve - give(d["key"], min(need(d["key"]), reserve)))
        # 2b) faizli kredinin erken kapatılması: kartlar ve kesin tarihlerden artan para birikime; yetince tek seferde
        payoff_from: dict[str, float] = {}
        for d in payoffs:
            key, b = d["key"], d["payoff_base"]
            pk = key + PK
            if key in payoff_by:
                continue
            rest = rem[key]  # bu ayın taksiti ödendikten sonra kalan taksitler
            amount = debt_schedule.payoff_at(b, rest)
            if rest <= 0 or not money.gt(rest - amount, 1.0):
                # taksitler bitti ya da erken kapatma artık faiz kazandırmıyor: birikim diğer borçlara
                budget = _r(budget + max(0.0, pot[key] - pot_jar[key]))
                pot[key] = pot_jar[key] = 0.0
                continue
            need_new = _r(amount - pot[key])
            if money.ge(budget, need_new):
                new = max(0.0, need_new)
                payoff_from[pk] = _r(min(pot[key], amount))
                pays[pk] = _r(new)
                # birikim kapatma tutarından fazlaysa plandan ayrılan artan geri (kumbaradaki artan kumbarada kalır)
                budget = _r(budget - new + max(0.0, min(-need_new, pot[key] - pot_jar[key])))
                payoff_by[key] = {"month": m, "amount": amount, "installments": rest, "saved": _r(rest - amount)}
                rem[key] = 0.0
                pot[key] = 0.0
                continue
            # bu ay yetmiyor: söz tarihi gelmiş (esnek) borç varsa ona da pay kalır (biri tamamen beklemesin)
            due_p = _r(sum(need(x["key"]) for x in people_s
                           if not x["strict"] and need(x["key"]) > 0 and _due_m(x) and _due_m(x) <= m))
            hold = min(budget, due_p, _ceil_to(budget * DUE_SHARE)) if due_p > 0 else 0.0
            avail = _r(budget - hold)
            if avail <= 0:
                break
            # taksitler bitmeden birikmeyecekse ayırma (yalnız taksitler ödenir; para diğer borçlara)
            left_n = math.ceil(rest / b["inst"] - 1e-9) if b["inst"] > 0 else 0
            if math.ceil((need_new - avail) / avail - 1e-9) >= left_n:
                continue
            saves[pk] = avail
            pot[key] = _r(pot[key] + avail)
            budget = _r(budget - avail)
            break  # sıradaki kredi bu kapanınca
        # 3) kalan para diğer borçlara (tarih grubu ya da küçükten), anlamlı tutarlarla
        if budget > 0:
            got = allocate_people(people_s, rem, budget, order, saved, any_paid=bool(pays or saves))
            for key, amt in got.items():
                give(key, amt, cap=False)
        used = _r(sum(pays.values()) + sum(saves.values()))
        # son ödeme ayı geçen kartta kalan borca faiz (+ vergi) işler: uyarı için varsayım
        month_interest = 0.0
        for d in cards_s:
            if rem[d["key"]] > 0 and (d["due_date"] or "9999")[:7] <= m:
                add = monthly_interest(rem[d["key"]], d["rate"]["monthly"])
                rem[d["key"]] = _r(rem[d["key"]] + add)
                interest_by[d["key"]] = _r(interest_by.get(d["key"], 0.0) + add)
                month_interest = _r(month_interest + add)
        rows = []
        for key in dict.fromkeys([*pays, *saves]):
            amt = pays.get(key) or saves.get(key) or 0.0
            base = key.removesuffix(PK)
            if key != base:  # erken kapatma satırı: birikim ya da kapatma
                closed = key in pays
                if closed:
                    closes[base] = m
                row = {"key": key, "kind": "fixed", "title": titles[key], "amount": _r(amt), "closes": closed,
                       "payoff": True}
                if closed:
                    row.update({"from_saved": payoff_from.get(key, 0.0), "pay_total": payoff_by[base]["amount"],
                                "interest_saved": payoff_by[base]["saved"]})
                else:
                    row.update({"saving": True, "saved": pot[base]})
                rows.append(row)
                continue
            closed = rem[key] <= 0 and not (base in payoff_by and payoff_by[base]["month"] == m)
            if closed and key not in closes:
                closes[key] = m
            row = {"key": key, "kind": kinds[key], "title": titles[key], "amount": _r(amt), "closes": closed}
            p = pinfo.get(key)
            if key in saves:
                row.update({"saving": True, "saved": saved[key]})
            elif key in from_saved and from_saved[key] > 0:
                row["from_saved"] = _r(from_saved[key])
            if p and p.get("price"):
                row.update({"asset": p["asset"], "units": round(amt / p["price"], 2),
                            "units_text": _fmt_units(amt / p["price"], p["asset"])})
            rows.append(row)
        left = _r(sum(v for v in rem.values() if v > 0))
        months.append({"month": m, "label": month_label(m), "total": used, "payments": rows, "groups": group_rows(rows),
                       "interest": month_interest, "left": left, "fixed_due": fixed_due, "fixed_short": fixed_short,
                       "saved": _r(sum(saved.values()) + sum(pot.values()))})
        if left <= 0:
            debt_free = m
            break
        if monthly <= 0:
            break  # tutar yok: tek ay gösterilir (faiz uyarısıyla)
    return {"months": months, "closes": closes, "interest": _r(sum(interest_by.values())), "interest_by": interest_by,
            "debt_free": debt_free, "saved_by": {k: v for k, v in saved.items() if v > 0}, "payoff_by": payoff_by}


def _fmt_units(x: float, asset: str) -> str:
    from app import finance

    return finance.fmt_asset(round(x, 2), asset)


def group_rows(rows: list[dict]) -> list[dict]:
    """Ay satırları kişi kişi: aynı kişiye (diğer borç) birden çok ödeme tek satır {key, kind, title, count, amount,
    closes (hepsi kapanıyorsa), keys}; birikim satırları ayrı (saving). Kart ve taksit satırları tek başına kalır."""
    out: list[dict] = []
    by: dict[tuple, dict] = {}
    for r in rows:
        if r["kind"] != "person":
            out.append({**r, "count": 1, "keys": [r["key"]]})
            continue
        gk = (person_key(r["title"]), bool(r.get("saving")))
        g = by.get(gk)
        if g is None:
            g = by[gk] = {"key": f"kisi:{gk[0]}" + (":birikim" if gk[1] else ""), "kind": "person", "title": r["title"],
                          "amount": 0.0, "closes": True, "count": 0, "keys": []}
            if gk[1]:
                g["saving"] = True
            out.append(g)
        g["amount"] = _r(g["amount"] + r["amount"])
        g["closes"] = g["closes"] and r["closes"]
        g["count"] += 1
        g["keys"].append(r["key"])
        if r.get("units_text") and g["count"] == 1:
            g["units_text"] = r["units_text"]
        elif g["count"] > 1:
            g.pop("units_text", None)
    return out

def _asgari(d: dict, rem_now: float, month: str, first: bool) -> float:
    """Kartın o ay en az yatırılacak tutarı (gecikme faizi işlemesin). Son ödeme ayına kadar (ve ilk ay) ekstredeki
    asgari − ödenen; sonraki aylarda ekstrenin kendi asgari/borç oranıyla kalan borçtan (yaklaşık). Asgari bilinmiyorsa 0:
    yasal oranlar kodda yok."""
    mp = d.get("min_payment")
    if not mp or mp <= 0:
        return 0.0
    if first or month <= (d.get("due_date") or "9999")[:7]:
        return _r(max(0.0, mp - (d.get("paid") or 0.0)))
    total = d.get("total") or 0.0
    return _r(rem_now * mp / total) if total > 0 else 0.0


def min_monthly(cards: list[dict], start: str, paid_now: float = 0.0, fixed: list[dict] | None = None) -> float:
    """Kart borçlarının hepsi son ödeme aylarında kapansın (faiz işlemesin) diye gereken en az aylık tutar, 10 TL'ye
    yukarı yuvarlı. Bu ay ödenen (paid_now) bu ayın tutarından düşüldüğü için eklenir. Ödeme planı olan borçların
    taksitleri her ay önce ayrıldığı için o aya kadarki taksitler de eklenir (ve tutar her ayın taksitine yetmeli)."""
    fixed_by: dict[str, float] = {}
    for f in fixed or []:
        for m, a in f["dues"].items():
            fixed_by[m] = fixed_by.get(m, 0.0) + a
    def fixed_until(k: int) -> float:
        return sum(a for m, a in fixed_by.items() if _months_between(start, m) <= k)
    need, cum = 0.0, 0.0
    for d in _sort_cards(cards):
        cum += d["remaining"]
        k = max(0, _months_between(start, (d["due_date"] or start)[:7]))
        need = max(need, (cum + paid_now + fixed_until(k)) / (k + 1))
    if cards:
        need = max([need] + [a + (paid_now if m == start else 0.0) for m, a in fixed_by.items()])
    return float(math.ceil(need / 10 - 1e-9) * 10)


# ---------------------------------------------------------------- yanıt



# ---------------------------------------------------------------- yanıt

def _paid_through(sim: dict, key: str, month: str) -> float:
    """Plana göre o aya kadar (o ay dahil) bir borca yapılan ödeme (birikim sayılmaz; tek seferde ödemede birikim dahil
    tamamı)."""
    out = 0.0
    for mo in sim["months"]:
        if mo["month"] > month:
            break
        for p in mo["payments"]:
            if p["key"] == key and not p.get("saving"):
                out += p["amount"] + (p.get("from_saved") or 0.0)
    return _r(out)


def need_monthly(key: str, deadline: str, cards, people, fixed, order: str, start: str, paid_now: float) -> float | None:
    """Bu borcun deadline ayına kadar kapanması için gereken en az aylık tutar (plan yeniden hesaplanarak), 10 TL'ye
    yukarı yuvarlı. Bulunamazsa None."""
    def ok(mo: float) -> bool:
        s = simulate(cards, people, mo, order, start, first_budget=_r(max(0.0, mo - paid_now)), fixed=fixed)
        c = s["closes"].get(key)
        return bool(c and c <= deadline)

    hi = int(math.ceil((sum(d["remaining"] for d in (*cards, *people, *fixed)) + paid_now) / 10)) + 1  # 10 TL birim
    if not ok(hi * 10):
        return None
    lo = 0  # ok(lo * 10) yanlış varsayılır
    while hi - lo > 1:
        mid = (lo + hi) // 2
        if ok(mid * 10):
            hi = mid
        else:
            lo = mid
    return float(hi * 10)


def agreed_month(key: str, close: str | None, cards, people, fixed, monthly: float, order: str, start: str,
                 first: float) -> str | None:
    """"Yeni tarihte anlaştık" için önerilecek ay: söz tarihi o aya alınınca borç sırada geriye düşebilir; plan yeni
    tarihle yeniden hesaplanır, borç o ayda kapanana kadar sonraki aya bakılır (en çok 12 deneme)."""
    cand = close
    for _ in range(12):
        if not cand:
            return None
        trial = [{**d, "due_date": debt_schedule.day_in(cand, int(d["due_date"][8:10]))} if d["key"] == key else d
                 for d in people]
        c = simulate(cards, trial, monthly, order, start, first_budget=first, fixed=fixed)["closes"].get(key)
        if c and c <= cand:
            return cand
        cand = c
    return cand


def _late(d: dict, sim: dict, start: str, close: str | None, need: float | None,
          agreed: str | None = None) -> dict | None:
    """Plan söz/vade tarihine yetişmiyorsa (ya da tarih geçtiyse ve bu ay kapanmıyorsa): tarih, o tarihte açık kalan,
    tahmini ödeme ayı. Sözde "Yeni tarihte anlaştık" önerilen günü; kesinde gereken aylık tutar."""
    if not d.get("due_date"):
        return None
    strict = bool(d.get("strict"))
    overdue = bool(d.get("overdue"))
    at = max(d["due_date"][:7], start)
    if close and close <= at:
        return None  # tarihine yetişiyor (tarihi geçmişse bu ay kapanıyor)
    label = _day_label(d["due_date"])
    kind = "kesin" if strict else "soz"
    head = f"{'Vade' if strict else 'Söz'}: {label}"
    open_ = _r(max(0.0, d["remaining"] - _paid_through(sim, d["key"], at)))
    parts = [head, f"O tarihte açık: {_tl(open_)}" if not overdue else f"Açık: {_tl(open_)}"]
    parts.append(f"Tahmini ödeme: {month_label(close)}" if close else "Bu tutarla 10 yılda kapanmıyor")
    out = {"kind": kind, "date": d["due_date"], "label": label, "head": head, "open": open_, "overdue": overdue,
           "close": {"month": close, "label": month_label(close)} if close else None, "text": " · ".join(parts)}
    if strict:
        out["need_monthly"] = need
        out["warning"] = (f"Bu tarih kesin; yetişmek için ayda {_tl(need)} gerekir." if need else
                          "Bu tarih kesin; ödeme gününden önce tutarı hazırlamak gerekir.")
        if overdue:
            out["warning"] = (f"Bu tarih kesin ve geçti; bir an önce kapatmak için bu ay {_tl(need)} gerekir." if need else
                              "Bu tarih kesin ve geçti; bir an önce ödemek gecikme bedelini durdurur.")
    else:
        out["hint"] = "Kendisine önceden haber vermek iyi olur."
        target = agreed or close
        if target:
            y, mo = map(int, target.split("-"))
            day = int(d["due_date"][8:10])
            new = debt_schedule.day_in(target, day)
            out["action"] = {"label": "Yeni tarihte anlaştık", "date": new, "date_label": _day_label(new) + f" {y}",
                             "confirm": f"Söz tarihi {_day_label(new)} {y} olarak kaydedilsin mi?"}
    return out


def _saving_info(d: dict, sim: dict, close: str | None, start: str) -> dict | None:
    """Tek seferde ödenecek borçta birikim: hangi ay ne kadar birikmiş olur."""
    if not _is_tek(d):
        return None
    saves = [p for mo in sim["months"] for p in mo["payments"] if p["key"] == d["key"] and p.get("saving")]
    if not saves and not (close and any(p["key"] == d["key"] and p.get("from_saved")
                                        for mo in sim["months"] for p in mo["payments"])):
        return None
    total = d["remaining"]
    if close:
        text = (f"{d['title']} için biriktir: {month_in(close, start)} {_tl(total)} birikmiş olur, tek seferde öde."
                if close != start else f"{d['title']}: biriktirdiğinle tamamını tek seferde öde.")
    else:
        text = f"{d['title']} için biriktir; tamamı birikince tek seferde öde."
    return {"month": close, "label": month_label(close) if close else None, "amount": total, "text": text}


def _day_note(d: dict, salary_day: int | None) -> dict | None:
    """Kesin tarihli taksitin/borcun günü maaş gününden önceyse nötr not (gün gün nakit akışı yok)."""
    if not salary_day:
        return None
    if d["kind"] == "fixed":
        day = int(d.get("day") or 1)
    elif d.get("strict") and d.get("due_date"):
        day = int(d["due_date"][8:10])
    else:
        return None
    if day >= salary_day:
        return None
    return {"day": day, "salary_day": salary_day,
            "text": f"{d['title']} {_loc(day)}, maaş {_loc(salary_day)}: bu ödemeyi önceki aydan ayır."}


def build(monthly: float | None = None, order: str | None = None, today: date | None = None) -> dict:
    from app import book

    today = today or date.today()
    start = _ym(today)
    saved = settings()
    order = norm_order(order) or saved["order"]
    sug = suggestion(today)
    source = "param" if monthly is not None else "saved" if saved["monthly"] is not None else (
        "suggestion" if sug and sug["amount"] > 0 else None)
    if monthly is None:
        monthly = saved["monthly"] if saved["monthly"] is not None else (sug["amount"] if sug else None)
    monthly = _r(max(0.0, monthly)) if monthly is not None else None
    fees_hidden = book.hide_fees()
    people_hidden = _hidden_people()

    cards, cards_done = _cards(today)
    people, other, people_done, fixed = _people(today)
    paid_now = _paid_now(cards, [*people, *fixed], cards_done, people_done)
    # bu ay bağlı kumbaraya ayrılan: planın bu ay o borca ayırdığı kadarı bu ayın tutarından düşer (ödenen gibi)
    jar_now = _jar_deduct(cards, people, fixed, monthly, order, start, paid_now) if monthly is not None else 0.0
    need = min_monthly(cards, start, paid_now + jar_now, fixed) if cards else 0.0
    if source == "suggestion" and sug and need > 0:
        # öneri artanın 100'e aşağı yuvarlısı: artan faizsiz tutara yetiyorsa öneri en az o tutar olsun (yuvarlama
        # yüzünden faizli bir plan önerilmesin); yetmiyorsa öneri cümlesi faizsiz tutarı da söyler
        if sug["amount"] < need <= sug.get("raw", sug["amount"]):
            sug = {**sug, "amount": need}
            monthly = need
        elif sug["amount"] < need:
            sug = {**sug, "text": sug["text"] + f" Faizsiz kapatmak için ayda {_tl(need)} gerekiyor."}
    first = _r(max(0.0, (monthly or 0) - paid_now - jar_now))
    sim = simulate(cards, people, monthly or 0.0, order, start, first_budget=first, fixed=fixed) \
        if monthly is not None else None
    jflags = _jar_flags()
    fixed_s = sorted(fixed, key=lambda d: (d["due_date"] or "9999", d["id"]))
    salary_day = _salary_day(today) if (fixed or any(d["strict"] for d in people)) else None

    debts = []
    for i, d in enumerate([*fixed_s, *_sort_cards(cards), *_sort_people(people, order)]):
        item = {k: v for k, v in d.items() if k not in ("paid_now", "debt_date", "period_end", "dues", "price",
                                                        "payoff_base", "jar", "jar_used", "jar_saved", "jar_now")}
        item["order"] = i + 1
        item["progress"] = round(d["paid"] / d["total"], 3) if d["total"] else 0.0
        item["due_label"] = _day_label(d["due_date"]) if d["due_date"] else None
        close = sim["closes"].get(d["key"]) if sim else None
        item["closes"] = {"month": close, "label": month_label(close)} if close else None
        if d["kind"] == "card":
            item["subtitle"] = ("Son ödeme günü geçti" if d["overdue"] else f"Son ödeme {item['due_label']}") \
                if d["due_date"] else "Kart ekstresi"
            item.update(_card_warning(d, fees_hidden))
            harm = _harm(d, sim, start, fees_hidden) if sim else None
            item["harm"] = harm
            if harm:
                item["warning"] = harm["text"]  # eski istemci de tek metin olarak gösterir
            if sim and sim["interest_by"].get(d["key"]) and not fees_hidden:
                item["projected_interest"] = sim["interest_by"][d["key"]]
        elif d["kind"] == "fixed":
            item["subtitle"] = " · ".join(x for x in (d["creditor_label"], d["schedule"]["text"]) if x)
            item["promise"] = None
            item["date_kind"] = "kesin"
            item["day_note"] = _day_note(d, salary_day)
            item["payoff"] = _payoff_row(d, sim, start, fees_hidden, bool(monthly))
        else:
            item["subtitle"] = " · ".join(x for x in (d.get("creditor_label") if d.get("creditor_type") != "kisi"
                                                      else None, d["note"]) if x) or "Diğer borç"
            item["terms_label"] = debt_schedule.TERMS.get(d["terms"], debt_schedule.TERMS["bilinmiyor"])
            item["date_kind"] = ("kesin" if d["strict"] else "soz") if d["due_date"] else None
            item["promise"] = _promise(d, close)
            nm = None
            if sim and monthly and d["strict"] and d["due_date"]:
                dl = max(d["due_date"][:7], start)
                if not (close and close <= dl):
                    nm = need_monthly(d["key"], dl, cards, people, fixed, order, start, paid_now + jar_now)
            late = _late(d, sim, start, close, nm) if sim and monthly else None
            if late and late["kind"] == "soz" and late.get("action"):
                ag = agreed_month(d["key"], close, cards, people, fixed, monthly, order, start, first)
                if ag and ag != close:
                    late = _late(d, sim, start, close, nm, ag)
            item["late"] = late
            item["saving"] = _saving_info(d, sim, close, start) if sim else None
            item["day_note"] = _day_note(d, salary_day)
            if d.get("price"):
                item["approx_note"] = PRICE_NOTE
                item["tl_text"] = f"{d['qty_text']} · ≈{_tl(d['remaining'])}"
        if d["kind"] != "card":
            item["jar"] = _jar_info(d, item, sim, start, jflags, fees_hidden)
        debts.append(item)

    warnings = []
    for it in debts:
        if it.get("warning"):
            warnings.append({"key": f"faiz:{it['key']}", "level": "bad" if it["overdue"] else "warn",
                             "title": it["title"], "text": it["warning"]})
        late = it.get("late")
        if late and late["kind"] == "kesin":
            warnings.append({"key": f"kesin:{it['key']}", "level": "bad", "title": it["title"],
                             "text": f"{late['warning']} {late['text']}."})
        elif late:
            warnings.append({"key": f"soz:{it['key']}", "level": "info", "title": it["title"],
                             "text": f"{late['text']}. {late['hint']}"})
        elif it.get("promise") and it["promise"].get("missed"):
            warnings.append({"key": f"soz:{it['key']}", "level": "info", "title": it["title"], "text": it["promise"]["text"]})
        if it["kind"] == "fixed" and it["overdue"] and it["schedule"].get("next"):
            nx = it["schedule"]["next"]
            warnings.append({"key": f"taksit:{it['key']}", "level": "warn", "title": it["title"],
                             "text": f"{nx['no']}. taksitin günü ({_day_label(nx['date'])}) geçti, ödenmemiş görünüyor. "
                                     "Ödediysen Ödedim'le işaretle."})
    day_notes = [{"key": it["key"], "title": it["title"], "text": it["day_note"]["text"]} for it in debts
                 if it.get("day_note")]

    # aylık tutar bu ayın taksitlerine yetmiyor: taksitler ödenmesi gereken tutarlardır (kalanı kartlara ve borçlara)
    fixed_short = None
    if sim and sim["months"] and sim["months"][0]["fixed_short"] > 0 and monthly:
        m0 = sim["months"][0]
        fixed_short = {"due": m0["fixed_due"], "short": m0["fixed_short"],
                       "title": "Aylık tutar bu ayın taksitlerine yetmiyor",
                       "text": f"Ödeme planı olan borçlarının bu ayki taksitleri {_tl(m0['fixed_due'])}. Aylık tutar "
                               "önce bunlara ayrılır; kalanı kartlara ve diğer borçlara gider."}

    shortfall = None
    if sim and sim["interest"] > 0 and cards:
        shortfall = {
            "min_monthly": need, "interest": None if fees_hidden else sim["interest"],
            "title": "Bu tutarla kart faizi önlenemiyor",
            "text": (f"Aylık tutarı {_tl(need)} yaparsan kart borçların son ödeme günlerine kadar kapanır, faiz işlemez."
                     + ("" if fees_hidden else f" Bu tutarla kartlarda yaklaşık {_tl(sim['interest'])} faiz ve vergi işler.")),
            "hint": "Bu tutar bu ay mümkün değilse: son ödeme gününden önce en az asgari tutarı, elinden geldiğince "
                    "fazlasını yatır. Asgari gecikme faizini önler; üstüne yatırdığın her lira kalan borcun faizini "
                    "azaltır. Kumbaranda ya da hesabında birikmiş para varsa önce kart borcunu kapatmak faizden korur.",
            "allocation": "Tutar yetmediği ayda plan önce her kartın asgarisini, o ayın taksitlerini ve vadesi gelen "
                          "kesin borçları tarih sırasıyla koyar; kalanı faizi en yüksek karta gider, o kapanınca "
                          "sıradakine. Kartlar bitince diğer borçlara geçer.",
        }
    stuck = None
    if sim and sim["debt_free"] is None and monthly:
        stuck = {"title": "Bu tutarla 10 yılda kapanmıyor",
                 "text": "Aylık tutarı artırabilirsen plan kısalır; Bütçeler ve Tasarruf'ta kısılabilecek harcamalara bak."}

    # yalnız diğer borç varken (kart yok: faiz uyarısı çıkmaz) nötr bir satır: bu tutarla diğer borçlar kaç ayda biter
    pace = None
    if sim and people and not cards and monthly:
        closes_p = [sim["closes"].get(d["key"]) for d in people]
        if all(closes_p):
            n = _months_between(start, max(closes_p)) + 1
            pace = {"months": n, "text": "Bu tutarla diğer borçlar bu ay biter." if n == 1 else
                    f"Bu tutarla diğer borçlar {n} ayda biter."}

    total_open = _r(sum(d["remaining"] for d in (*fixed, *cards, *people)))
    by_key = {d["key"]: d for d in debts}
    # aynı kişinin borçları açılınca ayırt edilsin: not ya da borç tarihi ("3 Mart 2026 borcu")
    subs = {d["key"]: d["note"] or f"{_day_label(d['debt_date'])} {d['debt_date'][:4]} borcu"
            for d in (*people, *people_done) if d.get("debt_date")}
    items = []
    if sim and sim["months"] and sim["months"][0]["month"] == start:
        for p in sim["months"][0]["payments"]:
            d = by_key[p["key"].removesuffix(PK)]
            if p.get("payoff"):
                items.append(_jar_item(_payoff_item(p, d, sim, start, fees_hidden, subs.get(d["key"])), d, jflags))
                continue
            full = money.ge(p["amount"] + (p.get("from_saved") or 0.0), d["remaining"])
            harm = d.get("harm")
            asg = harm["min_left"] if harm and harm.get("min_left") else None
            extra: dict = {}
            if d["kind"] == "fixed":
                nx = d["schedule"].get("next") or {}
                due_m = d["schedule"]["due_now"]
                nos = d.get("due_nos") or []
                if len(nos) > 1:  # geçmişte ödenmemiş taksit de bu aya geldi
                    text = f"{nos[0]}–{nos[-1]}. taksit · gecikmiş taksit dahil"
                else:
                    text = (f"{nx['no']}. taksit" if nx else "Taksit") + (f" · son gün {d['due_label']}" if d["due_label"] else "")
                if due_m and p["amount"] + TOL < due_m:
                    text += f" · bu ayki taksit {_tl(due_m)}"
            elif p.get("saving"):
                sv = d.get("saving") or {}
                text = sv.get("text") or "Biriktir; tamamı birikince tek seferde öde."
                extra = {"saving": True, "saved": p.get("saved")}
            elif p.get("from_saved"):
                text = f"Tamamını tek seferde öde ({_tl(p['from_saved'])} birikimden)"
                extra = {"from_saved": p["from_saved"], "pay_total": _r(p["amount"] + p["from_saved"])}
            else:
                text = (("Tamamını öde" if full else "Bu ay bu kadarını öde")
                        + (f" (asgari {_tl(asg)} dahil)" if asg and p["amount"] >= asg else "")
                        + (f" · son ödeme {d['due_label']}" if d["kind"] == "card" and d["due_date"] else ""))
                if d["kind"] == "person" and d.get("strict") and d.get("due_label"):
                    text += f" · vade {d['due_label']}"
            if p.get("units_text"):
                text += f" · {p['units_text']} (≈{_tl(p['amount'])})"
                extra.update({"asset": p["asset"], "units": p["units"], "units_text": p["units_text"],
                              "approx_note": PRICE_NOTE})
            items.append(_jar_item({"key": p["key"], "kind": d["kind"], "title": d["title"], "amount": p["amount"],
                                    "full": full and not p.get("saving"), "done": False, "due_date": d["due_date"],
                                    "due_label": d["due_label"], "min_left": asg, "text": text, "sub": subs.get(p["key"]),
                                    "payable": d["payable"], **extra}, d, jflags))
    for d in (*fixed, *cards, *people):
        if d["paid_now"] > 0:
            items.append({"key": d["key"], "kind": d["kind"], "title": d["title"], "amount": d["paid_now"], "full": False,
                          "done": True, "due_date": d["due_date"], "due_label": None, "sub": subs.get(d["key"]),
                          "text": f"Bu ay {_tl(d['paid_now'])} ödendi", "payable": d["payable"]})
    for d in (*fixed, *people):  # bu ay bağlı kumbaraya ayrılan (birikim; borç düşmez)
        if d.get("jar_now"):
            j = d["jar"]
            items.append({"key": d["key"] + (PK if d["kind"] == "fixed" else ""), "kind": d["kind"], "title": d["title"],
                          "amount": d["jar_now"], "full": False, "done": True, "saving": True, "due_date": None,
                          "due_label": None, "sub": subs.get(d["key"]), "jar_id": j["id"],
                          "text": f"Bu ay kumbaraya {_tl(d['jar_now'])} ayrıldı",
                          "payable": d["payable"] if d["kind"] == "person" else None,
                          **({"payoff": True} if d["kind"] == "fixed" else {})})
    for d in (*cards_done, *people_done):
        items.append({"key": d["key"], "kind": d["kind"], "title": d["title"], "amount": d["paid"], "full": True,
                      "done": True, "due_date": None, "due_label": None, "sub": subs.get(d["key"]), "text": "Bu ay kapandı",
                      "payable": d["payable"]})

    df = sim["debt_free"] if sim else None
    debt_free = None
    if df and total_open > 0:
        n = _months_between(start, df) + 1
        debt_free = {"month": df, "label": month_label(df), "months": n,
                     "text": f"Bu planla {month_label(df)} sonunda borçsuzsun" + (f" ({n} ay)." if n > 1 else ".")}
        if shortfall:  # faiz işleyen plan başarı gibi okunmasın
            debt_free["interest"] = True
            debt_free["text"] += (" Bu tutarla kartlarda faiz işler." if fees_hidden else
                                  f" Bu tutarla kartlarda yaklaşık {_tl(sim['interest'])} faiz işler.")
        last_inst = max((d["future_installments"]["last_month"] for d in cards if d.get("future_installments")), default=None)
        if last_inst and last_inst > df:
            debt_free["installments_until"] = {"month": last_inst, "label": month_label(last_inst)}
            debt_free["text"] += f" Kart taksitlerinin son ayı: {month_label(last_inst)}."

    commit_raw = _store()
    commitment = _commitment(commit_raw["current"], cards, people, sim, monthly, order, today, people_hidden,
                             any(d.get("harm") for d in debts), fixed) if commit_raw["current"] else None
    offer = None
    if not commitment and (cards or people or fixed) and monthly:
        offer = {"title": "Bu planla başla",
                 "text": "Planı başlatırsan her ay plana göre nerede olduğunu gösteririz. Tutarı denemek planı "
                         "değiştirmez; değiştirmek istediğinde \"Bu planla devam\" dersin.",
                 "action": "Planı başlat"}
        if shortfall:  # faizli plan: ikincil seçenek, uyarıdan sonra (istemci düğmeyi ikincil gösterir)
            offer = {"title": "Bu tutar faizi önlemiyor",
                     "text": "Yine de bu planla başlamak istersen her ay plana göre nerede olduğunu gösteririz. "
                             f"Aylık tutarı {_tl(need)} yaparsan kartlarda faiz işlemez.",
                     "action": "Planı başlat", "secondary": True}
    history = [{"started_at": h["started_at"], "start_month": h["start_month"], "monthly": h["monthly"],
                "ended_at": h.get("ended_at"),
                "label": f"Önceki plan: {month_in(h['start_month'], start)} başladı · ayda {_tl(h['monthly'])}"}
               for h in commit_raw["history"]]

    return {
        "available": True,
        "today": today.isoformat(), "month": start, "month_label": month_label(start),
        "settings": saved, "monthly": monthly, "monthly_source": source, "suggestion": sug,
        "order": order, "orders": [{"key": k, "label": v[0], "text": v[1]} for k, v in ORDER_INFO.items()],
        "intro": "Borçların tek listede. Önce kart borçları ve tarihi kesin olanlar: gecikme olmasın, kartta faiz "
                 "işlemesin. Sonra diğer borçlar söz verdiğin tarihe göre. Aylık tutar, her zamanki harcamaların ve "
                 "taksitlerin dışında borçlara ayırabileceğin para.",
        "totals": {"open": total_open, "cards": _r(sum(d["remaining"] for d in cards)),
                   "people": _r(sum(d["remaining"] for d in people)), "count": len(cards) + len(people) + len(fixed),
                   "card_count": len(cards), "person_count": len(people), "paid_this_month": paid_now,
                   # ödeme planı olan borçlar (yetenek debt_schedule): kalan toplam, sayı, bu ayki taksitler
                   "fixed": _r(sum(d["remaining"] for d in fixed)), "fixed_count": len(fixed),
                   "fixed_this_month": _r(sum(d["dues"].get(start, 0.0) for d in fixed)),
                   "min_monthly_no_interest": need,
                   "interest_projected": None if fees_hidden or not sim else sim["interest"],
                   # erken kapatmayla kurtulunan yaklaşık faiz (yetenek debt_payoff); faiz gizliyse None
                   "payoff_saving": None if fees_hidden or not sim else _r(sum(v["saved"] for v in sim["payoff_by"].values())),
                   "saving_this_month": _r(sum(i["amount"] for i in items if i.get("saving")))},
        "debts": debts,
        "months": sim["months"] if sim else [],
        "debt_free": debt_free,
        "this_month": {"month": start, "label": month_label(start), "budget": first if sim else None,
                       "planned": _r(sum(i["amount"] for i in items if not i["done"])), "paid": paid_now, "items": items,
                       "groups": group_items(items)},
        "rules_info": RULES_INFO,
        "warnings": warnings, "shortfall": shortfall, "fixed_short": fixed_short, "pace": pace, "stuck": stuck,
        "day_notes": day_notes, "price_note": PRICE_NOTE if any(d.get("price") for d in people) else None,
        "excluded": other,
        "hidden": {"fees": fees_hidden, "people": people_hidden},
        "can_edit": _can_edit(),
        "commitment": commitment, "commit_offer": offer, "commit_history": history, "faiz_info": FAIZ_INFO,
        # borca bağlı kumbara (yetenek debt_jar): istemci düğmeleri buna göre gösterir; Premium değilse lock (nötr)
        "debt_jar": {**jflags, "lock": None if jflags["premium"] else JAR_LOCK,
                     "saved_this_month": _jar_now([*people, *fixed])},
        "summary_card": {"eligible": len(cards) + len(people) + len(fixed) >= 2},
        "empty": None if cards or people or fixed else {
            "title": "Açık borcun görünmüyor",
            "text": "Kart ekstrelerin ödenmiş, Borç / Alacak'ta açık borcun yok. Başka bir borcun varsa Borç / "
                    "Alacak'tan ekle."},
    }


PAYOFF_NOTE = ("Kapatma tutarı ödenen her taksitle azalır; buradaki tutarlar yaklaşık. Kapatmadan önce güncel tutarı "
               "sor. Bankalar kısmi erken ödemeyi de kabul eder; yaparsan ödeme planını bankanın yeni planına göre güncelle.")


def _loan_word(d: dict) -> tuple[str, str]:
    """("Krediyi", "Bankana") bankada; diğer alacaklıda ("Bu borcu", "Alacaklına")."""
    return ("Krediyi", "Bankana") if d.get("creditor_type") == "banka" else ("Bu borcu", "Alacaklına")


def _payoff_row(d: dict, sim: dict | None, start: str, fees_hidden: bool, has_monthly: bool) -> dict | None:
    """Ödeme planlı borcun erken kapatma satırı (yalnız faiz "var"): girilmemişse nötr ipucu (ask); girildiyse planın
    kapatma ayı ve kurtulunan yaklaşık faiz (plan), kazandırmıyorsa no_benefit, bu tutarla taksitler bitmeden
    birikmiyorsa not_reached, aylık tutar yoksa bugünkü tutar (known)."""
    sch = d["schedule"]
    if sch.get("interest") != "var":
        return None
    obj, who = _loan_word(d)
    info = sch.get("payoff")
    if not d.get("payoff_base") or not info:
        return {"state": "ask", "title": "Erken kapatma",
                "text": f"{who} bugün kapatma tutarını sor; yazarsan erken kapatıp faizden kurtulma planı çıkar."}
    base = {"state": None, "title": "Erken kapatma", "entered": info["amount"], "entered_date": info["date"],
            "rate": info.get("rate"), "now": None if fees_hidden else info["now"], "note": PAYOFF_NOTE}
    if not info["benefit"]:
        return {**base, "state": "no_benefit",
                "text": "Kapatma tutarı kalan taksitlerden az değil; erken kapatmak faiz kazandırmıyor. Taksitler "
                        "planındaki gibi ödenir."}
    pb = (sim or {}).get("payoff_by", {}).get(d["key"])
    if pb:
        when = month_in(pb["month"], start)
        text = (f"{obj} {when} kapatırsan kalan faizden kurtulursun." if fees_hidden else
                f"{obj} {when} kapatırsan yaklaşık {_tl(pb['saved'])} faizden kurtulursun (kalan taksitler "
                f"{_tl(pb['installments'])}, kapatma tutarı {_tl(pb['amount'])}).")
        return {**base, "state": "plan", "month": pb["month"], "label": month_label(pb["month"]),
                "amount": None if fees_hidden else pb["amount"],
                "installments": None if fees_hidden else pb["installments"],
                "saved": None if fees_hidden else pb["saved"], "text": text}
    if sim and has_monthly:
        return {**base, "state": "not_reached",
                "text": "Bu aylık tutarla kapatma tutarı taksitler bitmeden birikmiyor; taksitler planındaki gibi "
                        "ödenir. Aylık tutarı artırırsan plan erken kapatmayı hesaplar."}
    text = ("Bugün kapatırsan kalan faizden kurtulursun." if fees_hidden else
            f"Bugün kapatırsan yaklaşık {_tl(info['now'])} ödersin; kalan taksitler {_tl(info['left'])}.")
    return {**base, "state": "known", "text": text + " Aylık tutarı yazınca plan erken kapatmayı hesaplar."}


def _payoff_item(p: dict, d: dict, sim: dict, start: str, fees_hidden: bool, sub: str | None) -> dict:
    """"Bu ay ödenecekler"de erken kapatma satırı: birikim ya da kapatma (Kapattım: POST /api/debts/{id}/pay
    {amount, close_early: true})."""
    obj, _ = _loan_word(d)
    pb = sim["payoff_by"].get(d["key"])
    row = {"key": p["key"], "kind": "fixed", "title": d["title"], "amount": p["amount"], "done": False,
           "due_date": None, "due_label": None, "min_left": None, "sub": sub, "payable": None, "payoff": True}
    if p.get("saving"):
        if pb:
            text = (f"{obj} erken kapatmak için biriktir: {month_in(pb['month'], start)} kapatma tutarı birikmiş olur, "
                    "tek seferde kapat." if fees_hidden else
                    f"{obj} erken kapatmak için biriktir: {month_in(pb['month'], start)} {_tl(pb['amount'])} birikmiş "
                    "olur, tek seferde kapat.")
        else:
            text = f"{obj} erken kapatmak için biriktir; kapatma tutarı birikince tek seferde kapat."
        return {**row, "full": False, "saving": True, "saved": p.get("saved"), "text": text}
    total = _r(p["amount"] + (p.get("from_saved") or 0.0))
    text = f"{obj} erken kapat: {_tl(total)}" + (f" ({_tl(p['from_saved'])} birikimden)" if p.get("from_saved") else "")
    if not fees_hidden and pb:
        text += f" · yaklaşık {_tl(pb['saved'])} faizden kurtulursun"
    return {**row, "full": True, "from_saved": p.get("from_saved") or 0.0, "pay_total": total, "text": text,
            "close_early": {"debt_id": d["id"], "amount": total, "label": "Kapattım",
                            "confirm": f"{d['title']}: {_tl(total)} ödeyip erken kapattın mı? Borç bu ödemeyle kapanır, "
                                       "bankanın almadığı kalan faiz düşer."}}


# ---------------------------------------------------------------- borca bağlı kumbara (yetenek debt_jar)

JAR_LOCK = {"state": "locked", "label": "Kumbarayla takip et", "badge": "Premium",
            "text": "Bu birikimi kumbarada ayrı tutmak Premium'da. Plan birikimi bugünkü gibi gösterir."}
JAR_OFFER_TEXT = ("Bu birikimi kumbarada ayrı tut: her ay ayırdığın orada durur, harcanabilir paradan düşer; plan "
                  "kumbaradaki gerçek birikime göre hesaplanır.")
JAR_PAY_LABEL = "Kumbaradan öde"
JAR_MOVE_LABEL = "Kumbaraya ayır"
JAR_MOVE_NOTE = "Borç planı"


def _jar_flags() -> dict:
    """Kumbara düğmeleri: available (kumbara sayfası açık), premium (yeni kumbara açma hakkı), can_open (sahip / tam
    yetkili ve Premium), can_move (ayırma: "Sadece görür" hariç; var olan kumbara her pakette kullanılır), can_pay
    (borç ödemesi: sahip / tam yetkili). Yetkiler sunucuda da uygulanır (book.denied, plans.gate_for)."""
    from app import book, plans

    r = book.active()
    available = not (r and "hesaplar" in r["hidden_pages"])
    m = book.member()
    role = book.role(m) if m else "owner"
    premium = plans.allows(db.current_user(), "kumbara")
    manage = available and role in ("owner", "full")
    return {"available": available, "premium": premium, "can_open": manage and premium,
            "can_move": available and role != "viewer", "can_pay": manage}


def _month_end(month: str) -> str:
    import calendar

    y, m = map(int, month.split("-"))
    return f"{month}-{calendar.monthrange(y, m)[1]:02d}"


def _saving_rows(sim: dict | None, key: str) -> list[float]:
    return [p["amount"] for mo in (sim or {}).get("months", []) for p in mo["payments"]
            if p["key"] == key and p.get("saving") and p["amount"] > 0]


def _payoff_now(d: dict) -> float | None:
    info = (d.get("schedule") or {}).get("payoff") or {}
    return info.get("now")


def _jar_offer(d: dict, sim: dict | None, start: str) -> dict:
    """"Kumbara aç"ın değerleri (sunucu POST /api/debts/{id}/jar aynısını kullanır): ad, hedef, son tarih, aylık."""
    if d["kind"] == "fixed":
        obj = "kredisini kapat" if d.get("creditor_type") == "banka" else "borcunu kapat"
        name = f"{d['title']} {obj}"
        goal = _payoff_now(d)
        pb = (sim or {}).get("payoff_by", {}).get(d["key"])
        month = pb["month"] if pb else None
        rows = _saving_rows(sim, d["key"] + PK)
    else:
        name = f"{d['title']} borcu"
        goal = d["remaining"]
        month = (sim or {}).get("closes", {}).get(d["key"]) or _due_m(d)
        rows = _saving_rows(sim, d["key"])
    monthly = _r(sum(rows) / len(rows)) if rows else None
    return {"name": name[:60], "goal": _r(goal) if goal else None,
            "deadline": _month_end(max(month, start)) if month else None,
            "deadline_label": month_label(month) if month else None, "monthly": monthly or None}


def _jar_pay(d: dict, j: dict, item: dict, sim: dict | None, start: str, fees_hidden: bool) -> dict | None:
    """"Kumbaradan öde": ödeme ayı geldiyse ya da kumbara hedefe ulaştıysa. Tek seferde borçta tutar kumbaradaki kadar
    (en çok kalan borç; azsa kısmi), kredide güncel tahmini kapatma tutarı (kullanıcı bankanın tutarını yazar)."""
    bal = _r(j["balance"])
    if bal <= 0:
        return None
    t = d["title"]
    if d["kind"] == "fixed":
        est = _payoff_now(d)
        pb = (sim or {}).get("payoff_by", {}).get(d["key"])
        if not est or not (money.ge(bal + TOL, est) or (pb and pb["month"] <= start)):
            return None
        # plan bu ay kapatıyorsa planın tutarı (bu ayın taksitinden sonra; satırdaki tutarla aynı), yoksa bugünkü tahmin
        amount = _r(pb["amount"] if pb and pb["month"] <= start else est)
        return {"label": JAR_PAY_LABEL, "jar_id": j["id"], "debt_id": d["id"], "amount": amount,
                "from_jar": _r(min(bal, amount)), "close_early": True, "partial": False,
                "leftover": _r(max(0.0, bal - amount)),
                "text": (f"Kumbarada {_tl(bal)} birikti. Bankana güncel kapatma tutarını sor, krediyi kumbaradan kapat."
                         if not fees_hidden else "Kumbaradaki birikimle krediyi kapatabilirsin."),
                "confirm": f"{t}: bankaya ödediğin kapatma tutarını yaz. Tutar kumbaradan düşer, borç bu ödemeyle "
                           "kapanır; bankanın almadığı kalan faiz düşer."}
    rem = d["remaining"]
    close = (sim or {}).get("closes", {}).get(d["key"])
    due = _due_m(d)
    if not (money.ge(bal + TOL, rem) or (close and close <= start) or (due and due <= start)):
        return None
    amount = _r(min(bal, rem))
    partial = money.lt(bal + TOL, rem)
    extra = _r(bal - rem) if money.gt(bal, rem + TOL) else 0.0
    if partial:
        text = f"Ödeme zamanı geldi: kumbarada {_tl(bal)}, borç {_tl(rem)}."
        confirm = (f"{t}: kumbaradaki {_tl(amount)} ödensin mi? Kalan {_tl(rem - amount)} borçta kalır; kumbara boşalır, "
                   "kalanı için ayırmaya devam edersin.")
    elif extra:
        text = f"Kumbarada {_tl(bal)} var; borcun tamamı kumbaradan ödenebilir."
        confirm = (f"{t}: {_tl(amount)} kumbaradan ödensin mi? Borç kapanır; artan {_tl(extra)} kumbarada kalır, "
                   "sonra ne yapacağını seçersin.")
    else:
        text = "Borcun tamamı kumbarada birikti."
        confirm = f"{t}: {_tl(amount)} kumbaradan ödensin mi? Borç kapanır, kumbara kapanır."
    return {"label": JAR_PAY_LABEL, "jar_id": j["id"], "debt_id": d["id"], "amount": amount, "from_jar": amount,
            "close_early": False, "partial": partial, "leftover": extra, "text": text, "confirm": confirm}


def _jar_info(d: dict, item: dict, sim: dict | None, start: str, flags: dict, fees_hidden: bool) -> dict | None:
    """debts[].jar: linked (bağlı kumbara: bakiye, hedef, pay) · offer ("Kumbara aç": ad, hedef, son tarih, aylık) ·
    locked (Premium değil: nötr öneri; plan bugünkü gibi) · None (uygun değil, kumbara sayfası kapalı ya da yetki yok)."""
    j = d.get("jar")
    if j:
        bal, goal = _r(j["balance"]), j.get("goal")
        out = {"state": "linked", "id": j["id"], "name": j["name"], "balance": bal, "goal": goal,
               "pct": min(100.0, round(bal / goal * 100, 1)) if goal else None, "used": bool(d.get("jar_used")),
               "text": f"Kumbarada {_tl(bal)} birikti" + (f" · hedef {_tl(goal)}" if goal else ""),
               "can_move": flags["can_move"], "pay": None}
        if flags["can_pay"] and (d["kind"] != "fixed" or d.get("payoff_base")):
            out["pay"] = _jar_pay(d, j, item, sim, start, fees_hidden)
        return out
    if not flags["available"] or not _jar_usable(d):
        return None
    if d["kind"] == "fixed" and (item.get("payoff") or {}).get("state") != "plan":
        return None  # erken kapatma planı yoksa (tutar girilmemiş, kazandırmıyor) kumbara önerilmez
    if d["kind"] != "fixed" and not item.get("saving"):
        return None  # plan bu borç için birikim ayırmıyor (bu ay tek seferde kapanıyor ya da aylık tutar yok)
    if not flags["premium"]:
        return dict(JAR_LOCK)
    if not flags["can_open"]:
        return None
    return {"state": "offer", "label": "Kumbara aç", "text": JAR_OFFER_TEXT, **_jar_offer(d, sim, start)}


def jar_offer_for(debt_id: int, today: date | None = None) -> dict:
    """POST /api/debts/{id}/jar için değerler (planın "Kumbara aç" satırıyla aynı): {name, goal, deadline, monthly}.
    LookupError: borç planda yok; db.DebtJarExists: açık kumbarası var; ValueError: kumbara bu borca uygun değil."""
    today = today or date.today()
    plan = build(today=today)
    it = next((d for d in plan["debts"] if d["kind"] != "card" and d["id"] == debt_id), None)
    if it is None:
        raise LookupError("Bu borç planda yok")
    j = it.get("jar") or {}
    if j.get("state") == "linked":
        raise db.DebtJarExists("Bu borcun zaten bir kumbarası var")
    if j.get("state") == "offer":
        return {k: j[k] for k in ("name", "goal", "deadline", "monthly")}
    tek = it["kind"] == "person" and it.get("terms") == "tek" and (it.get("asset") or "TRY") == "TRY"
    loan = it["kind"] == "fixed" and (it.get("payoff") or {}).get("state") in ("plan", "not_reached", "known")
    if not (tek or loan):
        raise ValueError("Kumbara, tek seferde ödenecek borçta ya da erken kapatma tutarı girilmiş kredide açılır")
    o = _jar_offer(it, None, _ym(today))  # plan bu ay birikim ayırmıyor: ad ve hedef yine borçtan
    return {k: o[k] for k in ("name", "goal", "deadline", "monthly")}


def _jar_item(row: dict, d: dict, flags: dict) -> dict:
    """"Bu ay ödenecekler" satırına kumbara düğmesi (d: debts[] satırı, jar bilgisiyle): birikimde "Kumbaraya ayır" (bu
    ayın birikimi tek dokunuşla kumbaraya), ödeme/kapatma satırında hazırsa "Kumbaradan öde"."""
    info = d.get("jar")
    if not info or info.get("state") != "linked" or not info.get("used"):
        return row
    if d["kind"] == "fixed" and not row.get("payoff"):
        return row  # taksit satırı: kumbara yalnız erken kapatma içindir
    row["jar_id"] = info["id"]
    if row.get("saving"):
        if flags["can_move"]:
            row["jar_action"] = {"label": JAR_MOVE_LABEL, "jar_id": info["id"], "amount": row["amount"],
                                 "note": JAR_MOVE_NOTE}
    elif info.get("pay"):
        row["jar_pay"] = info["pay"]
    return row


RULES_INFO = (f"Önce kart borçları, o ayın taksitleri ve tarihi kesin borçlar: tutar yetmezse önce her birinin "
              "gecikmesi önlenir (kartta asgari, taksitte o ayın taksiti), kalanı karta. Tarihi kesin bir borç ileride "
              "ise ona yalnız tarihine yetişmek için gereken kadar ayrılır. Sonra diğer borçlar söz tarihine göre: aynı "
              "aya düşenlerde para hepsine yetmiyorsa kapanabilenler önce kapanır (önce tek seferde ödenecek olan, "
              "sonra küçük borçlar); hiçbirine yetmiyorsa parça ödenebilenlere anlamlı paylar, tek seferde ödenecek olana "
              "birikim ayrılır. Faizli bir ödeme planına kapatma tutarı yazdıysan, kartlardan ve kesin tarihlerden artan "
              "para önce krediyi erken kapatmak için biriktirilir (söz tarihi gelmiş borç varken en çok yarısı); yetince "
              f"kredi tek seferde kapanır. Ödemeler kişi kişi toplanır ve ₺{ROUND_TO}'nin katlarına yuvarlanır; bir kişiye bir "
              f"ayda en az ₺{MIN_PAY} ödenir (kalanı daha azsa kalanın tamamı); son ödemede ₺{FOLD}'den az kalan "
              "bırakılmaz. Kart borcu ve taksitler tam tutarıyla yazılır.")


def group_items(items: list[dict]) -> list[dict]:
    """"Bu ay ödenecekler" kişi kişi: aynı kişinin diğer borçları tek satır (açınca borç borç); birikim satırları ayrı.
    Kart ve taksit tek başına. {key, kind, title, count, amount, done, full, text, due_label, items, payable (tek
    borçta), saving?}."""
    out: list[dict] = []
    by: dict[tuple, dict] = {}
    for it in items:
        if it["kind"] != "person":
            out.append({**it, "count": 1, "items": [it]})
            continue
        gk = (person_key(it["title"]), it["done"], bool(it.get("saving")))
        g = by.get(gk)
        if g is None:
            g = by[gk] = {"key": f"kisi:{gk[0]}:{'d' if it['done'] else 'p'}" + (":birikim" if gk[2] else ""),
                          "kind": "person", "title": it["title"], "done": it["done"], "items": []}
            if gk[2]:
                g["saving"] = True
            out.append(g)
        g["items"].append(it)
    for g in by.values():
        xs = g["items"]
        n = len(xs)
        g["count"] = n
        g["amount"] = _r(sum(x["amount"] for x in xs))
        g["full"] = all(x["full"] for x in xs)
        g["due_label"] = min((x["due_label"] for x in xs if x.get("due_label")), default=None)
        g["payable"] = xs[0]["payable"] if n == 1 else None
        if n == 1:
            g["text"] = xs[0]["text"]
            for k in ("units_text", "from_saved", "pay_total", "jar_id", "jar_action", "jar_pay"):
                if xs[0].get(k) is not None:
                    g[k] = xs[0][k]
        elif g["done"]:
            g["text"] = f"{n} borç · bu ay {_tl(g['amount'])} ödendi"
        elif g.get("saving"):
            g["text"] = f"{n} borç için biriktir; tamamı birikince tek seferde öde"
        else:
            closing = sum(1 for x in xs if x["full"])
            g["text"] = f"{n} borç · " + ("hepsi bu ay kapanır" if closing == n else
                                          f"{closing} tanesi bu ay kapanır" if closing else "bu ay bu kadarını öde")
    return out

def _card_warning(d: dict, fees_hidden: bool) -> dict:
    """Kartın bilgi uyarısı: yalnız asgariyi ödersen ne kadar faiz işler (seçenek değil, uyarı). rate_note: hesabın
    dayanağı (ekstredeki oran ya da genel varsayım)."""
    rate = d["rate"]["monthly"]
    pct = f"%{rate * 100:.2f}".replace(".", ",")
    note = (f"Hesap: ekstrendeki aylık {pct} faiz oranı ve vergiler (BSMV, KKDF)." if d["rate"]["source"] == "ekstre"
            else f"Hesap yaklaşık: aylık {pct} faiz ve vergiler (BSMV, KKDF); bankanın oranı ekstrende yazar.")
    if d["overdue"]:
        return {"interest_if_min": None, "rate_note": None, "warning":
                "Son ödeme günü geçti; kalan borca gecikme faizi işliyor. Tamamını bir an önce ödemek faizi durdurur."}
    if fees_hidden:
        return {"interest_if_min": None, "rate_note": None, "warning":
                "Tamamı son ödeme gününe kadar ödenmezse kalan borca faiz işler."}
    min_left = max(0.0, (d["min_payment"] or 0) - d["paid_now"]) if d["min_payment"] else None
    carried = d["remaining"] - min_left if min_left is not None else d["remaining"]
    if carried <= 0:
        return {"interest_if_min": 0.0, "rate_note": None, "warning": None}
    x = _r(carried * _eff(rate))
    if min_left is not None:
        text = f"Bu kartta yalnız asgariyi ödersen bu ay yaklaşık {_tl(x)} faiz işler. Tamamını öde, faiz işlemesin."
    else:
        text = f"Tamamı ödenmezse kalan borca bu ay yaklaşık {_tl(x)} faiz işler. Tamamını öde, faiz işlemesin."
    return {"interest_if_min": x, "rate_note": note, "warning": text}


def _paid_now(cards, people, cards_done, people_done) -> float:
    return _r(sum(d["paid_now"] for d in (*cards, *people)) + sum(d["paid"] for d in (*cards_done, *people_done)))


def _whole(x: float) -> float:
    return float(math.floor(max(0.0, x) + 0.5))


def _harm(d: dict, sim: dict, start: str, fees_hidden: bool) -> dict | None:
    """Zararı azaltma: plandaki tutar kartın tamamına son ödeme gününe kadar yetmiyorsa ne yapılır (yalnız bu durumda).
    Son ödeme günü geçmiş kartta yok (orada uyarı: tamamını bir an önce öde)."""
    if d["overdue"] or not d["due_date"]:
        return None
    due_m = d["due_date"][:7]
    if due_m < start:
        return None
    planned = _r(sum(p["amount"] for mo in sim["months"] if mo["month"] <= due_m for p in mo["payments"]
                     if p["key"] == d["key"]))
    rem = d["remaining"]
    if money.ge(planned, rem):
        return None
    eff = _eff(d["rate"]["monthly"])
    mp = d.get("min_payment")
    min_left = _r(min(rem, max(0.0, mp - (d["paid"] or 0.0)))) if mp else None
    label = _day_label(d["due_date"])
    when = "Bu ay" if due_m == start else f"{AYLAR[int(due_m[5:]) - 1]} ayında"
    title = f"{when} tamamını kapatamıyorsun"
    if min_left is None:
        s1 = f"Son ödeme gününden ({label}) önce en az asgari tutarı (ekstrende yazar) yatır — gecikme faizi işlemez."
    elif min_left <= 0:
        s1 = "Asgari tutarı yatırmışsın; gecikme faizi işlemez."
    else:
        s1 = f"Son ödeme gününden ({label}) önce en az asgari tutarı ({_tl(min_left)}) yatır — gecikme faizi işlemez."
    below = min_left is not None and planned < min_left
    y = _whole((rem - planned) * eff)  # yaklaşık: tam liraya
    if fees_hidden:
        s2 = "Asgarinin üstüne ne kadar yatırırsan faiz o kadar azalır; tamamını ödersen faiz işlemez."
    elif below:
        s2 = (f"Planladığın {_tl(planned)} asgarinin altında kalıyor; asgariyi tamamlayabilirsen gecikme faizi "
              f"işlemez. Asgarinin üstüne ne kadar yatırırsan faiz o kadar azalır (tamamını ödersen ₺0).")
    else:
        s2 = (f"Asgarinin üstüne ne kadar yatırırsan faiz o kadar azalır: planladığın {_tl(planned)} ile bu ay "
              f"yaklaşık {_tl(y)} faiz işler (tamamını ödersen ₺0).")
    compare = None
    if not fees_hidden:
        # sıra: Tamamı (₺0 faiz) önce, yalnız asgari en son: göz önce faizsiz yola gitsin
        compare = [{"key": "full", "label": "Tamamı", "amount": rem, "interest": 0.0, "late": False},
                   {"key": "plan", "label": "Planladığın", "amount": planned, "interest": y, "late": below}]
        if min_left is not None and 0 < min_left < rem:  # asgari borcun tamamıysa "Tamamı" ile aynı satır
            compare.append({"key": "asgari", "label": "Yalnız asgari", "amount": min_left,
                            "interest": _whole((rem - min_left) * eff), "late": False})
    return {"title": title, "steps": [s1, s2], "text": f"{title}. Faizi en aza indirmek için: 1) {s1} 2) {s2}",
            "min_left": min_left, "planned": planned, "interest_planned": None if fees_hidden else y,
            "below_min": below, "due_label": label, "compare": compare, "info": FAIZ_INFO}


# ---------------------------------------------------------------- plan başlat (anlık görüntü)

def _store() -> dict:
    try:
        raw = json.loads(db.get_setting(COMMIT) or "{}")
    except ValueError:
        raw = {}
    cur = raw.get("current") if isinstance(raw.get("current"), dict) else None
    hist = [h for h in raw.get("history") or [] if isinstance(h, dict)]
    return {"current": cur, "history": hist}


def _ident(d: dict) -> str:
    return f"card:{d['card_no']}" if d["kind"] == "card" else f"debt:{d['id']}"


def commit_plan(monthly: float | None = None, order: str | None = None, today: date | None = None) -> dict:
    """Bugünkü borçlar ve tutarla planı kaydet (eskisi geçmişe). Aylık tutar ve sıralama ayar olarak da kaydedilir."""
    today = today or date.today()
    start = _ym(today)
    saved = settings()
    order = norm_order(order) or saved["order"]
    if monthly is None:
        monthly = saved["monthly"]
        if monthly is None:
            sug = suggestion(today)
            monthly = sug["amount"] if sug else None
    if not monthly or monthly <= 0:
        raise ValueError("Önce her ay borçlara ayırabileceğin tutarı yaz.")
    monthly = _r(monthly)
    cards, cards_done = _cards(today)
    people, _, people_done, fixed = _people(today)
    if not cards and not people and not fixed:
        raise ValueError("Açık borcun görünmüyor; başlatılacak bir plan yok.")
    paid_now = _paid_now(cards, [*people, *fixed], cards_done, people_done)
    paid_now += _jar_deduct(cards, people, fixed, monthly, order, start, paid_now)
    sim = simulate(cards, people, monthly, order, start, first_budget=_r(max(0.0, monthly - paid_now)), fixed=fixed)
    people = [*fixed, *people]
    ids = {d["key"]: _ident(d) for d in (*cards, *people)}
    planned: dict[str, float] = {}
    months = []
    for mo in sim["months"]:
        pays, saves = {}, {}
        for p in mo["payments"]:
            i = ids[p["key"].removesuffix(PK)]  # erken kapatma satırı kredinin kendisine sayılır
            pays[i] = _r(pays.get(i, 0.0) + p["amount"])
            planned[i] = _r(planned.get(i, 0.0) + p["amount"])
            if p.get("saving"):  # birikim: hedefe sayılır, ödenene kadar "biriktirildi"
                saves[i] = _r(saves.get(i, 0.0) + p["amount"])
        months.append({"month": mo["month"], "total": mo["total"], "pays": pays, **({"saves": saves} if saves else {})})
    snap = {"started_at": today.isoformat(), "start_month": start, "monthly": monthly, "order": order,
            "debt_free": sim["debt_free"], "total": _r(sum(d["remaining"] for d in (*cards, *people))),
            "debts": [{"ident": ids[d["key"]], "kind": d["kind"], "title": d["title"], "remaining": d["remaining"],
                       "planned": planned.get(ids[d["key"]], 0.0),
                       # altın/döviz borcunda ödenen miktar biriminde, TL'ye plandaki fiyatla çevrilir
                       "paid0": _r(d["paid"] / d["price"]) if d.get("price") else d["paid"],
                       **({"price": d["price"]} if d.get("price") else {}),
                       **({"payoff": True} if d.get("payoff_base") else {}),
                       "stmt_id": d["id"] if d["kind"] == "card" else None, "period_end": d.get("period_end")}
                      for d in (*cards, *people)],
            "months": months}
    store = _store()
    if store["current"]:
        old = store["current"]
        store["history"] = [{"started_at": old["started_at"], "start_month": old["start_month"],
                             "monthly": old["monthly"], "order": old["order"], "ended_at": today.isoformat()},
                            *store["history"]][:HISTORY]
    store["current"] = snap
    db.set_setting(COMMIT, json.dumps(store))
    save_settings({"monthly": monthly, "order": order})
    return build(today=today)


def _past_saves(snap: dict, start: str) -> dict[str, float]:
    """Geçmiş aylarda plandaki birikimler (tek seferde ödenecek borç): ödenene kadar biriktirildi sayılır."""
    out: dict[str, float] = {}
    for mo in snap["months"]:
        if mo["month"] < start:
            for i, a in (mo.get("saves") or {}).items():
                out[i] = _r(out.get(i, 0.0) + a)
    return out


def _credited(snap: dict, people_hidden: bool, start: str | None = None,
              jars: dict[str, dict] | None = None) -> dict[str, float]:
    """Plan başladıktan sonra plandaki her borca ödenen (borcun plandaki toplam hedefiyle sınırlı). Kartta: başlangıç
    ekstresine o günden sonra ödenen + aynı kartın sonraki ekstrelerine ödenen. Silinen / vazgeçilen borç tamam sayılır.
    Tek seferde ödenecek borçta geçmiş ayların birikim hedefi ödenene kadar biriktirildi sayılır; borca bağlı kumbara
    varsa (jars: kimlik → kumbara) varsayım yerine kumbaradaki gerçek bakiye sayılır."""
    saves = _past_saves(snap, start) if start else {}
    jars = jars or {}
    stmts = db.list_statements()
    by_id = {s["id"]: s for s in stmts}
    debts = {d["id"]: d for d in db.list_debts()} if not people_hidden else {}
    out: dict[str, float] = {}
    for x in snap["debts"]:
        if x["kind"] == "card":
            card = x["ident"][5:]
            s0 = by_id.get(x["stmt_id"])
            if s0 is None:
                got = x["planned"]
            else:
                got = max(0.0, (s0.get("paid_amount") or 0.0) - (x["paid0"] or 0.0))
                got += sum(s.get("paid_amount") or 0.0 for s in stmts
                           if s["card_no"] == card and (s["period_end"] or "") > (x["period_end"] or ""))
        else:
            if people_hidden:
                continue
            d = debts.get(int(x["ident"][5:]))
            if d is None or d["written_off_at"]:
                got = x["planned"]
            else:
                got = max(0.0, (d["paid_amount"] or 0.0) - (x["paid0"] or 0.0)) * (x.get("price") or 1.0)
            if x["kind"] == "fixed":
                # ödeme planlı borçta birikim yalnız erken kapatma içindir: taksitler ayrıca ödenir (kapanınca ödenen
                # kapatma tutarı birikimi de içerir)
                if d is not None and not d["settled_at"]:
                    j = jars.get(x["ident"])
                    got += j["balance"] if j else saves.get(x["ident"], 0.0)
            elif x["ident"] in jars:
                got += max(0.0, jars[x["ident"]]["balance"])
            else:
                got = max(got, saves.get(x["ident"], 0.0))
        out[x["ident"]] = _r(min(got, x["planned"]))
    return out


def _commitment(snap: dict, cards: list[dict], people: list[dict], sim: dict | None, monthly: float | None, order: str,
                today: date, people_hidden: bool, harm_now: bool, fixed: list[dict] | None = None) -> dict:
    """Kaydedilen plana göre bu ay nerede olduğun. Sözler yalnız daha çok ayırmayı, harcamayı kısmayı ve (kartta tutar
    yetmiyorsa) son ödeme gününden önce en az asgariyi önerir; başka yol önermez."""
    start = _ym(today)
    skip = (lambda i: i.startswith("debt:")) if people_hidden else (lambda i: False)
    fixed = fixed or []
    # bağlı kumbara: varsayılan birikim yerine kumbaradaki gerçek bakiye
    jmap = {_ident(d): d["jar"] for d in (*people, *fixed) if d.get("jar_used")}
    credited = _credited(snap, people_hidden, start, jmap)
    got = _r(sum(credited.values()))
    before = _r(sum(a for mo in snap["months"] if mo["month"] < start for i, a in mo["pays"].items() if not skip(i)))
    target = _r(sum(a for mo in snap["months"] if mo["month"] == start for i, a in mo["pays"].items() if not skip(i)))
    expected = _r(before + target)
    total_open = _r(sum(d["remaining"] for d in (*cards, *people, *fixed)))
    df = snap.get("debt_free")
    # aynı tutar ve sıralamayla bugünkü borçlardan hesaplanan plan (önizleme farklıysa ayrı hesaplanır)
    snap_order = norm_order(snap.get("order")) or "tarih"
    differs = monthly != snap["monthly"] or order != snap_order
    live = sim
    if differs or live is None:
        cards_now, people_now = cards, people
        paid_now = _r(sum(d["paid_now"] for d in (*cards_now, *people_now, *fixed)))
        paid_now += _jar_deduct(cards_now, people_now, fixed, snap["monthly"], snap_order, start, paid_now)
        live = simulate(cards_now, people_now, snap["monthly"], snap_order, start,
                        first_budget=_r(max(0.0, snap["monthly"] - paid_now)), fixed=fixed)
    behind = _r(before - got)
    if total_open <= 0:
        status = {"key": "done", "title": "Plan tamamlandı ✓", "text": "Borçların kapandı. Ellerine sağlık."}
    elif behind > TOL:
        need = _r(expected - got)
        text = (f"Bu ay {_tl(need)} ödersen yetişirsin. Biraz daha "
                "ayırabilir ya da Bütçeler'de kısılabilecek harcamalara bakabilirsin.")
        if harm_now:
            text += " Kartta tamamı olmuyorsa son ödeme gününden önce en az asgari tutarı yatır; gecikme faizi işlemez."
        status = {"key": "behind", "title": f"Planın {_tl(behind)} gerisindesin", "text": text, "need": need}
    elif got > expected + TOL:
        ldf = live.get("debt_free")
        if ldf and df and ldf < df:
            text = f"{AYLAR[int(df[5:]) - 1]}{' ' + df[:4] if df[:4] != start[:4] else ''} yerine " \
                   f"{month_in(ldf, start)} bitebilir."
        else:
            text = f"Plandan {_tl(_r(got - expected))} fazla ödedin. Böyle devam."
        status = {"key": "ahead", "title": "Planın önündesin", "text": text}
    else:
        left = _r(max(0.0, expected - got))
        status = {"key": "ok", "title": "Plana uygun ✓",
                  "text": f"Bu ay planda {_tl(left)} daha var." if left > TOL else "Bu ayın ödemesi tamam."}
    reasons = []
    known = {x["ident"] for x in snap["debts"]}
    new = [d["title"] for d in (*cards, *people, *fixed) if _ident(d) not in known]
    if new:
        reasons.append({"key": "new_debt", "text": f"Yeni borç eklendi: {', '.join(new)}"})
    if behind > TOL:
        prev = max((mo["month"] for mo in snap["months"] if mo["month"] < start and mo["total"] > 0), default=None)
        reasons.append({"key": "missed", "text": (month_in(prev, start) if prev else "Geçen ay") +
                        " plandaki ödeme eksik kaldı"})
    changed = {"title": "Plan değişti", "reasons": reasons, "text": " · ".join(r["text"] for r in reasons),
               "action": "Planı yeniden oluştur",
               "confirm": "Plan bugünkü borçlarınla baştan kurulur. Şimdiki plan \"Önceki plan\" olarak kalır."} \
        if reasons else None
    k = _months_between(snap["start_month"], start)
    titles = {x["ident"]: x["title"] for x in snap["debts"]}
    paid_real = {}
    payoff_open = set()  # erken kapatma birikimi süren (kapanmamış) ödeme planlı borçlar
    if not people_hidden:
        dmap = {d["id"]: d for d in db.list_debts()}
        for x in snap["debts"]:
            if x["kind"] != "card":
                d = dmap.get(int(x["ident"][5:]))
                paid_real[x["ident"]] = 0.0 if d is None else max(0.0, (d["paid_amount"] or 0.0) - (x["paid0"] or 0.0))
                if x["kind"] == "fixed" and d is not None and not d["settled_at"]:
                    payoff_open.add(x["ident"])
    saved_rows = [{"ident": i, "title": titles.get(i, ""), "amount": a,
                   "text": (f"{titles.get(i, '')}: erken kapatma için biriktirildi: {_tl(a)}" if i in payoff_open else
                            f"{titles.get(i, '')} için biriktirildi: {_tl(a)}")}
                  for i, a in _past_saves(snap, start).items()
                  if a > 0 and not skip(i) and i not in jmap and (paid_real.get(i, 0.0) <= TOL or i in payoff_open)]
    for i, j in jmap.items():  # kumbaradaki gerçek birikim
        if i in titles and j["balance"] > 0 and not skip(i):
            t = titles[i]
            saved_rows.append({"ident": i, "title": t, "amount": _r(j["balance"]), "jar_id": j["id"],
                               "text": (f"{t}: erken kapatma için kumbarada {_tl(j['balance'])}" if i.startswith("debt:")
                                        and i in payoff_open else f"{t} için kumbarada {_tl(j['balance'])}")})
    return {"started_at": snap["started_at"], "start_month": snap["start_month"],
            "start_label": month_label(snap["start_month"]), "monthly": snap["monthly"], "order": snap_order,
            "order_label": ORDER_INFO[snap_order][0], "saved": saved_rows,
            "debt_free": {"month": df, "label": month_label(df)} if df else None,
            "month_no": k + 1, "month_count": len(snap["months"]), "expected": expected, "paid": got,
            "this_target": target, "status": status, "changed": changed, "differs": differs,
            "text": f"{month_in(snap['start_month'], start)} başladı · ayda {_tl(snap['monthly'])} · "
                    f"{ORDER_INFO[snap_order][0]}",
            "continue": {"action": "Bu planla devam",
                         "confirm": "Plan bu tutar ve sıralamayla yeniden kurulur. Şimdiki plan \"Önceki plan\" olarak kalır."}}


def _promise(d: dict, close: str | None) -> dict | None:
    """Eski istemciler için söz/vade satırı (yenisi debts[].late)."""
    if not d["due_date"]:
        return None
    label = _day_label(d["due_date"])
    strict = bool(d.get("strict"))
    if d["overdue"]:
        text = (f"{label} ödeme günü geçti; bir an önce ödemek gecikme bedelini durdurur." if strict else
                f"{label} için söz verdiğin tarih geçti. Kendisine haber verip yeni bir tarih konuşmak iyi olur.")
        return {"date": d["due_date"], "label": label, "missed": True, "text": text}
    if close and close > d["due_date"][:7]:
        text = (f"{label} ödeme günü; bu planla {month_label(close)} ayında kapanıyor. Bu tarih kesin." if strict else
                f"{label} için söz verdin; bu planla {month_label(close)} ayında kapanıyor. Önceden haber vermek iyi olur.")
        return {"date": d["due_date"], "label": label, "missed": True, "text": text}
    return {"date": d["due_date"], "label": label, "missed": False,
            "text": f"{'Ödeme günü' if strict else 'Söz verdiğin tarih'}: {label}"}


def _can_edit() -> bool:
    from app import book

    m = book.member()
    return not (m and book.role(m) == "viewer")

