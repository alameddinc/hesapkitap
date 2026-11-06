"""Kullanıcının yazdığı düzenli ifadeler (kategori kuralı deseni) için güvenli derleme ve arama.

Sorun (güvenlik denetimi, ReDoS): "(A+)+$" gibi bir desen Python'un re modülünde felaket geri izlemeye düşer; re GIL'i
bırakmadığı için aynı süreçteki bütün istekler (başka kullanıcılar da) desen bitene kadar bekler. Desen kayıtta ve
denemede saklanıp recategorize_all, book.filter_rules, kategorisiz önerileri gibi yerlerde çalıştırılıyordu.

İki katman:
1. Kayıtta / denemede statik denetim (problem): en çok MAX_LEN karakter; iç içe niceleyici ("(A+)+", "(\\w*)*"),
   nicelenmiş seçenek ("(A|AA)+") ve geri başvuru ("\\1", "(?P=ad)") yok. İleri/geri bakış yalnız tekrarsız
   içerikle ("UBER(?! EATS)" varsayılan kurallarda var; içinde +, *, {n,} olan bakış reddedilir).
2. Çalıştırırken süre sınırı: üçüncü parti `regex` modülüyle timeout (GIL'i de bırakır, concurrent=True); metin
   MAX_TEXT karakterde kesilir. Süresi dolan desen süreç boyunca bir daha denenmez (kayıtlarda log).

Veritabanında önceden kalmış, denetimden geçmeyen ya da derlenmeyen kural hiçbir şeyi çökertmez: atlanır, bir kez log.
"""
import logging
import re
from functools import lru_cache

import regex

log = logging.getLogger(__name__)

MAX_LEN = 200  # kullanıcının yazdığı desen (kayıt/deneme)
# çalıştırırken: kendiliğinden öğrenilen kural (re.escape(fişteki resmi unvan)) 200'ü geçebilir; yapısı güvenli, atlanmasın
RUN_MAX_LEN = 2000
MAX_TEXT = 500  # aranan metin (işlem açıklaması; normalize edilmiş açıklamalar bunun çok altında)
TIMEOUT = 0.05  # saniye, tek arama

try:  # Python 3.11+
    from re import _constants as _c
    from re import _parser as _p
except ImportError:  # pragma: no cover - eski Python
    import sre_constants as _c
    import sre_parse as _p

_REPEATS = {_c.MAX_REPEAT, _c.MIN_REPEAT} | ({_c.POSSESSIVE_REPEAT} if hasattr(_c, "POSSESSIVE_REPEAT") else set())
_LOOK = {_c.ASSERT, _c.ASSERT_NOT}
_BACKREF = {_c.GROUPREF, _c.GROUPREF_EXISTS} | ({_c.GROUPREF_IGNORE} if hasattr(_c, "GROUPREF_IGNORE") else set())

ERR_INVALID = "Kural geçerli bir arama kalıbı (düzenli ifade) değil: parantezler ve köşeli parantezler kapanmalı"
ERR_LONG = f"Kural en çok {MAX_LEN} karakter olabilir"
ERR_NESTED = "Kuralda iç içe tekrar var (ör. (A+)+): çok yavaş çalışır. Tekrarı tek katmanda yaz (ör. A+)"
ERR_ALT = "Kuralda tekrarlanan seçenek grubu var (ör. (A|B)+): çok yavaş çalışabilir. Seçenekleri tekrarsız yaz (ör. A|B)"
ERR_BACKREF = "Kuralda geri başvuru (\\1 gibi) kullanılamaz"
ERR_LOOK = "Kuralda ileri/geri bakışın ((?= (?! (?<= (?<!) içinde tekrar (+ * {n,}) kullanılamaz"


class _Unsafe(Exception):
    def __init__(self, msg: str):
        self.msg = msg


def _walk(items, in_repeat: bool) -> None:
    for op, av in items:
        if op in _LOOK:
            if _has_repeat(av[1]):
                raise _Unsafe(ERR_LOOK)
            _walk(av[1], in_repeat)
            continue
        if op in _BACKREF:
            raise _Unsafe(ERR_BACKREF)
        if op in _REPEATS:
            lo, hi, sub = av
            many = hi is _c.MAXREPEAT or hi > 1
            if many and in_repeat:
                raise _Unsafe(ERR_NESTED)
            if many and _has_branch(sub):
                raise _Unsafe(ERR_ALT)
            _walk(sub, in_repeat or many)
        elif op is _c.SUBPATTERN:
            _walk(av[-1], in_repeat)
        elif op is _c.BRANCH:
            for alt in av[1]:
                _walk(alt, in_repeat)
        elif op is getattr(_c, "ATOMIC_GROUP", object()):
            _walk(av, in_repeat)


def _has_repeat(items) -> bool:
    for op, av in items:
        if op in _REPEATS:
            return True
        if op is _c.SUBPATTERN and _has_repeat(av[-1]):
            return True
        if op is _c.BRANCH and any(_has_repeat(alt) for alt in av[1]):
            return True
        if op in _LOOK and _has_repeat(av[1]):
            return True
        if op is getattr(_c, "ATOMIC_GROUP", object()) and _has_repeat(av):
            return True
    return False


def _has_branch(items) -> bool:
    for op, av in items:
        if op is _c.BRANCH:
            return True
        if op is _c.SUBPATTERN and _has_branch(av[-1]):
            return True
        if op is getattr(_c, "ATOMIC_GROUP", object()) and _has_branch(av):
            return True
    return False


def problem(pattern: str, max_len: int = MAX_LEN) -> str | None:
    """Desen kaydedilemez/denenemezse Türkçe neden, uygunsa None."""
    if len(pattern) > max_len:
        return ERR_LONG
    try:
        re.compile(pattern)
        parsed = _p.parse(pattern)
        regex.compile(pattern)
    except (re.error, regex.error, RecursionError, OverflowError, ValueError):
        return ERR_INVALID
    try:
        _walk(parsed, False)
    except _Unsafe as e:
        return e.msg
    except RecursionError:
        return ERR_NESTED
    return None


_bad: set[str] = set()  # süresi dolan desenler: süreç boyunca bir daha çalıştırılmaz


@lru_cache(maxsize=2048)
def compiled(pattern: str):
    """Güvenli derlenmiş desen; güvensiz/geçersizse None (bir kez log)."""
    why = problem(pattern, RUN_MAX_LEN)
    if why:
        log.warning("kural deseni atlandı (%s): %r", why, pattern[:80])
        return None
    return regex.compile(pattern)


def search(pattern: str, text: str):
    """re.search yerine: güvensiz/geçersiz desen ya da süre aşımı → None (eşleşme yok)."""
    if pattern in _bad:
        return None
    rx = compiled(pattern)
    if rx is None:
        return None
    try:
        return rx.search((text or "")[:MAX_TEXT], timeout=TIMEOUT, concurrent=True)
    except TimeoutError:
        _bad.add(pattern)
        log.warning("kural deseni süre aşımı, bundan sonra atlanacak: %r", pattern[:80])
        return None
