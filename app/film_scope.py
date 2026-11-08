"""Reklam filmi hesapları (app/film_seed.py) için kapalı fiyat dünyası: uydurma zincirler ve onların uydurma fiyatları
yalnız o hesaplara görünür, gerçek kullanıcılar hiç görmez.

Neden: ortak defter demoda kapalı; film için hazır sürümde iki gerçek test hesabı (Aile paketi) doldurulur. Fiyat
ekranları (Önerimiz, piyasa bandı, fiş piyasa notu, Nereye gideyim?, tasarruf) piyasa verisi ister; gerçek zincir adının
yanına uydurma fiyat koymak karşılaştırmalı reklam sayılabilir, gerçek kullanıcının Nereden alınır listesine uydurma
zincir düşmesi de yanlış olur. Bu yüzden:

- Film verisi yalnız community_prices satırıdır, katkı kimliği CONTRIB_PREFIX ("film-") ile başlar (gerçek katkı kimliği
  24 haneli onaltılık HMAC'tir, bu önekle başlayamaz). official_prices / official_daily'ye HİÇ yazılmaz.
- Film hesabı (sys_settings KEY'deki kullanıcı id'leri) yalnız bu satırları görür: resmî kaynak (Market Fiyatı) ona
  kapalıdır (price_sources.enabled_names), topluluk sorguları yalnız "film-" satırlarını okur (community_clause).
- Herkes (demo dahil) "film-" satırlarını hiç görmez: aynı süzgeç tersine; dışa aktarım (app/community_export.py) da
  atlar. Film hesabının kendi fişleri topluluğa katılmaz (community_prices.contribute).
- CHAINS ("Market A" … "Market E") film hesabında bilinen market zinciri sayılır (price_advice._market_chain): Önerimiz
  ve piyasa karşılaştırması açılır. Başka hiçbir kullanıcıda bu adlar zincir değildir.
"""
import json
import os
import time

KEY = "film_seed:user_ids"     # sys_settings: [sahip id, üye id] (JSON); yalnız app/film_seed.py yazar
INFO_KEY = "film_seed:info"    # sys_settings: {owner, member, emails, created_at, plan_until} — kaldırırken kanıt
CONTRIB_PREFIX = "film-"
CHAINS = ("Market A", "Market B", "Market C", "Market D", "Market E")
_TTL = 30.0
_cache: dict[str, tuple[float, frozenset[int]]] = {}


def forget() -> None:
    """Önbelleği boşalt (tohumlama / silme sonrası; testler)."""
    _cache.clear()


def user_ids() -> frozenset[int]:
    """Film hesaplarının id'leri (sistem veritabanı başına ~30 sn önbellek)."""
    from app import system

    path = os.path.abspath(system._path())
    now = time.monotonic()
    hit = _cache.get(path)
    if hit and now - hit[0] < _TTL:
        return hit[1]
    try:
        raw = json.loads(system.get(KEY) or "[]")
        ids = frozenset(int(x) for x in raw if str(x).isdigit())
    except (ValueError, TypeError):
        ids = frozenset()
    _cache[path] = (now, ids)
    return ids


def is_film_user(uid: int | None = None) -> bool:
    """uid verilmezse isteği yapan kullanıcı (db.current_user)."""
    if uid is None:
        from app import db

        uid = (db.current_user() or {}).get("id")
    return bool(uid) and int(uid) in user_ids()


def community_clause() -> str:
    """community_prices sorgusuna eklenecek süzgeç (" AND …"): film hesabı yalnız film satırlarını, herkes geri kalanını
    görür. Öneki sabit (kullanıcı girdisi yok)."""
    op = "LIKE" if is_film_user() else "NOT LIKE"
    return f" AND contributor {op} '{CONTRIB_PREFIX}%'"


def market_chain(*names: str | None) -> str | None:
    """Film hesabında uydurma zincirin görünen adı ("market a" → "Market A"); başka herkes için None."""
    if not is_film_user():
        return None
    for n in names:
        s = " ".join((n or "").split()).casefold()
        for ch in CHAINS:
            if s == ch.casefold():
                return ch
    return None
