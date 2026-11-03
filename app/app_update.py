"""Mobil uygulama güncelleme yönlendirmesi (yetenek app_update): platform başına son sürüm, en düşük sürüm, mağaza adresi.

Değerler önce yöneticinin ayarından (system.db, web Ayarlar → Uygulama sürümü), yoksa ortam değişkeninden okunur:
HESAPKITAP_APP_IOS_LATEST, HESAPKITAP_APP_IOS_MIN, HESAPKITAP_APP_IOS_STORE_URL ve aynıları ANDROID için.
Varsayılan boş = özellik kapalı (platform yanıtta yer almaz). İstemci:
- sürüm < min    → "Güncelleme gerekli" (uygulama kullanılamaz)
- sürüm < latest → kapatılabilir "Yeni sürüm var" kartı
"""
import json
import os
import re
import time

from app import system

PLATFORMS = ("ios", "android")
FIELDS = ("latest", "min", "store_url")
KEY = "app_update"  # sys_settings: {"ios": {"latest": "1.0.1", ...}, ...} — yalnız yöneticinin girdiği alanlar
# resmî uygulamanın mağaza sayfaları (kendi sunucusunu kuran da aynı uygulamayı kullanır)
DEFAULT_STORE = {"ios": "https://apps.apple.com/app/id6828734294",
                 "android": "https://play.google.com/store/apps/details?id=com.alameddinc.hesapkitap"}
_VER = re.compile(r"^\d{1,4}(\.\d{1,4}){0,2}$")
_TTL = 10.0
_cache: tuple[float, dict, str] | None = None  # (zaman, ayar, system.db yolu)


def parse(v: str | None) -> tuple[int, int, int] | None:
    """'1.2' → (1, 2, 0). Geçersiz/boş: None."""
    v = (v or "").strip()
    if not _VER.match(v):
        return None
    parts = [int(x) for x in v.split(".")]
    return tuple(parts + [0] * (3 - len(parts)))  # type: ignore[return-value]


def _overrides() -> dict:
    global _cache
    now, where = time.monotonic(), system._path()
    if _cache and now - _cache[0] < _TTL and _cache[2] == where:
        return _cache[1]
    try:
        raw = json.loads(system.get(KEY) or "{}")
    except ValueError:
        raw = {}
    ov = {p: {f: str(v) for f, v in (raw.get(p) or {}).items() if f in FIELDS and v}
          for p in PLATFORMS if isinstance(raw, dict) and isinstance(raw.get(p), dict)}
    _cache = (now, ov, where)
    return ov


def _env(p: str, f: str) -> str:
    return os.environ.get(f"HESAPKITAP_APP_{p.upper()}_{f.upper()}", "").strip()


def raw() -> dict:
    """Yönetici ekranı: {platform: {alan: {"value", "source": "ayar"|"ortam"|None}}}."""
    ov = _overrides()
    out = {}
    for p in PLATFORMS:
        out[p] = {}
        for f in FIELDS:
            if v := ov.get(p, {}).get(f):
                out[p][f] = {"value": v, "source": "ayar"}
            elif v := _env(p, f):
                out[p][f] = {"value": v, "source": "ortam"}
            else:
                out[p][f] = {"value": None, "source": None}
    return out


def config() -> dict:
    """İstemciye giden: yalnız latest ya da min'i geçerli olan platformlar. Boş {} = kapalı."""
    out = {}
    for p, fields in raw().items():
        latest, low = fields["latest"]["value"], fields["min"]["value"]
        latest = latest if parse(latest) else None
        low = low if parse(low) else None
        if not (latest or low):
            continue
        if latest and low and parse(low) > parse(latest):  # tutarsız ayar: en düşük sürüm son sürüm sayılır
            latest = low
        out[p] = {"latest": latest or low, "min": low, "store_url": fields["store_url"]["value"] or DEFAULT_STORE[p]}
    return out


def set_config(values: dict, reset: bool = False) -> dict:
    """Yönetici: {platform: {alan: değer}}. Boş değer alanı ayardan düşürür (ortam değişkeni varsa o geçerli olur).
    Geçersiz sürüm, min > latest ya da https olmayan adres ValueError."""
    global _cache
    ov = {} if reset else {p: dict(v) for p, v in _overrides().items()}
    for p, fields in (values or {}).items():
        if p not in PLATFORMS:
            raise ValueError(f"Bilinmeyen platform: {p}")
        cur = ov.setdefault(p, {})
        for f, v in (fields or {}).items():
            if f not in FIELDS:
                raise ValueError(f"Bilinmeyen alan: {f}")
            v = (v or "").strip()
            if not v:
                cur.pop(f, None)
                continue
            if f == "store_url":
                if not v.startswith("https://") or len(v) > 300:
                    raise ValueError("Mağaza adresi https:// ile başlamalı")
            elif not parse(v):
                raise ValueError(f"Geçersiz sürüm: {v} (ör. 1.0.1)")
            cur[f] = v
        lat, low = cur.get("latest"), cur.get("min")
        if lat and low and parse(low) > parse(lat):
            raise ValueError(f"{'iOS' if p == 'ios' else 'Android'}: en düşük sürüm son sürümden büyük olamaz")
    ov = {p: v for p, v in ov.items() if v}
    system.put(KEY, json.dumps(ov, sort_keys=True) if ov else None)
    _cache = None
    return config()
