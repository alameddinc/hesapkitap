"""Banka dökümü satır açıklamasına taşmış sayfa altbilgisi/üstbilgisi temizleyicisi (tüm bankalar, tüm okuma yolları).

PDF'ten çıkan metinde son hareketin açıklaması sayfanın altbilgisiyle birleşebiliyor (Halkbank, tek sayfalık döküm):
    "GIDEN FAST - AYSE YILMAZ TARAFI Sayfa No:1 / 1 Türkiye Halk Bankası A.Ş halkbank.com.tr 0 850 222 0 400 Halkbank
     Diyalog NDAN GÖNDERILEN,…"
Altbilgi parçaları (sayfa no, banka unvanı, web adresi, çağrı merkezi, "Dekont yerine kullanılamaz", Mersis no…) art
arda bir küme oluşturur. Küme yalnız GÜÇLÜ bir işaret içeriyorsa silinir (sayfa no, çağrı merkezi numarası, dekont
uyarısı ya da unvan + web adresi yan yana): açıklamanın kendi parçası olan alıcı bankası ("…,Türkiye Vakiflar Bankasi
T.A.O.,TR…") ya da işyeri adresi ("TRENDYOL.COM") tek başına dokunulmaz. Altbilgiden sonra gelen devam satırı
("NDAN GÖNDERILEN,…") korunur ve bölünmüş kelime birleşir ("TARAFI NDAN" → "TARAFINDAN").

Saf fonksiyon: veritabanına yazmaz. İçe aktarmada (bank_import.finalize) ve gösterimde (db.list_entries; eski
kayıtlar) kullanılır. Parmak izi açıklamaya bakmaz (bakiyeli satır), temizlik çift kayıt yaratmaz.
"""
import re

from app.textnorm import normalize

_UP = "A-ZÇĞİÖŞÜ"
_LOW = "a-zçğıöşü"
# (ad, desen, güçlü işaret mi)
_PARTS = (
    ("page", rf"(?i:(?:sayfa|page)\s*(?:no\s*)?[:.]?\s*\d{{1,3}}\s*(?:/|of)\s*\d{{1,3}})", True),
    ("dekont", r"(?i:dekont\s+yerine\s+kullan[ıi]lamaz\.?)", True),
    ("phone", r"(?<![\d])(?:0\s?8[05]0(?:\s?\d){7}|444\s\d(?:\s?\d){3})(?![\d])", True),
    # "Türkiye Halk Bankası A.Ş", "T.C. Ziraat Bankası A.Ş.", "Kuveyt Türk Katılım Bankası A.Ş.", "TÜRKİYE HALK BANKASI A.Ş."
    ("legal", rf"(?:T\.\s?C\.\s+)?(?:(?:[{_UP}][{_LOW}]+|ve)\s+){{0,3}}(?:Bankas[ıi]|Bank)\s+"
              rf"(?:A\.\s?[ŞS]\.?|A[ŞS]\b|T\.\s?A\.\s?O\.?)"
              rf"|(?:TÜRKİYE|TURKIYE|T\.\s?C\.)\s+(?:[{_UP}]+\s+){{1,3}}BANKAS[Iİ]\s+(?:A\.\s?[ŞS]\.?|T\.\s?A\.\s?O\.?)", False),
    ("web", r"(?i:(?:https?://)?(?:www\.)?[a-z0-9-]+(?:\.[a-z0-9-]+)*\.(?:com\.tr|com|net|org)\b(?:/[^\s,]*)?)", False),
    ("mersis", r"(?i:(?:mersis|ticaret\s+sicil)\s*(?:no|numaras[ıi])?\s*[:.]?\s*[\d ]{4,})", False),
    ("contact", r"(?i:(?:müşteri\s+)?(?:iletişim|çağrı|hizmet)\s+merkezi)", False),
    ("diyalog", rf"[{_UP}][\w]*\s+(?i:diyalog)\w*", False),
)
_TOKEN = re.compile("|".join(f"(?P<{n}>{p})" for n, p, _ in _PARTS))
_STRONG = {n for n, _, s in _PARTS if s}
_GAP = re.compile(r"[\s,;:.|/\-–]*")
# kümenin hemen arkasında kalan altbilgi kelimeleri ("Halkbank Diyalog", kesik "Halkbank Dia")
_VOCAB = {"HALKBANK", "VAKIFBANK", "ZIRAAT", "BANKKART", "KUVEYT", "DIYALOG", "DEKONT", "YERINE", "KULLANILAMAZ",
          "DIALOG", "MUSTERI", "ILETISIM", "CAGRI", "MERKEZI", "HIZMET", "HATTI", "BANKACILIGI"}
_SPLIT = re.compile(r"\bTARAF(I\s+NDAN|IN\s+DAN|INDA\s+N)\b")
_HINT = re.compile(r"(?i)sayfa|page|kullan[ıi]lamaz|0\s?8[05]0|444\s\d|\.com|www\.|mersis")


def _is_vocab(word: str, last: bool) -> bool:
    w = re.sub(r"[^A-Z]", "", normalize(word))
    if not w:
        return True  # tek başına noktalama
    if w in _VOCAB:
        return True
    # açıklama 500 karakterde kesilmiş olabilir: en sondaki yarım kelime ("Dia")
    return last and len(w) >= 2 and any(v.startswith(w) for v in _VOCAB)


def clean(desc: str | None) -> str | None:
    """Açıklamadan sayfa altbilgisi/üstbilgisi kümelerini çıkar. Altbilgi yoksa metin aynen döner."""
    if not desc or not _HINT.search(desc):
        return desc
    spans = [(m.start(), m.end(), m.lastgroup) for m in _TOKEN.finditer(desc)]
    if not spans:
        return desc
    clusters: list[list[tuple[int, int, str]]] = []
    for s in spans:
        if clusters and _GAP.fullmatch(desc[clusters[-1][-1][1]:s[0]]):
            clusters[-1].append(s)
        else:
            clusters.append([s])
    cuts: list[tuple[int, int]] = []
    for cl in clusters:
        kinds = {k for _, _, k in cl}
        if not (kinds & _STRONG or {"legal", "web"} <= kinds):
            continue
        start, end = cl[0][0], cl[-1][1]
        # önünde kalan altbilgi kelimeleri ("VakıfBank www.vakifbank.com.tr …")
        for w in reversed(list(re.finditer(r"\S+", desc[:start]))):
            if not _is_vocab(w.group(0), last=False) or not re.sub(r"[^A-Z]", "", normalize(w.group(0))):
                break
            start = w.start()
        # arkasından gelen altbilgi kelimeleri ("Halkbank Diyalog") da kümeye dahil
        rest = desc[end:]
        for m in re.finditer(r"\S+", rest):
            if not _is_vocab(m.group(0), last=m.end() == len(rest.rstrip())):
                break
            end = len(desc) - len(rest) + m.end()
        cuts.append((start, end))
    if not cuts:
        return desc
    out, pos = [], 0
    for a, b in cuts:
        out.append(desc[pos:a])
        pos = b
    out.append(desc[pos:])
    text = " ".join(p.strip(" ,;|") for p in out if p.strip(" ,;|"))
    text = _SPLIT.sub("TARAFINDAN", re.sub(r"\s+", " ", text)).strip(" ,;|-")
    return text or desc
