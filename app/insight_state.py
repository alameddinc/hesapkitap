"""Özet uyarılarının (finance.insights) kişiye özel kapatma kaydı.

Her uyarının kararlı bir anahtarı ("key") vardır: tür + kapsam, ör. "butce:market:2026-11", "dup:ornek-market:2026-06",
"kategorisiz:2026-11:3f2a9c1d". Kapsam koşulu taşır (dönem, ilgili kayıtların parmak izi, tutar): koşul değişince
anahtar değişir ve uyarı yeniden çıkar. Kapatma bu anahtarla, kişi başına saklanır (app/reminders.py ile aynı kalıp):
ortak defterde üyenin kapattığı sahibinkini kapatmaz.

Süzme Özet önbelleğinin (finance.summary @_memo) DIŞINDA yapılır: önbellek kişiden bağımsız kalır.
"""
import json
from datetime import date, timedelta

from app import db

KEEP_DAYS = 400   # bu kadar eski kapatma kayıtları temizlenir (kalıcı bir koşul yılda bir yeniden hatırlatılır)
MAX_KEYS = 500    # kayıt şişmesin: en eskiler düşer
KEY_MAX = 200


def _skey() -> str:
    u = db.current_user()
    return f"insights:dismissed:{u['id']}" if u else "insights:dismissed"


def _state() -> dict[str, str]:
    try:
        raw = json.loads(db.get_setting(_skey()) or "{}")
    except ValueError:
        return {}
    return {k: v for k, v in raw.items() if isinstance(k, str) and isinstance(v, str)} if isinstance(raw, dict) else {}


def _save(st: dict[str, str], now: date) -> None:
    cut = (now - timedelta(days=KEEP_DAYS)).isoformat()
    kept = sorted(((k, v) for k, v in st.items() if v >= cut), key=lambda kv: kv[1], reverse=True)[:MAX_KEYS]
    db.set_setting(_skey(), json.dumps(dict(kept), ensure_ascii=False))


def dismiss(key: str, now: date | None = None) -> dict:
    now = now or date.today()
    st = _state()
    st[key] = now.isoformat()
    _save(st, now)
    return {"ok": True, "key": key}


def restore(key: str, now: date | None = None) -> dict:
    """Kapatmayı geri al ("Geri al" bildirimi)."""
    now = now or date.today()
    st = _state()
    if st.pop(key, None) is not None:
        _save(st, now)
    return {"ok": True, "key": key}


def visible(items: list[dict]) -> list[dict]:
    """Kapatılmış uyarıları çıkar (anahtarı olmayan uyarı hep görünür)."""
    st = _state()
    return [i for i in items if not (i.get("key") and i["key"] in st)] if st else items


def hidden_count(items: list[dict]) -> int:
    st = _state()
    return sum(1 for i in items if i.get("key") and i["key"] in st)
