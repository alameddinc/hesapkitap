"""Sürüm kontrolü: bu kurulumun sürümü (git commit) ile GitHub'daki son sürüm.

Kurulumun sürümü: APP_VERSION / Render (RENDER_GIT_COMMIT) / Railway (RAILWAY_GIT_COMMIT_SHA) ortam değişkeni ya da
derleme sırasında yazılan app/VERSION dosyası. GitHub sorgusu 6 saatte bir yapılır; sadece herkese açık commit
bilgisi çekilir, bu kurulumdan hiçbir şey gönderilmez.
"""
import json
import os
import time
from pathlib import Path

from app import system

REPO = os.environ.get("HESAPKITAP_REPO", "alameddinc/hesapkitap")

# İstemci el sıkışması (S17): mobil/web eski sunucuya göre davranabilsin. API_VERSION, geriye uyumsuz değişiklikte
# artar; CAPABILITIES yalnız eklenir (bir yetenek adı kaldırılmaz, anlamı değişmez). İstemci bilmediği adı yok sayar.
API_VERSION = 2
CAPABILITIES = (
    "idempotency",            # Idempotency-Key başlığı: kopan yanıtta tek kayıt
    "email_verify",           # kayıtta e-posta kodu adımı (açık olup olmadığı: flags.email_verify_required)
    "sessions",               # /api/me/sessions: açık oturumlar, iptal
    "invites_v2",             # davet önizleme (GET /api/auth/invite/{kod}), yenile/iptal, ortak defter yetkisi
    "raw_note",               # işlem satırında raw (banka açıklaması) ve note_user
    "summary_left",           # /api/summary totals.left / left_after_savings / card_spend_incl_pending / fees_hidden
    "transactions_totals",    # /api/transactions satır bayrakları counts_as_* ve ?totals=1
    "transactions_paging",    # /api/transactions?limit&before → {rows, next}
    "payables_totals",        # /api/payables?totals=1 (window ?days, paid_this_month)
    "networth_explain",       # /api/net-worth spendable_parts / spendable_explain
    "transfer_totals",        # /api/transfers totals
    "bills_overall",          # /api/bills/summary overall, average_12
    "holdings_totals",        # /api/holdings totals
    "budgets_totals",         # /api/budgets totals
    "rules_test_all",         # /api/rules/test pattern'sız: tüm kurallar
    "debt_payment_delete",    # DELETE /api/debt-payments/{id}; pay yanıtında payment_id
    "integrity",              # Yapılacaklar kind 'dogruluk' (gece doğruluk denetimi)
    "verification",           # belge verification alanları, belge_dogrulanamadi, documents/{id}/confirm
    "inbox_documents",        # Gelen kutusu kind dokum|ekstre, /api/inbox/{id}/import ve /file
)


def handshake(with_version: bool = False) -> dict:
    """{api_version, capabilities, flags[, version]}. flags: sunucuda o an açık olan davranışlar."""
    from app import email_verify

    out = {"api_version": API_VERSION, "capabilities": list(CAPABILITIES),
           "flags": {"email_verify_required": email_verify.required(),
                     "hold_unverified": os.environ.get("HESAPKITAP_HOLD_UNVERIFIED", "0").strip() == "1"}}
    if with_version:
        out["version"] = current()
    return out
TTL = 6 * 3600


def current() -> str | None:
    for k in ("APP_VERSION", "RENDER_GIT_COMMIT", "RAILWAY_GIT_COMMIT_SHA"):
        if v := os.environ.get(k, "").strip():
            return v
    f = Path(__file__).with_name("VERSION")
    if f.exists() and (v := f.read_text().strip()):
        return v
    return None


def platform() -> str:
    if os.environ.get("RENDER"):
        return "render"
    if os.environ.get("RAILWAY_ENVIRONMENT") or os.environ.get("RAILWAY_GIT_COMMIT_SHA"):
        return "railway"
    if os.environ.get("FLY_APP_NAME"):
        return "fly"
    return "docker"


def _get(url: str):
    import httpx

    return httpx.get(url, timeout=8, headers={"Accept": "application/vnd.github+json"}).json()


def check(force: bool = False) -> dict:
    cur = current()
    cached = json.loads(system.get("version_check") or "null")
    if cached and not force and cached.get("current") == cur and time.time() - cached["checked"] < TTL:
        return cached
    out = {"current": cur, "checked": time.time(), "latest": None, "behind": None, "changes": [], "platform": platform()}
    try:
        head = _get(f"https://api.github.com/repos/{REPO}/commits/main")
        out["latest"] = head["sha"]
        out["latest_date"] = head["commit"]["committer"]["date"]
        if cur and not head["sha"].startswith(cur[:7]):
            cmp = _get(f"https://api.github.com/repos/{REPO}/compare/{cur}...main")
            out["behind"] = cmp.get("ahead_by")
            out["changes"] = [c["commit"]["message"].splitlines()[0][:100] for c in reversed(cmp.get("commits") or [])][:8]
        elif cur:
            out["behind"] = 0
    except Exception:  # noqa: BLE001 - GitHub'a ulaşılamazsa sessizce geç
        out["error"] = "GitHub'a ulaşılamadı"
    system.put("version_check", json.dumps(out))
    return out
