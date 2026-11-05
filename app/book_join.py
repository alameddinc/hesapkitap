"""Ortak deftere var olan hesapla katılma ve ayrılma (yetenek "book_join").

Davet (system.create_invite, member_of = sahip) eskiden yalnız yeni hesap açarken kullanılabiliyordu. Artık hesabı
açık kullanıcı da daveti kabul eder:
- GET  /api/book/join/{kod}  önizleme: kim davet ediyor, hangi yetkiyle, katılabilir mi (değilse neden)
- POST /api/book/join/{kod}  katıl: kullanıcı sahibin defterine üye olur, davet tüketilir
- POST /api/book/leave       üye kendi isteğiyle ayrılır

Kendi verisi: katılan hesabın users.db_file'ı dokunulmadan kalır; üyeyken "Benim" defteri odur (system.own_db_path).
Davetle yeni açılan üyede db_file yoktur (Benim boş başlar) — "var olan hesapla katılan" ayrımı da budur: sahip böyle
bir üyeyi çıkarırsa ya da kendi hesabını silerse üyenin hesabı silinmez, yalnız defterden ayrılır (detach).

Uçlar /api/book/invites altında değil: o önek paket kapısında (plans.GATED "ortak_defter") ve katılan çoğu zaman
ücretsiz pakettedir.
"""
from app import plans, system

ERR = {  # neden → (HTTP durumu, Türkçe ileti)
    "unknown": (404, "Davet kodu bulunamadı; kodu kontrol et ya da davet edenden yeni bağlantı iste."),
    "used": (410, "Bu davet kodu kullanılmış; davet edenden yeni bağlantı iste."),
    "expired": (410, "Bu davetin süresi dolmuş; davet edenden yeni bağlantı iste."),
    "not_book": (400, "Bu bir ortak defter daveti değil; yeni hesap açmak içindir."),
    "own_invite": (409, "Bu senin kendi davetin; katılacak kişiye gönder."),
    "already_member": (409, "Bu ortak defterde zaten üyesin."),
    "other_book": (409, "Önce şu anki ortak defterden ayrıl (Diğer → Ortak defter → Ortak defterden ayrıl)."),
    "has_members": (409, "Senin de ortak defterinde üyeler var. Önce kendi ortak defterindeki üyeleri çıkar."),
    "admin": (409, "Yönetici hesabı başka bir ortak deftere katılamaz."),
    "owner_gone": (410, "Bu ortak defter artık yok."),
    "owner_plan": (409, "Davet edenin Aile paketi şu an aktif değil; paket yenilenince yeniden dene."),
    "full": (409, f"Aile koltukları dolu (sahip + {plans.FAMILY_SEATS - 1} kişi). Kendi Premium aboneliğin varsa "
                  "koltuk gerekmeden katılabilirsin; yoksa davet edenden yer açmasını iste."),
    "full_total": (409, f"Ortak defter dolu (en çok {plans.MAX_MEMBERS} üye)."),
    "not_member": (409, "Bir ortak deftere üye değilsin."),
}


class JoinError(Exception):
    def __init__(self, reason: str):
        self.reason = reason
        self.status, self.message = ERR[reason]
        super().__init__(self.message)


def _mask(email: str) -> str | None:
    """ay***@ornek.com — sahibin e-postası önizlemede tam görünmez."""
    if "@" not in (email or ""):
        return None
    local, dom = email.split("@", 1)
    return f"{local[:2]}***@{dom}"


def _owner_ok(owner: dict | None) -> bool:
    return bool(owner) and not owner["disabled"] and not owner.get("deleted_at") and not owner.get("member_of")


def own_subscription(u: dict) -> bool:
    """Kullanıcının kendi aktif Premium/Aile paketi var mı? Varsa Aile koltuğu kullanmaz, kendi paketiyle Premium kalır
    (plans.seat_state "own"); istemci "Premium'un devam ettiği için Aile koltuğu kullanmıyorsun" notunu gösterir.
    Sunucu aboneliğe dokunmaz (mağaza tarafı)."""
    return plans.own_plan(u) is not None


def _check(inv: dict, status: str, u: dict) -> dict:
    """Katılma kuralları; geçerse sahip. Geçmezse JoinError."""
    if status != "ok":
        raise JoinError(status)
    if not inv.get("member_of"):
        raise JoinError("not_book")
    if inv["member_of"] == u["id"]:
        raise JoinError("own_invite")
    if u.get("member_of"):
        raise JoinError("already_member" if u["member_of"] == inv["member_of"] else "other_book")
    if u["role"] == "admin" or not u.get("db_file"):
        raise JoinError("admin")  # ana veritabanını kullanan hesap: "Benim" defterine eşlenemez
    from app import deletion

    if any(not deletion.pending(m) for m in system.members(u["id"])):
        raise JoinError("has_members")
    owner = system.user(inv["member_of"])
    if not _owner_ok(owner):
        raise JoinError("owner_gone")
    if plans.hosted() and not plans.allows(owner, "ortak_defter"):
        raise JoinError("owner_plan")
    if plans.hosted():
        ms = [m for m in system.members(owner["id"]) if not m.get("deleted_at")]
        if len(ms) >= plans.MAX_MEMBERS:
            raise JoinError("full_total")
        if not own_subscription(u) and not plans.free_seat(owner["id"]):  # kendi aboneliği olan koltuk kullanmaz
            raise JoinError("full")
    return owner


def preview(code: str, u: dict) -> dict:
    """{valid, reason?, message?, owner?, owner_email?, role?, role_label?, expires_at?, can_join, own_subscription}"""
    from app import book

    inv, status = system.invite_status(code or "")
    if status != "ok" or not inv.get("member_of"):
        reason = status if status != "ok" else "not_book"
        return {"valid": False, "can_join": False, "reason": reason, "message": ERR[reason][1]}
    owner = system.user(inv["member_of"]) or {}
    rl = inv.get("book_role") or "editor"
    out = {"valid": True, "owner": (owner.get("username") or "").split("@")[0], "owner_email": _mask(owner.get("username")),
           "role": rl, "role_label": book.ROLE_LABELS.get(rl), "expires_at": inv["expires_at"],
           "own_subscription": own_subscription(u), "can_join": True}
    try:
        _check(inv, status, u)
    except JoinError as e:
        out.update(can_join=False, reason=e.reason, message=e.message)
    return out


def accept(code: str, u: dict) -> dict:
    """Daveti kabul et: üye ol, daveti tüket. Kendi verisi (db_file) olduğu gibi kalır → "Benim" defteri."""
    from app import book

    inv, status = system.invite_status(code or "")
    owner = _check(inv or {}, status, u)
    if not system.use_invite(inv["code"], u["id"]):  # aynı anda iki kabul: ikincisi
        raise JoinError("used")
    rl = inv.get("book_role") or "editor"
    system.set_membership(u["id"], owner["id"], rl)
    # kendi bekleyen ortak defter davetleri: artık sahip değil, o kodlarla kimse ona bağlanmasın
    for i in system.invites():
        if i.get("member_of") == u["id"] and not i.get("used_at"):
            system.delete_invite(i["code"])
    return {"ok": True, "owner": (owner.get("username") or "").split("@")[0], "role": rl,
            "role_label": book.ROLE_LABELS.get(rl), "own_subscription": own_subscription(u)}


def detach(m: dict) -> None:
    """Üyeyi defterden ayır (hesabı ve kendi defteri kalır). Sahibin defterindeki kişiye özel kuralı da düşer."""
    owner_id = m.get("member_of")
    before = plans.seat_holders(owner_id) if owner_id else set()
    system.set_membership(m["id"], None)
    plans.clear_seat(m["id"])
    owner = system.user(owner_id) if owner_id else None
    if owner and not owner.get("member_of"):
        from app import book, db

        with db.use_space(system.user_db_path(owner), owner):
            book.clear_rules(m["id"])
    if owner_id:  # boşalan koltuğa sıradaki (en eski bekleyen) kendiliğinden geçer: ona bildirim
        plans.notify_seat_changes(owner_id, before - {m["id"]})


def leave(u: dict) -> dict:
    if not u.get("member_of"):
        raise JoinError("not_member")
    detach(u)
    return {"ok": True}


def joined_with_account(m: dict) -> bool:
    """Var olan hesabıyla katılmış üye (kendi db_file'ı var): çıkarılınca / sahip silinince hesabı silinmez."""
    return bool(m.get("member_of") and m.get("db_file"))
