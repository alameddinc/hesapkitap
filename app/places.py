"""Ekstre açıklamasında işyeri adının ardından gelen konum sözcükleri: il (app/provinces.py), büyük şehirlerin ilçeleri,
yabancı şehir ve ülke / ülke kodu. Kart ekstresinde "MIGROS/ISTANBUL", "A101/KADIKOY", "FACEBK *ABC/DUBLIN" gibi eğik
çizgiden sonra çoğu zaman şube ya da şehir yazar; ödeme aracısı ön ekinden ("IYZICO/ORNEK KITAP") ayırmak için
kullanılır (app/gateways.py aday taraması, app/textnorm.py benzer yazım koruması).

Sözcükler normalize() biçiminde (büyük harf, Türkçe karaktersiz). Liste bilerek sınırlı: amaç "sağ taraf yalnız konum
mu" sorusuna kaba bir cevap; eksik ilçe aday taramasında yalnız bir işyeri fazla sayılmasına yol açar, yönetici onayı
yine gerekir.
"""
import functools

from app.provinces import PROVINCES

# İstanbul, Ankara, İzmir ilçeleri ve öteki büyükşehirlerin merkez ilçeleri; kişi adı ya da genel kelime olanlar
# (FATIH, LEVENT, SELCUK, YILDIRIM, EVREN, KONAK, KIRAZ…) bilerek yok: 'ORNEK FATIH' işyeri adı konum sayılmasın
_DISTRICTS = """
ADALAR ARNAVUTKOY ATASEHIR AVCILAR BAGCILAR BAHCELIEVLER BAKIRKOY BASAKSEHIR BAYRAMPASA BESIKTAS BEYKOZ BEYLIKDUZU
BEYOGLU BUYUKCEKMECE CATALCA CEKMEKOY ESENLER ESENYURT EYUP EYUPSULTAN GAZIOSMANPASA GUNGOREN KADIKOY KAGITHANE
KARTAL KUCUKCEKMECE MALTEPE PENDIK SANCAKTEPE SARIYER SILIVRI SULTANBEYLI SULTANGAZI SILE SISLI TUZLA UMRANIYE USKUDAR
ZEYTINBURNU MASLAK MECIDIYEKOY TAKSIM NISANTASI ETILER BOSTANCI GOZTEPE ACIBADEM ATAKOY FLORYA ISTINYE
AKYURT ALTINDAG BEYPAZARI CAMLIDERE CANKAYA CUBUK ELMADAG ETIMESGUT GOLBASI GUDUL HAYMANA KAHRAMANKAZAN
KALECIK KECIOREN KIZILCAHAMAM MAMAK NALLIHAN POLATLI PURSAKLAR SINCAN SEREFLIKOCHISAR YENIMAHALLE KIZILAY 
ALIAGA BALCOVA BAYINDIR BAYRAKLI BERGAMA BEYDAG BORNOVA BUCA CESME CIGLI DIKILI FOCA GAZIEMIR GUZELBAHCE KARABAGLAR
KARABURUN KARSIYAKA KEMALPASA MENDERES MENEMEN NARLIDERE ODEMIS SEFERIHISAR TORBALI URLA
ALSANCAK
NILUFER OSMANGAZI MUDANYA GEMLIK INEGOL MURATPASA KONYAALTI KEPEZ ALANYA MANAVGAT 
SELCUKLU MERAM KARATAY SEYHAN CUKUROVA YUREGIR SARICAM ODUNPAZARI TEPEBASI IZMIT GEBZE DARICA KARTEPE ADAPAZARI
SERDIVAN SAHINBEY SEHITKAMIL MELIKGAZI KOCASINAN TALAS ATAKUM ILKADIM ORTAHISAR AKCAABAT BODRUM FETHIYE MARMARIS
DALAMAN DATCA KUSADASI AYVALIK EDREMIT BANDIRMA CORLU CERKEZKOY SULEYMANPASA PAMUKKALE MERKEZEFENDI YENISEHIR
TOROSLAR MEZITLI TARSUS ERDEMLI ANTAKYA ISKENDERUN DOSEMEALTI
MERKEZ SUBE SUBESI MAGAZA MAGAZASI AVM
IST ANK IZM
"""

# Yabancı şehirler (dijital hizmetlerin fatura adresi) ve ülke adları / kodları
_ABROAD = """
DUBLIN LONDON LONDRA AMSTERDAM LUXEMBOURG LUKSEMBURG PARIS BERLIN MUNICH MUNCHEN FRANKFURT STOCKHOLM CORK LIMASSOL
NICOSIA LEFKOSA GIRNE MAGOSA BARCELONA MADRID MILANO MILAN ROMA ROME ZURICH GENEVA CENEVRE VIENNA VIYANA PRAGUE
WARSAW BUDAPEST HELSINKI OSLO COPENHAGEN KOPENHAG BRUSSELS BRUKSEL LISBON LIZBON ATHENS ATINA SINGAPORE SINGAPUR
DUBAI TOKYO SEOUL SEATTLE NY CA WA CUPERTINO REDMOND SUNNYVALE AUSTIN BOSTON CHICAGO MIAMI TORONTO VANCOUVER SYDNEY MELBOURNE
TURKIYE TURKEY IRELAND IRLANDA INGILTERE ENGLAND NETHERLANDS HOLLANDA GERMANY
ALMANYA FRANCE FRANSA SPAIN ISPANYA ITALY ITALYA SWEDEN ISVEC CYPRUS KIBRIS KKTC SWITZERLAND ISVICRE AUSTRIA
AVUSTURYA BELGIUM BELCIKA USA AMERIKA ABD CANADA KANADA
TR TUR TRY TRK US GB UK IE NL LU DE FR ES IT SE CY CH AT BE DK FI NO PL PT GR HU CZ AE SG JP KR HK
"""


@functools.lru_cache(maxsize=1)
def words() -> frozenset[str]:
    from app.textnorm import normalize

    out = {normalize(p) for p in PROVINCES} | set(_DISTRICTS.split()) | set(_ABROAD.split())
    for p in PROVINCES:  # boşluklu yazılan il adının parçaları
        out |= set(normalize(p).split())
    return frozenset(out)


def is_location(text: str) -> bool:
    """Metnin bütün kelimeleri konum sözcüğü mü (sayılar ve noktalama yok sayılır): 'ISTANBUL TR', 'KADIKOY',
    'DUBLIN IE' → True; 'SPOTIFY ISTANBUL', 'ORNEK KITAP' → False. Boş metin True (sağ taraf yok sayılır)."""
    import re

    ws = [w for w in re.split(r"[\s/*.,\-#:]+", text or "") if w and not w.isdigit()]
    loc = words()
    return all(w in loc for w in ws)


def strip_location(text: str) -> str:
    """Sondaki konum sözcüklerini ve sayıları at: 'SPOTIFY ISTANBUL TR' → 'SPOTIFY', 'ORNEK KITAP 34' → 'ORNEK KITAP'.
    Hepsi konumsa boş döner."""
    ws = (text or "").split()
    loc = words()
    while ws and (ws[-1] in loc or ws[-1].isdigit()):
        ws.pop()
    return " ".join(ws)


@functools.lru_cache(maxsize=1)
def _prefixes() -> frozenset[str]:
    """Konum sözcüklerinin kesik başları ('TU', 'ISTANB')."""
    return frozenset(w[:i] for w in words() for i in range(1, len(w)))


def is_prefix(word: str) -> bool:
    """Kelime bir konum sözcüğünün kesik başı mı: 'TU' (TURKIYE), 'ISTANB' (ISTANBUL)."""
    return word.isalpha() and word in _prefixes()
