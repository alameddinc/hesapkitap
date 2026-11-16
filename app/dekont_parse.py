"""Havale/EFT/FAST dekontunu oku (fotoğraf ya da PDF) ve Transferler'e tek işlem olarak kaydet.

Akış (POST /api/dekont/parse → istemci formu → POST /api/dekont/save):
- PDF'in metni varsa önce kural okuması (app/dekont.py etiketleri, yapay zekâsız); metin yoksa / yetmezse yapay zekâ
  (DEKONT_PROMPT, kısa çıktı).
- Kimden kime: gönderen ve alıcı (ad + IBAN + banka) kullanıcının kayıtlı hesaplarıyla (accounts.iban, banka adı) ve
  kendi adıyla (dökümlerin sahibi, kart sahibi) karşılaştırılır. Alıcı kullanıcıysa "gelen", gönderen kullanıcıysa
  "giden"; ikisi de değilse ya da ikisi de kullanıcıysa yön boş kalır (form iki seçeneği sorar).
- IBAN yanıtta hiçbir zaman tam dönmez: yalnız son 4 hane.
- Kayıt app/dekont.py import_text ile aynı yapıda: import_hash 'dekont:<tarih>:<tutar>:<ref>', bakiye çapası
  değişmez; o ayın hesap dökümü gelince db.import_bank_rows aynı satırla birleştirir (iki kez sayılmaz).
- Aynı dekont ikinci kez: (ref) ya da (tarih + tutar + karşı taraf) eşleşen kayıt varsa kaydedilmez (409, entry_id).
- Karşı tarafın açık alacak (gelen) / borç (giden) kaydı varsa debt_match önerilir; kullanıcı "evet" derse kayıt borç
  hareketi olarak bağlanır (db.pay_debt entry_id ile): gelir/gider sayılmaz, çift sayım olmaz.
"""
import re

from app import db, dekont, money

# ---------------------------------------------------------------- tür tanıma (yapay zekâsız)

# GONDEREN: bazı bankalar (VakıfBank "GONDEREN AD SOYAD/UNVAN") Türkçe harfsiz yazar
_TRANSFER_MARKS = re.compile(r"G[ÖO]NDEREN|\bALICI\b|ALICI IBAN|\bFAST\b|\bEFT\b|HAVALE|PARA TRANSFER|\bIBAN\b")
_STRONG_MARKS = re.compile(r"G[ÖO]NDEREN|ALICI IBAN|\bFAST\b|\bEFT\b|HAVALE|PARA TRANSFER")
# banka "fatura ödeme dekontu" da DEKONT başlığı taşır: Ödemeler akışına aittir
_BILL_MARKS = re.compile(r"FATURA|ABONE|TESİSAT|SÖZLEŞME HESAP|KURUM KODU|SON ÖDEME")


def _up(s: str) -> str:
    return s.replace("i", "İ").replace("ı", "I").upper()


def is_transfer(text: str | None) -> bool:
    """Metin havale/EFT/FAST dekontu mu: dekont başlığı + en az bir güçlü aktarım işareti (gönderen, alıcı IBAN,
    FAST/EFT/havale, para transferi) ya da başlıksız ama gönderen ve alıcı birlikte; fatura ödeme dekontu değil."""
    if not text:
        return False
    up = _up(text[:4000])
    if _BILL_MARKS.search(up):
        return False
    if dekont.is_dekont(text) and _STRONG_MARKS.search(up):
        return True
    return bool(re.search(r"G[ÖO]NDEREN", up) and re.search(r"\bALICI\b", up) and re.search(r"\bIBAN\b|FAST|EFT|HAVALE", up))


def classify_text(text: str | None) -> str | None:
    """PDF metninden öneri (Paylaş seçim sayfası): dekont | ekstre | fis | None. Hak düşmez, yapay zekâ yok.
    fis (yetenek share_pdf_receipt): fatura biçimli (e-Arşiv / e-Fatura) ama son ödemesiz, kurum işaretsiz satış
    belgesi — mağaza / restoran fişi; okuma da bunu fiş yapar (karekod.bill_without_due). Kurum faturası: öneri yok."""
    if not text:
        return None
    if is_transfer(text):
        return "dekont"
    from app.mailbox import classify, looks_like_bill

    if looks_like_bill(text):
        return "fis" if sale_doc(text) else None

    try:
        kind, sure = classify(text)
    except Exception:  # noqa: BLE001 - tanınmayan metin: öneri yok
        return None
    if kind in ("account", "card") and sure:
        return "ekstre"
    return None


def sale_doc(text: str) -> bool:
    """Fatura biçimli metin alışveriş belgesi mi: son ödeme / kurum (abonelik, kamu hizmeti, fatura şablonu) işareti
    yok ve en az bir tutar var. "Ödenmiştir" bakılmaz: mağazanın e-Arşivi de "kredi kartı ile ödenmiştir" yazar."""
    from app import karekod

    if karekod.bill_like(None, text):
        return False
    return bool(karekod._amounts(karekod._text_lines(text)[1]))


# ---------------------------------------------------------------- IBAN

def iban_parts(v: str | None) -> tuple[str, str]:
    """(tam IBAN ya da "", son 4 hane ya da ""). Maskeli yazım (TR12 **** **** 2345 67) da son haneyi verir."""
    s = (v or "").upper()
    if full := dekont._iban(s):
        return full, full[-4:]
    m = re.search(r"TR[\d\s*•xX.-]{8,40}", s)
    if not m:  # IBAN yerine hesap numarası ("ALICI HESAP NO 0012…3456"): IBAN'ın sonu, son 4 hane eşleşir
        n = re.sub(r"[\s-]", "", s)
        return ("", n[-4:]) if re.fullmatch(r"\d{8,26}", n) else ("", "")
    digits = re.sub(r"\D", "", m.group(0)[2:])
    return "", digits[-4:] if len(digits) >= 4 else ""


def iban_bank(full: str) -> str:
    from app.templates import IBAN_BANKS

    return IBAN_BANKS.get(full[4:9], "") if full else ""


# ---------------------------------------------------------------- okuma

def _clean(v) -> str:
    s = re.sub(r"\s+", " ", str(v or "")).strip()
    return "" if s.lower() in ("null", "none", "-", "bilinmiyor") else s


def _type_of(*texts: str) -> str | None:
    t = _up(" ".join(x for x in texts if x))
    for k, out in (("FAST", "fast"), ("EFT", "eft"), ("HAVALE", "havale")):
        if re.search(rf"\b{k}\b", t):
            return out
    return None


_DIR_LABELS = ("İŞLEM TÜRÜ", "İŞLEM TİPİ", "İŞLEM ADI", "İŞLEM")


def doc_direction(text: str | None, kind_txt: str | None = None) -> str | None:
    """Belgeyi düzenleyen bankanın bakışıyla yön: "giden" (GİDEN FAST/EFT, hesaptan gönderilen) | "gelen" (GELEN FAST,
    gelen havale) | None. Önce işlem türü alanı; yoksa metinde ilk geçen ("AÇIKLAMA: GİDEN FAST - X TARAFINDAN
    GÖNDERİLEN" alıcı bankanın dekontunda da gönderenin metnidir, işlem türü satırı ondan önce gelir)."""
    def pick(t: str) -> str | None:
        m = re.search(r"\b(GELEN|GİDEN|GIDEN)\b", _up(t))
        return None if not m else "gelen" if m.group(1) == "GELEN" else "giden"

    if kind_txt and (d := pick(kind_txt)):
        return d
    if not text:
        return None
    for lab in _DIR_LABELS:
        if (v := dekont._field(text, lab)) and (d := pick(v)):
            return d
    return pick(text[:4000])


def fields_from_text(text: str) -> dict:
    """Kural okuması (PDF metni): {date, amount, sender_name, sender_iban, sender_bank, receiver_name, receiver_iban,
    receiver_bank, bank, description, transfer_type, ref}. Tarih/tutar yoksa ValueError (yapay zekâya geçilir)."""
    from app.templates import bank_of

    p = dekont.parse(text)  # tarih, tutar, ref, belgeyi veren banka (tarih ya da tutar yoksa ValueError)
    f = dekont._field
    sender = f(text, "GÖNDEREN", "GÖNDEREN ADI", "GÖNDEREN AD SOYAD", "GÖNDEREN ADI SOYADI", "GÖNDEREN UNVAN",
               "GONDEREN", "GONDEREN AD SOYAD/UNVAN", "GONDEREN AD SOYAD", "GÖNDEREN AD SOYAD/UNVAN") or ""
    receiver = f(text, "ALICI", "ALICI ADI", "ALICI AD SOYAD", "ALICI ADI SOYADI", "ALICI UNVAN") or ""
    s_iban = f(text, "GÖNDEREN IBAN", "GÖNDEREN HESAP", "GONDEREN IBAN") or ""
    r_iban = f(text, "ALICI IBAN", "ALICI HESAP", "ALICI HESAP NO") or ""
    own = f(text, "IBAN") or ""
    kind_txt = f(text, "İŞLEM TÜRÜ", "İŞLEM TİPİ", "İŞLEM ADI", "İŞLEM") or ""
    side = doc_direction(text, kind_txt)
    if side == "gelen":  # gelen dekontunda belgenin sahibi alıcıdır: tek "IBAN" ve müşteri adı alıcıya
        if not r_iban and own and own != s_iban:
            r_iban = own
        if not receiver:
            receiver = f(text, "MÜŞTERİ AD SOYAD", "MÜŞTERİ ADI SOYADI", "MÜŞTERİ ADI", "AD SOYAD") or ""
    elif not s_iban and own and own != r_iban:  # belgede tek "IBAN" alanı: belgeyi alan (gönderen) hesap
        s_iban = own
    return {"date": p["date"], "amount": p["amount"], "sender_name": _clean(sender), "sender_iban": s_iban,
            "sender_bank": "", "receiver_name": _clean(receiver), "receiver_iban": r_iban,
            "receiver_bank": _clean(f(text, "ALICI BANKA", "ALICI BANKASI") or ""), "bank": p["bank"] or bank_of(text),
            "description": _clean(p["purpose"]), "transfer_type": _type_of(kind_txt, text[:1500]), "ref": p["ref"],
            "doc_direction": side}


DEKONT_PROMPT = """Bu belge Türkiye'den bir banka PARA TRANSFERİ dekontu (havale, EFT ya da FAST); fotoğraf ya da PDF.
SADECE şu JSON'u döndür (bilinmeyen alan null):
{
  "is_transfer": "belge kişiden kişiye/hesaba para transferi dekontuysa true; fatura, fiş, hesap dökümü ise false",
  "amount": 0.00,
  "date": "YYYY-MM-DD işlem tarihi",
  "sender_name": "GÖNDEREN ad soyad / unvan, belgede yazdığı gibi",
  "sender_iban": "gönderen IBAN, belgede yazdığı gibi (maskeli olabilir)",
  "sender_bank": "gönderen banka",
  "receiver_name": "ALICI ad soyad / unvan; belgede alıcı adı yoksa ve işlem GELEN ise belgenin müşterisi (MÜŞTERİ AD SOYAD)",
  "receiver_iban": "alıcı IBAN (yoksa ALICI HESAP NO)",
  "receiver_bank": "alıcı banka",
  "bank": "dekontu düzenleyen banka",
  "description": "açıklama / ödeme amacı / mesaj, belgede yazdığı gibi",
  "transfer_type": "havale | eft | fast",
  "doc_direction": "giden | gelen: belgeyi düzenleyen bankanın İŞLEM / İŞLEM TÜRÜ satırına göre ('Giden FAST' → giden, 'Gelen FAST', 'Gelen EFT', 'Gelen havale' → gelen); açıklama satırındaki gönderenin metnine göre değil",
  "ref": "sorgu / referans / işlem numarası (SORGU NO, SORGU REFERANS NO varsa o)"
}
Türk sayı formatında 1.234,56 = 1234.56. Tutar işlem tutarıdır (masraf hariç)."""

TEXT_HINT = """
Telefonda bu belgeden çıkarılmış metin (yardımcı; satır sırası bozuk olabilir, belgedeki yazı esastır):
---
{text}
---"""


def fields_from_ai(data: bytes, mime: str, ocr_text: str | None = None) -> dict:
    from app import receipt

    prompt = DEKONT_PROMPT + (TEXT_HINT.format(text=ocr_text[:6000]) if ocr_text else "")
    raw, meta = receipt._gemini_json(prompt, data, mime, max_output_tokens=1200, kind="belge")
    amount = receipt._num(raw.get("amount"))
    return {"date": receipt._iso(raw.get("date")), "amount": abs(amount) if amount else None,
            "sender_name": _clean(raw.get("sender_name")), "sender_iban": _clean(raw.get("sender_iban")),
            "sender_bank": _clean(raw.get("sender_bank")), "receiver_name": _clean(raw.get("receiver_name")),
            "receiver_iban": _clean(raw.get("receiver_iban")), "receiver_bank": _clean(raw.get("receiver_bank")),
            "bank": _clean(raw.get("bank")), "description": _clean(raw.get("description"))[:200],
            "transfer_type": (_clean(raw.get("transfer_type")).lower() or None) if
            _clean(raw.get("transfer_type")).lower() in ("havale", "eft", "fast") else None,
            "ref": re.sub(r"\W", "", _clean(raw.get("ref")))[:30],
            "doc_direction": _clean(raw.get("doc_direction")).lower() if
            _clean(raw.get("doc_direction")).lower() in ("giden", "gelen") else None,
            "not_transfer": str(raw.get("is_transfer")).strip().lower() == "false", "model": meta.get("model")}


# ---------------------------------------------------------------- kimden kime

def _key(name: str | None) -> str:
    return db._name_key(name or "") if name else ""


def _own_keys() -> set[str]:
    """Kullanıcının kendi adı: dökümlerin sahibi ve kart sahipleri (boşluksuz, Türkçe harfsiz; kısa adlar sayılmaz)."""
    names = [*db.owner_names(), *db.card_holders()]
    return {k for k in (_key(n) for n in names if n) if len(k) >= 6}


def _bank_accounts() -> list[dict]:
    return [a for a in db.list_accounts() if a.get("kind") in ("banka", "nakit", "diger")]


def _side(name: str, iban: str, bank: str, accounts: list[dict], own: set[str]) -> dict:
    full, last4 = iban_parts(iban)
    bank = iban_bank(full) or bank
    acc = None
    for a in accounts:
        a_full = (a.get("iban") or "").replace(" ", "").upper()
        if a_full and ((full and a_full == full) or (not full and last4 and a_full[-4:] == last4)):
            acc = a["id"]
            break
    me = acc is not None or (bool(name) and _key(name) in own)
    return {"name": dekont._title(name) if name else None, "iban_last4": last4 or None, "bank": bank or None,
            "is_me": me, "account_id": acc}


def _own_info(f: dict, s: dict, r: dict, side: str | None, accounts: list[dict]) -> dict:
    """Kendi hesapları arası dekont: kaynak/hedef hesap önerisi (IBAN, yoksa banka adı: alıcı bankanın gelen
    dekontunda düzenleyen banka alıcıdır, gidende gönderen) ve aynı aktarımın kayıtlı bacağı (ikinci dekont)."""
    issuer = f.get("bank")
    from_acc = s["account_id"] or _account_by_bank(s["bank"] or (issuer if side == "giden" else None), accounts)
    to_acc = r["account_id"] or _account_by_bank(r["bank"] or (issuer if side == "gelen" else None), accounts)
    if from_acc and from_acc == to_acc:  # iki taraf aynı hesaba düştü: yalnız belgenin tarafı kalır
        if side == "giden":
            to_acc = None
        elif side == "gelen":
            from_acc = None
        else:
            from_acc = to_acc = None
    out = {"from_account": from_acc, "to_account": to_acc, "side": side, "match": None}
    if f.get("amount") and f.get("date"):
        if m := own_match(f["date"], f["amount"], f.get("ref") or "", from_acc, to_acc):
            out["match"] = m
            out["from_account"] = out["from_account"] or m["from_account"]
            out["to_account"] = out["to_account"] or m["to_account"]
    return out


def _clean_ref(ref: str | None) -> str:
    return re.sub(r"[^0-9A-Za-z]", "", ref or "")[:30]


def own_match(on: str, amount: float, ref: str, from_acc: int | None, to_acc: int | None) -> dict | None:
    """Aynı aktarımın kayıtlı bacağı: aynı sorgu numarası ya da (aynı tutar, ±1 gün) kaynak hesapta çıkış / hedef
    hesapta giriş. {id (aktarım; pair varsa pair_id), from_account, to_account, has_file {from, to}}."""
    ref = _clean_ref(ref)
    legs = []
    if hit := db.own_leg_by_ref(ref):
        legs = db.own_pair_legs(hit["id"])
    else:
        for acc, signed in ((from_acc, -amount), (to_acc, amount)):
            if (leg := db.find_own_leg(on, signed, acc, ref or None)) is not None:
                legs = db.own_pair_legs(leg["id"]) if leg.get("pair_id") else [leg]
                break
    if not legs:
        return None
    out_leg = next((x for x in legs if x["amount"] < 0), None)
    in_leg = next((x for x in legs if x["amount"] > 0), None)
    return {"id": (out_leg or in_leg)["pair_id"] or (out_leg or in_leg)["id"],
            "from_account": (out_leg or {}).get("account_id"), "to_account": (in_leg or {}).get("account_id"),
            "has_file": {"from": bool((out_leg or {}).get("receipt_file")), "to": bool((in_leg or {}).get("receipt_file"))},
            "message": "Bu aktarım kayıtlı: dekont ona bağlanır"}


def _account_by_bank(bank: str | None, accounts: list[dict]) -> int | None:
    """Tek banka hesabı o bankadaysa onu öner (ad ya da dökümün banka adı)."""
    if not bank:
        return None
    from app.bank_import import short_bank

    short = short_bank(bank)
    hits = [a["id"] for a in accounts if a.get("kind") == "banka" and short in (a.get("name"), a.get("alias"))]
    return hits[0] if len(hits) == 1 else None


def resolve(f: dict) -> dict:
    """Okunan alanlardan istemci sonucu (IBAN'lar maskeli). type "dekont".
    Gönderenle alıcının adı aynıysa (ya da iki IBAN da kullanıcının hesabıysa) kendi hesapları arası aktarım:
    own_transfer true, own {from_account, to_account, side, match} (yetenek own_transfer_manual; kayıt
    POST /api/own-transfers). Taraflardan kullanıcı anlaşılamadıysa yön, belgenin işlem türünden (GİDEN / GELEN)."""
    accounts = _bank_accounts()
    own = _own_keys()
    s = _side(f.get("sender_name") or "", f.get("sender_iban") or "", f.get("sender_bank") or "", accounts, own)
    r = _side(f.get("receiver_name") or "", f.get("receiver_iban") or "", f.get("receiver_bank") or "", accounts, own)
    side = f.get("doc_direction") if f.get("doc_direction") in ("giden", "gelen") else None
    sk, rk = _key(f.get("sender_name")), _key(f.get("receiver_name"))
    if len(sk) >= 6 and sk == rk:  # gönderen = alıcı: kendi hesapları arası (adı henüz bilinmese de)
        s["is_me"] = r["is_me"] = True
    if s["is_me"] and not r["is_me"]:
        direction, mine, other = "giden", s, r
    elif r["is_me"] and not s["is_me"]:
        direction, mine, other = "gelen", r, s
    elif not s["is_me"] and not r["is_me"] and side:  # kullanıcı tanınmadı: belgenin işlem türü ("Giden FAST")
        direction, mine, other = side, (s if side == "giden" else r), (r if side == "giden" else s)
    else:
        direction, mine, other = None, None, None
    own_info = None
    if s["is_me"] and r["is_me"]:
        own_info = _own_info(f, s, r, side, accounts)
    counterparty = known = None
    if other and other["name"]:
        counterparty = db.known_counterparty(other["name"])
        known = _is_known(counterparty)
    account_hint = None
    if mine:
        account_hint = mine["account_id"] or _account_by_bank(mine["bank"] or (f.get("bank") if direction == "giden" else None),
                                                               accounts)
    for p in (s, r):  # kişi önerisi her iki taraf için (yön seçilince istemci karşı tarafı alır)
        if p["name"] and not p["is_me"]:
            p["name"] = db.known_counterparty(p["name"])
        p.pop("account_id", None)
    out = {"type": "dekont", "direction": direction, "amount": f.get("amount"), "date": f.get("date"),
           "counterparty": counterparty, "counterparty_known": known, "iban_last4": (other or {}).get("iban_last4"),
           "bank": (mine or {}).get("bank") or f.get("bank") or None, "description": f.get("description") or "",
           "transfer_type": f.get("transfer_type"), "ref": f.get("ref") or "", "account_hint": account_hint,
           "own_transfer": bool(s["is_me"] and r["is_me"]), "parties": {"sender": s, "receiver": r},
           "doc_direction": side}
    if own_info is not None:
        out["own"] = own_info
    if own_info is None and f.get("amount") and f.get("date"):
        kind = {"giden": "gider", "gelen": "gelir"}.get(direction or "")
        if dup := find_duplicate(f["date"], f["amount"], f.get("ref") or "", counterparty, kind, account_hint):
            out["duplicate"] = {"entry_id": dup, "message": "Bu dekont zaten eklenmiş"}
        if direction and counterparty and (d := debt_match(counterparty, kind, f["amount"], f["date"])):
            out["debt_match"] = d
    return out


def _is_known(name: str) -> bool:
    with db.connect() as c:
        return c.execute("SELECT 1 FROM entries WHERE counterparty = ? UNION SELECT 1 FROM debts WHERE person = ? "
                         "UNION SELECT 1 FROM counterparties WHERE name = ? LIMIT 1", (name, name, name)).fetchone() is not None


# ---------------------------------------------------------------- çift kayıt, borç önerisi

def dekont_hash(on: str, amount: float, ref: str) -> str:
    return f"dekont:{on}:{amount:.2f}:{re.sub(r'[^0-9A-Za-z]', '', ref or '')[:30]}"


def find_duplicate(on: str, amount: float, ref: str, counterparty: str | None, kind: str | None,
                   account_id: int | None = None) -> int | None:
    """Aynı dekont zaten kayıtlı mı (kayıt id'si): (1) aynı sorgu/referans numaralı dekont kaydı; (2) aynı gün, tutar
    (ve yön) ile aynı kişi; (3) aynı gün, tutar, yön ve hesapta hesap dökümünden gelmiş satır (dekonttan önce döküm
    yüklendiyse). Başka referans numaralı dekont kaydı aynı gün aynı tutarda olsa da ayrı işlemdir."""
    ref = re.sub(r"[^0-9A-Za-z]", "", ref or "")[:30]
    cents = money.kurus(amount)
    with db.connect() as c:
        if ref:
            r = c.execute("SELECT id FROM entries WHERE import_hash = ? LIMIT 1", (dekont_hash(on, amount, ref),)).fetchone()
            if r:
                return r["id"]
        cands = db.rows(c.execute("SELECT id, kind, counterparty, account_id, import_hash FROM entries WHERE entry_date = ? "
                                  "AND ROUND(amount * 100) = ? ORDER BY id", (on, cents)))
    key = _key(counterparty)
    for e in cands:
        if kind and e["kind"] != kind:
            continue
        h = e["import_hash"] or ""
        if h.startswith("dekont:"):
            other_ref = h.split(":", 3)[3] if h.count(":") >= 3 else ""
            if ref and other_ref and other_ref != ref:
                continue  # başka dekont
            if not e["counterparty"] or not key or _key(e["counterparty"]) == key:
                return e["id"]
            continue
        if key and e["counterparty"] and _key(e["counterparty"]) == key:
            return e["id"]
        if kind and account_id and e["account_id"] == account_id and h and not h.startswith("mail:"):
            return e["id"]  # dökümden gelmiş aynı hareket
    return None


def debt_match(person: str, kind: str | None, amount: float, on: str) -> dict | None:
    """Gelen havale ↔ kişinin açık ALACAK kaydı, giden ↔ açık BORÇ kaydı; tutar kalanı aşmıyorsa öneri."""
    if kind not in ("gider", "gelir"):
        return None
    want = "borc" if kind == "gider" else "alacak"
    key = _key(person)
    for d in db.list_debts(open_only=True):
        if (d["asset"] == "TRY" and d["direction"] == want and _key(d["person"]) == key and not d["written_off_at"]
                and d["debt_date"] <= on and money.gt(d["remaining"], 0) and money.le(amount, d["remaining"])):
            return {"id": d["id"], "person": d["person"], "direction": d["direction"], "remaining": d["remaining"],
                    "amount": d["amount"], "full": money.eq(amount, d["remaining"])}
    return None


# ---------------------------------------------------------------- kayıt

class Duplicate(Exception):
    def __init__(self, entry_id: int):
        super().__init__("Bu dekont zaten eklenmiş")
        self.entry_id = entry_id


def save(b: dict) -> dict:
    """b: {date, amount, direction, counterparty, account_id, description, note, ref, transfer_type, bank, dekont_file,
    debt_id, debt_link, force, created_by}. Dönüş {id, debt_settled, debt_step, merged_hint}."""
    kind = "gider" if b["direction"] == "giden" else "gelir"
    who = db.known_counterparty(dekont._title(b["counterparty"].strip()))
    amount = money.money(b["amount"])
    ref = re.sub(r"[^0-9A-Za-z]", "", b.get("ref") or "")[:30]
    if not b.get("force") and (dup := find_duplicate(b["date"], amount, ref, who, kind, b.get("account_id"))):
        raise Duplicate(dup)
    cp = db.get_counterparty(who) or {}
    msg = (b.get("description") or "").strip()
    desc = " · ".join(x for x in (who, msg) if x) if msg else f"{who} · {'gönderildi' if kind == 'gider' else 'geldi'}"
    kind_txt = {"fast": "FAST", "eft": "EFT", "havale": "Havale"}.get(b.get("transfer_type") or "", "")
    raw = " · ".join(x for x in ("Dekont", kind_txt, b.get("bank") or "", f"Sorgu no {ref}" if ref else "") if x)
    user_note = (b.get("note") or "").strip()
    h = dekont_hash(b["date"], amount, ref)
    with db.connect() as c:  # referanssız iki ayrı (zorla eklenmiş) dekont aynı izi almasın
        n = c.execute("SELECT COUNT(*) FROM entries WHERE import_hash = ? OR import_hash LIKE ?", (h, h + "~%")).fetchone()[0]
    if n:
        h = f"{h}~{n + 1}"
    eid = db.create_entry({
        "entry_date": b["date"], "description": desc[:120], "amount": amount, "kind": kind,
        "category": cp.get("category") or "Kategorisiz", "payment": "havale",
        "note": f"Hesap hareketi · {raw}" + (f"\n{user_note}" if user_note else ""),
        "import_hash": h, "counterparty": who, "account_id": b.get("account_id"),
        "receipt_file": b.get("dekont_file"), "created_by": b.get("created_by")})
    debt = None
    if b.get("debt_id"):
        d = next((x for x in db.list_debts() if x["id"] == b["debt_id"]), None)
        want = "borc" if kind == "gider" else "alacak"
        if not d or d["direction"] != want or _key(d["person"]) != _key(who):
            db.delete_entry(eid)
            raise ValueError("Bu borç/alacak kaydı bu kişiye ve yöne ait değil")
        try:
            db.pay_debt(d["id"], amount, b["date"], entry_id=eid)
        except db.OverpayError:
            db.delete_entry(eid)
            raise
        debt = ("odeme", d)
    elif b.get("debt_link", True):
        debt = db.link_debt_movement(who, kind, amount, b["date"], eid)
    return {"id": eid, "counterparty": who, "debt_settled": debt[1]["id"] if debt else None,
            "debt_step": debt[0] if debt else None}


# ---------------------------------------------------------------- kendi hesapları arası (POST /api/own-transfers)

def save_own(b: dict) -> dict:
    """Hesaplar arası aktarım: b {date, amount, from_account_id, to_account_id, description, note, ref, dekont_file,
    dekont_side (giden|gelen: dosya hangi bacağın), created_by}. Dekonttan geliyorsa (ref ya da dosya) aynı aktarımın
    kayıtlı bacağı aranır: ikinci dekont yeni kayıt açmaz, o bacağa bağlanır; dökümden gelmiş tek bacağın eksik karşılığı
    eklenir. Aynı sorgu numaralı, daha önce gelir/gider sanılıp kaydedilmiş dekont kaydı (borca bağlı değilse) aktarıma
    dönüşür (çift sayım kalkar). Dönüş {id, linked, created, replaced}. Aynı dekont aynı bacakta zaten varsa Duplicate."""
    amount = money.money(abs(b["amount"]))
    ref = _clean_ref(b.get("ref"))
    from_id, to_id = b["from_account_id"], b["to_account_id"]
    side = b.get("dekont_side") if b.get("dekont_side") in ("giden", "gelen") else None
    dfile = b.get("dekont_file")
    from_dekont = bool(ref or dfile)
    replaced = _dekont_entries(b["date"], amount, ref) if ref else []
    files = {"giden": None, "gelen": None}
    for e in replaced:  # yanlış kayda bağlı dekont dosyası aktarımın bacağına geçer
        files["giden" if e["kind"] == "gider" else "gelen"] = files["giden" if e["kind"] == "gider" else "gelen"] or e["receipt_file"]
    if side and dfile:
        files[side] = dfile
    if from_dekont:
        legs = []
        if hit := db.own_leg_by_ref(ref):
            legs = db.own_pair_legs(hit["id"])
        else:
            for acc, signed in ((from_id, -amount), (to_id, amount)):
                if (leg := db.find_own_leg(b["date"], signed, acc, ref or None)) is not None:
                    legs = db.own_pair_legs(leg["id"]) if leg.get("pair_id") else [leg]
                    break
        if legs:
            out = _link_legs(legs, b, amount, ref, files, replaced)
            _drop_entries(replaced)
            return out
    pid = db.add_own_pair(b["date"], amount, from_id, to_id, b.get("description"), b.get("note"),
                          source="dekont" if from_dekont else "elle", ref=ref or None, from_file=files["giden"],
                          to_file=files["gelen"], created_by=b.get("created_by"))
    _drop_entries(replaced)
    return {"id": pid, "linked": False, "created": 2, "replaced": [e["id"] for e in replaced]}


def _link_legs(legs: list[dict], b: dict, amount: float, ref: str, files: dict, replaced: list[dict]) -> dict:
    out_leg = next((x for x in legs if x["amount"] < 0), None)
    in_leg = next((x for x in legs if x["amount"] > 0), None)
    side = b.get("dekont_side")
    target = out_leg if side == "giden" else in_leg if side == "gelen" else None
    if target and target.get("receipt_file") and not replaced and (
            target["receipt_file"] == b.get("dekont_file") or (ref and target.get("ref") == ref)):
        raise Duplicate(target["pair_id"] or target["id"])  # bu dekont bu bacakta zaten var
    created = 0
    with db.connect() as c:
        for leg, key in ((out_leg, "giden"), (in_leg, "gelen")):
            if leg is None:
                continue
            sets = {"receipt_file": files[key]} if files[key] and not leg.get("receipt_file") else {}
            if ref and not leg.get("ref"):
                sets["ref"] = ref
            if sets:
                c.execute(f"UPDATE own_transfers SET {', '.join(f'{k} = ?' for k in sets)} WHERE id = ?",
                          (*sets.values(), leg["id"]))
    if out_leg is None or in_leg is None:  # dökümden gelmiş tek bacak: karşı hesaba eksik bacak eklenir
        have = out_leg or in_leg
        acc_id = b["to_account_id"] if out_leg else b["from_account_id"]
        if acc_id and acc_id != have.get("account_id"):
            with db.connect() as c:
                acc = db._own_account(c, acc_id)
                pid = have["pair_id"] or have["id"]
                c.execute("UPDATE own_transfers SET pair_id = ? WHERE id = ?", (pid, have["id"]))
                c.execute("INSERT INTO own_transfers (transfer_date, amount, account, description, account_id, pair_id, "
                          "source, ref, receipt_file, note, created_at, created_by) "
                          "VALUES (?, ?, ?, ?, ?, ?, 'dekont', ?, ?, ?, CURRENT_TIMESTAMP, ?)",
                          (have["transfer_date"], amount if out_leg else -amount, acc["name"],
                           have.get("description"), acc["id"], pid, ref or None,
                           files["gelen" if out_leg else "giden"], b.get("note") or None, b.get("created_by")))
            created = 1
    first = out_leg or in_leg
    return {"id": first["pair_id"] or first["id"], "linked": True, "created": created, "replaced": [e["id"] for e in replaced]}


def _dekont_entries(on: str, amount: float, ref: str) -> list[dict]:
    """Aynı sorgu numaralı dekontun gelir/gider sanılmış kaydı (borç hareketine bağlı değilse)."""
    h = dekont_hash(on, amount, ref)
    with db.connect() as c:
        return db.rows(c.execute(
            "SELECT id, kind, receipt_file FROM entries WHERE (import_hash = ? OR import_hash LIKE ?) AND debt_id IS NULL "
            "AND id NOT IN (SELECT entry_id FROM debt_payments WHERE entry_id IS NOT NULL)", (h, h + "~%")))


def _drop_entries(entries: list[dict]) -> None:
    """Aktarıma dönüşen kayıtları sil (dosyaları aktarımın bacağına geçti; delete_entry onu silmez)."""
    for e in entries:
        db.delete_entry(e["id"])
