"""Aylık belge hatırlatmaları: kesilen ama yüklenmeyen kart ekstresi, ayı kapanan ama dökümü gelmeyen banka hesabı,
yaklaşan son ödeme. Özet'teki "Yapılacaklar" kartı (mobil + web) ve ileride push bildirimleri aynı listeyi kullanır.

Her hatırlatmanın kimliği kararlıdır ve döneme bağlıdır: "ekstre:<kart no>:<YYYY-MM>", "dokum:<hesap id>:<YYYY-MM>",
"odeme:<kart no>:<YYYY-MM-DD>". Eşitleme soruları (app/sync.py) kayda bağlıdır: "esles:<kayıt id>:<e|t><id>",
"hesap:<hesap id>:<hesap id>"; cevaplanınca kendiliğinden düşer. Kapatma (dismiss) ve erteleme (snooze) bu kimlikle saklanır; sonraki dönemin
hatırlatması yeni kimlikle yeniden çıkar. Durum kişiye özeldir (ortak defterde üyenin kapattığı sahibinkini kapatmaz).

Dönem ve gün hesapları Europe/Istanbul saatine göre yapılır.
"""
import json
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from app import db

TZ = ZoneInfo("Europe/Istanbul")
AY = ["Ocak", "Şubat", "Mart", "Nisan", "Mayıs", "Haziran", "Temmuz", "Ağustos", "Eylül", "Ekim", "Kasım", "Aralık"]
GUN = ["Pazartesi", "Salı", "Çarşamba", "Perşembe", "Cuma", "Cumartesi", "Pazar"]

EKSTRE_GRACE = 1       # kesimden sonra bu kadar gün bekle: banka PDF'i hemen göndermeyebilir
EKSTRE_WINDOW = 45     # kesimin üstünden bu kadar gün geçtiyse kart muhtemelen kullanılmıyor: ısrar etme
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


def _ekstre(now: date, posta: bool) -> list[dict]:
    from app.finance import _latest_statement_per_account, _next_cut

    out = []
    for card_no, st in _latest_statement_per_account().items():
        cut, due = _next_cut(st)
        since = (now - cut).days
        if since < EKSTRE_GRACE + 1 or since > EKSTRE_WINDOW:
            continue
        ay = AY[cut.month - 1]
        text = f"Kesim tarihi {_d(cut)}'di. Ekstreyi yükle ki harcamaların ve son ödeme tarihin güncellensin."
        if posta:
            text = (f"Kesim tarihi {_d(cut)}'di. Ekstreyi yükle ya da bankanın mailini posta adresine ilet; "
                    "harcamaların ve son ödeme tarihin güncellensin.")
        sev = "warn" if since > 7 or (due and (due - now).days <= 5) else "info"
        out.append({"id": f"ekstre:{_cid(card_no)}:{_month(cut)}", "kind": "ekstre", "severity": sev,
                    "title": f"{st['bank']} ••{_last4(card_no)} · {ay} ekstren hazır", "text": text,
                    "cta": "Ekstreyi yükle", "route": "ekstreler", "action": "upload", "due": cut.isoformat(),
                    "tag": "Yeni" if since <= 3 else _ago(since), "period": _month(cut),
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
    """Takvim ayı kapanınca: döküm kullanan her banka hesabı için o ayın dökümü yoksa ayrı hatırlatma."""
    m_end = now.replace(day=1) - timedelta(days=1)
    m_start = m_end.replace(day=1)
    ay = AY[m_end.month - 1]
    since = (now - m_end).days  # ayın 1'i = 1
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
            out.append({"id": f"dokum:{a['id']}:{_month(m_end)}", "kind": "dokum",
                        "severity": "warn" if now.day > DOKUM_WARN_DAY else "info",
                        "title": f"{a['name']} · {ay} dökümü",
                        "text": f"Ay kapandı. {ay} hesap dökümünü yükle; maaş ve havaleler otomatik eşleşsin.",
                        "cta": "Dökümü yükle", "route": "ekstreler", "action": "upload", "due": m_end.isoformat(),
                        "tag": "Ay kapandı" if since <= DOKUM_WARN_DAY else _ago(since), "period": _month(m_end),
                        "bank": a["name"], "account_id": a["id"]})
    return out


def _odeme(now: date) -> list[dict]:
    from app.finance import _latest_statement_per_account, _status

    out = []
    for card_no, st in _latest_statement_per_account().items():
        if not st.get("due_date") or not (st.get("total_debt") or 0) > 0:
            continue
        due = date.fromisoformat(st["due_date"])
        left = (due - now).days
        if not 0 <= left <= ODEME_DAYS or _status(st["total_debt"], st.get("paid_amount"), st["due_date"], now) == "odendi":
            continue
        when = "bugün" if left == 0 else "yarın" if left == 1 else f"{left} gün sonra"
        ay = AY[date.fromisoformat(st["period_end"]).month - 1]
        out.append({"id": f"odeme:{_cid(card_no)}:{due.isoformat()}", "kind": "odeme",
                    "severity": "urgent" if left <= 1 else "warn",
                    "title": f"{st['bank']} ••{_last4(card_no)} · Son ödeme {when}",
                    "text": f"{ay} ekstresinin son ödeme günü {_d(due)} {GUN[due.weekday()]}. Ödediysen işaretle; "
                            "faiz işlemesin.",
                    "cta": "Ödemeye bak", "route": "ozet", "action": "pay", "due": due.isoformat(),
                    "tag": "Bugün" if left == 0 else "Yarın" if left == 1 else f"{left} gün kaldı",
                    "period": _month(due), "bank": st["bank"], "last4": _last4(card_no), "card_no": card_no,
                    "statement_id": st["id"]})
    return out


def _tl(x: float) -> str:
    s = f"{abs(x):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return "₺" + (s[:-3] if s.endswith(",00") else s)


CARD_POST_DAYS = 5  # harcama ekstreye birkaç gün geç düşebilir: kesimden bu kadar önceki harcama sonraki ekstrede


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


def _dogruluk() -> list[dict]:
    """Gece doğruluk denetimi ihlal bulduysa "Rakamları kontrol et" (app/integrity.py; kimlik dogruluk:<imza>)."""
    from app import book, integrity

    m = book.member()
    if m and book.role(m) == "viewer":
        return []
    t = integrity.todo()
    return [t] if t else []


def build(now: date | None = None, include_hidden: bool = False) -> list[dict]:
    """Bugün gösterilecek hatırlatmalar (önce acil olanlar)."""
    now = now or today()
    items: list[dict] = []
    upload = _can_upload()
    if upload and not _page_hidden("ekstreler"):
        items += _ekstre(now, _posta_on())
    if upload and not _page_hidden("hesaplar"):
        items += _dokum(now)
    if not _page_hidden("ekstreler"):
        items += _odeme(now)
    items += _esitleme(upload)
    items += _dogruluk()
    st = _state()
    if not include_hidden:
        items = [i for i in items if not _hidden(i["id"], st, now)]
    return sorted(items, key=lambda i: (SEVERITY_ORDER[i["severity"]], i["due"], i["id"]))
