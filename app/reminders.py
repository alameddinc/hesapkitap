"""Aylık belge hatırlatmaları: kesilen ama yüklenmeyen kart ekstresi, ayı kapanan ama dökümü gelmeyen banka hesabı,
yaklaşan son ödeme. Özet'teki "Yapılacaklar" kartı (mobil + web) ve ileride push bildirimleri aynı listeyi kullanır.

Her hatırlatmanın kimliği kararlıdır ve döneme bağlıdır: "ekstre:<kart no>:<YYYY-MM>", "dokum:<hesap id>:<YYYY-MM>",
"odeme:<kart no>:<YYYY-MM-DD>". Eşitleme soruları (app/sync.py) kayda bağlıdır: "esles:<kayıt id>:<e|t><id>",
"hesap:<hesap id>:<hesap id>", aynı olabilecek sabit kalemler "sabit:<silinecek>:<kalacak>"; cevaplanınca kendiliğinden düşer. Kapatma (dismiss) ve erteleme (snooze) bu kimlikle saklanır; sonraki dönemin
hatırlatması yeni kimlikle yeniden çıkar. Durum kişiye özeldir (ortak defterde üyenin kapattığı sahibinkini kapatmaz).

Kart başına beklenen kesim (card_cuts): ekstredeki "sonraki kesim", yoksa son ekstrelerden öğrenilen kesim günü (ay
sonu kesimi kısa ayda ayın son günü). Bir ay yüklenmediyse en son geçen kesim sorulur. Döküm: ay dönümü ayarına göre
(takvim ayı ya da ör. 17'si). ekstre/dokum maddelerinde "short": Özet'teki "N belge bekleniyor" grubunun kısa adı.

Dönem ve gün hesapları Europe/Istanbul saatine göre yapılır.
"""
import calendar
import hashlib
import json
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from app import db, workdays

TZ = ZoneInfo("Europe/Istanbul")
AY = ["Ocak", "Şubat", "Mart", "Nisan", "Mayıs", "Haziran", "Temmuz", "Ağustos", "Eylül", "Ekim", "Kasım", "Aralık"]
GUN = ["Pazartesi", "Salı", "Çarşamba", "Perşembe", "Cuma", "Cumartesi", "Pazar"]

EKSTRE_GRACE = 1       # kesimden sonra bu kadar gün bekle: banka PDF'i hemen göndermeyebilir
EKSTRE_STALE = 100     # son ekstrenin üstünden bu kadar gün geçtiyse kart muhtemelen kullanılmıyor: ısrar etme
DOKUM_WARN_DAY = 10    # ayın bu gününden sonra döküm hatırlatması "warn"
DOKUM_STALE = 100      # son dökümün üstünden bu kadar gün geçtiyse kişi dökümü bırakmış: hatırlatma
ODEME_DAYS = 3         # son ödemeye bu kadar gün kala
SEVERITY_ORDER = {"urgent": 0, "warn": 1, "info": 2}
KEEP_DAYS = 120        # bu kadar eski kapatma/erteleme kayıtları temizlenir


def today() -> date:
    return datetime.now(TZ).date()


def _d(d: date) -> str:
    return f"{d.day} {AY[d.month - 1]}"


def _month(d: date) -> str:
    return f"{d.year:04d}-{d.month:02d}"


def _last4(card_no: str) -> str:
    digits = "".join(ch for ch in card_no if ch.isdigit())
    return (digits or card_no)[-4:]


def _cid(card_no: str) -> str:
    """Kimlikte kart no: boşluksuz (URL ve bildirim yükünde güvenli)."""
    return "".join(card_no.split())


def _ago(n: int) -> str:
    return "Bugün" if n <= 0 else "Dün" if n == 1 else f"{n} gündür bekliyor"


# ---------------------------------------------------------------- kişiye özel durum (kapatma / erteleme)

def _key() -> str:
    u = db.current_user()
    return f"reminders:state:{u['id']}" if u else "reminders:state"


def _state() -> dict:
    try:
        raw = json.loads(db.get_setting(_key()) or "{}")
        return raw if isinstance(raw, dict) else {}
    except ValueError:
        return {}


def _save(st: dict, now: date) -> None:
    cut = (now - timedelta(days=KEEP_DAYS)).isoformat()
    st = {k: v for k, v in st.items() if isinstance(v, dict) and (v.get("at") or "") >= cut}
    db.set_setting(_key(), json.dumps(st, ensure_ascii=False))


def snooze(rid: str, days: int, now: date | None = None) -> dict:
    now = now or today()
    st = _state()
    until = (now + timedelta(days=max(1, min(days, 31)))).isoformat()
    st[rid] = {"until": until, "at": now.isoformat()}
    _save(st, now)
    return {"ok": True, "id": rid, "until": until}


def dismiss(rid: str, now: date | None = None) -> dict:
    now = now or today()
    st = _state()
    st[rid] = {"dismissed": True, "at": now.isoformat()}
    _save(st, now)
    return {"ok": True, "id": rid}


def _hidden(rid: str, st: dict, now: date) -> bool:
    s = st.get(rid)
    if not s:
        return False
    return bool(s.get("dismissed")) or (s.get("until") or "") > now.isoformat()


# ---------------------------------------------------------------- kurallar

def _posta_on() -> bool:
    """Posta adresi kurulu mu (bankanın maili ona iletilebilir)? Adres oluşturmaz."""
    from app import inbound, plans

    u = db.current_user()
    if not u or not plans.allows(u, "posta"):
        return False
    try:
        return inbound.address(u, create=False) is not None
    except Exception:  # noqa: BLE001 - sistem veritabanı yoksa (tek kullanıcılı kurulum) posta da yok
        return False


def _can_upload() -> bool:
    """Ortak defterde yükleme yalnız tam yetkili üyede (ve sahipte) açık."""
    from app import book

    m = book.member()
    return not m or book.role(m) == "full"


def _page_hidden(page: str) -> bool:
    from app import book

    r = book.active()
    return bool(r and page in r["hidden_pages"])


def _add_month(d: date, day: int, month_end: bool) -> date:
    """Bir sonraki ayın aynı kesim günü; ay kısaysa ayın son günü (31'i kesimli kart Şubat'ta 28'i)."""
    y, m = divmod(d.year * 12 + d.month, 12)  # d.month 1..12 → sonraki ay (0-tabanlı m)
    last = calendar.monthrange(y, m + 1)[1]
    return date(y, m + 1, last if month_end else min(day, last))


def _cut_rule(sts: list[dict]) -> tuple[int, bool]:
    """Kartın kesim günü, son ekstrelerden öğrenilir: son 6 ekstrenin olağan günü (workdays.usual_day: en sık gün;
    hafta sonu/bayram yüzünden kaymış tek kesim günü değiştirmez, tatille açıklanmayan yeni gün değiştirir); son iki
    ekstre de ay sonundaysa "ay sonu" (30 Nisan ve 31 Mayıs kesimli kart Haziran'da 30'unda kesilir, 28'inde değil)."""
    ends = sorted(date.fromisoformat(s["period_end"]) for s in sts)[-6:]

    def eom(d: date) -> bool:
        return d.day == calendar.monthrange(d.year, d.month)[1]

    tail = ends[-2:]
    return workdays.usual_day(ends), len(tail) == 2 and all(eom(d) and d.day >= 28 for d in tail)


def card_cuts(now: date, grace: int = EKSTRE_GRACE) -> list[dict]:
    """Kart başına beklenen son kesim (bugüne kadar geçmiş olan) ve son ödeme günü. Ekstrede "sonraki kesim" yazıyorsa o,
    yoksa öğrenilen kesim günüyle ay ay ilerlenir; bir ay atlanmışsa hatırlatma en son geçen kesime kayar (eski ayda
    takılıp kalmaz). Dönen: [{card_no, statement (son ekstre), cut, due, missed (yüklenmeyen kesim sayısı)}]. grace:
    kesimden sonra beklenen gün (push bildirimi 0: kesimden 1 gün sonra)."""
    by_card: dict[str, list[dict]] = {}
    for s in db.list_statements():  # ortak defterde gizli kart burada süzülü
        by_card.setdefault(s["card_no"], []).append(s)
    out = []
    for card_no, sts in by_card.items():
        st = max(sts, key=lambda s: s["period_end"])
        pe = date.fromisoformat(st["period_end"])
        day, month_end = _cut_rule(sts)
        cut = date.fromisoformat(st["next_period_end"]) if st.get("next_period_end") else _add_month(pe, day, month_end)
        if cut <= pe:  # bozuk okuma: sonraki kesim öncekinden önce
            cut = _add_month(pe, day, month_end)
        if not st.get("next_period_end") and (cut - pe).days > 45:
            # ay sonu kesimi tatil yüzünden sonraki ayın başına kaymıştı (30 Eki → 1 Kas): sıradaki kesim aynı ayın 30'u
            cut = date(pe.year, pe.month, min(day, calendar.monthrange(pe.year, pe.month)[1]))
        # son ödeme: ekstrede yazan "sonraki son ödeme" ya da son ekstredeki kesim → son ödeme aralığı
        gap = (date.fromisoformat(st["due_date"]) - pe) if st.get("due_date") else None
        due = date.fromisoformat(st["next_due_date"]) if st.get("next_due_date") else (cut + gap if gap else None)
        missed = 1
        while True:
            nxt = _add_month(cut, day, month_end)
            if (now - nxt).days < grace + 1:
                break
            cut, missed = nxt, missed + 1
            due = cut + gap if gap else None
        out.append({"card_no": card_no, "statement": st, "cut": cut, "due": due, "missed": missed})
    return out


def _ekstre(now: date, posta: bool, grace: int = EKSTRE_GRACE) -> list[dict]:
    """Her kart için ayrı: beklenen kesimin üstünden EKSTRE_GRACE gün geçti, o kesimin ekstresi yok. Ekstre yüklenince
    (elle, posta, Paylaş) son ekstre ilerler ve madde kendiliğinden düşer."""
    out = []
    for k in card_cuts(now, grace):
        card_no, st, cut, due = k["card_no"], k["statement"], k["cut"], k["due"]
        since = (now - cut).days
        if since < grace + 1:
            continue
        if (now - date.fromisoformat(st["period_end"])).days > EKSTRE_STALE:
            continue  # aylardır ekstre gelmiyor: kart muhtemelen kullanılmıyor, ısrar etme
        ay = AY[cut.month - 1]
        once = f" Önceki {k['missed'] - 1} ekstre de yüklenmemiş." if k["missed"] > 1 else ""
        text = f"Kesim tarihi {_d(cut)}'di. Ekstreyi yükle ki harcamaların ve son ödeme tarihin güncellensin.{once}"
        if posta:
            text = (f"Kesim tarihi {_d(cut)}'di. Ekstreyi yükle ya da bankanın mailini posta adresine ilet; "
                    f"harcamaların ve son ödeme tarihin güncellensin.{once}")
        sev = "warn" if since > 7 or k["missed"] > 1 or (due and (due - now).days <= 5) else "info"
        out.append({"id": f"ekstre:{_cid(card_no)}:{_month(cut)}", "kind": "ekstre", "severity": sev,
                    "title": f"{st['bank']} ••{_last4(card_no)} · {ay} ekstren hazır", "text": text,
                    "cta": "Ekstreyi yükle", "route": "ekstreler", "action": "upload", "due": cut.isoformat(),
                    "tag": "Yeni" if since <= 3 else _ago(since), "period": _month(cut),
                    "short": f"{st['bank']} ••{_last4(card_no)} (ekstre {cut.day} {AY[cut.month - 1][:3]})",
                    "bank": st["bank"], "last4": _last4(card_no), "card_no": card_no})
    return out


def _uploaded_dates(c, acc: dict) -> list[date]:
    """Bu hesaba ait döküm dosyalarının yüklenme günleri (İstanbul)."""
    from app.bank_import import short_bank

    out = []
    for r in c.execute("SELECT bank, account_id, uploaded_at FROM bank_documents"):
        if r["account_id"] != acc["id"] and short_bank(r["bank"] or "") != acc["name"]:
            continue
        try:
            ts = datetime.fromisoformat(str(r["uploaded_at"])).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
        out.append(ts.astimezone(TZ).date())
    return out


def _dokum(now: date) -> list[dict]:
    """Dönem kapanınca: döküm kullanan her banka hesabı için o dönemin dökümü yoksa ayrı hatırlatma. Takvim ayı
    kullananda ay sonu; ay dönümü ayarlıysa (ör. 17'si) dönem sonu (16'sı): 'Ekim dönemi 17 Eki – 16 Kas' görünen
    kullanıcıya 1 Kasım'da takvimin 'Ekim dökümü' sorulmaz."""
    from app.finance import add_months, period_bounds, period_name, period_span

    cycle = db.cycle_start_day() > 1
    if cycle:
        period = add_months(db.period_of(now), -1)
        m_start, m_end = period_bounds(period)
        ay = f"{period_name(period, year=False)} dönemi"  # ad dönemin başladığı aydan (db.period_name_month)
        span = f" ({period_span(period)})"
    else:
        m_end = now.replace(day=1) - timedelta(days=1)
        m_start = m_end.replace(day=1)
        ay, span = AY[m_end.month - 1], ""
    since = (now - m_end).days  # dönem bitiminin ertesi günü = 1
    out = []
    with db.connect() as c:
        for a in db.list_accounts():
            if a["kind"] != "banka":
                continue
            lo, hi = c.execute("SELECT MIN(entry_date), MAX(entry_date) FROM entries WHERE account_id = ? AND "
                               "import_hash IS NOT NULL AND import_hash NOT LIKE 'dekont:%'", (a["id"],)).fetchone()
            ups = _uploaded_dates(c, a)
            as_of = date.fromisoformat(a["as_of"]) if a.get("as_of") and a.get("source") == "dokum" else None
            if not hi and not ups and not as_of:
                continue  # hiç döküm yüklememiş: elle giriyor
            # dökümün ulaştığı son gün: içe aktarılan son hareket ya da dökümden gelen bakiye tarihi
            hi_d = max([x for x in (date.fromisoformat(hi) if hi else None, as_of) if x], default=None)
            last = max(x for x in (hi_d, *ups) if x)
            if (now - last).days > DOKUM_STALE:
                continue  # dökümü bırakmış
            covered = (hi_d and hi_d >= m_end) or (any(u > m_end for u in ups) and hi_d and hi_d >= m_start)
            if covered:
                continue
            if lo and date.fromisoformat(lo) > m_end:
                continue
            kapandi = "Dönem kapandı" if cycle else "Ay kapandı"
            out.append({"id": f"dokum:{a['id']}:{period if cycle else _month(m_end)}", "kind": "dokum",
                        "severity": "warn" if since > DOKUM_WARN_DAY else "info",
                        "title": f"{a['name']} · {ay} dökümü",
                        "text": f"{kapandi}. {ay}{span} hesap dökümünü yükle; maaş ve havaleler otomatik eşleşsin.",
                        "cta": "Dökümü yükle", "route": "ekstreler", "action": "upload", "due": m_end.isoformat(),
                        "tag": kapandi if since <= DOKUM_WARN_DAY else _ago(since),
                        "period": period if cycle else _month(m_end),
                        "short": f"{a['name']} (döküm)",
                        "bank": a["name"], "account_id": a["id"]})
    return out


def _odeme(now: date) -> list[dict]:
    from app.finance import _latest_statement_per_account, _status

    out = []
    for card_no, st in _latest_statement_per_account().items():
        if not st.get("due_date") or not (st.get("total_debt") or 0) > 0:
            continue
        # hatırlatma ekstrede yazan güne göre (ihtiyatlı: 3 gün, 1 gün, son gün). Yazan gün hafta sonu/bayramsa ve
        # geçtiyse son ödeme ilk iş günüdür (app/workdays.py; finance._status da o gün geçmeden "gecikti" demez):
        # o güne kadar hatırlatma sürer ("yarın", "bugün") — Cumartesi'yi kaçıran Pazartesi faizsiz öder
        printed = date.fromisoformat(st["due_date"])
        due = printed if printed >= now else workdays.next_business_day(printed)
        left = (due - now).days
        if not 0 <= left <= ODEME_DAYS or _status(st["total_debt"], st.get("paid_amount"), st["due_date"], now) == "odendi":
            continue
        when = "bugün" if left == 0 else "yarın" if left == 1 else f"{left} gün sonra"
        ay = AY[date.fromisoformat(st["period_end"]).month - 1]
        rest = max(0.0, (st["total_debt"] or 0) - (st.get("paid_amount") or 0))  # dönem borcunun kalanı (asgari değil)
        out.append({"id": f"odeme:{_cid(card_no)}:{due.isoformat()}", "kind": "odeme",
                    "severity": "urgent" if left <= 1 else "warn",
                    "title": f"{st['bank']} ••{_last4(card_no)} · Son ödeme {when}",
                    "text": f"{ay} ekstresinin son ödeme günü {_d(due)} {GUN[due.weekday()]}"
                            + (f" ({_d(printed)} tatile denk geldi)" if due != printed else "")
                            + ". Ödediysen işaretle; faiz işlemesin.",
                    "cta": "Ödemeye bak", "route": "ozet", "action": "pay", "due": due.isoformat(),
                    "tag": "Bugün" if left == 0 else "Yarın" if left == 1 else f"{left} gün kaldı",
                    "period": _month(due), "bank": st["bank"], "last4": _last4(card_no), "card_no": card_no,
                    "statement_id": st["id"], "amount": round(rest, 2)})
    return out


def _varlik_soru(now: date) -> list[dict]:
    """Aşama 2: "Bu hangi araç?" — bağlanmamış araç harcamaları tek kartta (işyeri başına ayrı kart değil)."""
    from app import asset_costs

    q = asset_costs.questions(now)
    if not q["count"] or not q["can_answer"]:
        return []
    top = q["items"][0]
    more = len(q["items"]) - 1
    return [{"id": f"varlik_soru:{now.isoformat()[:7]}", "kind": "varlik", "severity": "info", "action": "asset_question",
             "route": "varliklar", "title": f"{q['count']} araç harcaması: hangi araç?",
             "text": f"{top['merchant']}" + (f" ve {more} işyeri daha" if more > 0 else "") + ". Bir kez söyle, öğrenelim.",
             "cta": "Araç seç", "tag": "Hangi araç?", "period": _month(now), "due": now.isoformat(), "amount": None}]


def _varlik(now: date) -> list[dict]:
    """Varlıklarım: gecikmiş ve 7 gün içindeki bekleyen vadeler; 30 gün içinde tutarı girilmemiş vade ("Tutar gir") ve
    tahmini muayene ("ruhsattan doğrula"). Kart başına değil vade başına; snooze / dismiss mevcut düzenle."""
    from app import assets

    out = []
    for d in assets.dues(now - timedelta(days=400), now + timedelta(days=30), on=now):
        if d["status"] not in ("bekliyor", "gecikti"):
            continue
        left = d["days_left"]
        base = {"kind": "varlik", "route": "varliklar", "due_id": d["id"], "asset_id": d["asset_id"],
                "due": d["effective_date"], "period": _month(date.fromisoformat(d["effective_date"])), "amount": d["amount"]}
        if left < 0 or left <= 7:
            when = "gecikti" if left < 0 else "bugün son gün" if left == 0 else f"{left} gün kaldı"
            out.append({**base, "id": f"varlik:{d['id']}", "severity": "urgent" if left <= 1 else "warn",
                        "title": f"{d['asset_name']} · {d['title']} {when}",
                        "text": (d.get("late_text") or "Son günü kaçırma.") + " Ödediysen işaretle.",
                        "cta": "Vadeye bak", "action": "asset_due",
                        "tag": "Gecikti" if left < 0 else "Bugün" if left == 0 else f"{left} gün kaldı"})
        elif d["amount"] is None:
            out.append({**base, "id": f"varlik_tutar:{d['id']}", "severity": "info",
                        "title": f"{d['asset_name']} · {d['title']}: tutar gir",
                        "text": "Tutarı girersen Planlama'da bu ay için yer ayırırız.", "cta": "Tutar gir",
                        "action": "asset_due", "tag": f"{left} gün kaldı"})
        elif d["estimated"] and d["kind"] == "muayene":
            out.append({**base, "id": f"varlik_tahmin:{d['id']}", "severity": "info",
                        "title": f"{d['asset_name']} · muayene tarihi tahmini",
                        "text": "Ruhsattaki muayene geçerlilik tarihini girersen hatırlatma doğru güne kurulur.",
                        "cta": "Tarihi gir", "action": "asset_due", "tag": "Tahmin"})
    return out


def _tl(x: float) -> str:
    from app import money

    return money.tl(abs(x), trim=True)


# harcama ekstreye birkaç gün geç düşebilir (provizyon): kesimden bu kadar gün önceki harcama sonraki ekstreye devreder;
# o ekstre de gelip yine bulunamazsa sorulur. Eşleşme penceresiyle aynı (db.POSTING_DAYS)
CARD_POST_DAYS = db.POSTING_DAYS


def _kart_eslesmedi() -> list[dict]:
    """Kartla elle girilmiş gider, içermesi gereken ekstre yüklendiği hâlde ekstredeki bir satıra bağlanmamış (tutar ya
    da tarih toleransın dışında: döviz kuru, geç işlenen işlem). Hem elle kayıt hem kart satırı gider sayılıyor olabilir.
    Kimlik: kart:<kayıt id>. Ekstrede karşılığı bulunup eşleşince (linked_txn_id) kendiliğinden düşer."""
    from datetime import date as _date, timedelta as _td

    sts = db.list_statements()
    if not sts:
        return []
    with db.connect() as c:
        ents = db.rows(c.execute("SELECT * FROM entries WHERE payment = 'kredi_karti' AND kind = 'gider' AND skipped = 0 "
                                 "AND linked_txn_id IS NULL AND import_hash IS NULL"))
    from app import book

    ents = book.filter_entries(ents)
    cards: dict[str, list[str]] = {}
    for st in sts:
        cards.setdefault(st["card_no"][-4:], []).append(st["period_end"])
    out = []
    for e in ents:
        d = _date.fromisoformat(e["entry_date"])
        need = (d + _td(days=CARD_POST_DAYS)).isoformat()
        active = (d + _td(days=40)).isoformat()
        mine = [e["card_last4"]] if e.get("card_last4") and e["card_last4"] in cards else \
            [k for k, pes in cards.items() if min(pes) <= active]  # kart belli değil: o dönem ekstresi olan her kart
        if not mine or not all(any(pe >= need for pe in cards[k]) for k in mine):
            continue
        pe = min(pe for k in mine for pe in cards[k] if pe >= need)
        out.append({"id": f"kart:{e['id']}", "kind": "kart_eslesmedi", "severity": "warn",
                    "title": "Kart harcaması ekstrede bulunamadı",
                    "text": f"Kartla girdiğin “{e['description']}” {_tl(e['amount'])} ({_d(d)}), "
                            f"{_d(_date.fromisoformat(pe))} kesimli ekstrede eşleşmedi. Ekstrede farklı tutar ya da tarihle "
                            "geçtiyse eşleştir; geçmediyse ödeme şeklini düzelt. Yoksa iki kez sayılabilir.",
                    "cta": "Eşleştir", "route": "islemler", "action": "card_match", "due": e["entry_date"],
                    "period": e["month"], "entry_id": e["id"]})
    return out


def _borc_sorulari() -> list[dict]:
    """"Bu havale borçtan mı?": kişinin tek açık borcu/alacağı varken o kişiyle olan, kalandan küçük hareket artık
    kendiliğinden borca yazılmıyor (ilgisiz havale gelir/giderden düşüyordu). Kimlik: borc:<kayıt id>:<borç id>.
    Cevap: POST /api/debts/{borç}/pay {amount, paid_at, entry_id} (evet) ya da dismiss (hayır)."""
    from app import book

    m = book.member()
    if m and book.role(m) == "viewer":
        return []
    out = []
    for q in db.debt_questions():
        e, d = q["entry"], q["debt"]
        yon = "geri ödemesi" if d["direction"] == "borc" else "tahsilatı"
        out.append({"id": f"borc:{e['id']}:{d['id']}", "kind": "borc_odeme", "severity": "info",
                    "title": "Bu havale borçtan mı?",
                    "text": f"{e['counterparty']} ile {_tl(e['amount'])} ({_d(date.fromisoformat(e['entry_date']))}) "
                            f"hareketi, {d['person']} ile kalan {_tl(d['remaining'])} borcun {yon} mı? Evet dersen "
                            "gelir/gider sayılmaz, borçtan düşülür.",
                    "cta": "Evet, borçtan", "route": "borclar", "action": "debt_pay", "due": e["entry_date"],
                    "period": e["month"], "entry_id": e["id"], "debt_id": d["id"],
                    "pay": {"amount": e["amount"], "paid_at": e["entry_date"], "entry_id": e["id"]}})
    return out


def _esitleme(upload: bool) -> list[dict]:
    """"Bu ikisi aynı mı?" (elle girilen ↔ döküm/ekstre; yazma yetkisi olana) ve "Bu döküm X hesabına mı ait?"
    (hesapları yönetebilene). Kimlikler: esles:<kayıt id>:<e|t><id>, hesap:<dökümden açılan>:<elle açılan>."""
    from app import book, sync

    m = book.member()
    if m and book.role(m) == "viewer":
        return []
    out = sync.questions()
    asked = {q.get("entry_id") for q in out}
    out += [q for q in _kart_eslesmedi() if q["entry_id"] not in asked]
    if not _page_hidden("borclar"):
        out += _borc_sorulari()
    if upload and not _page_hidden("hesaplar"):
        visible = {a["id"] for a in db.list_accounts()}  # ortak defter: gizli hesap sorulmaz
        out += [q for q in sync.account_questions() if q["account_id"] in visible and q["into"] in visible]
    return out


def _belge(upload: bool) -> list[dict]:
    """Doğrulanamayan belgeler (app/verification.py): onay bekleyen e-posta belgesi, kontrol edilmemiş yükleme."""
    from app import verification

    if not upload:
        return []
    items = verification.todos(today())
    if _page_hidden("ekstreler"):
        items = [i for i in items if i.get("action") != "statement_confirm"]
    if _page_hidden("hesaplar"):
        items = [i for i in items if i.get("action") != "document_confirm"]
    return items


def _dogruluk() -> list[dict]:
    """Gece doğruluk denetimi ihlal bulduysa "Rakamları kontrol et" (app/integrity.py; kimlik dogruluk:<imza>)."""
    from app import book, integrity

    m = book.member()
    if m and book.role(m) == "viewer":
        return []
    t = integrity.todo()
    return [t] if t else []


def _finans() -> list[dict]:
    """Gece finans tutarlılık denetiminin düzeltilebilir maddeleri (app/finance_audit.py; kimlik finans:<kod>:<kayıt>):
    hesabı belli olmayan ödendi, eksi / eski bakiye, gelir+gider sayılmış aktarım … Ortak defter üyesine yok."""
    from app import finance_audit

    try:
        return finance_audit.todos()
    except Exception:  # noqa: BLE001 - denetim maddesi Yapılacaklar'ı bozmasın
        import logging

        logging.getLogger(__name__).exception("finans denetimi maddeleri hazırlanamadı")
        return []


def _sabit_tekrar(now: date) -> list[dict]:
    """"İki sabit kalem aynı olabilir" (app/recurring_dupes.py): sabit kalemleri değiştirebilene (sahip, tam yetkili
    üye), Planlama açıksa. Kimlik: sabit:<silinecek>:<kalacak>; birleşince ya da "Ayrı kalsın" denince düşer."""
    from app import book, recurring_dupes

    m = book.member()
    if (m and book.role(m) != "full") or _page_hidden("plan"):
        return []
    out = []
    for p in recurring_dupes.find(now):
        out.append({"id": p["id"], "kind": "sabit_tekrar", "severity": "warn", "title": p["title"], "text": p["text"],
                    "cta": "Birleştir", "route": "plan", "action": "recurring_merge", "due": now.isoformat(),
                    "keep_id": p["keep"]["id"], "drop_id": p["drop"]["id"], "keep_name": p["keep"]["description"],
                    "drop_name": p["drop"]["description"], "amount": p["keep"]["amount"], "reason": p["reason"],
                    "keep_linked": p["keep"]["linked"]})
    return out


def _duzenli(now: date) -> list[dict]:
    """"Bu düzenli mi?" (finance.regular_candidates; kullanıcı kararı 16.11): sabit kaleme bağlı olmayan, sıklık kalıbına
    (haftalık / 2 haftalık / aylık) uyan kart dışı tekrar. Kimlik duzenli:<grup anahtarı>:<kalıp>. Cevaplar:
    Yalnız ?regular=1 isteyen istemciye (build regular). Evet → POST /api/recurring {…öneri, frequency, anchor_date, link_group} (sabit kalem YALNIZ bu onayla oluşur);
    Hayır, geçici → POST /api/forecast/temporary {key, pattern} (tahmine girmez, REGULAR_TEMP_DAYS sorulmaz; kalıp
    değişirse yeniden sorulur); Sonra → snooze. Sabit kalemleri değiştirebilene (sahip, tam yetkili), Planlama açıksa."""
    from app import book, finance

    m = book.member()
    if (m and book.role(m) != "full") or _page_hidden("plan"):
        return []
    out = []
    for c in finance.regular_candidates(now):
        what = c["description"] or c["category"] or "Bu ödeme"
        title = (f"{what} artık {c['frequency_label']} gibi görünüyor — düzenli mi?" if c["changed"]
                 else f"{what} {c['frequency_label']} tekrarlıyor — düzenli mi?")
        kid = hashlib.sha1(c["key"].encode()).hexdigest()[:12]  # anahtar serbest metin; kimlik REMINDER_ID'ye uysun
        out.append({"id": f"duzenli:{kid}:{c['frequency']}", "kind": "duzenli_mi", "severity": "info",
                    "title": title, "text": f"Son {c['count']} kez yaklaşık {_tl(c['amount'])}. Düzenliyse sabit "
                    "kalem olur ve tahminde ayrıca sayılır; geçiciyse tahmine katılmaz.",
                    "cta": "Evet, düzenli", "cta_no": "Hayır, geçici", "action": "regular_confirm",
                    "route": "plan", "due": now.isoformat(), "amount": c["amount"], "key": c["key"],
                    "frequency": c["frequency"], "frequency_label": c["frequency_label"],
                    "anchor_date": c["anchor_date"], "day_of_month": c["day_of_month"], "category": c["category"],
                    "description": c["description"], "changed": c["changed"]})
    return out


def _ekstre_oku() -> list[dict]:
    """"Ekstre yeniden okunabilir" (app/statement_reread.py; yalnız önbellekten, ekstreyi değiştirebilene). Kimlik:
    ekstre_oku:<ekstre id>; "Şimdi değil" = dismiss; uygulanınca (kaynak 'sablon') kendiliğinden düşer."""
    from app import statement_reread

    try:
        return statement_reread.todos()
    except Exception:  # noqa: BLE001 - öneri hesabı Yapılacaklar'ı bozmasın
        import logging

        logging.getLogger(__name__).exception("ekstre yeniden okuma maddesi hesaplanamadı")
        return []


def build(now: date | None = None, include_hidden: bool = False, ekstre_grace: int = EKSTRE_GRACE,
          regular: bool = False, assets: bool = False) -> list[dict]:
    """Bugün gösterilecek hatırlatmalar (önce acil olanlar). ekstre_grace: push bildirimi 0 verir (kesimden 1 gün
    sonra "ekstreni yükle"); Yapılacaklar varsayılanla (banka PDF'i geç gönderebilir). regular: "Bu düzenli mi?"
    maddeleri (kind duzenli_mi) — yalnız tanıyan istemci ister (?regular=1); eski mobil bilinmeyen türü "Yükle" sanmasın."""
    now = now or today()
    items: list[dict] = []
    upload = _can_upload()
    if upload and not _page_hidden("ekstreler"):
        items += _ekstre(now, _posta_on(), ekstre_grace)
    if upload and not _page_hidden("hesaplar"):
        items += _dokum(now)
    if not _page_hidden("ekstreler"):
        items += _odeme(now)
    items += _esitleme(upload)
    try:
        items += _sabit_tekrar(now)
    except Exception:  # noqa: BLE001 - öneri hesabı Yapılacaklar'ı bozmasın
        import logging

        logging.getLogger(__name__).exception("sabit kalem tekrarı hesaplanamadı")
    items += _dogruluk()
    items += _finans()
    if assets and not _page_hidden("varliklar"):  # yalnız tanıyan istemci (?assets=1): eski mobil türü bilmez
        try:
            items += _varlik(now) + _varlik_soru(now)
        except Exception:  # noqa: BLE001 - varlık takvimi Yapılacaklar'ı bozmasın (eski şema vb.)
            import logging

            logging.getLogger(__name__).exception("varlık vadeleri hesaplanamadı")
    try:
        items += _duzenli(now) if regular else []
    except Exception:  # noqa: BLE001 - öneri hesabı Yapılacaklar'ı bozmasın
        import logging

        logging.getLogger(__name__).exception("düzenli mi adayları hesaplanamadı")
    oku = _ekstre_oku()
    fix = {i["statement_id"] for i in oku}  # düzeltmesi hazır ekstrede "kontrol et" ayrıca sorulmaz
    items += [i for i in _belge(upload) if not (i.get("action") == "statement_confirm" and i.get("statement_id") in fix)]
    items += oku
    st = _state()
    if not include_hidden:
        items = [i for i in items if not _hidden(i["id"], st, now)]
    return sorted(items, key=lambda i: (SEVERITY_ORDER[i["severity"]], i["due"], i["id"]))
