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
