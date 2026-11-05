"""Yapay zekâ maliyet ölçümü: her Gemini çağrısının token sayıları (system.db → ai_calls).

Kaydedilen: zaman, kullanıcı kimliği (yoksa NULL), tür (fis | fis_metin | fis_foto | belge | dokum | kategori), model,
girdi / çıktı / düşünme / önbellekli girdi token'ı. fis: fotoğraftan okunan fiş (telefon metni gelse de varsayılan budur; metin yalnız
yardımcı); fis_metin: telefonda çıkarılan metinden okunan fiş ve fis_foto: o metin tutarsız çıkınca fotoğrafla yapılan
yedek okuma — ikisi yalnız eski yol açıkken (HESAPKITAP_RECEIPT_TEXT_FIRST=1, app/receipt.py text_first) yazılır. Belgenin içeriği, istem ya da yanıt metni KAYDEDİLMEZ. Yönetici paneli bu ayın
özetini görür: tür başına çağrı ve okuma sayısı, ortalama token, tahmini maliyet (model fiyat tablosu ve dolar kuru
ayarlanabilir) ve en yoğun 10 kullanıcı (maskeli). users_month: kullanıcı başı aylık liste (çağrı, okuma, tahmini ₺,
paket, ai_alert_try eşiği, yönetici kısıtlaması — app/ai_restrictions.py). Kayıtlar 13 ay tutulur.
"""
import json
from datetime import date, datetime, timezone

KINDS = ("fis", "fis_metin", "fis_foto", "belge", "dokum", "kategori")
KIND_LABELS = {"fis": "Fiş", "fis_metin": "Fiş (telefonda okunan metinden)",
               "fis_foto": "Fiş (metin yetmedi, fotoğrafla yeniden)", "belge": "Belge (ekstre, fatura, posta)",
               "dokum": "Hesap dökümü", "kategori": "Kategori önerisi"}
# 1 milyon token başına ABD doları (Google faturasından ölçüldü, 1–2.11.2026). Düşünme (thinking) token'ı çıktı
# fiyatıyla ücretlenir. lite: ilk okuma (receipt.DEFAULT_MODEL); flash: yeniden okuma (receipt.REREAD_MODEL).
LITE, FLASH = "gemini-3.1-flash-lite", "gemini-3.5-flash"
DEFAULT_PRICES = {LITE: {"input": 0.25, "output": 1.50}, FLASH: {"input": 0.75, "output": 3.75}}
# Önbellekten okunan girdi token'ı (usageMetadata.cachedContentTokenCount; promptTokenCount'un içinde) girdi fiyatının
# bu oranıyla sayılır — Google fiyat sayfası 04.11.2026: lite $0,025 / $0,25, flash $0,075 / $0,75 (%10). Yönetici bir
# modele ayrıca "cached" fiyatı kaydederse o kullanılır.
CACHED_RATIO = 0.10
DEFAULT_USD_TRY = 50.0  # faturadan çıkan etkin kur ~49,7
PRICES_KEY, FX_KEY = "ai_prices", "ai_usd_try"
# kullanıcı başı aylık tahmini ₺ eşiği: yönetici listesinde kırmızı satır, "eşiği geçen N hesap" uyarısı
ALERT_KEY, DEFAULT_ALERT_TRY = "ai_alert_try", 50.0  # Premium net ~₺35–57/ay: zarar başlamadan görünsün
KEEP_MONTHS = 13


def record(uid: int | None, kind: str, model: str, usage: dict) -> None:
    from app import system

    now = datetime.now(timezone.utc)
    with system.connect() as c:
        c.execute("INSERT INTO ai_calls (at, month, user_id, kind, model, input, output, thoughts, cached) "
                  "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                  (now.isoformat(timespec="seconds"), date.today().strftime("%Y-%m"), uid,
                   kind if kind in KINDS else "belge", (model or "")[:60], int(usage.get("promptTokenCount") or 0),
                   int(usage.get("candidatesTokenCount") or 0), int(usage.get("thoughtsTokenCount") or 0),
                   int(usage.get("cachedContentTokenCount") or 0)))


def prune(months: int = KEEP_MONTHS) -> int:
    from app import finance, system

    cutoff = finance.add_months(date.today().strftime("%Y-%m"), -months)
    with system.connect() as c:
        return c.execute("DELETE FROM ai_calls WHERE month < ?", (cutoff,)).rowcount


def prices() -> dict:
    from app import system

    try:
        p = json.loads(system.get(PRICES_KEY) or "null")
    except ValueError:
        p = None
    # yönetici kaydı varsayılanın üstüne: eski kayıtta (yalnız lite) sonradan eklenen model fiyatsız kalmasın
    return {**DEFAULT_PRICES, **p} if isinstance(p, dict) and p else DEFAULT_PRICES


def alert_try() -> float:
    """Yönetici ayarı ai_alert_try: kullanıcı başı aylık tahmini ₺ eşiği (varsayılan 50)."""
    from app import system

    try:
        return float(system.get(ALERT_KEY) or DEFAULT_ALERT_TRY)
    except ValueError:
        return DEFAULT_ALERT_TRY


def usd_try() -> float:
    from app import system

    try:
        return float(system.get(FX_KEY) or DEFAULT_USD_TRY)
    except ValueError:
        return DEFAULT_USD_TRY


def set_settings(new_prices: dict | None = None, fx: float | None = None, alert: float | None = None) -> None:
    from app import system

    if new_prices is not None:
        clean = {}
        for model, v in new_prices.items():
            if not isinstance(v, dict):
                raise ValueError(f"{model}: girdi ve çıktı fiyatı gerekli")
            try:
                i, o = float(v["input"]), float(v["output"])
                ca = float(v["cached"]) if v.get("cached") is not None else None  # isteğe bağlı: önbellekli girdi
            except (KeyError, TypeError, ValueError) as e:
                raise ValueError(f"{model}: girdi ve çıktı fiyatı sayı olmalı") from e
            if not (0 <= i <= 1000 and 0 <= o <= 1000 and (ca is None or 0 <= ca <= 1000)) or not str(model).strip():
                raise ValueError(f"{model}: fiyat 0–1000 dolar aralığında olmalı")
            clean[str(model).strip()[:60]] = {"input": i, "output": o, **({"cached": ca} if ca is not None else {})}
        system.put(PRICES_KEY, json.dumps(clean) if clean else None)
    if fx is not None:
        if not 0 < fx < 10000:
            raise ValueError("Kur 0'dan büyük olmalı")
        system.put(FX_KEY, str(fx))
    if alert is not None:
        if not 0 < alert < 1_000_000:
            raise ValueError("Eşik 0'dan büyük olmalı")
        system.put(ALERT_KEY, str(alert))


def _price(model: str, table: dict) -> dict:
    if model in table:
        return table[model]
    # bilinmeyen model (ör. GEMINI_MODEL ile seçilmiş başka sürüm): en yakın aile — adında "flash-lite" → lite,
    # "flash" → flash; hiç eşleşmezse flash fiyatı (düşük tahmin etmesin)
    lite = "flash-lite" in (model or "").lower()
    for name, p in table.items():
        n = name.lower()
        if ("flash-lite" in n) if lite else ("flash" in n and "flash-lite" not in n):
            return p
    return table.get(FLASH) or DEFAULT_PRICES[FLASH]


def _cost(model: str, inp: int, out: int, th: int, table: dict, cached: int = 0) -> float:
    """cached: girdinin önbellekten okunan kısmı (inp'in içinde), indirimli fiyatla (p["cached"], yoksa girdi ×
    CACHED_RATIO)."""
    p = _price(model, table)
    cached = max(0, min(cached or 0, inp))
    pc = p.get("cached")
    pc = p["input"] * CACHED_RATIO if pc is None else pc
    return ((inp - cached) * p["input"] + cached * pc + (out + th) * p["output"]) / 1_000_000


def mask(u: dict | None, uid: int | None) -> str:
    """Kimlik/e-posta maskeli: "#12 · ay***@o***.com"."""
    if uid is None:
        return "Hesapsız (kendi sunucusu / sistem)"
    name = (u or {}).get("username") or ""
    if "@" in name:
        local, dom = name.split("@", 1)
        head, _, tld = dom.rpartition(".")
        name = f"{local[:2]}***@{head[:1]}***.{tld}" if head else f"{local[:2]}***@***"
    elif name:
        name = name[:2] + "***"
    return f"#{uid}" + (f" · {name}" if name else " · silinmiş")


def summary(month: str | None = None) -> dict:
    """Ayın özeti: tür başına çağrı, okuma (doc_reads sayaçları), ortalama token, tahmini maliyet; en yoğun 10 kullanıcı."""
    from app import system

    month = month or date.today().strftime("%Y-%m")
    table, fx = prices(), usd_try()
    with system.connect() as c:
        rows = [dict(r) for r in c.execute(
            "SELECT kind, model, user_id, COUNT(*) n, SUM(input) i, SUM(output) o, SUM(thoughts) t, SUM(cached) ca "
            "FROM ai_calls WHERE month = ? GROUP BY kind, model, user_id", (month,))]
        reads = {r["kind"]: r["n"] for r in c.execute(
            "SELECT kind, SUM(count) n FROM read_usage WHERE month = ? GROUP BY kind", (month,))}
    by_kind: dict[str, dict] = {k: {"kind": k, "label": KIND_LABELS[k], "calls": 0, "input": 0, "output": 0,
                                    "thoughts": 0, "cost_usd": 0.0} for k in KINDS}
    users: dict = {}
    for r in rows:
        k = by_kind.setdefault(r["kind"], {"kind": r["kind"], "label": r["kind"], "calls": 0, "input": 0, "output": 0,
                                           "thoughts": 0, "cost_usd": 0.0})
        cost = _cost(r["model"], r["i"] or 0, r["o"] or 0, r["t"] or 0, table, r["ca"] or 0)
        k["calls"] += r["n"]
        k["input"] += r["i"] or 0
        k["output"] += r["o"] or 0
        k["thoughts"] += r["t"] or 0
        k["cost_usd"] += cost
        uu = users.setdefault(r["user_id"], {"calls": 0, "cost_usd": 0.0})
        uu["calls"] += r["n"]
        uu["cost_usd"] += cost
    out_kinds = []
    for k in by_kind.values():
        n = k["calls"]
        out_kinds.append({"kind": k["kind"], "label": k["label"], "calls": n,
                          "reads": reads.get(k["kind"]) if k["kind"] in ("fis", "belge") else None,
                          "avg_input": round(k["input"] / n) if n else 0, "avg_output": round(k["output"] / n) if n else 0,
                          "avg_thoughts": round(k["thoughts"] / n) if n else 0,
                          "cost_usd": round(k["cost_usd"], 4), "cost_try": round(k["cost_usd"] * fx, 2)})
    top = sorted(users.items(), key=lambda x: (-x[1]["cost_usd"], -x[1]["calls"]))[:10]
    total = sum(k["cost_usd"] for k in by_kind.values())
    return {"month": month, "kinds": out_kinds, "calls": sum(k["calls"] for k in by_kind.values()),
            "cost_usd": round(total, 4), "cost_try": round(total * fx, 2),
            "top_users": [{"user": mask(system.user(uid) if uid is not None else None, uid), "calls": v["calls"],
                           "cost_usd": round(v["cost_usd"], 4), "cost_try": round(v["cost_usd"] * fx, 2)}
                          for uid, v in top],
            "prices": table, "usd_try": fx, "default_prices": DEFAULT_PRICES,
            "alert_try": (alert := alert_try()),
            "over_alert": sum(1 for uid, v in users.items() if uid is not None and v["cost_usd"] * fx > alert),
            "months": months()}


def months() -> list[str]:
    """Kaydı olan aylar (yeniden eskiye): yönetici ay seçicisi."""
    from app import system

    with system.connect() as c:
        return [r[0] for r in c.execute("SELECT DISTINCT month FROM ai_calls ORDER BY month DESC")]


def _reads_by_user(c, month: str) -> dict[int, int] | None:
    """Ayın okunan belge sayısı, kullanıcı başı (hazır sürüm sayacı ai_usage: aylık + ek paketten düşenler). Sayaç
    yapısı değişirse ya da kendi sunucusundaysa (orada ai_usage yapay zekâ çağrısıdır) None: liste okumasız gösterilir."""
    import sqlite3

    from app import plans

    if not plans.hosted():
        return None
    try:
        return {r[0]: int(r[1] or 0) for r in c.execute(
            "SELECT user_id, SUM(count) FROM ai_usage WHERE month = ? GROUP BY user_id", (month,))}
    except sqlite3.Error:
        return None


def users_month(month: str | None = None) -> dict:
    """Yönetici: ayın kullanıcı başı maliyeti, en pahalı üstte. Her satır: çağrı, okuma, tahmini $ / ₺, paket,
    eşiği geçti mi (ai_alert_try), yönetici kısıtlaması. E-posta maskeli (user) ve tam (username: yönetici panelinin
    kullanıcı listesindeki gibi; arayüz satıra dokununca gösterir)."""
    from app import ai_restrictions, plans, system

    month = month or date.today().strftime("%Y-%m")
    table, fx, alert = prices(), usd_try(), alert_try()
    with system.connect() as c:
        rows = [dict(r) for r in c.execute(
            "SELECT user_id, model, COUNT(*) n, SUM(input) i, SUM(output) o, SUM(thoughts) t, SUM(cached) ca "
            "FROM ai_calls WHERE month = ? GROUP BY user_id, model", (month,))]
        reads = _reads_by_user(c, month)
    agg: dict = {}
    for r in rows:
        a = agg.setdefault(r["user_id"], {"calls": 0, "cost_usd": 0.0})
        a["calls"] += r["n"]
        a["cost_usd"] += _cost(r["model"], r["i"] or 0, r["o"] or 0, r["t"] or 0, table, r["ca"] or 0)
    restr = ai_restrictions.all_rows()
    # okuması olup yapay zekâ çağrısı olmayan (yerel okuma) ve kısıtlı kullanıcılar da listede (kaldırılabilsin)
    for uid in [k for k, v in (reads or {}).items() if v] + list(restr):
        agg.setdefault(uid, {"calls": 0, "cost_usd": 0.0})
    out = []
    for uid, a in agg.items():
        u = system.user(uid) if uid is not None else None
        cost_try = round(a["cost_usd"] * fx, 2)
        out.append({"user_id": uid, "user": mask(u, uid), "username": (u or {}).get("username"),
                    "plan": plans.plan_of(u) if u else None, "calls": a["calls"],
                    "reads": None if reads is None or uid is None else reads.get(uid, 0),
                    "cost_usd": round(a["cost_usd"], 4), "cost_try": cost_try,
                    "over": uid is not None and a["cost_usd"] * fx > alert,
                    "restriction": restr.get(uid) if uid is not None else None})
    out.sort(key=lambda x: (-x["cost_usd"], -x["calls"], x["user_id"] or 0))
    return {"month": month, "users": out, "alert_try": alert, "over_alert": sum(1 for x in out if x["over"]),
            "usd_try": fx, "months": months()}
