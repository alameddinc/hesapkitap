"""Alışveriş listesinde yazılan marka (shop_brand_auto): "çaykur turist çayı 1kg" yazan kişi Çaykur Turist'in
fiyatını görür, başka markanın en ucuz 1 kg çayını değil.

Liste markadan bağımsızdır (app/product_groups.py: marka atılır, "Çay · 1 kg" kalır); satırın markası (brand) yalnız
kişinin seçimiyle gelirdi (shop_brand_size / shop_combo_picker). detect(): satırın metninde bilinen bir marka geçiyorsa
ve o marka bu ürünü gerçekten satıyorsa marka satıra yazılır (brand_auto 1); adda markanın yanında kalan, sözlükte
olmayan kelimeler ("Turist", "Rize") markanın ürün adlarında geçiyorsa ürün serisi (brand_line) olur ve teklifler
adında o kelimeler geçen ürünlere süzülür (price_sources.best_offers line).

Yanlış eşleşmeye karşı:
- aday markalar: o ürünün fiyat verisindeki markalar (Market Fiyatı + kendi fişlerin; price_sources.brands) ve bilinen
  marka listesi (product_groups: BRANDS + app/brands_tr.txt + katalog markaları). Sözlükte geçen tek kelime ("nar",
  "sade") zaten marka sayılmaz; market zincirinin adı ("Migros süt") marka değildir (mağaza demek olabilir).
- listedeki marka yalnız ürünün kendisini satıyorsa yazılır: fiyat verisinde o ürünün markası ya da katalogda o markanın
  bu genel addan ürünü var (product_catalog.brand_index). Hiç veri yoksa marka yazılmaz (eski davranış: marka fark
  etmez) — veride olmayan markayı yazıp fiyatı gizlemek yerine.
- Türkçe harf / büyük-küçük farkı yok ("caykur", "ÇAYKUR", "Çaykur").
Kişinin kendi ürününe bağlanan satır (shopping_known) zaten o ürünün kendisidir: marka yazılmaz.
"""
from app import product_groups as pg

SERIES_MAX = 3  # bundan çok bilinmeyen kelime: seri değil, belirsiz ad (seri yazılmaz)
# iyelik / çoğul eki ("ÇAYI", "SUYU", "PEYNİRLERİ"): kökü sözlükteyse kelime ürünün kendisidir, seri değil
_SUFFIXES = ("LARI", "LERI", "LAR", "LER", "SI", "SU", "YI", "YU", "I", "U")


def _market_chains() -> set[tuple[str, ...]]:
    from app.receipt_templates import CHAINS

    return {tuple(pg.name_norm(n).split()) for n, _, cat in CHAINS if cat == "Market & Gıda"}


def _words(text: str) -> list[tuple[str, str]]:
    """(kelimenin yazıldığı biçim, normalize ilk parçası): "çaykur'un" → ("çaykur'un", "CAYKUR"), "1kg" → ("1kg", "1")."""
    out = []
    for w in (text or "").split():
        toks = pg.name_norm(w).split()
        out.append((w, toks[0] if toks else ""))
    return out


def _find(seq: list[str], ph: tuple[str, ...]) -> int:
    for i in range(len(seq) - len(ph) + 1):
        if tuple(seq[i:i + len(ph)]) == ph:
            return i
    return -1


def _plain(ph: tuple[str, ...]) -> bool:
    """Markanın kendisi ürün dili mi ("DOGAL", "KOY", "SUT" adlı marka): öyleyse addan marka okunmaz."""
    return not ph or all(t in pg._VOCAB or t in pg.DESCRIPTORS or not t.isalpha() for t in ph) or len("".join(ph)) < 3


def _title(w: str) -> str:
    low = w.replace("I", "ı").replace("İ", "i").lower()
    return ({"i": "İ", "ı": "I"}.get(low[0], low[0].upper()) + low[1:]) if low else low


def _typed(words: list[tuple[str, str]], i: int, n: int) -> str:
    """Kişinin yazdığı kelimeler baş harfi büyük ("çaykur" → "Çaykur"); noktalama atılır."""
    return " ".join(_title("".join(ch for ch in w if ch.isalnum())) for w, _ in words[i:i + n])


def _bases(base: str) -> set[str]:
    out = {base, *pg.child_bases(base)}
    g = pg.generic_of_key(base)
    if g and pg.form_parent(g):
        out.add(pg.slug(pg.form_parent(g)))
    return out


def _food_word(t: str) -> bool:
    """Ürünü anlatan kelime (seri değil): sözlük / sıfat / tanımlayıcı ya da eki atılınca sözlük kelimesi ("ÇAYI")."""
    if t in pg.DESCRIPTORS or pg._known(t):
        return True
    return any(t.endswith(s) and len(t) - len(s) >= 2 and t[:-len(s)] in pg._VOCAB for s in _SUFFIXES)


def _series(words: list[tuple[str, str]], skip: set[int], group: dict) -> list[int]:
    """Markanın dışında kalan, ürünü anlatmayan kelimelerin sırası (en çok SERIES_MAX; fazlası belirsiz: hiç)."""
    own = set(pg.name_norm((group.get("label") or "").split(" · ")[0]).split())
    idx = [i for i, (_w, t) in enumerate(words) if i not in skip and t.isalpha() and len(t) >= 3
           and t not in own and not _food_word(t)]
    return idx if len(idx) <= SERIES_MAX else []


def _titles(bn: str, keys: list[str]) -> list[frozenset]:
    """Markanın ürün adları (kelime kümesi): Market Fiyatı satırları (bu ürünün grupları, son 30 gün) + katalog."""
    from datetime import date, timedelta

    from app import price_sources, product_catalog, system

    out = list((product_catalog.brand_index().get(bn) or {}).get("titles", ()))
    if keys:
        since = (date.today() - timedelta(days=30)).isoformat()
        try:
            with system.connect() as c:
                rows = c.execute(f"SELECT DISTINCT title, brand FROM official_prices WHERE group_key IN "
                                 f"({','.join('?' * len(keys))}) AND day >= ? AND brand IS NOT NULL", [*keys, since])
                out += [frozenset(pg.name_norm(t).split()) for t, b in rows if price_sources.brand_norm(b) == bn]
        except Exception:  # noqa: BLE001 - eski veritabanı
            pass
    return out


def detect(text: str, group: dict | None) -> dict | None:
    """Metindeki marka (+ seri): {brand ("Çaykur"; veride yazıldığı gibi), line ("Turist" | None)} ya da None.
    group: satırın çözülen grubu (geçici / bilinmeyen grupta None döner)."""
    from app import price_sources, product_catalog, shopping

    if not group or group.get("pending") or not text:
        return None
    base = group["key"].split("|", 1)[0]
    words = _words(text)
    seq = [t for _w, t in words]
    chains = _market_chains()
    keys = shopping.brand_keys(base)
    for b in pg.child_bases(base):
        keys += shopping.brand_keys(b)
    found: list[tuple[int, int, str, str]] = []  # (sıra, -uzunluk, görünen ad, brand_norm)
    for d in price_sources.brands(keys):  # 1) bu ürünün verisindeki markalar (her biri ürünü satıyor)
        ph = tuple(pg.name_norm(d["brand"]).split())
        if _plain(ph) or ph in chains:
            continue
        i = _find(seq, ph)
        if i >= 0:
            found.append((i, -len(ph), d["brand"], price_sources.brand_norm(d["brand"])))
    if not found:  # 2) bilinen marka listesi; katalogda bu ürünü satıyorsa
        idx = product_catalog.brand_index()
        want = _bases(base)
        for i, t in enumerate(seq):
            cands = [ph for ph in pg._BRAND_MULTI.get(t, ()) if tuple(seq[i:i + len(ph)]) == ph]
            if t in pg._BRAND_SINGLE:
                cands.append((t,))
            for ph in cands:
                if _plain(ph) or ph in chains:
                    continue
                a = idx.get(pg.slug(" ".join(ph)))
                if a and a["generics"] & want:
                    found.append((i, -len(ph), price_sources._brand_label(a["name"]) or _typed(words, i, len(ph)),
                                  pg.slug(" ".join(ph))))
                    break
    if not found:
        return None
    i, n, label, bn = min(found)
    span = set(range(i, i - n))
    line = None
    cand = _series(words, span, group)
    if cand:
        toks = [seq[j] for j in cand]
        best: list[int] = []
        for tt in _titles(bn, keys):  # adlarda birlikte geçen en çok kelime (yazım hatası / fazla kelime düşer)
            got = [j for j, t in zip(cand, toks) if any(x.startswith(t) for x in tt)]
            if len(got) > len(best):
                best = got
        if best:
            line = " ".join(_typed(words, j, 1) for j in best)
    return {"brand": label, "line": line}


def label(brand: str | None, line: str | None) -> str | None:
    """"Çaykur Turist" (marka + seri); marka yoksa None."""
    if not brand:
        return None
    return f"{brand} {line}" if line else brand
