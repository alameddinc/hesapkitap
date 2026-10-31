"""Banka hesap dökümü şablonları: yapay zekâsız okuma kuralı.

Şablon KOD DEĞİL, küçük bir tanımdır ve belgeden metin taşımaz (kişisel veri içermez):

    {"v": 1, "kind": "account", "bank": "Halkbank",
     "keywords": ["hesap hareket"],              # sabit sözlükten, belgede geçenler
     "date": {"sep": "-", "order": "dmy"},       # satır bu biçimde tarihle başlar
     "amount_idx": 0, "balance_idx": 1}          # satır bloğundaki para değerlerinden hangisi tutar / bakiye

Okuma: tarihle başlayan her satır bir hareket; sonraki satırlar (yeni tarih satırına kadar) açıklamanın devamı.
Belgede çok tekrar eden satırlar (sayfa başlıkları) atlanır. Sonuç her zaman BAKİYE ZİNCİRİYLE doğrulanır; tutmazsa
şablon kullanılmaz. Öğrenme: olası birkaç kombinasyon denenir, zinciri tutan seçilir — yapay zekâ gerekmez.
"""
import json
import re
from collections import Counter

from app import system

MONEY = re.compile(r"(?<![\d.,])(-?\s?\d{1,3}(?:\.\d{3})*,\d{2})(?![\d,])")
KEYWORDS = ("hesap hareket", "hesap ekstre", "hesap özeti", "hesap dökümü", "işlem dökümü", "vadesiz", "bakiye",
            "açıklama", "dekont", "valör", "işlem tarihi", "kullanılabilir bakiye")
_DATE_RES = {
    ("dmy", "."): r"(\d{2})\.(\d{2})\.(\d{4})", ("dmy", "-"): r"(\d{2})-(\d{2})-(\d{4})",
    ("dmy", "/"): r"(\d{2})/(\d{2})/(\d{4})", ("ymd", "-"): r"(\d{4})-(\d{2})-(\d{2})",
}

SCHEMA = """
CREATE TABLE IF NOT EXISTS bank_templates (
    id INTEGER PRIMARY KEY,
    bank TEXT NOT NULL,
    spec TEXT NOT NULL,                     -- JSON (yukarıdaki biçim)
    source TEXT NOT NULL DEFAULT 'learned', -- learned (bu sunucuda öğrenildi) | community (depodan)
    uses INTEGER NOT NULL DEFAULT 0,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    shared_at TEXT,                         -- topluluğa gönderildi
    UNIQUE (bank, spec)
);
"""


def _init(c) -> None:
    c.executescript(SCHEMA)


def _low(s: str) -> str:
    return s.replace("İ", "i").replace("I", "ı").lower()


def _money(v: str) -> float:
    return float(v.replace(" ", "").replace(".", "").replace(",", "."))


def _date_re(spec: dict) -> re.Pattern:
    return re.compile(r"^\s*" + _DATE_RES[(spec["date"]["order"], spec["date"]["sep"])] + r"\b")


_PAGE_NO = re.compile(r"^(sayfa|page)\s*(no)?\s*[:.]?\s*\d+\s*(/|of|-)\s*\d+$", re.I)
_EDGE = 6  # sayfanın ilk/son bu kadar satırı başlık/altlık bölgesi


def _shape(ln: str) -> str:
    """Sayfadan sayfaya yalnız rakamı değişen satırlar ('Sayfa No:4 / 5') aynı sayılsın."""
    return re.sub(r"\d+", "#", ln)


def _pages(text: str) -> list[list[str]] | None:
    """Metnin sayfaları (satır listeleri): PDF'ten okunduysa gerçek sayfa sınırları, değilse 'Sayfa 2 / 5' satırları."""
    from app.parsers import pages_of

    pages = pages_of(text)
    if pages:
        return [[ln.strip() for ln in p.splitlines() if ln.strip()] for p in pages]
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    marks = [i for i, ln in enumerate(lines) if _PAGE_NO.match(ln)]
    if len(marks) < 2:
        return None
    out, start = [], 0
    for i in marks:
        out.append(lines[start:i + 1])
        start = i + 1
    if start < len(lines):
        out.append(lines[start:])
    return out


def noise_lines(text: str, dre: re.Pattern) -> set[str]:
    """Sayfa başlığı/altlığı satırları (biçimleri; _shape). Sayfa sınırları biliniyorsa yalnız sayfa kenarında tekrar
    edenler — açıklamanın sık tekrarlanan devam satırları ('…,Ayşe Yılmaz,Tür' / 'kiye Vakiflar Bankasi…') sayfa
    ortasında da geçtiği için korunur. Sınır bilinmiyorsa eski kural: üç ve daha çok tekrar eden satır."""
    pages = _pages(text)
    if not pages or len(pages) < 2:
        counts = Counter(ln.strip() for ln in text.splitlines() if ln.strip())
        return {_shape(ln) for ln, n in counts.items() if n >= 3 and not dre.match(ln)}
    total: Counter = Counter()
    edge: Counter = Counter()
    edge_pages: Counter = Counter()
    for p in pages:
        zone = set(range(min(_EDGE, len(p)))) | set(range(max(0, len(p) - _EDGE), len(p)))
        seen: set[str] = set()
        for i, ln in enumerate(p):
            if dre.match(ln):
                continue
            s = _shape(ln)
            total[s] += 1
            if i in zone:
                edge[s] += 1
                seen.add(s)
        edge_pages.update(seen)
    need = max(2, (len(pages) + 1) // 2)
    out = {s for s, n in edge_pages.items() if n >= need and edge[s] >= 0.7 * total[s]}
    return out | {s for s in total if _PAGE_NO.match(s.replace("#", "1"))}


_REF_TAIL = re.compile(r"\s+[A-Z]?\d{6,}[-/]\d+(?:[-/]\d+)*$")  # satır sonundaki dekont/işlem no (M20990101-120000-000001/01)


_SMALL_WORDS = {"ve", "ile", "veya", "ya", "de", "da", "ki", "için"}


def _join(parts: list[str]) -> str:
    """Açıklama satırlarını birleştir. Bazı bankalar (Halkbank) sütunu sabit genişlikte keser: 'Tür' + 'kiye',
    'TARAFI' + 'NDAN'. Harfle biten satırın devamı küçük harfle başlıyorsa kelime bölünmüştür: boşluksuz birleşir."""
    out = ""
    for p in parts:
        p = _REF_TAIL.sub("", MONEY.sub(" ", p)).strip()
        if not p:
            continue
        if out and ((out[-1].isalpha() and p[0].isalpha() and p[0].islower() and p.split()[0] not in _SMALL_WORDS)
                    or (p[0].isdigit() and re.search(r"TR\d+$", out))):  # IBAN da bölünür: 'TR12000100' + '0001…'
            out += p
        else:
            out = f"{out} {p}" if out else p
    out = re.sub(r"\bTARAF(I\s+NDAN|IN\s+DAN|INDA\s+N)\b", "TARAFINDAN", out)
    return re.sub(r"\s+", " ", out).strip()


def apply(text: str, spec: dict) -> dict | None:
    """Şablonla oku → bank_pdf ile aynı sonuç sözlüğü (rows + zincir kontrolü) ya da None."""
    from app.bank_pdf import verify_chain

    dre = _date_re(spec)
    lines = [ln.strip() for ln in text.splitlines()]
    noise = noise_lines(text, dre)  # sayfa başlığı/altlığı
    blocks: list[tuple[str, list[str]]] = []
    for ln in lines:
        if not ln or (_shape(ln) in noise and not dre.match(ln)):
            continue
        m = dre.match(ln)
        if m:
            g = m.groups()
            d = f"{g[2]}-{g[1]}-{g[0]}" if spec["date"]["order"] == "dmy" else f"{g[0]}-{g[1]}-{g[2]}"
            blocks.append((d, [ln[m.end():].strip()]))
        elif blocks:
            blocks[-1][1].append(ln)
    rows = []
    ai, bi = spec["amount_idx"], spec["balance_idx"]
    for d, parts in blocks:
        if spec.get("scope") == "line":  # tutarlar tarih satırında (yetmezse hemen alt satırda); kalanı açıklama
            vals = [m.group(1) for m in MONEY.finditer(parts[0])]
            if len(vals) < 2 and len(parts) > 1:
                vals += [m.group(1) for m in MONEY.finditer(parts[1])]
        else:
            vals = [m.group(1) for p in parts for m in MONEY.finditer(p)]
        try:
            amt, bal = _money(vals[ai]), _money(vals[bi])
        except (ValueError, IndexError):
            continue
        rows.append({"date": d, "amount": amt, "balance": bal, "description": _join(parts)[:500]})
    if len(rows) < 2:
        return None

    def hits(rs):
        return sum(abs(round(b["balance"] - a["balance"], 2) - b["amount"]) <= 0.01 or
                   abs(abs(round(b["balance"] - a["balance"], 2)) - abs(b["amount"])) <= 0.01 for a, b in zip(rs, rs[1:]))
    if hits(rows[::-1]) > hits(rows):
        rows.reverse()
    opening = round(rows[0]["balance"] - rows[0]["amount"], 2)
    check = verify_chain(rows, opening, rows[-1]["balance"])
    return {"owner_name": "", "bank": spec.get("bank", ""), "rows": rows, "template": True, **check}


# IBAN'daki 5 haneli banka kodu (TRkk BBBBB …) → banka; başlıktaki ilk IBAN hesabın kendisidir
IBAN_BANKS = {"00010": "Ziraat", "00012": "Halkbank", "00015": "VakıfBank", "00032": "TEB", "00046": "Akbank",
              "00059": "Şekerbank", "00062": "Garanti", "00064": "İş Bankası", "00067": "Yapı Kredi",
              "00099": "ING", "00103": "Fibabanka", "00111": "QNB", "00123": "HSBC", "00134": "DenizBank",
              "00135": "Anadolubank", "00143": "Aktif Bank", "00146": "Odeabank", "00203": "Albaraka",
              "00205": "Kuveyt Türk", "00206": "Türkiye Finans", "00209": "Ziraat Katılım", "00210": "Vakıf Katılım",
              "00211": "Emlak Katılım"}
_IBAN = re.compile(r"TR\s?\d{2}\s?(\d{4})\s?(\d)")


def bank_of(text: str) -> str:
    """Hesabın bankası: ilk IBAN'ın banka kodu; yoksa başlıktaki banka adı; bilinmiyorsa boş."""
    from app.bank_import import short_bank

    m = _IBAN.search(text[:6000].upper())
    if m and (code := m.group(1) + m.group(2)) in IBAN_BANKS:
        return IBAN_BANKS[code]
    b = short_bank(text[:600])
    return "" if b == "Banka hesabı" else b


def _keywords(text: str) -> list[str]:
    head = _low(text[:4000])
    return [k for k in KEYWORDS if k in head][:4]


def learn(text: str, bank: str) -> tuple[dict, dict] | None:
    """Zinciri tutan kuralı ara (yapay zekâsız). (spec, sonuç) ya da None."""
    best = None
    for (order, sep), pat in _DATE_RES.items():
        if len(re.findall(r"(?m)^\s*" + pat, text)) < 3:
            continue
        for scope, (ai, bi) in [(sc, ix) for sc in ("block", "line")
                                for ix in ((0, 1), (1, 0), (0, 2), (1, 2), (-2, -1), (-1, -2), (0, -1), (2, 1))]:
            spec = {"v": 1, "kind": "account", "bank": bank, "keywords": _keywords(text),
                    "date": {"sep": sep, "order": order}, "amount_idx": ai, "balance_idx": bi, "scope": scope}
            res = apply(text, spec)
            if res and res["verified"] and len(res["rows"]) >= 3 and (best is None or len(res["rows"]) > len(best[1]["rows"])):
                best = (spec, res)
    return best


def matches(text: str, spec: dict) -> bool:
    head = text[:4000]
    if spec.get("bank") and bank_of(text) != spec["bank"]:
        return False
    return all(k in _low(head) for k in spec.get("keywords", []))


# ---------------------------------------------------------------- doğrulama (paylaşım ve topluluktan alma)

def is_safe(spec: dict) -> bool:
    """Şablon sadece izin verilen alanları, sabit sözlük kelimelerini ve küçük indeksleri içerebilir — belgeden
    metin (isim, IBAN, tutar) taşıyamaz, kod çalıştıramaz."""
    if isinstance(spec, dict) and spec.get("kind") == "card":
        from app.card_reader import is_safe as card_safe

        return card_safe(spec)
    try:
        if set(spec) - {"v", "kind", "bank", "keywords", "date", "amount_idx", "balance_idx", "scope"}:
            return False
        if spec.get("kind") != "account" or spec.get("bank") not in set(IBAN_BANKS.values()) | {""}:
            return False
        if not all(k in KEYWORDS for k in spec.get("keywords", [])):
            return False
        if (spec["date"]["order"], spec["date"]["sep"]) not in _DATE_RES or set(spec["date"]) != {"order", "sep"}:
            return False
        if not all(isinstance(spec[k], int) and -3 <= spec[k] <= 3 for k in ("amount_idx", "balance_idx")):
            return False
        return spec.get("scope", "block") in ("block", "line")
    except (KeyError, TypeError, AttributeError):
        return False


def all_templates() -> list[dict]:
    with system.connect() as c:
        _init(c)
        return [{**dict(r), "spec": json.loads(r["spec"])} for r in c.execute("SELECT * FROM bank_templates ORDER BY uses DESC, id")]


def _key(spec: dict) -> str:
    return json.dumps(spec, ensure_ascii=False, sort_keys=True)


def save(spec: dict, source: str = "learned") -> bool:
    """Yeni kaydedildiyse True."""
    if not is_safe(spec):
        return False
    with system.connect() as c:
        _init(c)
        return c.execute("INSERT OR IGNORE INTO bank_templates (bank, spec, source) VALUES (?, ?, ?)",
                         (spec.get("bank") or "?", _key(spec), source)).rowcount > 0


def _used(tid: int) -> None:
    with system.connect() as c:
        c.execute("UPDATE bank_templates SET uses = uses + 1 WHERE id = ?", (tid,))


def read(text: str) -> dict | None:
    """Kayıtlı şablonlardan biri bu belgeyi zinciri tutturarak okuyorsa sonucu; yoksa yeni kural öğrenmeyi dene."""
    sync_community()
    for t in all_templates():
        if t["spec"].get("kind", "account") != "account" or not matches(text, t["spec"]):
            continue
        res = apply(text, t["spec"])
        if res and res["verified"]:
            _used(t["id"])
            return res
    learned = learn(text, bank_of(text))
    if learned:
        spec, res = learned
        if spec["bank"] and save(spec):  # banka adı bilinmiyorsa kaydetme (yanlış eşleşmesin)
            share_async(spec)
        return res
    return None


# ---------------------------------------------------------------- topluluk: paylaşma (varsayılan açık) ve alma
# Akış: bir sunucu yeni bankayı öğrenir → (paylaşım açıksa) şablonu merkeze gönderir → yönetici onaylar ve GitHub'daki
# templates/ klasörüne ekler → tüm sunucular günde bir kez oradan çeker. Gönderilen: sadece şablon (kişisel veri yok).

import os  # noqa: E402
import threading  # noqa: E402
import time  # noqa: E402

HUB = os.environ.get("HESAPKITAP_HUB", "https://ekstre.alameddinc.com")
REPO_TEMPLATES = os.environ.get("HESAPKITAP_TEMPLATES_URL",
                                "https://api.github.com/repos/alameddinc/hesapkitap/contents/templates")

SUB_SCHEMA = """
CREATE TABLE IF NOT EXISTS template_submissions (
    id INTEGER PRIMARY KEY,
    bank TEXT NOT NULL,
    spec TEXT NOT NULL UNIQUE,
    count INTEGER NOT NULL DEFAULT 1,       -- kaç sunucudan geldi
    status TEXT NOT NULL DEFAULT 'bekliyor', -- bekliyor | onaylandi | reddedildi
    received_at TEXT DEFAULT CURRENT_TIMESTAMP
);
"""


def is_hub() -> bool:
    return os.environ.get("HESAPKITAP_IS_HUB") == "1"


def sharing_enabled() -> bool:
    from app import demo_mode

    if demo_mode.enabled():  # demoda öğrenilen kurallar topluluğa gönderilmez
        return False
    return system.get("share_templates") != "0"  # varsayılan açık


def set_sharing(on: bool) -> None:
    system.put("share_templates", "1" if on else "0")


def submit_local(spec: dict) -> bool:
    """Merkez (bu sunucu hub ise): gelen şablonu onay kuyruğuna al."""
    if not is_safe(spec) or not spec.get("bank"):
        return False
    with system.connect() as c:
        c.executescript(SUB_SCHEMA)
        c.execute("INSERT INTO template_submissions (bank, spec) VALUES (?, ?) "
                  "ON CONFLICT(spec) DO UPDATE SET count = count + 1", (spec["bank"], _key(spec)))
    return True


def submissions() -> list[dict]:
    with system.connect() as c:
        c.executescript(SUB_SCHEMA)
        return [{**dict(r), "spec": json.loads(r["spec"])}
                for r in c.execute("SELECT * FROM template_submissions ORDER BY status = 'bekliyor' DESC, id DESC")]


def review(sid: int, status: str) -> bool:
    with system.connect() as c:
        c.executescript(SUB_SCHEMA)
        return c.execute("UPDATE template_submissions SET status = ? WHERE id = ?", (status, sid)).rowcount > 0


def _mark_shared(spec: dict) -> None:
    with system.connect() as c:
        c.execute("UPDATE bank_templates SET shared_at = CURRENT_TIMESTAMP WHERE spec = ?", (_key(spec),))


def share(spec: dict) -> bool:
    import httpx

    if not sharing_enabled() or not is_safe(spec):
        return False
    if is_hub():
        ok = submit_local(spec)
    else:
        try:
            ok = httpx.post(f"{HUB}/api/templates/submit", json={"spec": spec}, timeout=10).status_code == 200
        except Exception:  # noqa: BLE001 - paylaşılamazsa okuma etkilenmez
            ok = False
    if ok:
        _mark_shared(spec)
    return ok


def share_async(spec: dict) -> None:
    threading.Thread(target=share, args=(spec,), daemon=True).start()


def sync_community(force: bool = False) -> int:
    """GitHub'daki onaylı şablonları çek (günde bir kez). Ağ yoksa sessizce geçer."""
    import httpx

    last = system.get("templates_synced")
    if not force and last and time.time() - float(last) < 86400:
        return 0
    system.put("templates_synced", str(time.time()))
    added = 0
    try:
        files = httpx.get(REPO_TEMPLATES, timeout=6, headers={"Accept": "application/vnd.github+json"}).json()
        for f in files if isinstance(files, list) else []:
            if not str(f.get("name", "")).endswith(".json"):
                continue
            data = httpx.get(f["download_url"], timeout=6).json()
            for spec in data if isinstance(data, list) else [data]:
                if isinstance(spec, dict) and save(spec, "community"):
                    added += 1
    except Exception:  # noqa: BLE001
        pass
    return added
