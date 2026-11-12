"""Hazır abonelik kataloğu (yerli + global): ad, renkli baş harf, grup, ekstre açıklaması ve makbuz adı kalıpları.

Logo yok: marka logoları dışarıdan çekilmez, dosya olarak da taşınmaz. Her hizmet bir renk + 1–3 karakterlik baş harfle
gösterilir (istemci kare içinde çizer; yazı rengi fg). Renkler markanın bilinen ana rengine yakın düz renklerdir.

Eşleşme kuralları sunucuda (TEK KAYNAK):
- patterns: kart ekstresi / hesap dökümü açıklaması (textnorm.normalize çıktısı: BÜYÜK harf, Türkçe karaktersiz)
- names:    App Store / Google Play makbuzundaki ürün adı ve mağaza satırındaki ad ("GOOGLE *YouTube" → "YOUTUBE"),
            yine normalize biçimde; makbuzda "YouTube Premium (Aylık)" gibi ek yazılar atılarak aranır.
İstemci yalnız GET /api/subscriptions/catalog'daki id, ad, renk ve grubu görür (kalıplar gitmez).
"""
import re
from functools import lru_cache

from app.textnorm import normalize

GROUPS = {"video": "Dizi ve film", "muzik": "Müzik", "spor": "Spor", "bulut": "Depolama ve paket",
          "yazilim": "Yazılım", "yapay_zeka": "Yapay zekâ", "oyun": "Oyun", "kitap": "Kitap ve sesli kitap",
          "egitim": "Eğitim", "alisveris": "Alışveriş üyeliği", "diger": "Diğer"}

# (id, ad, baş harf, renk, grup, mağazalar, ekstre kalıbı, makbuz/mağaza adları)
# mağazalar: hizmetin App Store / Google Play içinden de satın alınabildiği yerler (yalnız bilgi; eşleşmeyi kısıtlamaz)
_ROWS: list[tuple[str, str, str, str, str, tuple[str, ...], str, tuple[str, ...]]] = [
    ("netflix", "Netflix", "N", "#E50914", "video", ("web",), r"NETFLIX", ("NETFLIX",)),
    ("youtube_premium", "YouTube Premium", "YT", "#FF0000", "video", ("apple", "google", "web"),
     r"YOUTUBE ?PREMIUM|YOUTUBEPREMIUM|GOOGLE \*? ?YOUTUBE(?! ?MUSIC)", ("YOUTUBE PREMIUM", "YOUTUBEPREMIUM", "YOUTUBE")),
    ("youtube_music", "YouTube Music", "YM", "#C4302B", "muzik", ("apple", "google", "web"), r"YOUTUBE ?MUSIC",
     ("YOUTUBE MUSIC",)),
    ("spotify", "Spotify", "S", "#1DB954", "muzik", ("web", "google"), r"SPOTIFY", ("SPOTIFY", "SPOTIFY PREMIUM")),
    ("disney_plus", "Disney+", "D+", "#113CCF", "video", ("apple", "google", "web"), r"DISNEY", ("DISNEY+", "DISNEY PLUS", "DISNEY")),
    ("amazon_prime", "Amazon Prime", "P", "#00A8E1", "video", ("web", "apple", "google"),
     r"AMAZON ?PRIME|AMAZONPRIME|PRIME ?VIDEO|AMZN ?PRIME", ("AMAZON PRIME", "PRIME VIDEO")),
    ("exxen", "Exxen", "EX", "#FFCC00", "video", ("apple", "google", "web"), r"EXXEN", ("EXXEN",)),
    ("blutv", "BluTV", "B", "#1A6AFF", "video", ("apple", "google", "web"), r"BLU ?TV", ("BLUTV", "BLU TV")),
    ("max", "Max", "M", "#002BE7", "video", ("apple", "google", "web"), r"HBO ?MAX|HBOMAX", ("HBO MAX", "MAX")),
    ("gain", "Gain", "G", "#FF3D57", "video", ("apple", "google", "web"), r"\bGAIN\b", ("GAIN",)),
    ("tod", "TOD", "TOD", "#6C2BD9", "spor", ("apple", "google", "web"), r"\bTOD\b|TODTV|TOD TV", ("TOD", "TOD TV")),
    ("bein_connect", "beIN CONNECT", "bC", "#4B1E78", "spor", ("apple", "google", "web"), r"BEIN ?CONNECT",
     ("BEIN CONNECT",)),
    ("tabii", "tabii", "t", "#00A99D", "video", ("apple", "google", "web"), r"\bTABII\b", ("TABII",)),
    ("tvplus", "TV+", "TV+", "#0091D5", "video", ("apple", "google", "web"), r"\bTV ?\+|TVPLUS", ("TV+", "TVPLUS")),
    ("mubi", "MUBI", "MU", "#0F1C2E", "video", ("apple", "google", "web"), r"\bMUBI\b", ("MUBI",)),
    ("apple_music", "Apple Music", "AM", "#FA243C", "muzik", ("apple",), r"APPLE ?MUSIC", ("APPLE MUSIC",)),
    ("icloud", "iCloud+", "iC", "#3693F3", "bulut", ("apple",), r"ICLOUD", ("ICLOUD+", "ICLOUD")),
    ("apple_one", "Apple One", "A1", "#1D1D1F", "bulut", ("apple",), r"APPLE ?ONE", ("APPLE ONE",)),
    ("apple_tv", "Apple TV+", "tv", "#2C2C2E", "video", ("apple",), r"APPLE ?TV", ("APPLE TV+", "APPLE TV")),
    ("apple_arcade", "Apple Arcade", "AA", "#FF2D55", "oyun", ("apple",), r"APPLE ?ARCADE", ("APPLE ARCADE",)),
    ("google_one", "Google One", "G1", "#4285F4", "bulut", ("google", "apple", "web"), r"GOOGLE ?\*? ?(GOOGLE )?ONE\b",
     ("GOOGLE ONE", "GOOGLE AI PRO", "GOOGLE AI PREMIUM")),
    ("microsoft_365", "Microsoft 365", "365", "#D83B01", "yazilim", ("apple", "google", "web"),
     r"MICROSOFT ?\*? ?365|MSFT ?\*? ?365|OFFICE ?365", ("MICROSOFT 365", "OFFICE 365")),
    ("xbox", "Xbox Game Pass", "X", "#107C10", "oyun", ("web",), r"XBOX|GAME ?PASS", ("XBOX GAME PASS", "GAME PASS")),
    ("playstation", "PlayStation Plus", "PS", "#003791", "oyun", ("web",), r"PLAYSTATION|\bPSN\b|SONY ?INTERACTIVE",
     ("PLAYSTATION PLUS", "PS PLUS")),
    ("chatgpt", "ChatGPT Plus", "AI", "#10A37F", "yapay_zeka", ("apple", "google", "web"), r"OPENAI|CHATGPT",
     ("CHATGPT PLUS", "CHATGPT")),
    ("claude", "Claude Pro", "C", "#D97757", "yapay_zeka", ("apple", "google", "web"), r"ANTHROPIC|CLAUDE",
     ("CLAUDE PRO", "CLAUDE")),
    ("duolingo", "Duolingo", "D", "#58CC02", "egitim", ("apple", "google", "web"), r"DUOLINGO", ("DUOLINGO", "SUPER DUOLINGO")),
    ("storytel", "Storytel", "ST", "#FF5C28", "kitap", ("web",), r"STORYTEL", ("STORYTEL",)),
    ("audible", "Audible", "AU", "#F8991C", "kitap", ("apple", "web"), r"AUDIBLE", ("AUDIBLE",)),
    ("scribd", "Everand (Scribd)", "SC", "#1E7B85", "kitap", ("apple", "google", "web"), r"SCRIBD|EVERAND", ("SCRIBD", "EVERAND")),
    ("canva", "Canva", "C", "#00C4CC", "yazilim", ("apple", "google", "web"), r"CANVA", ("CANVA PRO", "CANVA")),
    ("adobe", "Adobe", "A", "#FA0F00", "yazilim", ("apple", "google", "web"), r"ADOBE", ("ADOBE",)),
    ("dropbox", "Dropbox", "DB", "#0061FF", "bulut", ("apple", "google", "web"), r"DROPBOX", ("DROPBOX",)),
    ("linkedin", "LinkedIn Premium", "in", "#0A66C2", "diger", ("apple", "google", "web"), r"LINKEDIN", ("LINKEDIN PREMIUM", "LINKEDIN")),
    ("tinder", "Tinder", "T", "#FE3C72", "diger", ("apple", "google"), r"TINDER", ("TINDER",)),
    ("deezer", "Deezer", "DZ", "#A238FF", "muzik", ("apple", "google", "web"), r"DEEZER", ("DEEZER",)),
    ("fizy", "fizy", "f", "#FFC900", "muzik", ("apple", "google", "web"), r"\bFIZY\b", ("FIZY",)),
    ("github", "GitHub", "GH", "#24292F", "yazilim", ("web",), r"GITHUB", ("GITHUB",)),
    ("hepsiburada_premium", "Hepsiburada Premium", "hp", "#FF6000", "alisveris", ("web",), r"HEPSIBURADA ?PREMIUM",
     ("HEPSIBURADA PREMIUM",)),
]


def _fg(color: str) -> str:
    """Rengin üstündeki yazı: açık zeminde koyu, koyu zeminde beyaz (göreli parlaklık, WCAG)."""
    r, g, b = (int(color[i:i + 2], 16) / 255 for i in (1, 3, 5))
    lin = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in (r, g, b)]
    lum = 0.2126 * lin[0] + 0.7152 * lin[1] + 0.0722 * lin[2]
    return "#111111" if lum > 0.4 else "#FFFFFF"


CATALOG: list[dict] = [
    {"id": i, "name": n, "glyph": gl, "color": c, "fg": _fg(c), "group": grp, "stores": list(st)}
    for i, n, gl, c, grp, st, _p, _names in _ROWS
]
BY_ID = {x["id"]: x for x in CATALOG}
_PATTERNS = [(re.compile(p), i) for i, _n, _g, _c, _grp, _st, p, _names in _ROWS]
_NAMES = sorted(((nm, i) for i, *_r, names in _ROWS for nm in names), key=lambda x: -len(x[0]))


def public() -> dict:
    """İstemciye: hizmetler (ad sırasıyla) ve grup adları."""
    return {"items": sorted(CATALOG, key=lambda x: x["name"].casefold()), "groups": GROUPS}


def get(cid: str | None) -> dict | None:
    return BY_ID.get(cid or "")


@lru_cache(maxsize=4096)
def match_description(description: str) -> str | None:
    """Ekstre / döküm açıklaması → katalog id ('GOOGLE *YouTube' → youtube_premium). Bulamazsa None."""
    text = normalize(description or "")
    for rx, cid in _PATTERNS:
        if rx.search(text):
            return cid
    return None


def _clean_name(name: str) -> str:
    n = normalize(name or "")
    n = re.sub(r"\((AYLIK|YILLIK|HAFTALIK|MONTHLY|YEARLY|ANNUAL|WEEKLY|1 AY|1 YIL|1 MONTH|1 YEAR)\)", " ", n)
    n = re.sub(r"[^A-Z0-9+ ]+", " ", n)
    return " ".join(n.split())


@lru_cache(maxsize=4096)
def match_name(name: str) -> str | None:
    """Makbuzdaki ya da mağaza satırındaki ürün adı → katalog id. En uzun ad önce ('YOUTUBE MUSIC' 'YOUTUBE'dan önce);
    ad, kelime sınırında geçmeli ('MAX' 'MAXIMUM'da eşleşmez). Bulamazsa açıklama kalıplarına bakar."""
    n = _clean_name(name)
    if not n:
        return None
    for nm, cid in _NAMES:
        if re.search(rf"(^|\s){re.escape(nm)}($|\s)", n) or (len(nm) >= 6 and nm.replace(" ", "") in n.replace(" ", "")):
            return cid
    return match_description(name)


def view(cid: str | None) -> dict | None:
    """Kayıtta gösterilecek kısa bilgi: {id, name, glyph, color, fg, group}."""
    x = get(cid)
    return {k: x[k] for k in ("id", "name", "glyph", "color", "fg", "group")} if x else None


# Katalogda olmayan ad için de istemciler aynı rengi seçsin: addan kararlı renk (palet)
_PALETTE = ("#5B6CFF", "#0EA5A4", "#E25822", "#7C3AED", "#2563EB", "#DB2777", "#059669", "#B45309")


def glyph_for(name: str) -> dict:
    """Katalogda yoksa: adın baş harfi + addan kararlı renk."""
    import hashlib

    clean = (name or "?").strip()
    words = [w for w in re.split(r"\s+", clean) if w]
    g = (words[0][:1] + (words[1][:1] if len(words) > 1 else "")).upper() if words else "?"
    c = _PALETTE[int(hashlib.sha1(clean.casefold().encode()).hexdigest(), 16) % len(_PALETTE)]
    return {"id": None, "name": clean, "glyph": g.replace("İ", "I") if g == "İ" else g, "color": c, "fg": _fg(c), "group": "diger"}
