"""Alacakta da borçla aynı yapı (yetenek debt_terms_receivable): kimden (Kişi · Kurum / firma · Diğer), nasıl geri
ödenecek (parça / tek / taksitli / bilmiyorum), tarih türü (boşsa söz) ve ödeme planı (taksitler yalnız kaydedilir,
günü gelince nötr hatırlatma). Borç planı alacağı almaz. Kişiler ve tutarlar uydurmadır."""
import re
from datetime import date
from pathlib import Path

from tests.test_debt_plan import _all_strings, _banned_in
from tests.test_debt_schedule import _restructure_in

TODAY = date(2026, 11, 10)
PLAN = {"plan_installment": 1000, "plan_count": 3, "plan_start": "2026-11", "plan_day": 12}


def _debt(client, did):
    return next(d for d in client.get("/api/debts").json() if d["id"] == did)


def _alacak(client, **body):
    r = client.post("/api/debts", json={"direction": "alacak", "debt_date": "2026-10-01", **body})
    assert r.status_code == 200, r.text
    return r.json()["id"]


def test_capability_listed(client):
    assert "debt_terms_receivable" in client.get("/api/meta").json()["capabilities"]


def test_receivable_terms_create_list_and_defaults(client):
    # kimden kurum, tek seferde, kesin tarih
    a = _alacak(client, person="Örnek Firma", amount=4000, due_date="2026-12-20", creditor_type="kurum",
                pay_terms="tek", due_strict=True)
    d = _debt(client, a)
    assert d["creditor_type"] == "kurum" and d["creditor_label"] == "Kurum / firma"
    assert d["pay_terms"] == "tek" and d["terms"] == "tek" and d["terms_label"] == "Tek seferde"
    assert d["due_strict"] is True and d["strict"] is True and d["schedule"] is None
    # varsayılanlar: kişi, parça parça, söz (kurumda da tarih türü boşsa söz: alacakta kesin varsayılmaz)
    b = _alacak(client, person="Ahmet", amount=600, due_date="2026-11-30", creditor_type="kisi", pay_terms="parca")
    d = _debt(client, b)
    assert d["terms_label"] == "Parça parça" and d["strict"] is False and d["creditor_label"] == "Kişi"
    c = _alacak(client, person="Örnek Firma 2", amount=600, due_date="2026-11-30", creditor_type="kurum")
    assert _debt(client, c)["strict"] is False and _debt(client, c)["terms"] == "bilinmiyor"
    # alacakta vergi / banka / mağaza anlamsız: kurum olur; borçta "kurum" diğer olur
    e = _alacak(client, person="Örnek Banka", amount=100, creditor_type="banka")
    assert _debt(client, e)["creditor_type"] == "kurum"
    f = client.post("/api/debts", json={"person": "Örnek Kurum", "direction": "borc", "amount": 100,
                                        "creditor_type": "kurum"}).json()["id"]
    assert _debt(client, f)["creditor_type"] == "diger"
    # koşullar düzenlenir; yön değişince tür çevrilir (alacakta kurum ↔ borçta diğer, kişi kişi kalır)
    assert client.patch(f"/api/debts/{a}", json={"pay_terms": "parca", "due_strict": False}).status_code == 200
    d = _debt(client, a)
    assert d["terms"] == "parca" and d["strict"] is False
    assert client.patch(f"/api/debts/{a}", json={"direction": "borc"}).status_code == 200
    assert _debt(client, a)["creditor_type"] == "diger" and _debt(client, a)["terms_label"] == "Parça parça ödenebilir"
    g = client.post("/api/debts", json={"person": "Örnek Mağaza", "direction": "borc", "amount": 100,
                                        "creditor_type": "magaza"}).json()["id"]
    assert client.patch(f"/api/debts/{g}", json={"direction": "alacak"}).status_code == 200
    assert _debt(client, g)["creditor_type"] == "kurum"
    # taksitli ama plan yok: alacak sözüyle hata
    r = client.post("/api/debts", json={"person": "X", "direction": "alacak", "amount": 100, "pay_terms": "taksitli"})
    assert r.status_code == 422 and "alacakta" in r.json()["detail"]
    r = client.patch(f"/api/debts/{b}", json={"pay_terms": "taksitli"})
    assert r.status_code == 422 and "alacakta" in r.json()["detail"]


def test_receivable_with_installments_tracked_and_reminded(client):
    from app import push

    did = _alacak(client, person="Ahmet", **PLAN, plan_interest="var")
    d = _debt(client, did)
    s = d["schedule"]
    assert d["amount"] == 3000.0 and d["due_date"] is None and d["terms"] == "taksitli" and d["strict"] is True
    assert d["creditor_type"] == "kisi" and s["creditor_label"] == "Kişi"  # varsayılan kişi
    assert d["plan_interest"] is None and s["interest"] is None  # alacağa faiz sorulmaz, gönderilse de yazılmaz
    assert s["next"]["no"] == 1 and s["next"]["date"] == "2026-11-12" and s["count"] == 3
    # hatırlatma: 2 gün kala ve o gün, nötr metin, dokununca Borç / Alacak'ta o kayıt
    items = [x for x in push._borc(TODAY) if f"borc:{did}:" in x["id"]]
    assert len(items) == 1
    assert items[0]["title"] == "Ahmet: 1. taksit günü 2 gün sonra"
    assert items[0]["body"] == "Taksit ₺1.000. Geldiyse Borç / Alacak'ta işaretle."
    assert items[0]["route"] == f"#/diger/borclar/kayit/{did}" and items[0]["kind"] == "borc"
    assert [x["title"] for x in push._borc(date(2026, 11, 12)) if f"borc:{did}:" in x["id"]] == \
        ["Ahmet: 1. taksit günü bugün"]
    assert not [x for x in push._borc(date(2026, 11, 11)) if f"borc:{did}:" in x["id"]]
    # "Aldım" (tutar boş): sıradaki taksit; sonra 2. taksit hatırlatılır
    r = client.post(f"/api/debts/{did}/pay", json={"paid_at": "2026-11-12"})
    assert r.status_code == 200, r.text
    d = _debt(client, did)
    assert d["paid_amount"] == 1000.0 and d["schedule"]["paid_count"] == 1 and d["schedule"]["next"]["no"] == 2
    assert [x["title"] for x in push._borc(date(2026, 12, 10)) if f"borc:{did}:" in x["id"]] == \
        ["Ahmet: 2. taksit günü 2 gün sonra"]
    # erken kapatma alanları alacakta yok
    r = client.post("/api/debts", json={"person": "Y", "direction": "alacak", **PLAN, "plan_interest": "var",
                                        "payoff_amount": 2500})
    assert r.status_code == 422


def test_receivable_promise_date_reminded_by_existing_rule(client):
    from app import push

    soz = _alacak(client, person="Ayşe", amount=750, due_date="2026-11-12")
    kesin = _alacak(client, person="Örnek Firma", amount=900, due_date="2026-11-12", creditor_type="kurum",
                    due_strict=True)
    got = {x["id"].split(":")[1]: x for x in push._borc(TODAY) if x["id"].startswith("borc:")}
    assert got[str(soz)]["title"] == "Ayşe: söz verdiği gün 2 gün sonra"
    assert got[str(soz)]["body"] == "Kalan ₺750. Geldiyse Borç / Alacak'ta işaretle."
    assert got[str(kesin)]["title"] == "Örnek Firma: ödeme günü 2 gün sonra"
    assert got[str(soz)]["route"] == f"#/diger/borclar/kayit/{soz}"
    assert not [x for x in push._borc(date(2026, 11, 11)) if x["id"].startswith(f"borc:{soz}:")]


def test_debt_plan_and_payables_do_not_take_receivables(client):
    from app import debt_plan as dp, finance

    al_plan = _alacak(client, person="Ahmet", **PLAN)
    al_tek = _alacak(client, person="Ayşe", amount=2000, due_date="2026-12-01", pay_terms="tek", creditor_type="kisi")
    borc = client.post("/api/debts", json={"person": "Burak", "direction": "borc", "amount": 1500,
                                           "debt_date": "2026-10-01", "due_date": "2026-12-01"}).json()["id"]
    plan = dp.build(monthly=5000, today=TODAY)
    ids = {d["id"] for d in plan["debts"] if d["kind"] != "card"}
    assert borc in ids and al_plan not in ids and al_tek not in ids
    assert plan["totals"]["fixed_this_month"] == 0  # alacağın taksiti borç planında "önce ayrılan" değil
    pays = [p for p in finance.payables(TODAY) if p["type"] == "debt"]
    assert {p["id"] for p in pays} == {borc}


def test_schedule_added_later_on_receivable(client):
    did = _alacak(client, person="Örnek Firma", amount=2400, creditor_type="kurum", pay_terms="parca",
                  due_date="2026-12-01")
    client.post(f"/api/debts/{did}/pay", json={"amount": 400, "paid_at": "2026-10-15"})
    r = client.put(f"/api/debts/{did}/schedule", json={**PLAN, "plan_count": 4, "plan_installment": 500,
                                                       "creditor_type": "vergi_sgk", "plan_interest": "var"})
    assert r.status_code == 200, r.text
    d = r.json()["debt"]
    s = d["schedule"]
    assert s["count"] == 4 and s["total"] == 2000.0 and s["paid_before"] == 400.0 and d["due_date"] is None
    assert d["creditor_type"] == "kurum" and s["creditor_label"] == "Kurum / firma" and s["interest"] is None
    assert d["pay_terms"] == "taksitli" and d["terms_label"] == "Taksitli (ödeme planı)"
    # kaldır: alacak kalır, tür kalır, koşul boşalır
    r = client.delete(f"/api/debts/{did}/schedule")
    d = r.json()["debt"]
    assert d["schedule"] is None and d["amount"] == 2400.0 and d["creditor_type"] == "kurum"
    assert d["pay_terms"] is None and d["terms"] == "bilinmiyor"
    # PATCH plan_off da (mobil "Nasıl geri ödenecek" taksitliden başka koşula)
    client.put(f"/api/debts/{did}/schedule", json=PLAN)
    assert client.patch(f"/api/debts/{did}", json={"plan_off": True, "pay_terms": "tek"}).status_code == 200
    d = _debt(client, did)
    assert d["schedule"] is None and d["terms"] == "tek"
    # erken kapatma alacakta yazılmaz
    r = client.post(f"/api/debts/{did}/pay", json={"amount": 100, "paid_at": "2026-11-01", "close_early": True})
    assert r.status_code == 422


def test_old_client_receivable_unchanged(client):
    """Eski istemci alacakta koşul göndermez: kayıt eskisi gibi; GET'te yeni alanlar yalnız eklenir (eski istemci okumaz).
    Planlı alacağı eski istemci düzenlerse (vade göndermeden kişi / not) plan ve koşul korunur."""
    old = _alacak(client, person="Deniz", amount=500, due_date="2026-12-05", note="elden")
    d = _debt(client, old)
    assert d["creditor_type"] is None and d["pay_terms"] is None and d["due_strict"] is None
    assert d["schedule"] is None and d["terms"] == "bilinmiyor" and d["strict"] is False
    assert d["creditor_label"] is None and d["due_date"] == "2026-12-05"
    planli = _alacak(client, person="Ahmet", **PLAN, creditor_type="kurum")
    r = client.patch(f"/api/debts/{planli}", json={"person": "Ahmet Bey", "note": "taksitle", "amount": 3000,
                                                   "debt_date": "2026-10-01", "account_id": None})
    assert r.status_code == 200, r.text
    d = _debt(client, planli)
    assert d["schedule"]["count"] == 3 and d["creditor_type"] == "kurum" and d["terms"] == "taksitli"
    # eski web "alacak" düzenlemesi due_date null gönderir: planlı alacakta zaten boş
    assert d["due_date"] is None


def test_receivable_texts_never_suggest_interest_or_restructuring(client):
    from app import push

    _alacak(client, person="Ahmet", **PLAN)
    _alacak(client, person="Ayşe", amount=750, due_date="2026-11-12", pay_terms="tek")
    for d in client.get("/api/debts").json():
        for s in _all_strings(d):
            assert not _banned_in(s) and not _restructure_in(s) and "faiz" not in s.lower(), s
    for x in push._borc(TODAY):
        for s in (x["title"], x["body"]):
            assert not _banned_in(s) and not _restructure_in(s) and "faiz" not in s.lower(), s


def test_sources_never_suggest_interest_on_receivable():
    """Alacak koşulları ve hatırlatması: kullanıcıya giden dizgilerde yapılandırma / erteleme / kredi önerisi yok; alacak
    metinlerinde faiz hiç geçmez (alacağa faiz sorulmaz)."""
    root = Path(__file__).resolve().parent.parent
    src = (root / "app" / "push.py").read_text(encoding="utf-8")
    borc = src[src.index("def _borc("):src.index("def _butce(")]
    kosul = (root / "web" / "src" / "lib" / "borcKosul.ts").read_text(encoding="utf-8")
    for name, text in [("push._borc", borc), ("borcKosul.ts", kosul),
                       ("debt_schedule.py", (root / "app" / "debt_schedule.py").read_text(encoding="utf-8"))]:
        text = re.sub(r'"""[\s\S]*?"""', "", text)
        text = re.sub(r"/\*[\s\S]*?\*/", "", text)
        text = re.sub(r"(^|\s)(#|//)[^\n]*", "", text)
        flat = " | ".join(a or b for a, b in re.findall(r'"([^"\n]{3,})"|`([^`]{3,})`', text))
        assert not _banned_in(flat) and not _restructure_in(flat), (name, _banned_in(flat), _restructure_in(flat))
    # alacak sözleri (borcKosul.ts KIMDEN / KOSULLAR_ALACAK / TARIH_TURU_ALACAK) faiz içermez
    alacak = kosul[kosul.index("export const KIMDEN"):kosul.index("// alacak sözleri sonu")]
    assert "faiz" not in alacak.lower()
