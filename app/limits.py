"""Belge okuma kaynak sınırları: tek istek ya da tek dosya bütün sunucuyu (bellek, CPU) düşüremesin.

Ortam değişkenleriyle değiştirilebilir; varsayılanlar gerçek ekstre/döküm/yedek boyutlarının çok üstünde:
    HESAPKITAP_MAX_FILES        bir yüklemede en fazla dosya (30)
    HESAPKITAP_MAX_FILE_MB      dosya başına (15; hazır sürümde nginx de 15 MB)
    HESAPKITAP_PDF_MAX_PAGES    PDF başına sayfa (300)
    HESAPKITAP_PDF_TIMEOUT      PDF metne çevirme süresi, saniye (90; sayfalar arasında denetlenir)
    HESAPKITAP_PARSE_WAIT       ağır okuma sırasında bekleme, saniye (120; dolarsa 503 "sunucu meşgul")
Zip (xlsx, yedek): açılmış toplam boyut ve sıkıştırma oranı (zip bombası) üye başlıklarından, açmadan denetlenir.
"""
import io
import os
import time
import zipfile


class LimitError(ValueError):
    """Kullanıcıya gösterilecek Türkçe neden."""


def _num(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, default))
    except ValueError:
        return default


def max_files() -> int:
    return int(_num("HESAPKITAP_MAX_FILES", 30))


def max_file_bytes() -> int:
    return int(_num("HESAPKITAP_MAX_FILE_MB", 15) * 1024 * 1024)


def pdf_max_pages() -> int:
    return int(_num("HESAPKITAP_PDF_MAX_PAGES", 300))


def pdf_timeout() -> float:
    return _num("HESAPKITAP_PDF_TIMEOUT", 90)


def parse_wait() -> float:
    return _num("HESAPKITAP_PARSE_WAIT", 120)


def check_count(n: int) -> None:
    if n > max_files():
        raise LimitError(f"Tek seferde en fazla {max_files()} dosya yüklenebilir ({n} seçildi); kalanını ayrıca yükle")


def check_size(data: bytes, name: str | None = None) -> None:
    if len(data) > max_file_bytes():
        mb = max_file_bytes() // (1024 * 1024)
        raise LimitError(f"{name + ': ' if name else ''}dosya çok büyük (en fazla {mb} MB)")


XLSX_MAX_TOTAL = 200 * 1024 * 1024    # banka dökümü xlsx'i açılmış hâliyle birkaç MB olur
BACKUP_MAX_TOTAL = 2 * 1024 ** 3      # yedek: veritabanı + fiş/fatura dosyaları
MAX_RATIO = 200                        # sıradan xlsx/sqlite 5–50 kat sıkışır; bomba binlerce kat
RATIO_FLOOR = 10 * 1024 * 1024         # küçük üyelerde oran anlamsız (boş sayfalar çok sıkışır)
MAX_MEMBERS = 20_000


def check_zip(data: bytes, max_total: int, what: str = "dosya") -> None:
    """Zip bombası: üye başlıklarındaki açılmış boyut toplamı ve oranı. zipfile okurken başlıktaki boyuttan fazlasını
    üretmez (fazlası 'Bad CRC'/'truncated' hatası), bu yüzden başlık denetimi yeterli."""
    try:
        z = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        return  # açılamıyorsa okuyucu kendi hatasını verir
    infos = z.infolist()
    if len(infos) > MAX_MEMBERS:
        raise LimitError(f"Bu {what} çok fazla parça içeriyor; bozuk ya da zararlı olabilir")
    total = 0
    for i in infos:
        total += i.file_size
        if i.file_size > RATIO_FLOOR and i.file_size > MAX_RATIO * max(i.compress_size, 1):
            raise LimitError(f"Bu {what} açıldığında olağan dışı büyüyor (sıkıştırma bombası olabilir); yüklenmedi")
    if total > max_total:
        raise LimitError(f"Bu {what} açıldığında {total // (1024 * 1024)} MB tutuyor (en fazla "
                         f"{max_total // (1024 * 1024)} MB); yüklenmedi")


class Deadline:
    """Sayfa sayfa işlenen belgede süre ve sayfa sınırı (iş parçacığı dışarıdan durdurulamaz; sayfa arası denetim)."""

    def __init__(self, seconds: float | None = None):
        self.end = time.monotonic() + (pdf_timeout() if seconds is None else seconds)

    def check(self) -> None:
        if time.monotonic() > self.end:
            raise LimitError("Belge çok uzun sürede okunuyor; daha küçük parçalara bölüp yükle")
