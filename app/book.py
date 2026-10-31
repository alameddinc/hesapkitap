"""Ortak defter: üye (ör. eş) kendi kullanıcısıyla girer, sahibin defterini görür; yetkisine göre ekler ya da yönetir.

Yetki (users.book_role, davette seçilir): viewer (sadece görür), editor (görür ve ekler), full (yükler, düzenler, siler;
defter ayarları, davetler ve üyeler yine sahibin). Üyenin ayrıca kendine ait özel bir defteri vardır ("Benim"):
o defterde bu kuralların hiçbiri geçmez.

Sahip neyin görünmeyeceğine karar verir; kurallar defterin içinde durur: kişiye özel "book:rules:<üye id>", yoksa
ortak "book:rules":
    hidden_cards     kart numaraları: işlemleri, ekstreleri, ödemeleri, tahminleri görünmez
    hidden_accounts  hesap id'leri: bakiyesi ve hareketleri görünmez
    hide_fees        faiz, gecikme ve kart ücretleri görünmez (faiz alarmı dahil)
    hidden_pages     sayfalar: menüde yoktur, arka uçları da kapalıdır
ve tek tek işlemler "gizli" işaretlenebilir (entries.hidden, transactions.hidden).

Gizleme sunucuda uygulanır: veri okuma fonksiyonları (kart işlemleri, kayıtlar, ekstreler, hesaplar) üye isteğinde
süzülür; özet, grafik, plan ve tahminler bunlardan hesaplandığı için gizli veri hiçbir toplama da girmez.
"""
import json
import re

from app import db

PAGES = {  # sayfa → üyeye kapanan API önekleri
    "plan": ("/api/plan", "/api/recurring"),
    "istatistik": ("/api/compare", "/api/year-summary", "/api/interest-history"),
    "ekstreler": ("/api/statements",),
    "hesaplar": ("/api/accounts", "/api/jars", "/api/wallets", "/api/net-worth"),
    "transferler": ("/api/transfers",),
    "borclar": ("/api/debts", "/api/debt-payments"),
    "zekat": ("/api/zakat", "/api/donations", "/api/holdings"),
    "tasarruf": ("/api/savings", "/api/goals", "/api/subscriptions", "/api/targets"),
    "fiyatlar": ("/api/prices",),
}
PAGE_LABELS = {"plan": "Planlama", "istatistik": "İstatistik", "ekstreler": "Ekstreler", "hesaplar": "Hesaplar",
               "transferler": "Transferler", "borclar": "Borç / Alacak", "zekat": "Zekât ve Sadaka",
               "tasarruf": "Tasarruf", "fiyatlar": "Fiyatlar"}
# üyenin yapabileceği değişiklikler (geri kalan her yazma sahibe ait)
MEMBER_WRITES = [
    ("POST", r"/api/entries"), ("PATCH", r"/api/entries/\d+"), ("PATCH", r"/api/card-transactions/\d+"),
    ("POST", r"/api/entries/\d+/(match|unmatch)"),  # eşitleme: "Bu ikisi aynı mı?" ve eşleşmeyi geri al (görebildiği kayıtta)
    ("POST", r"/api/receipts/parse"), ("POST", r"/api/bills/parse"), ("POST", r"/api/splits/propose"),
    ("PUT", r"/api/entries/\d+/receipt-items"), ("POST", r"/api/entries/\d+/receipt-reread"), ("POST", r"/api/receipts/reread"),
    ("POST", r"/api/share"), ("POST", r"/api/inbox/\d+/\w+"), ("POST", r"/api/digest/dismiss"),
    ("POST", r"/api/jars/\d+/moves"), ("POST", r"/api/jars/\d+/buy"),  # ortak kumbara: aile birlikte biriktirir
    ("POST", r"/api/settings/password"), ("PUT", r"/api/me/ui"), ("POST", r"/api/settings/tokens"), ("DELETE", r"/api/settings/tokens/\d+"),
]
MEMBER_CLOSED = ("/api/settings", "/api/admin", "/api/imports", "/api/templates", "/api/book/", "/api/rules",
                 "/api/bank-import", "/api/onboarding", "/api/mail", "/api/match-rules")
MEMBER_OPEN = ("/api/settings/password", "/api/settings/tokens", "/api/book/me")
ENTRY_MEMBER_FIELDS = {"category", "note", "description", "counterparty", "location"}  # başkasının kaydında
EMPTY = {"hidden_cards": [], "hidden_accounts": [], "hide_fees": False, "hidden_pages": []}
ROLES = ("viewer", "editor", "full")
ROLE_LABELS = {"viewer": "Sadece görür", "editor": "Görür ve ekler", "full": "Tam yetki"}
# tam yetkili üyeye de kapalı: defterin yönetimi sahibindir
FULL_CLOSED = ("/api/settings", "/api/admin", "/api/book/")
# yetkisi ne olursa olsun kişinin kendi hesabıyla ilgili yazmalar
PERSONAL_WRITES = [("POST", r"/api/settings/password"), ("PUT", r"/api/me/ui"), ("POST", r"/api/settings/tokens"),
                   ("DELETE", r"/api/settings/tokens/\d+"), ("POST", r"/api/me/delete"), ("POST", r"/api/digest/dismiss"),
                   ("POST", r"/api/reminders/[^/]+/(snooze|dismiss)"),  # hatırlatma durumu kişiye özel
                   ("DELETE", r"/api/me/sessions/[\w-]+"), ("POST", r"/api/me/sessions/revoke-all")]  # kendi oturumları


def member() -> dict | None:
    """İsteği yapan, ortak defteri açmış bir üye mi? (sahip, tek kullanıcılı kurulum ve "Benim" defteri: None)"""
    u = db.current_user()
    return u if u and u.get("member_of") and not u.get("own_book") else None


def role(u: dict | None = None) -> str:
    u = u or member()
    r = (u or {}).get("book_role")
    return r if r in ROLES else "editor"


def _key(uid: int | None) -> str:
    return f"book:rules:{uid}" if uid else "book:rules"


def rules(uid: int | None = None, inherit: bool = True) -> dict:
    """uid verilirse o üyenin kuralları; kendine ait yoksa (inherit) ortak kurallar."""
    for key in ([_key(uid)] if uid else []) + (["book:rules"] if inherit or not uid else []):
        raw = db.get_setting(key)
        if raw:
            try:
                return {**EMPTY, **json.loads(raw)}
            except ValueError:
                break
    return dict(EMPTY)


def has_own_rules(uid: int) -> bool:
    return db.get_setting(_key(uid)) is not None


def set_rules(data: dict, uid: int | None = None) -> dict:
    r = {**rules(uid), **{k: v for k, v in data.items() if k in EMPTY}}
    r["hidden_pages"] = [p for p in r["hidden_pages"] if p in PAGES]
    db.set_setting(_key(uid), json.dumps(r, ensure_ascii=False))
    return r


def clear_rules(uid: int) -> None:
    """Üyenin kişiye özel kurallarını kaldır: ortak kurallara döner."""
    db.set_setting(_key(uid), None)


def active() -> dict | None:
    """Üye isteğinde uygulanacak kurallar (kişiye özel ya da ortak); sahip için None (her şeyi görür)."""
    m = member()
    return rules(m["id"]) if m else None


# ---------------------------------------------------------------- erişim (middleware)

def denied(method: str, path: str) -> str | None:
    """Üye bu isteği yapabilir mi? Yapamıyorsa nedeni. ("Benim" defterinde çağrılmaz.)"""
    rl = role()
    closed = FULL_CLOSED if rl == "full" else MEMBER_CLOSED
    if any(path.startswith(p) for p in MEMBER_OPEN):
        pass
    elif any(path.startswith(p) for p in closed):
        return "Bu bölüm defter sahibine ait"
    m = member()
    r = rules(m["id"]) if m else rules()
    for page in r["hidden_pages"]:
        if any(path.startswith(p) for p in PAGES.get(page, ())):
            return "Bu sayfa ortak defterde kapalı"
    if re.fullmatch(r"/api/entries/\d+/candidates", path):  # eşleştirme adayları ham kart işlemlerinden gelir
        return "Bu işlemi defter sahibi yapabilir"
    if path.startswith("/api/net-worth/at") and (r["hidden_accounts"] or r["hidden_cards"]):
        return "Geçmiş net durum ortak defterde kapalı"  # geçmiş bakiyeler tüm hesaplardan hesaplanır
    if method in ("GET", "HEAD", "OPTIONS") or path == "/api/auth/logout":
        return None
    if any(method == mt and re.fullmatch(p, path) for mt, p in PERSONAL_WRITES):
        return None
    if rl == "viewer":
        return "Bu defterde sadece görüntüleme yetkin var"
    if rl == "full":
        return None
    if not any(method == mt and re.fullmatch(p, path) for mt, p in MEMBER_WRITES):
        return "Bu işlemi defter sahibi yapabilir"
    return None


# ---------------------------------------------------------------- süzgeçler (db okuma fonksiyonları çağırır)

def _hidden_last4(r: dict) -> set[str]:
    return {c[-4:] for c in r["hidden_cards"]}


def filter_statements(rows: list[dict]) -> list[dict]:
    r = active()
    if not r:
        return rows
    hidden = set(r["hidden_cards"])
    return [s for s in rows if s["card_no"] not in hidden]


def filter_card_txns(rows: list[dict]) -> list[dict]:
    r = active()
    if not r:
        return rows
    hidden = set(r["hidden_cards"])
    return [t for t in rows if t.get("account") not in hidden and not t.get("hidden")
            and not (r["hide_fees"] and t.get("txn_type") == "faiz_ucret")]


def filter_entries(rows: list[dict]) -> list[dict]:
    r = active()
    if not r:
        return rows
    accs, last4 = set(r["hidden_accounts"]), _hidden_last4(r)
    return [e for e in rows if not e.get("hidden") and e.get("account_id") not in accs
            and not (e.get("payment") == "kredi_karti" and e.get("card_last4") in last4)]


def filter_card_payments(rows: list[dict]) -> list[dict]:
    """Hesaptan yapılan kart ödemeleri: gizli kartın ya da gizli hesabın ödemesi görünmez."""
    r = active()
    if not r:
        return rows
    last4 = _hidden_last4(r)
    with db.connect() as c:
        names = {n for (n,) in c.execute(f"SELECT name FROM accounts WHERE id IN ({','.join('?' * len(r['hidden_accounts']))})",
                                         r["hidden_accounts"])} if r["hidden_accounts"] else set()
    from app.bank_import import short_bank

    return [p for p in rows if p.get("card_last4") not in last4 and short_bank(p.get("account") or "") not in names]


def filter_accounts(rows: list[dict]) -> list[dict]:
    r = active()
    if not r:
        return rows
    accs = set(r["hidden_accounts"])
    return [a for a in rows if a["id"] not in accs]


def hide_fees() -> bool:
    r = active()
    return bool(r and r["hide_fees"])


def can_see_entry(entry_id: int) -> bool:
    with db.connect() as c:
        e = c.execute("SELECT * FROM entries WHERE id = ?", (entry_id,)).fetchone()
    return bool(e) and bool(filter_entries([dict(e)]))


def can_see_txn(txn_id: int) -> bool:
    return any(t["id"] == txn_id for t in db.card_transactions(include_deleted=True))


def can_see_statement(sid: int) -> bool:
    with db.connect() as c:
        s = c.execute("SELECT * FROM statements WHERE id = ?", (sid,)).fetchone()
    return bool(s) and bool(filter_statements([dict(s)]))


def can_see_account(aid: int) -> bool:
    r = active()
    return not r or aid not in set(r["hidden_accounts"])


CAN_SEE = {"entry": can_see_entry, "txn": can_see_txn, "card": can_see_txn, "statement": can_see_statement,
           "account": can_see_account}
# yoldaki id → nesne türü. Bu uçlardaki her istek (okuma ve yazma) üyenin göremediği nesnede 404 alır; uç kendi
# kontrolünü unutsa da (ör. gizli kaydı id ile silmek) middleware'de durur.
PATH_TARGETS = [
    (re.compile(r"/api/entries/(\d+)(?:/.*)?"), "entry"),
    (re.compile(r"/api/card-transactions/(\d+)(?:/.*)?"), "txn"),
    (re.compile(r"/api/statements/(\d+)(?:/.*)?"), "statement"),
    (re.compile(r"/api/accounts/(\d+)(?:/.*)?"), "account"),
    (re.compile(r"/api/(?:splits|donations/mark)/(entry|card)/(\d+)"), None),  # tür yolda
]


def visible(kind: str, oid: int | None) -> bool:
    """Üye bu nesneyi görebilir mi? (sahip ve "Benim" defteri: her zaman; id yoksa denetlenecek bir şey yok)"""
    if oid is None or not member():
        return True
    return CAN_SEE[kind](int(oid))


def hidden_target(path: str) -> bool:
    """Yol, üyenin göremediği (gizli kart/hesap, gizli işaretli) bir nesneyi mi gösteriyor?"""
    for rx, kind in PATH_TARGETS:
        m = rx.fullmatch(path)
        if m:
            k, oid = (m.group(1), m.group(2)) if kind is None else (kind, m.group(1))
            return not visible(k, int(oid))
    return False


def filter_rules(rules_: list[dict]) -> list[dict]:
    """Kategori kuralları satıcı adından öğrenilir (ör. gizli kartta sık geçen mağaza). Üyeye, yalnız göremediği
    işlemlerle eşleşen kural gösterilmez: hem gizli kartta hem görünen yerde geçen kural kalır."""
    if not active():
        return rules_
    from app.textnorm import normalize

    with db.connect() as c:
        txns = [dict(r) for r in c.execute(
            "SELECT t.id, t.description, t.hidden, t.txn_type, s.card_no AS account FROM transactions t "
            "JOIN statements s ON s.id = t.statement_id")]
        ents = [dict(r) for r in c.execute("SELECT * FROM entries")]
    seen_t = {t["id"] for t in filter_card_txns(txns)}
    seen_e = {e["id"] for e in filter_entries(ents)}
    shown = [normalize(t["description"] or "") for t in txns if t["id"] in seen_t]
    shown += [normalize(e["description"] or "") for e in ents if e["id"] in seen_e]
    secret = [normalize(t["description"] or "") for t in txns if t["id"] not in seen_t]
    secret += [normalize(e["description"] or "") for e in ents if e["id"] not in seen_e]
    if not secret:
        return rules_

    def hits(rx, texts):
        return any(rx.search(t) for t in texts)

    out = []
    for r in rules_:
        try:
            rx = re.compile(r["pattern"])
        except re.error:
            out.append(r)
            continue
        if hits(rx, secret) and not hits(rx, shown):
            continue
        out.append(r)
    return out


def people() -> list[dict]:
    """Defterdeki kişiler (sahip + üyeler): kayıtlarda "kim ekledi" baş harfi için."""
    from app import system

    u = db.current_user()
    if not u:
        return []
    owner_id = u.get("member_of") or u["id"]
    owner = system.user(owner_id)
    out = [owner] if owner else []
    out += system.members(owner_id)
    return [{"id": p["id"], "name": p["username"].split("@")[0], "owner": p["id"] == owner_id,
             "role": None if p["id"] == owner_id else role(p)} for p in out]


def info() -> dict:
    """Arayüz için: bu kullanıcı üye mi, hangi defter açık, yetkisi, neler kapalı, defterdeki kişiler."""
    u = db.current_user()
    if not u or not u.get("member_of"):
        ppl = people() if u else []
        return {"member": False, "me": u["id"] if u else None, "people": ppl if len(ppl) > 1 else []}
    from app import system

    owner = system.user(u["member_of"]) or {}
    base = {"member": True, "me": u["id"], "owner": owner.get("username"), "role": role(u), "role_label": ROLE_LABELS[role(u)],
            "book": "kendi" if u.get("own_book") else "ortak", "can_switch": True, "people": people()}
    if u.get("own_book"):
        return {**base, "hidden_pages": [], "hide_fees": False}
    r = rules(u["id"])
    return {**base, "hidden_pages": r["hidden_pages"], "hide_fees": r["hide_fees"]}
