"""Fatura şablonları: aynı kurumun faturası yapay zekâya gitmeden, metinden yerelde okunur (maliyet sıfır).

Metin: PDF'in metin katmanı (e-fatura / e-arşiv), e-postayla gelen fatura metni ya da telefonda çıkarılmış OCR metni.
Fotoğrafın kendisine şablon uygulanmaz (metni yok).

Şablon KOD DEĞİL, küçük bir tanımdır ve belgedeki değerleri (abone no, ad, adres, tutar, tarih) TAŞIMAZ:

    {"v": 1, "sig": ["ornek", "enerji"],           # kurum imzası: metinde kelime olarak geçmesi gereken sözcükler
     "provider": "Örnek Enerji", "category": "Elektrik",
     "fields": {"amount": [{"label": "odenecek tutar", "off": 0, "nth": 0, "occ": 0}],
                "due_date": [{"label": "fatura tarihi son odeme tarihi", "off": 1, "nth": 1, "occ": 0}], ...}}

- Çapa (anchor): etiket (yalnız sabit LABEL_WORDS sözlüğündeki kelimeler, katlanmış: küçük harf, Türkçe harfsiz) +
  değerin yeri. off=0: değer etiketin hemen sağında, aynı satırda. off=1..2: etiket kendi satırında, değer o kadar alttaki
  satırın nth'inci değeri (sütunlu düzen: "Fatura Tarihi  Son Ödeme Tarihi" / "01.10.2026  20.10.2026"). occ: etiket
  belgede birden çok geçiyorsa kaçıncısı.
- Öğrenme: yapay zekâ bir faturayı okuyunca (ödenmiş dekont değilse, güven "düşük" değilse) bulduğu değerler metinde
  aranır, her değerin önündeki etiket çapa olur. Çapalar aynı metne uygulanıp yapay zekânın değerlerini AYNEN veremiyorsa
  o alan atılır; tutar ve son ödeme tarihi tutmazsa şablon kaydedilmez.
- Okuma: imzası tutan şablon uygulanır; sonuç tutarlı değilse (tutar/tarih yok, tarih makul aralıkta değil) yapay
  zekâya düşülür ve şablonun hata sayısı artar; art arda iki hata → devre dışı. Yapay zekâ okuması farklı bir kural
  öğretirse şablon yenilenir (hata sıfırlanır). Başarılı okuma hata sayısını sıfırlar.
- Ödeme dekontu / tahsilat makbuzu (PAID_WORDS) şablonla okunmaz: her zaman yapay zekâ (ödendi bilgisi ve tarihi).
- Yerleşik şablonlar (BUILTINS): büyük kurumlar için yalnız kamuya açık, yaygın etiketlerle (aynı satır); kurumdan
  öğrenilen şablon yerleşiğin yerine geçer.

Saklama: defter başına (kullanıcının kendi defteri, bill_templates tablosu). Topluluğa (hub) GÖNDERİLMEZ: imza ve kurum
adı belgeden gelir (aidatta kişi adı olabilir). Okuma hakkı (app/doc_reads.py) çağıranlarda aynen düşer; şablon yolunda
yapay zekâ maliyet kaydı (ai_calls) yazılmaz.
"""
import json
import logging
import re
from datetime import date, timedelta

log = logging.getLogger("hesapkitap.bill_templates")

SCHEMA = """
CREATE TABLE IF NOT EXISTS bill_templates (
    id INTEGER PRIMARY KEY,
    sig TEXT NOT NULL UNIQUE,               -- imza sözcükleri (boşlukla); yerleşik şablonun durumu: 'builtin:<imza>'
    provider TEXT,
    spec TEXT,                              -- JSON (yukarıdaki biçim); yerleşikte NULL (tanım kodda)
    source TEXT NOT NULL DEFAULT 'learned', -- learned | builtin
    uses INTEGER NOT NULL DEFAULT 0,
    fails INTEGER NOT NULL DEFAULT 0,       -- art arda tutarsız okuma
    disabled INTEGER NOT NULL DEFAULT 0,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT DEFAULT CURRENT_TIMESTAMP
);
"""
MAX_FAILS = 2

# etiketlerde izin verilen kelimeler (katlanmış). Şablona bunların dışında belgeden kelime girmez.
LABEL_WORDS = frozenset("""
odenecek odenmesi gereken toplam toplami genel tutar tutari fatura faturanin faturasi son odeme odemesi tarihi
tarih tarihli gunu gun vade vadesi donem donemi donemine ait abone abonelik abonenin aboneli tesisat tesisatin sozlesme
hesap hesabi musteri hizmet hizmeti no nosu numarasi numara num kdv dahil dahildir tl try borc borcu borcunuz
duzenleme duzenlenme duzenlendigi kesim kesildigi ilk okuma tuketim tahakkuk guncel yuvarlamali net odenecektir
""".split())
FIELD_WORDS = {  # alanın etiketinde en az biri olmalı
    "amount": {"tutar", "tutari", "toplam", "toplami", "odenecek", "borc", "borcu", "borcunuz"},
    "due_date": {"son", "vade", "vadesi"},
    "issue_date": {"fatura", "duzenleme", "duzenlenme", "duzenlendigi", "kesim", "kesildigi"},
    "period": {"donem", "donemi", "donemine"},
    "subscriber": {"abone", "abonelik", "abonenin", "tesisat", "tesisatin", "sozlesme", "musteri", "hizmet"},
}
PREFER = {"amount": {"odenecek": 3, "toplam": 1, "tutar": 1, "tutari": 1}, "due_date": {"son": 3},
          "issue_date": {"fatura": 2, "duzenleme": 2}, "subscriber": {"abone": 2, "tesisat": 2, "sozlesme": 1}}
# ödeme dekontu / makbuz işaretleri: şablonla okunmaz (yapay zekâ ödendi bilgisini ve tarihini çıkarır)
PAID_WORDS = ("dekont", "odendi", "odenmistir", "tahsil edildi", "tahsil edilmistir", "islem basarili",
              "islem basariyla", "makbuz", "odeme alindi")
CATEGORIES = {"Elektrik", "Doğalgaz", "Su", "İnternet & TV", "Telefon", "Kira & Aidat", "Faturalar"}
_LEGAL = {"a", "s", "as", "aş", "ltd", "sti", "san", "tic", "ve", "anonim", "sirketi", "sirket", "limited", "inc", "co",
          "dis", "tas", "gen"}
_GENERIC = {"elektrik", "dogalgaz", "gaz", "su", "enerji", "internet", "telefon", "fatura", "aidat", "dagitim",
            "perakende", "satis", "hizmetleri", "iletisim", "idaresi", "genel", "mudurlugu", "turkiye", "yonetimi"}
MONTHS = ("ocak", "subat", "mart", "nisan", "mayis", "haziran", "temmuz", "agustos", "eylul", "ekim", "kasim", "aralik")
MONTHS_TR = ("Ocak", "Şubat", "Mart", "Nisan", "Mayıs", "Haziran", "Temmuz", "Ağustos", "Eylül", "Ekim", "Kasım",
             "Aralık")

PATTERNS = {
    "money": re.compile(r"(?<![\d.,])(\d{1,3}(?:\.\d{3})+,\d{2}|\d+,\d{2})(?![\d,])"),
    "date": re.compile(r"(?<![\d.])(\d{1,2})([./-])(\d{1,2})\2(\d{4})(?![\d.]\d)|(?<!\d)(\d{4})-(\d{2})-(\d{2})(?!\d)"),
    "period": re.compile(r"(?<![a-z])(" + "|".join(MONTHS) + r")\s*[-/.]?\s*(\d{4})(?!\d)|(?<![\d.])(\d{1,2})[./](\d{4})(?![\d.])"),
    "digits": re.compile(r"(?<![\d])(\d{5,20})(?![\d])"),
}
FIELD_PATTERN = {"amount": "money", "due_date": "date", "issue_date": "date", "period": "period",
                 "subscriber": "digits"}

_GEN_LABELS = {  # yerleşik şablonların etiketleri: yalnız yaygın, kamuya açık adlar; değer aynı satırda
    "amount": ["odenecek toplam tutar", "toplam odenecek tutar", "odenecek tutar", "odenecek toplam"],
    "due_date": ["son odeme tarihi"],
    "issue_date": ["fatura tarihi", "duzenleme tarihi"],
    "period": ["fatura donemi"],
    "subscriber": ["abone no", "tesisat no", "sozlesme hesap no", "hizmet no", "musteri no", "abone numarasi",
                   "tesisat numarasi"],
}
_GEN_FIELDS = {f: [{"label": lb, "off": 0, "nth": 0, "occ": 0} for lb in labels] for f, labels in _GEN_LABELS.items()}
# (imza sözcükleri, kurum, kategori, olmaması gereken sözcükler)
BUILTINS = [
    (["enerjisa"], "Enerjisa", "Elektrik", []),
    (["ck", "bogazici"], "CK Boğaziçi Elektrik", "Elektrik", []),
    (["igdas"], "İGDAŞ", "Doğalgaz", []),
    (["baskentgaz"], "Başkentgaz", "Doğalgaz", []),
    (["iski"], "İSKİ", "Su", []),
    (["aski"], "ASKİ", "Su", []),
    (["turk", "telekom"], "Türk Telekom", "İnternet & TV", []),
    (["superonline"], "Turkcell Superonline", "İnternet & TV", []),
    (["vodafone"], "Vodafone", "Telefon", []),
    (["turkcell"], "Turkcell", "Telefon", ["superonline"]),
]


def _today() -> date:
    return date.today()


def _init(c) -> None:
    c.executescript(SCHEMA)


_FOLD = str.maketrans("şığüöçâîûŞĞÜÖÇÂÎÛ", "siguocaiuSGUOCAIU")


def fold(s: str) -> str:
    """Küçük harf, Türkçe harfsiz: OCR'ın düşürdüğü harfler (Ş→S, İ→I) de aynı yazılsın. Uzunluk korunur."""
    return s.replace("İ", "i").replace("I", "ı").translate(_FOLD).lower()


def _lines(text: str) -> list[str]:
    return [fold(ln.strip()) for ln in (text or "").splitlines() if ln.strip()]


def _words(s: str) -> list[str]:
    return re.findall(r"[a-z]+", s)


# ---------------------------------------------------------------- değerler

def _value(kind: str, m: re.Match):
    """Desen eşleşmesi → karşılaştırılabilir değer (tutar float, tarih/dönem ISO), geçersizse None."""
    try:
        if kind == "money":
            return round(float(m.group(1).replace(".", "").replace(",", ".")), 2)
        if kind == "date":
            if m.group(1):
                return date(int(m.group(4)), int(m.group(3)), int(m.group(1))).isoformat()
            return date(int(m.group(5)), int(m.group(6)), int(m.group(7))).isoformat()
        if kind == "period":
            if m.group(1):
                return f"{m.group(2)}-{MONTHS.index(m.group(1)) + 1:02d}"
            mo = int(m.group(3))
            return f"{m.group(4)}-{mo:02d}" if 1 <= mo <= 12 else None
        return m.group(1)
    except (ValueError, TypeError):
        return None


def _label_re(label: str) -> re.Pattern:
    return re.compile(r"(?<![a-z])" + r"[^a-z0-9]{1,3}".join(map(re.escape, label.split())) + r"(?![a-z])")


def _hits(lines: list[str], anchor: dict, kind: str) -> list:
    """Çapanın belgedeki her geçişinin verdiği değer (sırayla; değer çıkmayan geçiş atlanır)."""
    pat, out = PATTERNS[kind], []
    if anchor["off"] == 0:
        lre = _label_re(anchor["label"])
        for ln in lines:
            for lm in lre.finditer(ln):
                rest = ln[lm.end():]
                m = pat.search(rest)
                # değer etiketten sonraki İLK sayı olmalı (arada başka sayı varsa başka alanın değeri)
                if m and not re.search(r"\d", rest[:m.start()]) and (v := _value(kind, m)) is not None:
                    out.append(v)
                    break
    else:
        want = anchor["label"].split()
        for i, ln in enumerate(lines):
            if _words(ln) != want or re.search(r"\d", ln) or i + anchor["off"] >= len(lines):
                continue
            ms = list(pat.finditer(lines[i + anchor["off"]]))
            if len(ms) > anchor["nth"] and (v := _value(kind, ms[anchor["nth"]])) is not None:
                out.append(v)
    return out


def _extract(lines: list[str], anchors: list[dict], kind: str):
    for a in anchors:
        hits = _hits(lines, a, kind)
        if len(hits) > a.get("occ", 0):
            return hits[a.get("occ", 0)]
    return None


def apply(text: str, spec: dict) -> dict:
    """Şablonla oku: {amount, due_date, issue_date, period, subscriber} (bulunamayan None)."""
    lines = _lines(text)
    return {f: _extract(lines, anchors, FIELD_PATTERN[f]) for f, anchors in spec.get("fields", {}).items()}


def consistent(v: dict) -> bool:
    """Şablon sonucu kullanılabilir mi: tutar ve son ödeme tarihi var, makul aralıkta."""
    amt, due = v.get("amount"), v.get("due_date")
    if not amt or not (0 < amt < 1_000_000) or not due:
        return False
    d, today = date.fromisoformat(due), _today()
    if not (today - timedelta(days=400) <= d <= today + timedelta(days=120)):
        return False
    if v.get("issue_date"):
        i = date.fromisoformat(v["issue_date"])
        if i > d or (d - i).days > 90:
            return False
    return True


def is_paid_doc(text: str) -> bool:
    low = " ".join(_lines(text))
    return any(re.search(r"(?<![a-z])" + w.replace(" ", r"\s+"), low) for w in PAID_WORDS)


# ---------------------------------------------------------------- imza

def signature(provider: str, text: str) -> list[str] | None:
    """Kurum adının metinde kelime olarak geçen sözcükleri (hukuki ekler hariç); yalnız genel sözcükse None."""
    words = " " + " ".join(_words(" ".join(_lines(text)))) + " "
    toks = [t for t in _words(fold(provider or "")) if t not in _LEGAL and len(t) >= 2]
    found = [t for t in dict.fromkeys(toks) if f" {t} " in words][:5]
    if not any(len(t) >= 4 and t not in _GENERIC for t in found):
        return None
    return found


def matches(text: str, sig: list[str], exclude: list[str] | None = None) -> bool:
    words = set(_words(" ".join(_lines(text))))
    return all(t in words for t in sig) and not any(t in words for t in exclude or [])


# ---------------------------------------------------------------- doğrulama

def is_safe(spec: dict) -> bool:
    """Şablon yalnız izinli alanlar, sözlük kelimelerinden etiketler ve küçük indeksler içerir; belgedeki değerleri
    (tutar, tarih, abone no, ad, adres) taşıyamaz."""
    try:
        if set(spec) - {"v", "sig", "provider", "category", "fields"} or spec.get("v") != 1:
            return False
        if not (1 <= len(spec["sig"]) <= 5 and all(re.fullmatch(r"[a-z]{2,30}", t) for t in spec["sig"])):
            return False
        if not isinstance(spec["provider"], str) or len(spec["provider"]) > 60 or re.search(r"\d", spec["provider"]):
            return False
        if spec["category"] not in CATEGORIES or not {"amount", "due_date"} <= set(spec["fields"]):
            return False
        for f, anchors in spec["fields"].items():
            if f not in FIELD_PATTERN or not 1 <= len(anchors) <= 8:
                return False
            for a in anchors:
                ws = a["label"].split()
                if set(a) != {"label", "off", "nth", "occ"} or not 1 <= len(ws) <= 10 \
                        or not all(w in LABEL_WORDS for w in ws) or a["label"] != " ".join(ws):
                    return False
                if not all(isinstance(a[k], int) and 0 <= a[k] <= lim for k, lim in (("off", 2), ("nth", 5), ("occ", 5))):
                    return False
        return True
    except (KeyError, TypeError, AttributeError):
        return False


# ---------------------------------------------------------------- öğrenme

def _candidates(lines: list[str], field: str, target) -> list[dict]:
    kind = FIELD_PATTERN[field]
    need = FIELD_WORDS[field]
    out = []
    for i, ln in enumerate(lines):
        for m in PATTERNS[kind].finditer(ln):
            if _value(kind, m) != target:
                continue
            prefix = ln[:m.start()]
            last = [d.end() for d in re.finditer(r"\d", prefix)]
            ws = _words(prefix[last[-1]:] if last else prefix)
            label = []
            for w in reversed(ws):  # değere en yakın, sözlükteki kelimeler (önündeki ad/adres girmez)
                if w not in LABEL_WORDS or len(label) == 6:
                    break
                label.insert(0, w)
            if label and need & set(label) and not (field == "issue_date" and {"son", "vade"} & set(label)):
                out.append({"label": " ".join(label), "off": 0, "nth": 0, "occ": 0})
                continue
            if label:
                continue
            nth = [x.start() for x in PATTERNS[kind].finditer(ln)].index(m.start())
            for off in (1, 2):
                if i - off < 0:
                    break
                prev = lines[i - off]
                pw = _words(prev)
                if pw and not re.search(r"\d", prev) and len(pw) <= 10 and all(w in LABEL_WORDS for w in pw) \
                        and need & set(pw):
                    out.append({"label": " ".join(pw), "off": off, "nth": nth, "occ": 0})
                    break
    return out


def _score(field: str, a: dict, hits: list) -> int:
    ws = set(a["label"].split())
    return (sum(p for w, p in PREFER.get(field, {}).items() if w in ws) + (2 if len(hits) == 1 else 0)
            + (1 if a["off"] == 0 else 0))


def _anchor(lines: list[str], field: str, target) -> dict | None:
    best = None
    for a in _candidates(lines, field, target):
        hits = _hits(lines, a, FIELD_PATTERN[field])
        if target not in hits:
            continue
        a["occ"] = hits.index(target)
        s = _score(field, a, hits)
        if best is None or s > best[0]:
            best = (s, a)
    return best[1] if best else None


def fields_of(raw: dict) -> dict:
    """Yapay zekânın ham yanıtı (fatura istemi ya da belge istemi) → karşılaştırılabilir değerler."""
    from app.receipt import BILL_CATEGORY, _iso, _num

    period = None
    if raw.get("period"):
        m = PATTERNS["period"].search(fold(str(raw["period"])))
        period = _value("period", m) if m else None
    sub = re.sub(r"[\s-]", "", str(raw.get("subscriber_no") or ""))
    amount = raw.get("amount") if raw.get("amount") is not None else raw.get("total")
    return {
        "provider": (raw.get("provider") or raw.get("merchant") or "").strip()[:60],
        "category": BILL_CATEGORY.get(raw.get("type") or raw.get("bill_type"), "Faturalar"),
        "amount": _num(amount),
        "due_date": _iso(raw.get("due_date")),
        "issue_date": _iso(raw.get("issue_date") or raw.get("date")),
        "period": period,
        "subscriber": sub if re.fullmatch(r"\d{5,20}", sub) else None,
        "paid": raw.get("paid") is True or str(raw.get("paid")).strip().lower() == "true",
        "confidence": raw.get("confidence") or "orta",
    }


def build(text: str, raw: dict) -> dict | None:
    """Yapay zekâ sonucundan şablon (kaydetmeden); öğrenilemezse None."""
    v = fields_of(raw)
    if v["paid"] or v["confidence"] == "düşük" or not v["amount"] or not v["due_date"] or is_paid_doc(text):
        return None
    sig = signature(v["provider"], text)
    if not sig:
        return None
    lines = _lines(text)
    fields = {}
    for f in FIELD_PATTERN:
        if v.get(f) is None:
            continue
        a = _anchor(lines, f, v[f])
        if a:
            fields[f] = [a]
    spec = {"v": 1, "sig": sig, "provider": v["provider"], "category": v["category"], "fields": fields}
    if not is_safe(spec):
        return None
    got = apply(text, spec)  # aynı metinde yapay zekânın değerlerini aynen vermeli
    if any(got.get(f) != v[f] for f in fields) or not consistent(got):
        return None
    return spec


def learn(text: str | None, raw: dict) -> bool:
    """Yapay zekâ okumasından şablon öğren ya da yenile. Hata okumayı bozmaz. Kaydedildi/yenilendiyse True."""
    if not text:
        return False
    try:
        spec = build(text, raw)
        if not spec:
            return False
        from app import db

        key = " ".join(spec["sig"])
        js = json.dumps(spec, ensure_ascii=False, sort_keys=True)
        with db.connect() as c:
            _init(c)
            row = c.execute("SELECT spec FROM bill_templates WHERE sig = ?", (key,)).fetchone()
            if row and row["spec"] == js:
                return False  # aynı kural: durumu (hata sayısı, devre dışı) korunur
            c.execute("INSERT INTO bill_templates (sig, provider, spec) VALUES (?, ?, ?) ON CONFLICT(sig) DO UPDATE "
                      "SET spec = excluded.spec, provider = excluded.provider, source = 'learned', fails = 0, "
                      "disabled = 0, updated_at = CURRENT_TIMESTAMP", (key, spec["provider"], js))
        return True
    except Exception:  # noqa: BLE001
        log.warning("fatura şablonu öğrenilemedi", exc_info=True)
        return False


# ---------------------------------------------------------------- okuma

def _templates(c) -> list[dict]:
    """Uygulanacak şablonlar: öğrenilenler, sonra (aynı imzalı öğrenilen yoksa) yerleşikler. Devre dışılar hariç."""
    rows = {r["sig"]: dict(r) for r in c.execute("SELECT * FROM bill_templates")}
    out = [{"key": k, "spec": json.loads(r["spec"]), "uses": r["uses"], "exclude": []}
           for k, r in rows.items() if r["source"] == "learned" and r["spec"] and not r["disabled"]]
    for sig, provider, cat, excl in BUILTINS:
        k = " ".join(sig)
        if k in rows or rows.get("builtin:" + k, {}).get("disabled"):
            continue
        out.append({"key": "builtin:" + k, "builtin": True, "uses": 0, "exclude": excl,
                    "spec": {"v": 1, "sig": sig, "provider": provider, "category": cat, "fields": _GEN_FIELDS}})
    return sorted(out, key=lambda t: (-len(t["spec"]["sig"]), bool(t.get("builtin")), -t["uses"]))


def _mark(c, t: dict, ok: bool) -> None:
    if t.get("builtin"):
        c.execute("INSERT OR IGNORE INTO bill_templates (sig, provider, source) VALUES (?, ?, 'builtin')",
                  (t["key"], t["spec"]["provider"]))
    if ok:
        c.execute("UPDATE bill_templates SET uses = uses + 1, fails = 0 WHERE sig = ?", (t["key"],))
    else:
        c.execute("UPDATE bill_templates SET fails = fails + 1, disabled = (fails + 1 >= ?), "
                  "updated_at = CURRENT_TIMESTAMP WHERE sig = ?", (MAX_FAILS, t["key"]))


def read(text: str | None) -> dict | None:
    """İmzası tutan şablonla oku; tutarlı sonuç yoksa None (çağıran yapay zekâya gider). Hata okumayı bozmaz."""
    if not text or len(text) > 200_000:
        return None
    try:
        if is_paid_doc(text):
            return None
        from app import db

        with db.connect() as c:
            _init(c)
            for t in _templates(c):
                if not matches(text, t["spec"]["sig"], t["exclude"]):
                    continue
                v = apply(text, t["spec"])
                ok = consistent(v)
                _mark(c, t, ok)
                if ok:
                    return {**v, "provider": t["spec"]["provider"], "category": t["spec"]["category"]}
                return None  # en uygun şablon tutmadı: yapay zekâ
    except Exception:  # noqa: BLE001
        log.warning("fatura şablonu uygulanamadı", exc_info=True)
    return None


def text_of(data: bytes | None, mime: str) -> str | None:
    """Şablonun uygulanacağı metin: PDF metin katmanı ya da düz metin (e-posta). Fotoğrafta None."""
    if not data:
        return None
    if mime == "text/plain":
        return data.decode("utf-8", "ignore")
    if mime == "application/pdf":
        try:
            from app import parsers

            return parsers.extract_text(data) or None
        except Exception:  # noqa: BLE001 - şifreli/bozuk PDF: yapay zekâ kendisi dener
            return None
    return None


def _description(v: dict) -> str:
    period = None
    if v.get("period"):
        y, m = v["period"].split("-")
        period = f"{MONTHS_TR[int(m) - 1]} {y}"
    return " · ".join(x for x in (period, v.get("subscriber") and f"Abone {v['subscriber']}") if x)


def as_bill(v: dict) -> dict:
    """receipt.parse_bill biçiminde sonuç (şablon yolu)."""
    return {"provider": v["provider"], "category": v["category"], "amount": v["amount"], "due_date": v["due_date"],
            "issue_date": v.get("issue_date"), "description": _description(v), "paid": False, "paid_at": None,
            "payment": None, "confidence": "yüksek", "source": "sablon"}


def as_doc(v: dict) -> dict:
    """receipt.parse_document biçiminde fatura sonucu (şablon yolu)."""
    out = as_bill(v)
    out.pop("issue_date")
    return {"type": "bill", "legal_name": None, **out}
