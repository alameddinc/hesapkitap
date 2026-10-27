"""Kişiselleştirme: hangi özellikler açık (defter bazında) ve görünüm tercihleri (kişi bazında: sade mod, Özet düzeni).

Özellik kapatmak bir gizlilik/erişim kuralı değil, sadeleştirmedir: menüden, Özet'ten ve uyarılardan kalkar; veri silinmez,
tekrar açınca her şey yerinde.
"""
import json

from app import db

# anahtar → (ad, açıklama). Özet, İşlemler, Ekstreler ve Ayarlar her zaman açık.
MODULES = {
    "plan": ("Planlama", "Gelecek aylar, sabit kalemler (maaş, kira), taksit yükü"),
    "istatistik": ("İstatistik", "Karşılaştırmalar, aylık grafikler, yıllık özet, faiz geçmişi"),
    "hesaplar": ("Hesaplar ve kumbara", "Banka bakiyeleri, net durum, altın/döviz, kumbaralar"),
    "tasarruf": ("Tasarruf", "Abonelikler, kısma hedefleri"),
    "zekat": ("Zekât ve Sadaka", "Nisap, kameri yıl, fitre, kurban, bağışlar"),
    "borclar": ("Borç / Alacak", "Kime ne borcun var, kimden ne alacağın var"),
    "transferler": ("Transferler", "Kime ne gönderdin, kimden ne geldi"),
    "fiyatlar": ("Fiyat takibi", "Fişlerden ürün fiyatları, en ucuz market"),
    "tablo": ("Tablo", "Filtreli liste, pivot, Excel"),
    "kurallar": ("Kategori kuralları", "Gelişmiş: işyeri → kategori kuralları"),
}
# uyarının yönlendirdiği sayfa → ait olduğu özellik (kapalıysa uyarı gösterilmez)
LINK_MODULE = {"zekat": "zekat", "plan": "plan", "tasarruf": "tasarruf", "borclar": "borclar", "transferler": "transferler",
               "hesaplar": "hesaplar", "istatistik": "istatistik", "fiyatlar": "fiyatlar"}


def modules() -> dict[str, bool]:
    try:
        saved = json.loads(db.get_setting("modules") or "{}")
    except ValueError:
        saved = {}
    return {k: bool(saved.get(k, True)) for k in MODULES}


def modules_chosen() -> bool:
    return db.get_setting("modules") is not None


def set_modules(data: dict) -> dict[str, bool]:
    m = {**modules(), **{k: bool(v) for k, v in data.items() if k in MODULES}}
    db.set_setting("modules", json.dumps(m))
    return m


def enabled(module: str) -> bool:
    return modules().get(module, True)


def _ui_key() -> str:
    u = db.current_user()
    return f"ui:{u['id']}" if u and u.get("id") else "ui:owner"


def ui() -> dict:
    """{simple: bool, layout: [bileşen, …] | None}"""
    try:
        return {"simple": False, "layout": None, **json.loads(db.get_setting(_ui_key()) or "{}")}
    except ValueError:
        return {"simple": False, "layout": None}


def set_ui(data: dict) -> dict:
    cur = ui()
    if "simple" in data:
        cur["simple"] = bool(data["simple"])
    if "layout" in data:
        lay = data["layout"]
        cur["layout"] = [str(x)[:40] for x in lay][:40] if isinstance(lay, list) else None
    db.set_setting(_ui_key(), json.dumps(cur, ensure_ascii=False))
    return cur
