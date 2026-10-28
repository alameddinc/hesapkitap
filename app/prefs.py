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


# Barındırılan sürümde açık rıza isteyen özellikler (KVKK özel nitelikli veri): varsayılan kapalı, açarken onay.
CONSENT = {"zekat": "Zekât, sadaka, fitre ve kurban kayıtların dini inancına dair bilgi verebilir (özel nitelikli kişisel "
                    "veri). Bu bilgiler yalnızca senin hesabında, hesaplama ve hatırlatma için işlenir; kimseyle paylaşılmaz, "
                    "anonim istatistiklere girmez. Özelliği kapatınca rızan geri alınmış olur."}
CONSENT_VERSION = "1"


def _consent_needed() -> set[str]:
    from app import plans

    return set(CONSENT) if plans.hosted() else set()


def modules() -> dict[str, bool]:
    try:
        saved = json.loads(db.get_setting("modules") or "{}")
    except ValueError:
        saved = {}
    need = _consent_needed()
    return {k: bool(saved.get(k, k not in need)) for k in MODULES}


class ConsentRequired(Exception):
    pass


def modules_chosen() -> bool:
    return db.get_setting("modules") is not None


def set_modules(data: dict, consent: list[str] | None = None) -> dict[str, bool]:
    from datetime import datetime, timezone

    before = modules()
    m = {**before, **{k: bool(v) for k, v in data.items() if k in MODULES}}
    for k in _consent_needed():
        if m[k] and not before[k]:
            if k not in (consent or []):
                raise ConsentRequired(k)
            u = db.current_user() or {}
            db.set_setting(f"consent:{k}", json.dumps({"at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                                                      "version": CONSENT_VERSION, "user": u.get("id"), "text": CONSENT[k]},
                                                     ensure_ascii=False))
        elif before[k] and not m[k]:
            db.set_setting(f"consent:{k}", None)  # rıza geri alındı
    db.set_setting("modules", json.dumps(m))
    return m


def enabled(module: str) -> bool:
    return modules().get(module, True)


def _ui_key() -> str:
    u = db.current_user()
    return f"ui:{u['id']}" if u and u.get("id") else "ui:owner"


def ui() -> dict:
    """{simple, layout: web Özet düzeni, layout_mobile: mobil uygulamanın Özet düzeni (ayrı: biri öbürünü ezmesin)}"""
    base = {"simple": False, "layout": None, "layout_mobile": None}
    try:
        return {**base, **json.loads(db.get_setting(_ui_key()) or "{}")}
    except ValueError:
        return base


def set_ui(data: dict) -> dict:
    cur = ui()
    if "simple" in data:
        cur["simple"] = bool(data["simple"])
    for k in ("layout", "layout_mobile"):
        if k in data:
            lay = data[k]
            cur[k] = [str(x)[:40] for x in lay][:40] if isinstance(lay, list) else None
    db.set_setting(_ui_key(), json.dumps(cur, ensure_ascii=False))
    return cur
