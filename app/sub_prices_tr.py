"""Uygulama aboneliklerinin Türkiye'deki YAKLAŞIK mağaza fiyatları: adsız adayda "Olabilir: …" önerisi için.

KESİN DEĞİLDİR. Mağaza fiyatları sık değişir, ülkeye, kampanyaya, öğrenci/aile paketine ve vergiye göre oynar. Bu tablo
yalnız "bu tutar şu hizmetlerden biri olabilir" demek için kullanılır; kullanıcıya hiçbir zaman kesin ad diye
gösterilmez (istemci "Olabilir" der, tek dokunuşla seçilir ya da "Bilmiyorum"). Güncellemek için satırı düzelt ya da
ekle; AS_OF'u değiştir. Tutar eşleşmesi TOLERANCE içinde (zam sonrası eski tutar da kısa süre tutabilsin diye).

Satır: (katalog id, paket adı, mağaza "apple" | "google" | "*" (ikisi) | "web" (mağaza içinden satılmıyor: yalnız elle
eklemede), dönem "aylik" | "yillik", ₺ tutar)
"""

AS_OF = "2026-11"
TOLERANCE = 0.04  # ±%4 (en az ₺1)

PRICES: list[tuple[str, str, str, str, float]] = [
    # Apple
    ("icloud", "iCloud+ 50 GB", "apple", "aylik", 39.99),
    ("icloud", "iCloud+ 200 GB", "apple", "aylik", 129.99),
    ("icloud", "iCloud+ 2 TB", "apple", "aylik", 399.99),
    ("apple_music", "Apple Music Bireysel", "apple", "aylik", 64.99),
    ("apple_music", "Apple Music Aile", "apple", "aylik", 99.99),
    ("apple_music", "Apple Music Öğrenci", "apple", "aylik", 34.99),
    ("apple_one", "Apple One Bireysel", "apple", "aylik", 149.99),
    ("apple_one", "Apple One Aile", "apple", "aylik", 199.99),
    ("apple_tv", "Apple TV+", "apple", "aylik", 199.99),
    ("apple_arcade", "Apple Arcade", "apple", "aylik", 64.99),
    # Google / YouTube
    ("youtube_premium", "YouTube Premium Bireysel", "*", "aylik", 79.99),
    ("youtube_premium", "YouTube Premium Aile", "*", "aylik", 159.99),
    ("youtube_premium", "YouTube Premium Öğrenci", "*", "aylik", 52.99),
    ("youtube_music", "YouTube Music Premium", "*", "aylik", 59.99),
    ("google_one", "Google One 100 GB", "*", "aylik", 59.99),
    ("google_one", "Google One 100 GB", "*", "yillik", 599.99),
    ("google_one", "Google One 2 TB", "*", "aylik", 249.99),
    ("google_one", "Google One 2 TB", "*", "yillik", 2499.99),
    ("google_one", "Google AI Pro", "*", "aylik", 719.99),
    # yerli dizi/film ve spor (mağaza içi satın alma)
    ("exxen", "Exxen", "*", "aylik", 229.9),
    ("exxen", "ExxenSpor", "*", "aylik", 459.9),
    ("blutv", "BluTV", "*", "aylik", 199.9),
    ("max", "Max Standart", "*", "aylik", 249.9),
    ("gain", "Gain", "*", "aylik", 99.0),
    ("tod", "TOD Süper Lig", "*", "aylik", 549.0),
    ("bein_connect", "beIN CONNECT", "*", "aylik", 499.0),
    ("tabii", "tabii Premium", "*", "aylik", 99.99),
    ("tvplus", "TV+", "*", "aylik", 149.9),
    # global
    ("netflix", "Netflix Temel", "web", "aylik", 189.99),
    ("netflix", "Netflix Standart", "web", "aylik", 289.99),
    ("netflix", "Netflix Özel", "web", "aylik", 379.99),
    ("disney_plus", "Disney+ Standart", "*", "aylik", 349.9),
    ("disney_plus", "Disney+ Yıllık", "*", "yillik", 3499.0),
    ("spotify", "Spotify Premium Bireysel", "web", "aylik", 99.0),
    ("spotify", "Spotify Premium Duo", "web", "aylik", 135.0),
    ("spotify", "Spotify Premium Aile", "web", "aylik", 165.0),
    ("spotify", "Spotify Premium Öğrenci", "web", "aylik", 55.0),
    ("amazon_prime", "Amazon Prime", "web", "aylik", 69.9),
    ("microsoft_365", "Microsoft 365 Bireysel", "*", "yillik", 1299.99),
    ("microsoft_365", "Microsoft 365 Bireysel", "*", "aylik", 129.99),
    ("chatgpt", "ChatGPT Plus", "*", "aylik", 899.99),
    ("claude", "Claude Pro", "*", "aylik", 899.99),
    ("duolingo", "Super Duolingo", "*", "yillik", 1299.99),
    ("duolingo", "Super Duolingo", "*", "aylik", 249.99),
    ("tinder", "Tinder Plus", "*", "aylik", 199.99),
    ("linkedin", "LinkedIn Premium Career", "*", "aylik", 899.99),
    ("canva", "Canva Pro", "*", "aylik", 189.99),
    ("storytel", "Storytel", "web", "aylik", 149.9),
]


def guesses(amount: float, store: str | None, cycle: str | None, limit: int = 4) -> list[dict]:
    """Bu tutara yakın bilinen paketler (en yakın önce, aynı hizmet bir kez). Kesin değil: yalnız öneri."""
    from app import sub_catalog

    if not amount or amount <= 0:
        return []
    out: dict[str, dict] = {}
    for cid, label, st, per, price in PRICES:
        if st == "web" and store in ("apple", "google"):
            continue  # mağazadan satın alınamıyor: App Store / Google Play tahsilatı olamaz
        if st not in ("*", "web") and store and st != store:
            continue
        if cycle in ("aylik", "yillik") and per != cycle:
            continue
        diff = abs(amount - price)
        if diff > max(1.0, price * TOLERANCE):
            continue
        cur = out.get(cid)
        rank = (diff, 0 if st == store else 1)  # aynı tutarda o mağazaya özgü paket önce (₺129,99: iCloud+ App Store'da)
        if cur is None or rank < cur["_rank"]:
            v = sub_catalog.view(cid)
            if v:
                out[cid] = {**v, "plan": label, "price": price, "cycle": per, "_rank": rank}
    rows = sorted(out.values(), key=lambda x: (x["_rank"], x["name"].casefold()))[:limit]
    return [{k: v for k, v in r.items() if k != "_rank"} for r in rows]
