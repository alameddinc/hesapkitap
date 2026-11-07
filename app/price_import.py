"""Resmî fiyat kaynağı içe aktarımı (KAPALI varsayılan): Market Fiyatı arama yanıtı biçimindeki JSON (dosya ya da adres)
→ official_prices tablosu (system.db). Topluluk fiyatlarından (community_prices) ayrı tutulur.

Biçim: {numberOfFound, content: [{id, title, brand, refinedVolumeOrWeight, categories[], main_category, menu_category,
productDepotInfoList: [{depotId, depotName, price, unitPrice, unitPriceValue, marketAdi, longitude, latitude, indexTime,
discount, discountRatio, promotionText}]}], facetMap}.
- marketAdi (carrefour, migros, a101, sok, bim, hakmar, tarim_kredi) → zincirin görünen adı (CHAINS); bilinmeyen atlanır.
- title + brand (+ adda boy yoksa refinedVolumeOrWeight) → ürün grubu (app/product_groups.py; marka atılır). Grubu henüz
  çözülemeyen (yapay zekâ kuyruğunda) ürün atlanır; ad kuyruğa girer, sonraki içe aktarımda gruplanır.
- Satır: (zincir, grup, ürün adı, marka, fiyat, birim fiyat, gün, şube no, indirim). Konum, şube adı saklanmaz.

Gösterim: price_sources "resmi" kaynağı; yalnız HESAPKITAP_PRICE_SOURCES "resmi" içerir VE HESAPKITAP_OFFICIAL_PRICES_URL
ya da HESAPKITAP_OFFICIAL_PRICES_FILE ayarlıysa etkin. Teklif "attribution" alanıyla gelir; istemci kaynağı yazar.
Kullanım (zamanlayıcı yok): python -m app.price_import <dosya|https://…>  (argümansız: ortamdaki adres / dosya)
"""
import json
import logging
import os
import re
import sys
import urllib.request
from datetime import date, timedelta

log = logging.getLogger("hesapkitap.price_import")

ATTRIBUTION = "Kaynak: Market Fiyatı (T.C. Sanayi ve Teknoloji Bakanlığı / TÜBİTAK BİLGEM)"
DEMO_ATTRIBUTION = "Örnek veri: demodaki fiyatlar uydurmadır, gerçek market fiyatı değildir"


def attribution() -> str:
    """Teklifin / piyasa bandının kaynak yazısı; demoda (uydurma veri, app/demo_prices.py) DEMO_ATTRIBUTION."""
    from app import demo_mode

    return DEMO_ATTRIBUTION if demo_mode.enabled() else ATTRIBUTION
CHAIN_MAP = {"carrefour": "CarrefourSA", "carrefoursa": "CarrefourSA", "migros": "Migros", "a101": "A101",
             "sok": "ŞOK", "bim": "BİM", "hakmar": "Hakmar", "tarim_kredi": "Tarım Kredi", "tarimkredi": "Tarım Kredi"}
KEEP_DAYS = int(os.environ.get("HESAPKITAP_OFFICIAL_KEEP_DAYS") or 35)  # sunucuda kaç gün tutulur
MAX_BYTES = 64 * 1024 * 1024
URL_ENV, FILE_ENV = "HESAPKITAP_OFFICIAL_PRICES_URL", "HESAPKITAP_OFFICIAL_PRICES_FILE"


def configured() -> str | None:
    """Ortamdaki kaynak (adres önce); yoksa None."""
    return (os.environ.get(URL_ENV) or os.environ.get(FILE_ENV) or "").strip() or None


def load(src: str) -> dict:
    """Dosya yolu ya da http(s) adresi → JSON (en çok MAX_BYTES)."""
    if re.match(r"^https?://", src):
        req = urllib.request.Request(src, headers={"Accept": "application/json", "User-Agent": "HesapKitap/price-import"})
        with urllib.request.urlopen(req, timeout=60) as r:  # noqa: S310 - yönetici verdiği adres
            raw = r.read(MAX_BYTES + 1)
    else:
        with open(src, "rb") as f:
            raw = f.read(MAX_BYTES + 1)
    if len(raw) > MAX_BYTES:
        raise ValueError("fiyat dosyası çok büyük")
    return json.loads(raw)


def _day(v, today: date) -> str:
    """indexTime → YYYY-MM-DD ("2026-11-07T10:00:00", "07.11.2026 10:00"); okunamazsa bugün."""
    s = str(v or "")
    if (m := re.match(r"(\d{4})-(\d{2})-(\d{2})", s)):
        return f"{m[1]}-{m[2]}-{m[3]}"
    if (m := re.match(r"(\d{2})\.(\d{2})\.(\d{4})", s)):
        return f"{m[3]}-{m[2]}-{m[1]}"
    return today.isoformat()


def _num(v) -> float | None:
    if isinstance(v, bool) or v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    try:
        return float(str(v).replace(".", "").replace(",", ".") if "," in str(v) else str(v))
    except ValueError:
        return None


def full_title(title, volume) -> str:
    """Ürün adı; adda boy yoksa kaynağın boyu eklenir ("Örnek Süt" + "1 L" → "Örnek Süt 1 L"). En çok 120 karakter."""
    from app import product_groups as pg

    t = str(title or "").strip()[:120]
    if volume and not pg.parse_size(pg.name_norm(t).split())[0]:
        t = f"{t} {str(volume).strip()}"[:120]
    return t


def group_names(names: list[dict], chunk: int = 500) -> list[dict | None]:
    """[{name, brand}] → ürün grupları (product_groups.groups_for; çözülemeyen ad yapay zekâ kuyruğuna girer)."""
    from app import product_groups as pg

    out: list[dict | None] = []
    for i in range(0, len(names), chunk):
        out += pg.groups_for(names[i:i + chunk])
    return out


def rows_from(data: dict, today: date | None = None) -> tuple[list[tuple], dict]:
    """Yanıt → official_prices satırları ve özet {products, matched, rows, skipped_chain, unmatched}."""
    today = today or date.today()
    products = [p for p in (data or {}).get("content") or [] if isinstance(p, dict) and p.get("title")]
    names = [{"name": full_title(p["title"], p.get("refinedVolumeOrWeight")), "brand": p.get("brand") or None}
             for p in products]
    groups = group_names(names)
    out, stats = [], {"products": len(products), "matched": 0, "rows": 0, "skipped_chain": 0, "unmatched": 0}
    for p, n, g in zip(products, names, groups):
        if not g or g.get("pending"):
            stats["unmatched"] += 1
            continue
        stats["matched"] += 1
        for d in p.get("productDepotInfoList") or []:
            if not isinstance(d, dict):
                continue
            chain = CHAIN_MAP.get(str(d.get("marketAdi") or "").strip().lower())
            price = _num(d.get("price"))
            if not chain:
                stats["skipped_chain"] += 1
                continue
            if not price or price <= 0:
                continue
            out.append((chain, g["key"], n["name"][:120], (p.get("brand") or None), round(price, 2),
                        _num(d.get("unitPriceValue")), _day(d.get("indexTime"), today), str(d.get("depotId") or "")[:40],
                        1 if d.get("discount") else 0))
    stats["rows"] = len(out)
    return out, stats


def import_data(data: dict, today: date | None = None) -> dict:
    """İçe aktar (aynı zincir / şube / ürün / gün yeniden gelirse üzerine yazılır); KEEP_DAYS'ten eskiler silinir."""
    from app import product_groups, system

    rows, stats = rows_from(data, today)
    cut = ((today or date.today()) - timedelta(days=KEEP_DAYS)).isoformat()
    with system.connect() as c:
        c.executemany("INSERT OR REPLACE INTO official_prices (chain, group_key, title, brand, price, unit_price, day, "
                      "depot_id, discount) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)", rows)
        c.execute("DELETE FROM official_prices WHERE day < ?", (cut,))
    product_groups.kick()  # çözülemeyen adlar: arka planda yapay zekâ (açıksa)
    return stats


def main(argv: list[str]) -> int:
    logging.basicConfig(level=logging.INFO)
    src = argv[1] if len(argv) > 1 else configured()
    if not src:
        print(f"kullanım: python -m app.price_import <dosya|adres>  (ya da {URL_ENV} / {FILE_ENV})", file=sys.stderr)
        return 2
    stats = import_data(load(src))
    print(json.dumps(stats, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
