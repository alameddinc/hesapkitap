"""Türkiye'nin 81 ili (kişinin "Bölgem" tercihi, resmî fiyat bölgesi). Ad, Market Fiyatı verisindeki bölge adıyla birebir
aynıdır ("İstanbul", "Ankara", "İzmir"). Not: app/data/ .gitignore'da (kişisel veri klasörü), liste bu yüzden burada.

FALLBACK: kişinin ilinde bölgesel zincirin (Migros, CarrefourSA, Hakmar, Tarım Kredi) fiyatı yoksa bakılacak yakın büyük
şehir; listede yoksa İstanbul. Sabit ve küçük tutulur (konum yok, mesafe hesabı yok).
"""

PROVINCES = (
    "Adana", "Adıyaman", "Afyonkarahisar", "Ağrı", "Aksaray", "Amasya", "Ankara", "Antalya", "Ardahan", "Artvin",
    "Aydın", "Balıkesir", "Bartın", "Batman", "Bayburt", "Bilecik", "Bingöl", "Bitlis", "Bolu", "Burdur", "Bursa",
    "Çanakkale", "Çankırı", "Çorum", "Denizli", "Diyarbakır", "Düzce", "Edirne", "Elazığ", "Erzincan", "Erzurum",
    "Eskişehir", "Gaziantep", "Giresun", "Gümüşhane", "Hakkari", "Hatay", "Iğdır", "Isparta", "İstanbul", "İzmir",
    "Kahramanmaraş", "Karabük", "Karaman", "Kars", "Kastamonu", "Kayseri", "Kilis", "Kırıkkale", "Kırklareli",
    "Kırşehir", "Kocaeli", "Konya", "Kütahya", "Malatya", "Manisa", "Mardin", "Mersin", "Muğla", "Muş", "Nevşehir",
    "Niğde", "Ordu", "Osmaniye", "Rize", "Sakarya", "Samsun", "Şanlıurfa", "Siirt", "Sinop", "Şırnak", "Sivas",
    "Tekirdağ", "Tokat", "Trabzon", "Tunceli", "Uşak", "Van", "Yalova", "Yozgat", "Zonguldak",
)
_SET = frozenset(PROVINCES)
DEFAULT = "İstanbul"

# il → yakın büyük şehir (bölgesel zincirin fiyatı o ilde yoksa). Yazılmayan il: DEFAULT.
FALLBACK = {
    "Kocaeli": "İstanbul", "Sakarya": "İstanbul", "Tekirdağ": "İstanbul", "Yalova": "İstanbul", "Kırklareli": "İstanbul",
    "Edirne": "İstanbul", "Düzce": "İstanbul", "Bursa": "İstanbul", "Bilecik": "İstanbul", "Çanakkale": "İstanbul",
    "Manisa": "İzmir", "Aydın": "İzmir", "Muğla": "İzmir", "Denizli": "İzmir", "Uşak": "İzmir", "Balıkesir": "İzmir",
    "Konya": "Ankara", "Eskişehir": "Ankara", "Kırıkkale": "Ankara", "Kırşehir": "Ankara", "Çankırı": "Ankara",
    "Bolu": "Ankara", "Aksaray": "Ankara", "Karaman": "Ankara", "Afyonkarahisar": "Ankara", "Kütahya": "Ankara",
    "Yozgat": "Ankara", "Nevşehir": "Ankara", "Kayseri": "Ankara", "Çorum": "Ankara", "Zonguldak": "Ankara",
    "Bartın": "Ankara", "Karabük": "Ankara", "Kastamonu": "Ankara",
    "Isparta": "Antalya", "Burdur": "Antalya",
    "Mersin": "Adana", "Osmaniye": "Adana", "Hatay": "Adana",
    "Kilis": "Gaziantep", "Şanlıurfa": "Gaziantep", "Adıyaman": "Gaziantep", "Kahramanmaraş": "Gaziantep",
}
# kaynaktaki bölge takma adları (Market Fiyatı varsayılan bölgesi Kadıköy) → il
ALIASES = {"DEFAULT_KADIKOY": "İstanbul"}


def valid(name: str | None) -> str | None:
    """Listede olan il adı (boşluk kırpılır) ya da None."""
    s = (name or "").strip()
    return s if s in _SET else None


def province_of(region: str | None) -> str | None:
    """Kaynaktaki bölge adı → il ("DEFAULT_KADIKOY" → "İstanbul"); tanınmayan None."""
    s = (region or "").strip()
    return ALIASES.get(s) or valid(s)


def source_names(province: str) -> list[str]:
    """İlin kaynaktaki adları (kendisi + takma adları)."""
    return [province, *(k for k, v in ALIASES.items() if v == province)]


def fallback_chain(province: str | None) -> list[str]:
    """Bölgesel zincir için bakılacak iller sırasıyla: kişinin ili → yakın büyük şehir → İstanbul (tekrarsız)."""
    p = valid(province) or DEFAULT
    out = [p]
    for x in (FALLBACK.get(p), DEFAULT):
        if x and x not in out:
            out.append(x)
    return out
