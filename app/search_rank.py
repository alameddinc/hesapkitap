"""Ürün araması sıralaması (alışveriş listesi önerisi, Nereden alınır, kalem adı önerisi): Elasticsearch yok, saf Python.

Katman (küçük önce):
  EXACT        0  yazılan ad birebir ("şeker" → "Şeker")
  FIRST_WORD   1  ad yazılanla başlıyor ve yazılan tam kelime ("şeker" → "Şeker Pancarı")
  FIRST_PREFIX 2  ilk kelime yazılanla başlıyor ("şeker" → "Şekerleme")
  WORD_PREFIX  3  başka bir kelime yazılanla başlıyor ("şeker" → "Toz Şeker", "Pudra Şekeri")
  INSIDE       4  kelimenin içinde geçiyor (en az 3 harf; "eker" → "Şeker")
  FUZZY        5  bir harf hata (en az 4 harfli kelimede; "sekr" / "şekre" → "Şeker") ya da Türkçe harf farkı
                  (aşağıda)
Aynı katmanda sıra çağıranın: kişinin geçmişi / sık aldıkları önce.

Türkçe: ş/s, ı/i, ç/c, ğ/g, ö/o, ü/u ve büyük/küçük harf eşit sayılır (textnorm.normalize), Türkçe klavyesi olmayan
"seker" yazınca "Şeker" birebir olur. Ama yazılan Türkçe harf içeriyorsa ve aday düzgün Türkçe yazılmışsa (strict: ürün
grubu adları; ya da adayda Türkçe harf varsa) harfler de tutmalı: "sü" yazınca "Su" ilk sıraya çıkmaz (FUZZY'ye düşer),
"Süt" çıkar. Fiş adları çoğu zaman Türkçe harfsiz yazılır ("KIRMIZI" → "Kirmizi"); bunlar katlanarak eşleşir.

Kısa sorgu: 2 harften az boş döner (MIN_LEN); 3 harften az sorguda yalnız kelime başı (içinde geçen ve bulanık yok).
"""
import functools
import re

from app.textnorm import normalize

EXACT, FIRST_WORD, FIRST_PREFIX, WORD_PREFIX, INSIDE, FUZZY = range(6)
MIN_LEN = 2
INSIDE_MIN = 3
FUZZY_MIN = 4
_TR = set("şŞıİçÇğĞöÖüÜ")
_NON_ALNUM = re.compile(r"[^\w]+", re.UNICODE)


@functools.lru_cache(maxsize=4096)
def fold(s: str | None) -> str:
    """"Şeker-Pancarı 1kg" → "seker pancari 1kg" (Türkçe harfsiz, küçük, noktalama boşluk)."""
    return " ".join(re.sub(r"[^A-Z0-9]+", " ", normalize(s or "")).lower().split())


@functools.lru_cache(maxsize=4096)
def tr_lower(s: str | None) -> str:
    """Türkçe küçük harf, harfler korunur: "KIRMIZI Şeker" → "kırmızı şeker"."""
    t = (s or "").replace("I", "ı").replace("İ", "i").lower()
    return " ".join(_NON_ALNUM.sub(" ", t).replace("_", " ").split())


def has_tr(s: str | None) -> bool:
    return any(ch in _TR for ch in (s or ""))


def _near(a: str, b: str) -> bool:
    """Optimal hizalı Damerau–Levenshtein ≤ 1 (bir harf eksik / fazla / farklı ya da yan yana iki harf yer değişmiş)."""
    if a == b:
        return True
    la, lb = len(a), len(b)
    if abs(la - lb) > 1:
        return False
    if la == lb:
        diff = [i for i in range(la) if a[i] != b[i]]
        return len(diff) == 1 or (len(diff) == 2 and diff[1] == diff[0] + 1
                                  and a[diff[0]] == b[diff[1]] and a[diff[1]] == b[diff[0]])
    if la > lb:
        a, b = b, a
    i = 0
    while i < len(a) and a[i] == b[i]:
        i += 1
    return a[i:] == b[i + 1:]


def _fuzzy_word(q: str, w: str) -> bool:
    """Yazılan kelime (≥ FUZZY_MIN) adın kelimesine ya da (yazılırken) o kelimenin aynı uzunluktaki başına bir harf yakın."""
    if len(q) < FUZZY_MIN:
        return False
    return _near(q, w) or any(_near(q, w[:n]) for n in (len(q) - 1, len(q), len(q) + 1) if 0 < n < len(w))


def _tier(q: str, t: str) -> int | None:
    if not q or not t:
        return None
    if t == q:
        return EXACT
    words, qw = t.split(), q.split()
    if t.startswith(q + " "):
        return FIRST_WORD
    if t.startswith(q):
        return FIRST_PREFIX
    if all(any(w.startswith(x) for w in words) for x in qw):
        return WORD_PREFIX
    if len(q.replace(" ", "")) >= INSIDE_MIN and (q in t or all(any(x in w for w in words) for x in qw)):
        return INSIDE
    if all(any(w.startswith(x) or _fuzzy_word(x, w) for w in words) for x in qw) and any(len(x) >= FUZZY_MIN for x in qw):
        return FUZZY
    return None


def tier(q: str | None, text: str | None, strict: bool = False) -> int | None:
    """Yazılan q ile aday adın katmanı (yukarıdaki sabitler) ya da eşleşmiyorsa None. strict: aday düzgün Türkçe yazılmış
    (ürün grubu adı); yazılan Türkçe harf içeriyorsa harfler de tutmalı, tutmazsa FUZZY."""
    qf = fold(q)
    if len(qf.replace(" ", "")) < MIN_LEN:
        return None
    t = _tier(qf, fold(text))
    if t is None or t == FUZZY or not has_tr(q) or not (strict or has_tr(text)):
        return t
    lt = _tier(tr_lower(q), tr_lower(text))
    return FUZZY if lt is None else max(t, lt)


def best_tier(q: str | None, *texts: str | None, strict: bool = False) -> int | None:
    """Birden çok ad (metin, etiket) içinden en iyi katman."""
    got = [x for x in (tier(q, t, strict) for t in texts if t) if x is not None]
    return min(got) if got else None
