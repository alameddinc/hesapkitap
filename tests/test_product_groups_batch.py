"""Ürün gruplama maliyeti: fiş anında çağrı yok (kuyruk), gecede bir kez toplu ve kısa yanıt, önbellekteki ad yeniden
sorulmaz, sert ₺ tavanı her yolda (toplu, liste, 07.11.2026'daki gibi elle başlatılan iş) durdurur ve yöneticiye
"sistem" push'u gider."""
from datetime import datetime

import pytest

from app import product_groups as pg
from tests import test_plans

hosted = test_plans.hosted

ENTRY = {"entry_date": "2026-11-01", "description": "A101", "amount": 40, "kind": "gider",
         "category": "Market & Gıda", "payment": "nakit"}


def _names(prompt: str) -> list[tuple[int, str]]:
    return [(int(ln.split(".")[0]), ln.split(". ", 1)[1]) for ln in prompt.splitlines()
            if ln[:1].isdigit() and ". " in ln]


@pytest.fixture()
def gemini(monkeypatch):
    """Sahte model: kısa biçimde yanıt verir, ai_calls'a token yazar (girdi ≈ karakter / 3, çıktı 1.800 — eski
    ortalama — ama maxOutputTokens'ı geçemez, gerçek model gibi)."""
    from app import ai_costs, receipt

    monkeypatch.setenv("GEMINI_API_KEY", "test")
    calls = []

    def fake(prompt, max_output_tokens=None, kind="kategori", model=None):
        names = _names(prompt)
        calls.append({"kind": kind, "model": model, "n": len(names), "max_out": max_output_tokens})
        ai_costs.record(None, kind, model, {"promptTokenCount": len(prompt) // 3,
                                            "candidatesTokenCount": min(1800, max_output_tokens)})
        return {"r": [[i, None] if "POSET" in n else [i, "Ornekyemek", "Hazır Yemekler"] if "QWX" in n
                      else [i, "Süt", "Süt"] for i, n in names]}, {}

    monkeypatch.setattr(receipt, "gemini_text_json", fake)
    return calls


@pytest.fixture()
def alerts(monkeypatch):
    from app import push

    sent = []
    monkeypatch.setattr(push, "system_alert", lambda pid, title, text: sent.append((pid, title, text)) or 1)
    return sent


# ---------------------------------------------------------------- kuyruk: fiş eklenince çağrı yok

def test_receipt_queues_without_calling_ai(client, gemini):
    r = client.post("/api/entries", json={**ENTRY, "items": [{"name": "ORNKZ QWX 500G", "qty": 1, "amount": 40}]})
    assert r.status_code == 200
    assert client.get("/api/prices").status_code == 200              # fiş kalemleri gruplanır (db.attach_groups)
    assert gemini == []                                               # anında çağrı yok
    assert pg.pending() == 1
    assert pg.group_for("ORNKZ QWX 500G", learn=False)["pending"] is True   # arayüzde "gruplanıyor"
    assert not hasattr(pg, "kick")                                    # anında çağrı yolu kaldırıldı


# ---------------------------------------------------------------- gece toplu: tek çağrıda çok ad, kısa yanıt

def test_nightly_job_groups_queue_in_one_short_call(gemini):
    from app import jobs, system

    pg.groups_for([{"name": f"ORNEKURUN{i} 1L"} for i in range(120)] + [{"name": "ORNKZ QWX 500G"},
                                                                       {"name": "POSET"}])
    assert pg.pending() == 122 and gemini == []
    now = datetime.now()
    assert jobs.run_global(now.replace(hour=1)) is None and gemini == []         # gece 4'ten önce değil
    jobs.run_global(now.replace(hour=5))
    assert len(gemini) == 1 and gemini[0]["n"] == 122                              # 122 ad tek çağrıda
    assert gemini[0]["kind"] == "grup" and gemini[0]["max_out"] == 64 + pg.OUT_TOKENS_PER_NAME * 122
    assert pg.pending() == 0
    assert pg.group_for("ORNEKURUN7 1L") == {"key": "sut|1l", "label": "Süt · 1 L", "category": "Süt ve Kahvaltılık",
                                             "subcategory": "Süt"}
    assert pg.group_for("ORNKZ QWX 500G") == {"key": "ornekyemek|500g", "label": "Ornekyemek · 500 g",
                                               "category": "Konserve ve Hazır Gıda", "subcategory": "Hazır Yemekler"}
    assert pg.group_for("POSET") is None
    with system.connect() as c:                                                    # ai_calls kaydı aynen (kind grup)
        assert c.execute("SELECT COUNT(*), MAX(output) FROM ai_calls WHERE kind = 'grup'").fetchone()[:] == (1, 1800)
    jobs.run_global(now.replace(hour=6))                                           # günde bir kez
    assert len(gemini) == 1


def test_short_answer_parsing():
    rows = pg._rows({"r": [[0, "Süt", "Süt"], [1, None], [2, "Silgi", "Kırtasiye"], [3, "Kalem", "Uydurma"],
                           [9, "Taşan"], ["x", "Bozuk"], [True, "Bool"]]}, 4)
    assert rows == {0: ("Süt", "Süt ve Kahvaltılık", "Süt"), 1: (None, None, None),
                    2: ("Silgi", "Kırtasiye", None), 3: ("Kalem", None, None)}
    assert pg._rows({"items": [{"i": 0, "g": "Süt", "c": "Süt ve Kahvaltılık", "s": "Süt"}]}, 1) == \
        {0: ("Süt", "Süt ve Kahvaltılık", "Süt")}                                 # eski biçim de okunur


# ---------------------------------------------------------------- önbellek: aynı ad yeniden sorulmaz

def test_cached_or_now_resolvable_names_are_not_asked(gemini):
    from app import system

    pg.groups_for([{"name": "ORNEKURUN 1L"}, {"name": "ORNEKIKI 1L"}, {"name": "ORNEKUC 1L"}])
    with system.connect() as c:
        # başka yoldan önbelleğe girdi (katalog / tutarlılık) ve bugün kuralın çözdüğü (sözlük büyüdü) ad
        pg._store(c, "ORNEKIKI 1 L", pg.rule_group("Süt 1L"), "catalog")
        c.execute("UPDATE product_group_queue SET name = 'Torku Süt 1L' WHERE name_norm = 'ORNEKUC 1 L'")
    assert pg.classify_pending() == 1
    assert len(gemini) == 1 and gemini[0]["n"] == 1                       # yalnız gerçekten bilinmeyen ad soruldu
    assert pg.pending() == 0
    # öğrenilen ad (yazılışı aynı ya da normalize adı aynı) bir daha kuyruğa girmez, sorulmaz
    pg.groups_for([{"name": "ORNEKURUN 1L"}, {"name": "ornekurun 1 l"}])
    assert pg.pending() == 0 and pg.classify_pending(max_calls=5) == 0 and len(gemini) == 1


# ---------------------------------------------------------------- sert maliyet tavanı

def test_cost_cap_stops_batch_and_alerts_admin(gemini, alerts, monkeypatch):
    from app import ai_costs, system

    monkeypatch.setattr(pg, "BATCH", 10)
    monkeypatch.setattr(pg, "AI_CALLS_PER_DAY", 10_000)
    ai_costs.set_settings(group_day=0.3, group_month=100)   # ≈ ₺0,04 / çağrı (10 ad) → birkaç çağrıda dolar
    pg.groups_for([{"name": f"ORNEKURUN{i} 1L"} for i in range(100)])
    pg.classify_pending(max_calls=50)
    k = len(gemini)
    assert 1 <= k < 10
    day, _month = ai_costs.spent_try("grup")
    assert day <= 0.3                                                    # tavan aşılmadı
    assert pg.pending() == 100 - 10 * k
    with system.connect() as c:                                          # durdurulan partinin denemesi sayılmadı
        assert c.execute("SELECT MAX(tries) FROM product_group_queue WHERE tries > 0").fetchone()[0] is None
    assert [a[0].split(":")[:3] for a in alerts] == [["sistem", "grup-tavan", "günlük"]]
    assert "durdu" in alerts[0][1]
    # aylık tavan da durdurur
    ai_costs.set_settings(group_day=100, group_month=0.3)
    assert pg.classify_pending(max_calls=5) == 0 and len(gemini) == k
    assert alerts[-1][0].startswith("sistem:grup-tavan:aylık:")
    # 0: yapay zekâyla gruplama kapalı
    ai_costs.set_settings(group_day=0, group_month=0)
    assert pg.classify_pending(max_calls=5) == 0 and len(gemini) == k


def test_cap_alert_reaches_admin_once_as_system_push(gemini, monkeypatch):
    from app import ai_costs, feedback, push

    sent = []
    monkeypatch.setattr(feedback, "admins", lambda: [{"id": 1}])
    monkeypatch.setattr(push, "notify", lambda uid, items, **k: sent.append((uid, items)) or items)
    ai_costs.set_settings(group_day=0)
    pg.groups_for([{"name": "ORNEKURUN 1L"}])
    pg.classify_pending()
    assert gemini == [] and len(sent) == 1
    uid, items = sent[0]
    assert uid == 1 and items[0]["kind"] == "sistem" and items[0]["route"] == push.R_SISTEM


def test_0711_style_manual_run_cannot_leak(gemini, alerts, monkeypatch):
    """07.11.2026: elle başlatılan iş modül sabitini ezdi (AI_CALLS_PER_DAY = 320, max_calls=300) → 316 çağrı. Artık
    tavan kayıtlı harcamadan (ai_calls) sayılır: sabit ezilse de varsayılan günlük tavanı geçemez."""
    from app import ai_costs, system

    assert ai_costs.group_caps() == (ai_costs.DEFAULT_GROUP_CAP_DAY_TRY, ai_costs.DEFAULT_GROUP_CAP_MONTH_TRY)
    # bugün başka çağrılarla tavana yaklaşılmış (₺4,90; varsayılan günlük tavan ₺5)
    ai_costs.record(None, "grup", ai_costs.LITE, {"promptTokenCount": 0,
                                                  "candidatesTokenCount": int(4.9 / 50 / 1.5 * 1_000_000)})
    monkeypatch.setattr(pg, "AI_CALLS_PER_DAY", 320)
    pg.groups_for([{"name": f"ORNEKURUN{i} 1L"} for i in range(400)])
    pg.classify_pending(max_calls=300)
    assert gemini == [] and pg.pending() == 400                                  # 300 çağrı değil, hiç
    assert ai_costs.spent_try("grup")[0] <= ai_costs.DEFAULT_GROUP_CAP_DAY_TRY
    assert alerts
    # liste yolu da (kişinin yazdığı ad, hemen) aynı tavana takılır: tavan tam dolmuş
    ai_costs.record(None, "grup", ai_costs.LITE, {"promptTokenCount": 0,
                                                  "candidatesTokenCount": int(0.2 / 50 / 1.5 * 1_000_000)})
    n = len(gemini)
    with system.connect() as c:
        c.execute("DELETE FROM product_group_queue")
    pg.enqueue(["ORNEKLISTE 1L"])
    assert pg.classify_names(["ORNEKLISTE 1L"], uid=None) == 0 and len(gemini) == n


def test_admin_sets_group_caps(hosted):
    from app import ai_costs

    r = hosted.put("/api/admin/ai-costs", json={"group_day_try": 2.5, "group_month_try": 40})
    assert r.status_code == 200 and r.json()["group_cap"] == {"day_try": 2.5, "month_try": 40.0,
                                                              "spent_day_try": 0.0, "spent_month_try": 0.0}
    assert ai_costs.group_caps() == (2.5, 40.0)
    assert hosted.put("/api/admin/ai-costs", json={"group_day_try": -1}).status_code == 422
    assert hosted.get("/api/admin/ai-costs").json()["group_cap"]["day_try"] == 2.5


def test_truncated_answer_is_still_counted(monkeypatch):
    """Yarım kalan / bozuk JSON yanıtı da ücretlidir: ai_calls'a yazılır ki tavan onu da saysın."""
    import httpx

    from app import receipt, system

    monkeypatch.setenv("GEMINI_API_KEY", "AIzaTEST0000")
    monkeypatch.setattr(receipt.httpx, "post", lambda *a, **k: httpx.Response(200, json={
        "candidates": [{"content": {"parts": [{"text": '{"r": [[0, "Süt", "Sü'}]}}],
        "usageMetadata": {"promptTokenCount": 2000, "candidatesTokenCount": 4864}}))
    pg.groups_for([{"name": "ORNEKURUN 1L"}])
    assert pg.classify_pending() == 0 and pg.pending() == 1                   # ad kuyrukta kalır
    with system.connect() as c:
        assert [tuple(r) for r in c.execute("SELECT kind, output FROM ai_calls")] == [("grup", 4864)]
