"""Yazı tipi kodlu PDF metnini kurtarma: Type3 glif ŞEKLİ → karakter.

Bazı bankaların ekstresi (Akbank) metni Type3 yazı tipleriyle çizer: karakter eşlemesi (ToUnicode) yok, glif adları
anlamsız ("C1a"), kodlar belgeden belgeye değişir — pdfminer metni "(cid:N)" olarak verir, şablon öğrenilemez. Ama
aynı karakterin çizim programı (CharProc) bütün belgelerde aynı bayttır: programın özeti (glif kimliği) → karakter
eşlemesi bir kez öğrenilince sonraki ekstreler OCR'sız, birebir metne çevrilir (yerleşim pdfplumber'ın kendisi).

Eşleme (system.db glyph_votes; SUNUCU GENELİ, belgeden içerik yok — yalnız şekil özeti ve tek karakter):
  1. Bilinen glifler hemen çevrilir; boş glif (çizim yok ya da hiç nokta boyamayan bitmap) boşluktur.
  2. Bilinmeyen glif belgede iz bırakacak kadar çoksa ve Tesseract kuruluysa: o sayfalar 300 dpi çizilip OCR'lanır
     (seyrek metin kipi, ikişer sayfa paralel), OCR kelime kutusu içine düşen PDF karakterleri (sayı tutuyorsa) harf
     harf eşlenir ve oy verilir. Oylar birikir (belge başı glif-karakter çifti en fazla VOTE_CAP); karar en çok oyu
     alan karakter (payı ≥ %60). Akbank'ın glifleri bütün ekstrelerde aynı: ilk bir iki ekstreden sonra OCR gerekmez.
Kurtarılan metin yine ekstre denklemiyle doğrulanır (kart şablonu yalnız denklem tutarsa okur / öğrenir); OCR'ın
yanlış oyu en kötü şablonun tutmamasına (yapay zekâya düşmeye) yol açar.

Yalnız metin "(cid:N)" ağırlıklıysa ve belgede Type3 yazı tipi varsa çalışır; normal PDF'e dokunmaz.
    HESAPKITAP_GLYPH_OCR_SECONDS  belge başı OCR süresi, saniye (60; 0 → OCR yok, yalnız bilinen glifler)
    HESAPKITAP_GLYPH_OCR_PAGES    belge başı en fazla OCR'lanan sayfa (6)
    HESAPKITAP_GLYPH_OCR_DPI      OCR çizim çözünürlüğü (300; 200 ≈ %30 hızlı, biraz daha çok tanınmayan glif)
"""
import hashlib
import io
import logging
import os
import re
import subprocess
import threading
import time
from collections import Counter
from contextlib import contextmanager
from contextvars import ContextVar

log = logging.getLogger(__name__)

SCHEMA = """
CREATE TABLE IF NOT EXISTS glyph_votes (    -- Type3 glif şekli (CharProc özeti) → karakter oyları; belgeden içerik yok
    shape TEXT NOT NULL,                     -- blake2b(CharProc baytları), 16 onaltılık
    ch TEXT NOT NULL,                        -- tek karakter
    votes INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (shape, ch)
);
"""
VOTE_CAP = 10           # bir belgeden aynı (glif, karakter) için en fazla oy: tek belge eşlemeyi ele geçirmesin
SHARE = 0.6             # karar: en çok oyu alan karakterin payı
DPI = 300
PARALLEL = 2            # belge başı aynı anda OCR'lanan sayfa
# OCR'a gitmek için bilinmeyen glif geçişi: en az 10 ve bütün glif geçişlerinin %0,5'inden çok
OCR_MIN_UNKNOWN, OCR_MIN_SHARE = 10, 1 / 200
PSM = "11"              # Tesseract seyrek metin kipi: karışık punto / sütunlu ekstrede kelime kutuları en isabetlisi
UNKNOWN = "\ufffd"
_PUA = 0xF0000          # çıkarma sırasında glif kimliği: düzlem 15 özel kullanım alanı (belgede kendiliğinden olmaz)
# boyama operatörü ya da satır içi görüntü (Akbank glifleri "BI /IM true … ID <ikili> EI" bitmap maskesi; ikili veri
# "EI"ye bitişik olabilir, BI'ye bakılır)
_PAINT = re.compile(rb"(?:^|\s)(?:f\*?|F|B\*?|b\*?|S|s|Do|BI|sh)(?=\s|$)")
_OCR_SLOTS = threading.Semaphore(2)  # aynı anda en fazla iki belge OCR'lanır (CPU)


def _num(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, default))
    except ValueError:
        return default


def cid_heavy(text: str) -> bool:
    """card_reader._cid_text ile aynı ölçü: metnin önemli kısmı "(cid:N)"."""
    n = text.count("(cid:")
    return n >= 20 and n * 7 > len(text) * 0.2


def shape_id(proc: bytes) -> str:
    return hashlib.blake2b(proc, digest_size=8).hexdigest()


_MASK = re.compile(rb"(?:^|\s)BI\s(.*?)\sID\s", re.S)


def _blank(proc: bytes) -> bool:
    """Boşluk glifi: çizim komutu yok (yalnız d0/d1 genişliği) ya da tek bitmap maskesi hiç nokta boyamıyor (Akbank'ın
    boşluğu: bütün baytları "boyama" değerinin tersi)."""
    if not _PAINT.search(proc):
        return True
    m = _MASK.search(proc)
    if not m or proc.count(b"BI") != 1 or _PAINT.search(proc[:m.start()] + proc[proc.rfind(b"EI") + 2:]):
        return False
    head = m.group(1)
    if not re.search(rb"/(?:IM|ImageMask)\s+true", head) or re.search(rb"/(?:F|Filter)\b", head):
        return False
    d = re.search(rb"/(?:D|Decode)\s*\[\s*(\d)\s+(\d)\s*\]", head)
    paint = 1 if d and d.group(1) == b"1" else 0  # maske: Decode [0 1] → 0 boyar, [1 0] → 1 boyar
    payload = proc[m.end():proc.rfind(b"EI")].rstrip()
    return bool(payload) and set(payload) == ({0x00} if paint else {0xFF})


# ---------------------------------------------------------------- eşleme deposu (system.db)

def _decide(votes: Counter) -> str | None:
    total = sum(votes.values())
    if not total:
        return None
    (ch, n), *rest = votes.most_common(2)
    if rest and rest[0][1] == n:
        return None
    return ch if n >= SHARE * total else None


# Yazmadan deneme (geriye dönük öğrenmenin --dry-run'ı): oylar veritabanına değil bu sayaca eklenir, okurken birleşir
_DRY: ContextVar[Counter | None] = ContextVar("glyph_dry_votes", default=None)


@contextmanager
def dry_run():
    tok = _DRY.set(Counter())
    try:
        yield
    finally:
        _DRY.reset(tok)


def labels(shapes) -> dict[str, str]:
    from app import system

    shapes = list(shapes)
    if not shapes:
        return {}
    votes: dict[str, Counter] = {}
    if (mem := _DRY.get()) is not None:
        wanted = set(shapes)
        for (s, ch), n in mem.items():
            if s in wanted:
                votes.setdefault(s, Counter())[ch] += n
    with system.connect() as c:
        c.executescript(SCHEMA)
        for i in range(0, len(shapes), 500):
            part = shapes[i:i + 500]
            for r in c.execute(f"SELECT shape, ch, votes FROM glyph_votes WHERE shape IN ({','.join('?' * len(part))})",
                               part):
                votes.setdefault(r["shape"], Counter())[r["ch"]] += r["votes"]
    return {s: ch for s, v in votes.items() if (ch := _decide(v)) is not None}


def add_votes(votes: Counter) -> None:
    """votes: (şekil, karakter) → bu belgedeki oy (VOTE_CAP ile kırpılır)."""
    from app import system

    rows = [(s, ch, min(n, VOTE_CAP)) for (s, ch), n in votes.items() if n > 0 and len(ch) == 1 and not ch.isspace()]
    if not rows:
        return
    if (mem := _DRY.get()) is not None:
        for s, ch, n in rows:
            mem[(s, ch)] += n
        return
    with system.connect() as c:
        c.executescript(SCHEMA)
        c.executemany("INSERT INTO glyph_votes (shape, ch, votes) VALUES (?, ?, ?) "
                      "ON CONFLICT (shape, ch) DO UPDATE SET votes = votes + excluded.votes", rows)


# ---------------------------------------------------------------- çıkarma: Type3 glifleri kimlikleriyle

class _ShapeMap:
    """pdfminer unicode_map yerine: kod → glif kimliği (özel kullanım karakteri) ya da boşluk."""

    def __init__(self, codes: dict[int, str], reg: "_Registry"):
        self.codes, self.reg = codes, reg

    def get_unichr(self, cid: int) -> str:
        shape = self.codes.get(cid)
        if shape is None:
            raise KeyError(cid)
        return self.reg.char(shape)


class _Registry:
    def __init__(self):
        self.ids: dict[str, int] = {}
        self.shapes: list[str] = []
        self.blank: set[str] = set()

    def char(self, shape: str) -> str:
        if shape in self.blank:
            return " "
        i = self.ids.get(shape)
        if i is None:
            i = self.ids[shape] = len(self.shapes)
            self.shapes.append(shape)
        return chr(_PUA + i)

    def shape_of(self, ch: str) -> str | None:
        i = ord(ch) - _PUA
        return self.shapes[i] if 0 <= i < len(self.shapes) else None


def _type3_codes(spec, reg: _Registry) -> dict[int, str]:
    from pdfminer.psparser import PSLiteral
    from pdfminer.pdftypes import resolve1

    enc = resolve1(spec.get("Encoding"))
    procs = resolve1(spec.get("CharProcs")) or {}
    out: dict[int, str] = {}
    if not isinstance(enc, dict) or not isinstance(procs, dict):
        return out
    code = 0
    for x in resolve1(enc.get("Differences")) or []:
        x = resolve1(x)
        if isinstance(x, int):
            code = x
        elif isinstance(x, PSLiteral):
            name = x.name if isinstance(x.name, str) else x.name.decode("latin-1")
            p = procs.get(name)
            if p is not None:
                data = resolve1(p).get_data()
                shape = shape_id(data)
                if _blank(data):
                    reg.blank.add(shape)
                out[code] = shape
            code += 1
    return out


def _rsrcmgr(reg: _Registry):
    from pdfminer.pdffont import PDFType3Font
    from pdfminer.pdfinterp import PDFResourceManager

    class Mgr(PDFResourceManager):
        def get_font(self, objid, spec):
            font = super().get_font(objid, spec)
            if isinstance(font, PDFType3Font) and not getattr(font, "_hk_shapes", False):
                font._hk_shapes = True
                try:
                    codes = _type3_codes(spec, reg)
                except Exception:  # noqa: BLE001 - bozuk yazı tipi: olduğu gibi kalsın
                    codes = {}
                if codes:
                    font.unicode_map = _ShapeMap(codes, reg)
            return font

    return Mgr()


def _extract_shapes(data: bytes, password: str | None, reg: _Registry) -> tuple[list[str], list[list[tuple]], list]:
    """Sayfa metinleri (glifler özel kullanım karakteri), sayfa karakterleri (x0, top, x1, bottom, metin) ve sayfa
    kutusu (cropbox; dönük sayfa None)."""
    import pdfplumber

    from app import limits

    texts, chars, boxes = [], [], []
    with pdfplumber.open(io.BytesIO(data), password=password or "") as pdf:
        pdf.rsrcmgr = _rsrcmgr(reg)
        deadline = limits.Deadline()
        for p in pdf.pages:
            deadline.check()
            try:
                texts.append(p.extract_text() or "")
                chars.append([(c["x0"], c["top"], c["x1"], c["bottom"], c["text"]) for c in p.chars])
                boxes.append(None if p.rotation else tuple(p.cropbox))
            finally:
                p.close()
    return texts, chars, boxes


def _has_type3(data: bytes, password: str | None) -> bool:
    from pdfminer.pdfdocument import PDFDocument
    from pdfminer.pdfpage import PDFPage
    from pdfminer.pdfparser import PDFParser
    from pdfminer.pdftypes import resolve1

    doc = PDFDocument(PDFParser(io.BytesIO(data)), password=password or "")
    for i, page in enumerate(PDFPage.create_pages(doc)):
        if i >= 3:
            break
        fonts = resolve1((resolve1(page.resources) or {}).get("Font")) or {}
        for ref in fonts.values():
            f = resolve1(ref)
            if isinstance(f, dict) and str(resolve1(f.get("Subtype"))) in ("/'Type3'", "/Type3"):
                return True
    return False


# ---------------------------------------------------------------- OCR ile oy

def ocr_available() -> bool:
    from app import ocr

    return ocr.available()


def _ocr_words(png: bytes, timeout: float) -> list[tuple[int, int, int, int, str]]:
    from app import ocr

    langs = "tur+eng" if ocr._has_lang("tur") else "eng"
    r = subprocess.run(["tesseract", "stdin", "stdout", "-l", langs, "--psm", PSM, "tsv"], input=png,
                       capture_output=True, timeout=max(1.0, timeout), env={**os.environ, "OMP_THREAD_LIMIT": "1"})
    out = []
    for ln in r.stdout.decode("utf-8", "ignore").splitlines()[1:]:
        f = ln.split("\t")
        if len(f) < 12 or f[0] != "5":
            continue
        word = f[11].strip()
        try:
            conf = float(f[10])
        except ValueError:
            continue
        if word and conf >= 50:
            out.append((int(f[6]), int(f[7]), int(f[8]), int(f[9]), word))
    return out


def _vote_page(words, chars, box, scale: float, reg: _Registry, votes: Counter) -> int:
    """OCR kelime kutusuna merkezi düşen PDF karakterleri (boşluk hariç, soldan sağa) kelimeyle aynı sayıdaysa harf harf
    oy; eşleşen kelime sayısını döndürür. Type3 karakter kutusunun dikey yeri yazı tipinin FontBBox'ına göre kayık
    olabilir (pdfminer glifin gerçek çizimini bilmez): sayfa başı sabit dikey kayma, aday kaymalar arasında en çok
    kelimeyi eşleyen olarak seçilir."""
    import bisect

    x0b, topb = box[0], box[1]
    pts = sorted((((top + bottom) / 2 - topb) * scale, ((x0 + x1) / 2 - x0b) * scale, x0, t)
                 for x0, top, x1, bottom, t in chars if t.strip())
    if not pts or not words:
        return 0
    ys = [p[0] for p in pts]
    hs = sorted(h for *_, h, _ in words)
    reach = 3 * hs[len(hs) // 2]

    def inside(word, dy):
        left, top, w, h, _ = word
        padx, pady = 0.35 * h, 0.35 * h
        lo = bisect.bisect_left(ys, top - pady - dy)
        hi = bisect.bisect_right(ys, top + h + pady - dy)
        return sorted((p for p in pts[lo:hi] if left - padx <= p[1] <= left + w + padx), key=lambda p: p[2])

    # aday kaymalar: kelime merkezi - aynı sütundaki karakter merkezi (4 piksellik kovalar), en sık altısı
    hist = Counter()
    for left, top, w, h, _ in words:
        cy = top + h / 2
        lo, hi = bisect.bisect_left(ys, cy - reach), bisect.bisect_right(ys, cy + reach)
        for p in pts[lo:hi]:
            if left <= p[1] <= left + w:
                hist[round((cy - p[0]) / 4)] += 1
    best, best_n = 0.0, -1
    for b, _ in hist.most_common(6):
        n = sum(len(inside(wd, b * 4)) == len(wd[4]) for wd in words)
        if n > best_n:
            best, best_n = b * 4, n
    matched = 0
    for wd in words:
        got = inside(wd, best)
        if len(got) != len(wd[4]):
            continue
        matched += 1
        for p, ch in zip(got, wd[4]):
            if (shape := reg.shape_of(p[3])) is not None:
                votes[(shape, ch)] += 1
    return matched


def _ocr_votes(data: bytes, password: str | None, pages: list[int], chars, boxes, reg: _Registry,
               unknown: set[str]) -> tuple[Counter, int]:
    """(oylar, OCR'lanan sayfa sayısı). Sayfalar ikişer ikişer paralel OCR'lanır (Tesseract ayrı süreç; pdfium
    çizimi iş parçacığına güvenli değil, sırayla); bilinmeyen her glif oy aldıysa kalan sayfalara geçilmez."""
    from concurrent.futures import ThreadPoolExecutor

    import pypdfium2 as pdfium

    budget = _num("HESAPKITAP_GLYPH_OCR_SECONDS", 60)
    end = time.monotonic() + budget
    votes: Counter = Counter()
    done = 0
    dpi = _num("HESAPKITAP_GLYPH_OCR_DPI", DPI)
    scale = dpi / 72
    pages = [k for k in pages if boxes[k] is not None][:int(_num("HESAPKITAP_GLYPH_OCR_PAGES", 6))]
    if not pages or not _OCR_SLOTS.acquire(timeout=max(0.0, budget)):
        return votes, 0
    try:
        pdf = pdfium.PdfDocument(data, password=password or None)
        try:
            with ThreadPoolExecutor(PARALLEL) as pool:
                for i in range(0, len(pages), PARALLEL):
                    left = end - time.monotonic()
                    if left <= 1:
                        break
                    batch = pages[i:i + PARALLEL]
                    pngs = []
                    for k in batch:
                        buf = io.BytesIO()
                        pdf[k].render(scale=scale, grayscale=True).to_pil().save(buf, format="PNG")
                        pngs.append(buf.getvalue())
                    futs = [pool.submit(_ocr_words, png, left) for png in pngs]
                    for k, f in zip(batch, futs):
                        try:
                            words = f.result()
                        except subprocess.TimeoutExpired:
                            continue
                        done += 1
                        _vote_page(words, chars[k], boxes[k], scale, reg, votes)
                    if unknown <= {s for s, _ in votes}:
                        break  # bilinmeyen her glife en az bir oy: kalan sayfalar gereksiz
        finally:
            pdf.close()
    finally:
        _OCR_SLOTS.release()
    return votes, done


# ---------------------------------------------------------------- dış arayüz

def recover(data: bytes, password: str | None, text: str) -> tuple[str, list[str], dict] | None:
    """cid ağırlıklı metni Type3 glif eşlemesiyle kurtar: (metin, sayfalar, bilgi) ya da None (Type3 yok / olmadı).
    bilgi: {"via": "glyph" | "glyph_ocr", "unknown": bilinmeyen glif sayısı (metinde UNKNOWN), "ocr_pages": n}.
    Hata okumayı bozmaz (None)."""
    if not cid_heavy(text):
        return None
    try:
        return _recover(data, password)
    except Exception as e:  # noqa: BLE001
        from app.limits import LimitError

        if isinstance(e, LimitError):
            raise
        log.warning("glif eşlemesiyle metin kurtarılamadı", exc_info=True)
        return None


def _recover(data: bytes, password: str | None) -> tuple[str, list[str], dict] | None:
    if not _has_type3(data, password):
        return None
    reg = _Registry()
    texts, chars, boxes = _extract_shapes(data, password, reg)
    if not reg.shapes:
        return None
    known = labels(reg.shapes)
    used = Counter(ch for t in texts for ch in t if reg.shape_of(ch) is not None)
    unknown = {reg.shape_of(ch) for ch in used} - set(known)
    info = {"via": "glyph", "ocr_pages": 0}
    unk = sum(n for ch, n in used.items() if reg.shape_of(ch) in unknown)
    # OCR yalnız bilinmeyen glif belgede iz bırakacak kadar çoksa: OCR'ın da tanıyamadığı birkaç nadir glif (işyeri
    # adında bir harf) her yüklemede yeniden OCR'a yol açmasın
    if unk >= OCR_MIN_UNKNOWN and unk > OCR_MIN_SHARE * sum(used.values()) and ocr_available() \
            and _num("HESAPKITAP_GLYPH_OCR_SECONDS", 60) > 0:
        # en çok bilinmeyen glif içeren sayfadan başla
        need = Counter()
        for k, t in enumerate(texts):
            need[k] = sum(1 for ch in t if reg.shape_of(ch) in unknown)
        pages = [k for k, n in need.most_common() if n]
        votes, done = _ocr_votes(data, password, pages, chars, boxes, reg, unknown)
        info["ocr_pages"] = done
        if votes:
            add_votes(votes)
            known = labels(reg.shapes)
            info["via"] = "glyph_ocr"
    table = {_PUA + i: known.get(s, UNKNOWN) for i, s in enumerate(reg.shapes)}
    pages_out = [t.translate(table) for t in texts]
    out = "\n".join(pages_out)
    info["unknown"] = out.count(UNKNOWN)
    return out, pages_out, info
