import json

from app import templates

HALK = """HESAP ÖZETİ
IBAN: TR12 0001 2009 9990 0001 2345 67
Hesap Hareketleri
İşlem Tarihi Tutar Bakiye Açıklama Dekont No
01-10-2026 10.000,00 15.000,00 Maaş ödemesi
M1-2
05-10-2026 -2.500,00 12.500,00 FAST - KİRA
Ali Veli
10-10-2026 -500,00 12.000,00 Market
11-10-2026 1.250,50 13.250,50 İade
"""


def test_learn_apply_and_reuse():
    assert templates.bank_of(HALK) == "Halkbank"
    spec, res = templates.learn(HALK, "Halkbank")
    assert res["verified"] and len(res["rows"]) == 4 and res["rows"][1]["amount"] == -2500
    assert "Ali Veli" in res["rows"][1]["description"]  # devam satırı açıklamaya eklenir
    # şablon belgeden metin taşımaz: sadece sabit sözlük kelimeleri, rakam yok
    blob = json.dumps(spec, ensure_ascii=False)
    assert "Ali" not in blob and "TR12" not in blob and not any(ch.isdigit() for ch in json.dumps(spec["keywords"], ensure_ascii=False))
    # ilk okumada öğrenilip kaydedilir, ikincide kayıtlı şablon kullanılır
    assert templates.read(HALK)["verified"]
    t = templates.all_templates()
    assert len(t) == 1 and t[0]["bank"] == "Halkbank"
    templates.read(HALK)
    assert templates.all_templates()[0]["uses"] == 1


def test_unverifiable_is_rejected():
    broken = HALK.replace("12.500,00", "99.999,99")  # bakiye zinciri bozuk
    assert templates.learn(broken, "Halkbank") is None
    assert templates.read(broken) is None and templates.all_templates() == []


def test_account_pdf_uses_template(monkeypatch):
    from app import bank_pdf

    monkeypatch.setattr(bank_pdf, "extract_text", lambda data, pw=None: HALK)
    res = bank_pdf.parse_account_pdf(b"%PDF")
    assert res["verified"] and res["bank"] == "Halkbank" and res.get("template")


def test_hub_submission_and_review(client, monkeypatch):
    monkeypatch.setenv("HESAPKITAP_IS_HUB", "1")
    spec, _ = templates.learn(HALK, "Halkbank")
    assert client.post("/api/templates/submit", json={"spec": spec}).status_code == 200
    assert client.post("/api/templates/submit", json={"spec": spec}).status_code == 200  # aynı şablon: sayaç
    bad = {**spec, "keywords": ["AYŞE YILMAZ"]}
    assert client.post("/api/templates/submit", json={"spec": bad}).status_code == 422  # sözlük dışı metin
    assert client.post("/api/templates/submit", json={"spec": {**spec, "x": "kod"}}).status_code == 422
    subs = client.get("/api/templates").json()["submissions"]
    assert len(subs) == 1 and subs[0]["count"] == 2 and subs[0]["status"] == "bekliyor"
    assert client.post(f"/api/templates/submissions/{subs[0]['id']}/onaylandi").status_code == 200


def test_submit_closed_on_non_hub(client):
    assert client.post("/api/templates/submit", json={"spec": {}}).status_code == 404


def test_sharing_toggle_and_share_marks(client, monkeypatch):
    monkeypatch.setenv("HESAPKITAP_IS_HUB", "1")
    assert client.get("/api/templates").json()["sharing"] is True  # varsayılan açık
    spec, _ = templates.learn(HALK, "Halkbank")
    templates.save(spec)
    assert templates.share(spec) and templates.all_templates()[0]["shared_at"]
    client.put("/api/templates/sharing", json={"on": False})
    assert templates.share(spec) is False


def test_community_sync(monkeypatch):
    import importlib

    import httpx

    mod = importlib.reload(templates)  # conftest'teki sahte sync'i kaldır
    spec, _ = mod.learn(HALK, "Halkbank")

    class R:
        def __init__(self, data):
            self._d = data

        def json(self):
            return self._d

    def fake_get(url, **kw):
        if url.endswith("/templates"):
            return R([{"name": "halkbank.json", "download_url": "https://x/halkbank.json"}, {"name": "README.md"}])
        return R([spec, {"kind": "account", "keywords": ["kötü"], "bank": "Halkbank"}])

    monkeypatch.setattr(httpx, "get", fake_get)
    assert mod.sync_community(force=True) == 1
    assert [t["source"] for t in mod.all_templates()] == ["community"]
    assert mod.sync_community() == 0  # günde bir kez
