"""Sadaka hatırlatması, süreklilik ve gelire oranı (kişiye özel, sunucu hesaplar; web ve mobil aynı metni gösterir).

Sadaka kaynakları (ledger):
- Sadaka ve bağış kayıtları (donations: sadaka, fitre, fidye, kurban, adak, kefaret, vakıf/dernek bağışı).
- "Bağış & Sadaka" kategorisindeki giderler (elle kayıt, kart işlemi; Özet'in gider sayımıyla: finance.counts).
Zekât SAYILMAZ: zekât olarak işaretlenen hareket (zakat_payments.entry_id / txn_id) ve zekât ödemesinin açtığı gider kaydı
ayrı tutulur. Hareketten işaretlenen bağış hareketle tek sayılır (elle girilip ekstreyle eşleşmiş kart satırı da elle
kayıtla tek). Gelecek tarihli kayıt sayılmaz.

Ritim (sistem veritabanı "sadaka:<kullanıcı id>"; kişinin bütün defterlerinde aynı):
- haftalik ("Her Cuma"): dönem Cuma'dan Cuma'ya (Cumartesi–Cuma, Cuma'da biter); hatırlatma o Cuma sabahı.
- aylik ("Ayda bir"): dönem takvim ayı; hatırlatma ayın ilk Cuması.
- kapali ("Hatırlatma"): Özet'te kart yok, bildirim yok.
(Gün API'de değişebilir: day 0–6, varsayılan 4 Cuma; arayüz yalnız Cuma'yı sunar.)
Seçim yapılmamışsa düzenli hatırlatma YOK (kapalı sayılır):
- 1.0.7+ telefonu olan ve özelliği açık gördüğü ilk Cuma (o hafta kaydı yoksa) "Hayırlı Cumalar · Bu hafta sadaka verdiysen not edelim mi?";
  dokununca sadaka formu. Kayıttan sonra formda "Her Cuma hatırlatalım mı?" (Her Cuma / Ayda bir / Hatırlatma).
- Dokunmaz ya da kayıt yapmazsa (ilk sorudan beri kaydı yok) sonraki ayın ilk Cuması bir kez daha (OFFER_MAX = 2); sonra
  susar.
Süreklilik ("4 Cumadır sadakan kayıtlı", "3 aydır her ay sadakan kayıtlı"): ardışık kayıtlı dönem sayısı; içinde bulunulan
dönemde henüz kayıt yoksa önceki dönemden sayılır (bozuldu denmez, yalnız "Bu Cuma için henüz kayıt yok"). Rozet, puan,
paylaşma yok. "Sürekliliği göster" varsayılan açık (show_streak).

Gelire oranı (ratio): dönem dönem sadaka toplamı / gelir (Özet'in gelir tanımı, faiz geliri HARİÇ: arındırılacak). Geliri
MIN_INCOME'dan az olan dönemde oran yok ("gelir kaydı yok"). Özet cümlesi son 3 tamamlanmış dönemi önceki 3 dönemle
karşılaştırır; öneri, tutar, "daha fazla" yok.

Ortak defter: herkes kendi sadakasını görür. Kaydın kişisi harcayan kuralıyla (finance.Spenders: elle seçilen, ekleyen,
kartın kişisi); elle girilen bağışta ekleyen (created_by); kişisi belli olmayan (eski kayıt, kişiye bağlanmamış kart, banka
dökümü) defter sahibinin. Oranda gelir de kişinin: üyenin kendi gelir kaydı yoksa oran yok, yalnız tutar. "Benim" defteri ve
tek kişilik defterde bütün kayıtlar kişinindir. Zekât ve Sadaka sayfası üyeye kapalıysa ya da özellik kapalıysa (hazır
sürümde zekât rızası yoksa) hiçbiri yoktur.

Metin: nazik, suçlamasız; tutar önerilmez. "Hayırlı Cumalar" selamı sahibin isteği; başka dini ifade yok.
"""
import json
from collections import defaultdict
from datetime import date, timedelta

from app import db

RHYTHMS = {"haftalik": "Her Cuma", "aylik": "Ayda bir", "kapali": "Hatırlatma"}
DAYS = ["Pazartesi", "Salı", "Çarşamba", "Perşembe", "Cuma", "Cumartesi", "Pazar"]
DAYS_OF = ["Pazartesisi", "Salısı", "Çarşambası", "Perşembesi", "Cuması", "Cumartesisi", "Pazarı"]  # "ayın ilk Cuması"
AY = ["Ocak", "Şubat", "Mart", "Nisan", "Mayıs", "Haziran", "Temmuz", "Ağustos", "Eylül", "Ekim", "Kasım", "Aralık"]
FRIDAY = DEFAULT_DAY = 4  # date.weekday
LOCAL_KEY = "sadaka:reminder"  # kullanıcısız (tek kişilik, girişsiz) kurulumda defter ayarı
GREETING = "Hayırlı Cumalar"   # sahibin isteği (Cuma'ya düşen bildirimin başlığı)
OFFER_BODY = "Bu hafta sadaka verdiysen not edelim mi?"
ASK_TITLE = "Her Cuma hatırlatalım mı?"  # sadaka kaydedilince, seçim yoksa
# seçeneklerin sırası (web ve mobil aynı sırayla gösterir); ilk sıradaki önerilen
CHOICES = [{"rhythm": "haftalik", "label": "Her Cuma", "hint": "O hafta sadaka kaydın yoksa Cuma sabahı", "recommended": True},
           {"rhythm": "aylik", "label": "Ayda bir", "hint": "O ay sadaka kaydın yoksa ayın ilk Cuması", "recommended": False},
           {"rhythm": "kapali", "label": "Hatırlatma", "hint": "Bildirim gelmez, Özet'te görünmez", "recommended": False}]
OFFER_MAX = 2          # seçim yokken "Hayırlı Cumalar" bildirimi en çok bu kadar
MIN_INCOME = 1000.0    # dönem geliri bundan azsa oran hesaplanmaz ("gelir kaydı yok")
SAME_PP = 0.2          # oran farkı bundan küçükse (puan) "aynı kaldı"
WINDOW = 3             # özet cümlesi: son 3 tamamlanmış dönem, önceki 3 dönemle


# ---------------------------------------------------------------- tercih

def _uid() -> int | None:
    u = db.current_user()
    return u.get("id") if u else None


def _state(uid: int | None) -> dict:
    """Kişinin kayıtlı durumu: rhythm (seçildiyse), day, offers, last_offer, show_streak."""
    from app import system

    raw = system.get(f"sadaka:{uid}") if uid else db.get_setting(LOCAL_KEY)
    try:
        v = json.loads(raw) if raw else {}
    except ValueError:
        v = {}
    v = v if isinstance(v, dict) else {}
    day, offers = v.get("day"), v.get("offers")
    return {"rhythm": v.get("rhythm") if v.get("rhythm") in RHYTHMS else None,
            "day": day if isinstance(day, int) and 0 <= day <= 6 else DEFAULT_DAY,
            "offers": offers if isinstance(offers, int) and offers >= 0 else 0,
            "last_offer": v.get("last_offer") if isinstance(v.get("last_offer"), str) else None,
            "show_streak": v.get("show_streak") is not False}


def _save(uid: int | None, st: dict) -> None:
    from app import system

    val = json.dumps({k: v for k, v in st.items() if v is not None})
    if uid:
        system.put(f"sadaka:{uid}", val)
    else:
        db.set_setting(LOCAL_KEY, val)


def set_pref(rhythm: str | None = None, day: int | None = None, show_streak: bool | None = None) -> dict:
    """Kişinin seçimi (yalnız gelen alanlar değişir). ValueError: bilinmeyen ritim / gün."""
    if rhythm is not None and rhythm not in RHYTHMS:
        raise ValueError("Ritim haftalık, aylık ya da kapalı olmalı")
    if day is not None and not (isinstance(day, int) and 0 <= day <= 6):
        raise ValueError("Gün 0 (Pazartesi) ile 6 (Pazar) arasında olmalı")
    uid = _uid()
    st = _state(uid)
    if rhythm is not None:
        st["rhythm"] = rhythm
    if day is not None:
        st["day"] = day
    if show_streak is not None:
        st["show_streak"] = bool(show_streak)
    _save(uid, st)
    return st


# ---------------------------------------------------------------- erişim

def available() -> bool:
    """Zekât ve Sadaka özelliği bu defterde açık (hazır sürümde rıza verilmiş) ve üyeye kapalı değil."""
    from app import book, prefs

    r = book.active()
    return prefs.enabled("zekat") and not (r and "zekat" in r["hidden_pages"])


# ---------------------------------------------------------------- sadaka kayıtları (kişinin)

def _mine():
    """Satırın bu kişiye ait olup olmadığı: mine(satır, yedek kişi) -> bool. Tek kişilik defterde (ya da "Benim") None:
    hepsi onun. Kişisi belli olmayan satır yedek kişinin, o da yoksa defter sahibinin."""
    from app import book, finance

    ppl = book.shared_people()
    u = db.current_user()
    if not ppl or not u or not u.get("id"):
        return None
    me, owner = u["id"], u.get("member_of") or u["id"]
    sp = finance.Spenders(ppl)

    def of(r: dict, fallback: int | None = None) -> bool:
        uid = sp._uid(r)
        return (uid if uid is not None else fallback if fallback is not None else owner) == me

    return of


def ledger(today: date | None = None) -> list[dict]:
    """Kişinin sadakaları [{date, amount, month (dönem)}], bugün ve öncesi. Zekât dahil değil."""
    from app import donations, finance, zakat

    today = today or date.today()
    with db.connect() as c:
        zakat._init(c)
        donations._init(c)
        dons = db.rows(c.execute("SELECT id, given_date, amount, entry_id, txn_id, created_by FROM donations"))
        zak_e = {r[0] for r in c.execute("SELECT entry_id FROM zakat_payments WHERE entry_id IS NOT NULL")}
        zak_t = {r[0] for r in c.execute("SELECT txn_id FROM zakat_payments WHERE txn_id IS NOT NULL")}
        linked = {r[0]: r[1] for r in c.execute("SELECT linked_txn_id, id FROM entries WHERE linked_txn_id IS NOT NULL")}

    def canon(source: str, rid: int) -> tuple[str, int]:  # ekstreyle eşleşmiş kart satırı = elle kayıt
        if source == "card" and rid in linked:
            return ("manual", linked[rid])
        return (source, rid)

    mine = _mine()
    rows = finance.transactions(None)  # üyeye süzülmüş (gizli kart/hesap/kayıt yok)
    by_key = {canon(r["source"], r["id"]): r for r in rows if r.get("source") in ("manual", "card")}
    out: list[dict] = []
    seen: set[tuple[str, int]] = set()
    iso = today.isoformat()
    for r in rows:
        if r.get("source") not in ("manual", "card") or r.get("category") != donations.CATEGORY or not finance.counts(r)[0]:
            continue
        key = canon(r["source"], r["id"])
        if (key[0] == "manual" and key[1] in zak_e) or (r["source"] == "card" and r["id"] in zak_t):
            continue
        seen.add(key)
        if str(r["date"])[:10] > iso or (mine is not None and not mine(r)):
            continue
        out.append({"date": date.fromisoformat(str(r["date"])[:10]), "amount": r["amount"], "month": r["month"]})
    for d in dons:
        if d["given_date"][:10] > iso:
            continue
        key = canon("manual", d["entry_id"]) if d["entry_id"] else canon("card", d["txn_id"]) if d["txn_id"] else None
        if key and key in seen:  # hareketin kendisi sayıldı
            continue
        src = by_key.get(key) if key else None
        if mine is not None:
            if key and src is None:  # üyeye gizli (ya da silinmiş) hareketin işareti: onun değil
                continue
            if not mine(src or {"source": None}, d["created_by"]):
                continue
        out.append({"date": date.fromisoformat(d["given_date"][:10]), "amount": d["amount"],
                    "month": db.period_of(d["given_date"])})
    return out


def dates(today: date | None = None) -> list[date]:
    """Kişinin sadaka tarihleri (bugün ve öncesi), yeniden eskiye."""
    return sorted({x["date"] for x in ledger(today)}, reverse=True)


# ---------------------------------------------------------------- dönem, süreklilik, metin

def _week_end(d: date, day: int = DEFAULT_DAY) -> date:
    """d'nin ait olduğu haftalık dönemin son günü (seçilen gün; Cuma'dan Cuma'ya: Cumartesi–Cuma)."""
    return d + timedelta(days=(day - d.weekday()) % 7)


def period(rhythm: str, today: date, day: int = DEFAULT_DAY) -> tuple[date, str]:
    """Dönemin ilk günü ve anahtarı (bildirim kimliği): haftalık → dönemin Cuması "2026-11-13"; aylık → "2026-11"."""
    if rhythm == "haftalik":
        end = _week_end(today, day)
        return end - timedelta(days=6), end.isoformat()
    return today.replace(day=1), today.strftime("%Y-%m")


def _first(d: date, day: int = DEFAULT_DAY) -> date:
    """Ayın seçilen ilk günü (ilk Cuma)."""
    first = d.replace(day=1)
    return first + timedelta(days=(day - first.weekday()) % 7)


def reminder_day(rhythm: str, day: int, today: date) -> date | None:
    """Bu dönemin hatırlatma günü: haftalık → dönemin son günü (Cuma); aylık → ayın ilk Cuması."""
    if rhythm == "haftalik":
        return _week_end(today, day)
    if rhythm == "aylik":
        return _first(today, day)
    return None


def next_reminder(rhythm: str, day: int, today: date) -> date | None:
    """Bugün ya da sonraki ilk hatırlatma günü."""
    d = reminder_day(rhythm, day, today)
    if d is None or d >= today:
        return d
    nm = (today.replace(day=28) + timedelta(days=4)).replace(day=1)
    return _first(nm, day)


def streak(rhythm: str, day: int, ds: list[date], today: date) -> int:
    """Ardışık kayıtlı dönem sayısı. İçinde bulunulan dönemde kayıt yoksa önceki dönemden sayılır (bitmemiş dönem
    sürekliliği bozmaz)."""
    if rhythm == "haftalik":
        have = {_week_end(d, day) for d in ds}
        cur = _week_end(today, day)
        step = lambda x: x - timedelta(days=7)  # noqa: E731
    elif rhythm == "aylik":
        have = {(d.year, d.month) for d in ds}
        cur = (today.year, today.month)
        step = lambda x: (x[0] - 1, 12) if x[1] == 1 else (x[0], x[1] - 1)  # noqa: E731
    else:
        return 0
    if cur not in have:
        cur = step(cur)
    n = 0
    while cur in have and n < 600:
        n += 1
        cur = step(cur)
    return n


def streak_text(rhythm: str, day: int, n: int) -> str | None:
    if n < 2:
        return None
    if rhythm == "haftalik":
        return f"{n} Cumadır sadakan kayıtlı" if day == FRIDAY else f"{n} haftadır her hafta sadakan kayıtlı"
    if rhythm == "aylik":
        return f"{n} aydır her ay sadakan kayıtlı"
    return None


def ago(days: int) -> str:
    if days <= 0:
        return "bugün"
    if days == 1:
        return "dün"
    if days < 45:
        return f"{days} gün önce"
    if days < 365:
        return f"{days // 30} ay önce"
    return "bir yıldan uzun süre önce"


def texts(rhythm: str, day: int, last: date | None, in_period: bool, today: date) -> tuple[str, str, str]:
    """Özet kartı: (başlık, alt satır, ton: ok | nudge | empty)."""
    weekly = rhythm == "haftalik"
    if in_period and last:
        return f"Bu {'hafta' if weekly else 'ay'} sadakan kayıtlı", f"Son kayıt {ago((today - last).days)}", "ok"
    pending = f"Bu {DAYS[day]} için henüz kayıt yok" if weekly else "Bu ay için henüz kayıt yok"
    if last:
        return f"Son sadakan {ago((today - last).days)}", pending, "nudge"
    return "Henüz sadaka kaydın yok", pending, "empty"


def push_text(rhythm: str, day: int, streak_line: str | None) -> tuple[str, str]:
    """Hatırlatma (dönemde kayıt yokken): haftalık Cuma bildiriminin başlığı "Hayırlı Cumalar"; aylıkta (sahibin kararı,
    11.11) "Sadaka hatırlatması"."""
    title = GREETING if rhythm == "haftalik" and day == FRIDAY else "Sadaka hatırlatması"
    if rhythm == "haftalik":
        return title, f"{streak_line} · bu haftakini not edelim mi?" if streak_line else OFFER_BODY
    return title, "Bu ay sadaka verdiysen not edelim mi?"


def when(rhythm: str, day: int) -> str | None:
    """"Her Cuma sabahı" / "Her ayın ilk Cuması" (ayar satırının alt yazısı)."""
    if rhythm == "haftalik":
        return f"Her {DAYS[day]} sabahı"
    if rhythm == "aylik":
        return f"Her ayın ilk {DAYS_OF[day]}"
    return None


def status(today: date | None = None) -> dict:
    """GET /api/donations/reminder."""
    today = today or date.today()
    ok = available()
    st = _state(_uid())
    rhythm, day = st["rhythm"] or "kapali", st["day"]
    ds = dates(today) if ok else []
    last = ds[0] if ds else None
    out = {"available": ok, "rhythm": rhythm, "day": day, "chosen": st["rhythm"] is not None,
           "show": ok and rhythm != "kapali", "show_streak": st["show_streak"], "streak": 0, "streak_text": None,
           "last_date": last.isoformat() if last else None, "days_since": (today - last).days if last else None,
           "in_period": False, "period_key": None, "title": None, "text": None, "tone": None, "next_reminder": None,
           "when": when(rhythm, day), "rhythms": RHYTHMS, "days": DAYS, "choices": CHOICES,
           # sadaka kaydedilince sorulur (seçim yapılmadıysa)
           "ask_after_save": ok and st["rhythm"] is None, "ask_title": ASK_TITLE}
    if ok and rhythm != "kapali":
        start, key = period(rhythm, today, day)
        inp = bool(last and last >= start)
        title, text, tone = texts(rhythm, day, last, inp, today)
        n = streak(rhythm, day, ds, today)
        nx = next_reminder(rhythm, day, today)
        out.update({"in_period": inp, "period_key": key, "title": title, "text": text, "tone": tone, "streak": n,
                    "streak_text": streak_text(rhythm, day, n) if st["show_streak"] else None,
                    "next_reminder": nx.isoformat() if nx else None})
    return out


# ---------------------------------------------------------------- bildirim

def offer_due(st: dict, ds: list[date], today: date) -> int | None:
    """Seçim yokken bugün "Hayırlı Cumalar" bildirimi gidecekse kaçıncısı: ilki (o hafta kaydı yoksa) ilk Cuma; ikincisi
    sonraki bir ayın ilk Cuması, ilk bildirimden beri kayıt yoksa. En çok OFFER_MAX."""
    if st["rhythm"] or today.weekday() != FRIDAY or st["offers"] >= OFFER_MAX:
        return None
    if any(today - timedelta(days=6) <= d <= today for d in ds):  # bu hafta (Cumartesi–Cuma) zaten kaydı var
        return None
    if st["offers"] == 0:
        return 1
    last = st["last_offer"]
    if today != _first(today) or not last or today.strftime("%Y-%m") <= last[:7]:
        return None
    if any(d >= date.fromisoformat(last) for d in ds):  # ilk bildirimden sonra kayıt yaptı (soruyu gördü)
        return None
    return st["offers"] + 1


OFFER_MIN_APP = (1, 0, 7)  # kayıttan sonraki ritim sorusu bu sürümde; eski sürümde dokununca yalnız Zekât sayfası açılır


def _ver(v: str | None) -> tuple[int, ...]:
    """Cihaz kaydındaki sürüm ("1.0.7 (39)", "1.0.7") → (1, 0, 7); okunamazsa ()."""
    head = (v or "").split(" ")[0]
    try:
        return tuple(int(x) for x in head.split("."))
    except ValueError:
        return ()


def _has_new_app(uid: int | None) -> bool:
    """İlk "Hayırlı Cumalar" (seçim) bildirimi yalnız OFFER_MIN_APP ve sonrası bir telefonu olan kişiye (sahibin kararı,
    11.11): eski sürümde akış eksik. Sayaç gönderilmeyince artmaz; kişi güncelleyince ilk Cuma başlar."""
    from app import push

    if uid is None:
        return False
    return any(_ver(d.get("app_version")) >= OFFER_MIN_APP for d in push.devices(uid))


def push_items(today: date) -> list[dict]:
    """Günlük işin "sadaka" bildirimi (bağlam: kişinin defteri). Çağıran (push.collect) yalnız tür açıkken ve cihazı
    olan kişi için çağırır: seçimsiz bildirimin sayacı burada artar. Kimlik: seçimsizde "sadaka:cuma:<n>", hatırlatmada
    dönem ("sadaka:h:2026-11-13" / "sadaka:a:2026-11"): aynı dönem için bir kez (push_sent)."""
    from app import push

    if not available():
        return []
    uid = _uid()
    st = _state(uid)
    ds = dates(today)
    n = offer_due(st, ds, today) if _has_new_app(uid) else None
    if n:
        st.update({"offers": n, "last_offer": today.isoformat()})
        _save(uid, st)
        return [push.item("sadaka", f"sadaka:cuma:{n}", GREETING, OFFER_BODY, push.R_SADAKA)]
    rhythm, day = st["rhythm"], st["day"]
    if rhythm not in ("haftalik", "aylik") or reminder_day(rhythm, day, today) != today:
        return []
    start, key = period(rhythm, today, day)
    if ds and ds[0] >= start:  # bu dönemde kaydı var
        return []
    line = streak_text(rhythm, day, streak(rhythm, day, ds, today)) if st["show_streak"] else None
    title, body = push_text(rhythm, day, line)
    return [push.item("sadaka", f"sadaka:{'h' if rhythm == 'haftalik' else 'a'}:{key}", title, body, push.R_SADAKA)]


# ---------------------------------------------------------------- gelire oranı

_UNITS = ["sıfır", "bir", "iki", "üç", "dört", "beş", "altı", "yedi", "sekiz", "dokuz"]
_TENS = ["", "on", "yirmi", "otuz", "kırk", "elli", "altmış", "yetmiş", "seksen", "doksan"]


def _spoken_last(num: str) -> str:
    """"2,1" → "bir" (iki virgül BİR), "12" → "iki", "40" → "kırk": ekin uyacağı son okunan sözcük."""
    whole, _, frac = num.partition(",")
    n = int(frac) if frac.strip("0") else int(whole or 0)
    if n == 0:
        return "sıfır"
    if n % 10:
        return _UNITS[n % 10]
    if n % 100:
        return _TENS[(n // 10) % 10]
    return "yüz" if n % 1000 else "bin"


def _back(word: str) -> bool:
    return next((ch in "aıou" for ch in reversed(word) if ch in "aeıioöuü"), False)


def ablative(num: str) -> str:
    """"%2,1'den", "%1,4'ten", "%6'dan", "%40'tan"."""
    w = _spoken_last(num)
    return f"'{'t' if w[-1] in 'fstkçşhp' else 'd'}{'a' if _back(w) else 'e'}n"


def dative(num: str) -> str:
    """"%1,4'e", "%2'ye", "%6'ya", "%9'a"."""
    w = _spoken_last(num)
    v = "a" if _back(w) else "e"
    return f"'y{v}" if w[-1] in "aeıioöuü" else f"'{v}"


def pct(x: float) -> str:
    """Tek ondalık, Türkçe virgül; tam sayıda ondalık yok: 2,1 · 2 · 0,4."""
    s = f"{round(x, 1):.1f}".replace(".", ",")
    return s[:-2] if s.endswith(",0") else s


def ratio_sentence(sad_old: float, inc_old: float, sad_new: float, inc_new: float) -> str:
    """"Son 3 ayda gelirin %12 arttı; sadakanın gelire oranı %2,1'den %1,4'e indi." (öneri yok)."""
    chg = (inc_new - inc_old) / inc_old * 100
    head = (f"Son {WINDOW} ayda gelirin aynı kaldı" if abs(chg) < 1
            else f"Son {WINDOW} ayda gelirin %{round(abs(chg))} {'arttı' if chg > 0 else 'azaldı'}")
    r_old, r_new = sad_old / inc_old * 100, sad_new / inc_new * 100
    if abs(r_new - r_old) < SAME_PP:
        return f"{head}; sadaka oranın gelirine göre aynı kaldı."
    a, b = pct(r_old), pct(r_new)
    return f"{head}; sadakanın gelire oranı %{a}{ablative(a)} %{b}{dative(b)} {'çıktı' if r_new > r_old else 'indi'}."


def ratio(months: int = 12, today: date | None = None) -> dict:
    """GET /api/donations/ratio: son `months` dönemin sadakası, geliri (faiz geliri hariç), oranı (%) ve özet cümlesi."""
    from app import book, finance, zakat

    today = today or date.today()
    months = 6 if months <= 6 else 12
    if not available():
        return {"available": False, "months": [], "sentence": None, "ratio_available": False}
    cur = finance.current_period(today)
    span = max(months, 2 * WINDOW + 1)
    keys = [finance.add_months(cur, -i) for i in range(span - 1, -1, -1)]
    sad: dict[str, float] = defaultdict(float)
    for it in ledger(today):
        sad[it["month"]] += it["amount"]
    mine = _mine()
    inc: dict[str, float] = defaultdict(float)
    for r in finance.transactions(None):
        if not finance.counts(r)[1] or finance.is_interest_income(r):  # faiz geliri arındırılacak: oranın tabanı değil
            continue
        if mine is not None and not mine(r):
            continue
        inc[r["month"]] += r["amount"]
    member = book.member() is not None
    # ortak defter üyesi: kendi gelir kaydı yoksa oran yok (defterin geliri kişinin sadakasıyla karışmasın)
    ratio_ok = not member or any(v > 0 for v in inc.values())
    zak: dict[str, float] | None = None
    if not member:  # zekât ayrı (oranda yok); üyede zekât kişileri defter sahibinin
        zak = defaultdict(float)
        with db.connect() as c:
            zakat._init(c)
            for d, a in c.execute("SELECT pay_date, amount FROM zakat_payments WHERE pay_date <= ?", (today.isoformat(),)):
                zak[db.period_of(d)] += a
    rows = []
    for m in keys:
        income = round(inc.get(m, 0.0), 2)
        enough = ratio_ok and income >= MIN_INCOME
        s = round(sad.get(m, 0.0), 2)
        y, mo = int(m[:4]), int(m[5:7])
        rows.append({"month": m, "label": f"{AY[mo - 1]} {y}", "short": AY[mo - 1][:3], "sadaka": s,
                     "income": income if ratio_ok else None, "ratio": round(s / income * 100, 2) if enough else None,
                     "note": None if enough or not ratio_ok else "gelir kaydı yok",
                     "zakat": round(zak.get(m, 0.0), 2) if zak is not None else None, "partial": m == cur})
    done = [r for r in rows if not r["partial"]]
    new, old = done[-WINDOW:], done[-2 * WINDOW:-WINDOW]
    sentence = None
    if ratio_ok and len(old) == WINDOW and all(r["ratio"] is not None for r in new + old):
        sentence = ratio_sentence(sum(r["sadaka"] for r in old), sum(r["income"] for r in old),
                                  sum(r["sadaka"] for r in new), sum(r["income"] for r in new))
    return {"available": True, "months": rows[-months:], "sentence": sentence, "ratio_available": ratio_ok,
            "basis": "kisi" if mine is not None else "defter", "min_income": MIN_INCOME,
            "note": None if ratio_ok else "Ortak defterde oran yalnız kendi gelir kaydın varsa hesaplanır."}
