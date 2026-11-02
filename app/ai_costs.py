"""Yapay zekâ maliyet ölçümü: her Gemini çağrısının token sayıları (system.db → ai_calls).

Kaydedilen: zaman, kullanıcı kimliği (yoksa NULL), tür (fis | fis_metin | fis_foto | belge | dokum | kategori), model,
girdi / çıktı / düşünme token'ı. fis_metin: telefonda çıkarılan metinden okunan fiş; fis_foto: o metin tutarsız çıkınca
fotoğrafla yapılan yedek okuma. Belgenin içeriği, istem ya da yanıt metni KAYDEDİLMEZ. Yönetici paneli bu ayın
özetini görür: tür başına çağrı ve okuma sayısı, ortalama token, tahmini maliyet (model fiyat tablosu ve dolar kuru
ayarlanabilir) ve en yoğun 10 kullanıcı (maskeli). Kayıtlar 13 ay tutulur.
"""
import json
from datetime import date, datetime, timezone

KINDS = ("fis", "fis_metin", "fis_foto", "belge", "dokum", "kategori")
KIND_LABELS = {"fis": "Fiş", "fis_metin": "Fiş (telefonda okunan metinden)",
               "fis_foto": "Fiş (metin yetmedi, fotoğrafla yeniden)", "belge": "Belge (ekstre, fatura, posta)",
               "dokum": "Hesap dökümü", "kategori": "Kategori önerisi"}
# 1 milyon token başına ABD doları. Düşünme (thinking) token'ı çıktı fiyatıyla ücretlenir.
DEFAULT_PRICES = {"gemini-3.1-flash-lite": {"input": 0.25, "output": 1.50}}
DEFAULT_USD_TRY = 42.0
PRICES_KEY, FX_KEY = "ai_prices", "ai_usd_try"
KEEP_MONTHS = 13


def record(uid: int | None, kind: str, model: str, usage: dict) -> None:
    from app import system

    now = datetime.now(timezone.utc)
    with system.connect() as c:
        c.execute("INSERT INTO ai_calls (at, month, user_id, kind, model, input, output, thoughts) "
                  "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                  (now.isoformat(timespec="seconds"), date.today().strftime("%Y-%m"), uid,
                   kind if kind in KINDS else "belge", (model or "")[:60], int(usage.get("promptTokenCount") or 0),
                   int(usage.get("candidatesTokenCount") or 0), int(usage.get("thoughtsTokenCount") or 0)))


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
    return p if isinstance(p, dict) and p else DEFAULT_PRICES


def usd_try() -> float:
    from app import system

    try:
        return float(system.get(FX_KEY) or DEFAULT_USD_TRY)
    except ValueError:
        return DEFAULT_USD_TRY


def set_settings(new_prices: dict | None = None, fx: float | None = None) -> None:
    from app import system

    if new_prices is not None:
        clean = {}
        for model, v in new_prices.items():
            if not isinstance(v, dict):
                raise ValueError(f"{model}: girdi ve çıktı fiyatı gerekli")
            try:
                i, o = float(v["input"]), float(v["output"])
            except (KeyError, TypeError, ValueError) as e:
                raise ValueError(f"{model}: girdi ve çıktı fiyatı sayı olmalı") from e
            if not (0 <= i <= 1000 and 0 <= o <= 1000) or not str(model).strip():
                raise ValueError(f"{model}: fiyat 0–1000 dolar aralığında olmalı")
            clean[str(model).strip()[:60]] = {"input": i, "output": o}
        system.put(PRICES_KEY, json.dumps(clean) if clean else None)
    if fx is not None:
        if not 0 < fx < 10000:
            raise ValueError("Kur 0'dan büyük olmalı")
        system.put(FX_KEY, str(fx))


def _price(model: str, table: dict) -> dict:
    if model in table:
        return table[model]
    # bilinmeyen model: tablodaki ilk (varsayılan) fiyat — tahmini maliyet boş kalmasın
    return next(iter(table.values()))


def _cost(model: str, inp: int, out: int, th: int, table: dict) -> float:
    p = _price(model, table)
    return (inp * p["input"] + (out + th) * p["output"]) / 1_000_000


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
            "SELECT kind, model, user_id, COUNT(*) n, SUM(input) i, SUM(output) o, SUM(thoughts) t FROM ai_calls "
            "WHERE month = ? GROUP BY kind, model, user_id", (month,))]
        reads = {r["kind"]: r["n"] for r in c.execute(
            "SELECT kind, SUM(count) n FROM read_usage WHERE month = ? GROUP BY kind", (month,))}
    by_kind: dict[str, dict] = {k: {"kind": k, "label": KIND_LABELS[k], "calls": 0, "input": 0, "output": 0,
                                    "thoughts": 0, "cost_usd": 0.0} for k in KINDS}
    users: dict = {}
    for r in rows:
        k = by_kind.setdefault(r["kind"], {"kind": r["kind"], "label": r["kind"], "calls": 0, "input": 0, "output": 0,
                                           "thoughts": 0, "cost_usd": 0.0})
        cost = _cost(r["model"], r["i"] or 0, r["o"] or 0, r["t"] or 0, table)
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
            "prices": table, "usd_try": fx, "default_prices": DEFAULT_PRICES}
