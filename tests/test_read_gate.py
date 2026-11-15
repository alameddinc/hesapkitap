"""Okuma tutarlılık kapısı (app/parsers/gate.py) ve yedek yol (app/parsers/__init__.py _gate_fallback).

Olay biçimi (Kasım 2026, uydurma veriyle): banka tutarları ABD biçiminden (17,752.21) Türkçe biçime (17.752,21) geçti;
eski VakıfBank okuyucusu tutarları "17.75"te kesti, işlem satırlarını hiç tanımadı ve sessizce "0 işlem" kaydetti.
Eski okuyucu, bugünkü modülün tutar deseni eski haline döndürülerek kurulur (_old_vakifbank). Kişiler, işyerleri ve
kart numaraları uydurma (tests/fixtures/vakifbank_*.txt)."""
import inspect
import re
import types
import pytest

from app import card_reader, incidents, parsers, system
from app.parsers import gate, vakifbank
from app.parsers.base import FEE, PAYMENT, PURCHASE, Statement, Transaction
from tests.conftest import FIXTURES
from tests.test_book import owner  # noqa: F401 - fixture
from tests.test_card_templates import gemini, pdf  # noqa: F401 - fixture
from tests.test_push import nokeys  # noqa: F401 - fixture


@pytest.fixture(autouse=True)
def _clean():
    incidents._buf.clear()
    incidents._alert_times.clear()
    yield
    incidents._buf.clear()


def _old_vakifbank() -> types.ModuleType:
    """f3c1a71 öncesi VakıfBank okuyucusu: yalnız ABD biçimi tutar deseni ve textnorm.parse_amount."""
    src = inspect.getsource(vakifbank)
    src, n = re.subn(r"^_AMT = .*$", r'_AMT = r"\\+?\\d{1,3}(?:,\\d{3})*\\.\\d{2}"', src, flags=re.M)
    assert n == 1, "vakifbank._AMT satırı bulunamadı (test kurulumu güncellenmeli)"
    src += "\nfrom app.textnorm import parse_amount  # noqa: E402,F401 - eski davranış\n"
    mod = types.ModuleType("app.parsers.vakifbank_eski")
    exec(compile(src, "vakifbank_eski", "exec"), mod.__dict__)  # noqa: S102 - depodaki kendi kaynağımız
    return mod


def _tr(text: str) -> str:
    """ABD biçimli tutarları (17,752.21) Türkçe biçime (17.752,21) çevir: bankanın biçim değişikliği."""
    return re.sub(r"(?<![\d.,])(\d{1,3}(?:,\d{3})*)\.(\d{2})(?![\d.,])",
                  lambda m: m.group(1).replace(",", ".") + "," + m.group(2), text)


def _drift_text(name: str = "vakifbank_a.txt") -> str:
    return _tr((FIXTURES / name).read_text(encoding="utf-8"))


@pytest.fixture()
def old_parser(monkeypatch):
    old = _old_vakifbank()
    monkeypatch.setattr(parsers, "DETECTORS", [(vakifbank.is_vakifbank, old.parse_text)])
    return old


@pytest.fixture()
def no_templates(monkeypatch):
    """Şablon yedeğini kapat: yapay zekâya düşüşü yalın sına."""
    monkeypatch.setattr(card_reader, "read", lambda text: None)


def _reply(st: Statement) -> dict:
    """Doğru okumanın (yeni okuyucu) yapay zekâ yanıtı biçimi (app/parsers/ai.py PROMPT)."""
    return {"bank": st.bank, "card_no": "4543********1234", "period_end": st.period_end.isoformat(),
            "due_date": st.due_date.isoformat(), "next_period_end": None, "next_due_date": None,
            "total_debt": st.total_debt, "min_payment": st.min_payment, "credit_limit": st.credit_limit,
            "available_limit": None, "previous_balance": st.previous_balance, "payments": st.payments,
            "period_activity": round(st.period_activity + st.interest_fees, 2),
            "transactions": [{"date": t.txn_date.isoformat(), "description": t.description, "amount": abs(t.amount),
                              "type": t.txn_type, "card": None, "installment_no": None, "installments_total": None,
                              "orig_amount": None, "orig_currency": None} for t in st.transactions]}


def _drift_events() -> list[dict]:
    return [e for _, e in incidents._buf if e["code"] == "parser_drift"]


def _kinds(code="parser_drift") -> list[dict]:
    incidents.flush()
    with system.connect() as c:
        return [dict(r) for r in c.execute("SELECT * FROM incident_kinds WHERE code = ? ORDER BY id", (code,))]


def _sistem(uid):
    from app import push

    return [x["payload"] for x in push.queue(uid) if x["payload"]["kind"] == "sistem"]


# ---------------------------------------------------------------- kapı kuralları

def _st(txns, **kw) -> Statement:
    from datetime import date

    base = dict(bank="Örnek Bank", card_no="4111***1111", period_end=date(2026, 11, 10), due_date=date(2026, 11, 20),
                total_debt=0.0, min_payment=None, credit_limit=None)
    return Statement(**{**base, **kw}, transactions=txns)


def _t(amount, kind=PURCHASE):
    from datetime import date

    return Transaction(txn_date=date(2026, 11, 1), description="ORNEK", amount=amount, txn_type=kind, card="x")


def test_gate_passes_consistent_statement_and_fixtures():
    st = _st([_t(1200.0), _t(300.0), _t(-1000.0, PAYMENT)], previous_balance=1000.0, payments=1000.0,
             period_activity=1500.0, total_debt=1500.0)
    assert gate.check(st).ok
    for name in ("vakifbank_a.txt", "vakifbank_b.txt", "kuveytturk.txt"):
        r = gate.check(parsers.parse_text((FIXTURES / name).read_text(encoding="utf-8")))
        assert r.ok and r.check is None, (name, r)


def test_gate_zero_transactions_with_activity():
    r = gate.check(_st([], previous_balance=20.0, payments=20.0, period_activity=17.75, total_debt=17.75))
    assert (r.ok, r.check) == (False, gate.ISLEM_YOK) and r.numbers["islem"] == 0
    # gerçekten boş ekstre (harcama yok, borç yok) geçer
    assert gate.check(_st([])).ok


def test_gate_scale_shift_both_directions():
    # özet doğru (Türkçe biçimi okuyan özet satırı), işlemler binlikte kesilmiş
    small = [_t(53.88), _t(12.49), _t(1.31)]
    r = gate.check(_st(small, previous_balance=0.0, period_activity=123456.78, total_debt=123456.78))
    assert (r.ok, r.check) == (False, gate.OLCEK)
    # özet kesilmiş, işlemler doğru
    big = [_t(60000.0), _t(63456.78)]
    r = gate.check(_st(big, previous_balance=0.0, period_activity=123.45, total_debt=123.45))
    assert (r.ok, r.check) == (False, gate.OLCEK)
    # yüksek devreden borç + küçük harcamalar ölçek kayması değildir (denklem tutuyor)
    r = gate.check(_st([_t(250.0), _t(750.0)], previous_balance=150000.0, payments=0.0, period_activity=1000.0,
                       total_debt=151000.0))
    assert r.ok


def test_gate_equation_and_transaction_sum_tolerance():
    ok = dict(previous_balance=500.0, payments=500.0, period_activity=1000.0)
    assert gate.check(_st([_t(600.0), _t(400.0)], total_debt=1000.9, **ok)).ok  # ±1 TL içinde
    r = gate.check(_st([_t(600.0), _t(400.0)], total_debt=1010.0, **ok))
    assert (r.ok, r.check) == (False, gate.DENKLEM) and r.numbers["denklem_farki"] == 10.0
    r = gate.check(_st([_t(600.0), _t(350.0)], total_debt=1000.0, **ok))  # bir satır kaçmış
    assert (r.ok, r.check) == (False, gate.ISLEM_TOPLAMI) and r.numbers["islem_farki"] == -50.0
    # faiz ayrı satırda: denklem faizi ayrı biçimle de dener
    st = _st([_t(1000.0), _t(25.0, FEE)], total_debt=1025.0, interest_fees=25.0, **ok)
    assert gate.check(st).ok


# ---------------------------------------------------------------- olay: Türkçe biçime geçiş

def test_old_parser_on_turkish_format_reads_zero_transactions_but_equation_holds(old_parser):
    """Olayın özü: eski okuyucuda denklem kendi içinde tutuyor (hepsi 1000'de bir) — yalnız denklem yetmez."""
    st = old_parser.parse_text(_drift_text())
    assert st.transactions == [] and round(st.total_debt, 2) == 17.75  # 17.752,21 → 17.75221
    r = gate.check(st)
    assert (r.ok, r.check) == (False, gate.ISLEM_YOK) and r.numbers["denklem_farki"] == 0.0


def test_drift_falls_back_to_ai_and_ai_reading_is_kept(tmpdb, gemini, pdf, old_parser, no_templates):  # noqa: F811
    text = _drift_text()
    good = vakifbank.parse_text(text)  # bugünkü okuyucu: doğru okuma (yapay zekâ yanıtı bunun gibi)
    pdf["a"] = text
    gemini["replies"].append(_reply(good))
    st = parsers.parse_statement(b"STMT:a")
    assert gemini["calls"] == 1 and st.source == "yapay_zeka" and st.ai_parsed
    assert gate.check(st).ok and st.gate_fallback == gate.ISLEM_YOK
    assert st.total_debt == 17752.21 and len(st.transactions) == len(good.transactions)
    ev = _drift_events()
    assert len(ev) == 1 and ev[0]["place"] == "VakıfBank"
    assert "okuyucu=parser:vakifbank_eski" in ev[0]["ozet"] and "kontrol=islem_yok" in ev[0]["ozet"]


def test_drift_recovered_by_card_template_without_ai(tmpdb, gemini, pdf, old_parser):  # noqa: F811
    """Bankaya özel okuyucu patlayınca önce kart şablonları (ücretsiz): Türkçe biçimi yapay zekâsız öğrenilir."""
    pdf["a"] = _drift_text()
    st = parsers.parse_statement(b"STMT:a")
    assert gemini["calls"] == 0 and st.source == "sablon" and gate.check(st).ok
    assert st.gate_fallback == gate.ISLEM_YOK and st.total_debt == 17752.21
    assert len(_drift_events()) == 1  # alarm yine de: okuyucu düzeltilmeli


def test_ai_also_inconsistent_keeps_old_flow(tmpdb, gemini, pdf, old_parser, no_templates):  # noqa: F811
    text = _drift_text()
    bad = _reply(vakifbank.parse_text(text))
    bad["transactions"] = bad["transactions"][:3]  # yapay zekâ satır kaçırdı: işlem toplamı tutmaz
    pdf["a"] = text
    gemini["replies"].append(bad)
    st = parsers.parse_statement(b"STMT:a")
    assert gemini["calls"] == 1
    assert st.source == "parser" and st.transactions == [] and st.gate_fallback == gate.ISLEM_YOK
    assert st.gate_ai_called is True
    from app import verification

    assert verification.statement_check(st)["verified"] is False  # bugünkü "Rakamlar tutmadı" akışı


def test_ai_error_keeps_old_flow(tmpdb, gemini, pdf, old_parser, no_templates, monkeypatch):  # noqa: F811
    from app import receipt

    def boom(*a, **k):
        raise receipt.ReceiptError("servis yok")

    monkeypatch.setattr(receipt, "_gemini_json", boom)
    pdf["a"] = _drift_text()
    st = parsers.parse_statement(b"STMT:a")
    assert st.source == "parser" and st.transactions == []


def test_no_ai_key_keeps_old_flow_but_alarms(tmpdb, pdf, old_parser, no_templates):  # noqa: F811
    pdf["a"] = _drift_text()
    st = parsers.parse_statement(b"STMT:a")
    assert st.source == "parser" and st.transactions == [] and len(_drift_events()) == 1


def test_consistent_statement_passes_directly_no_ai_no_alarm(tmpdb, gemini, pdf):  # noqa: F811
    pdf["a"] = (FIXTURES / "vakifbank_b.txt").read_text(encoding="utf-8")
    st = parsers.parse_statement(b"STMT:a")
    assert st.source == "parser" and gemini["calls"] == 0 and not _drift_events()
    assert not hasattr(st, "gate_fallback")


def test_internal_reread_without_ai_skips_gate(tmpdb, pdf, old_parser):  # noqa: F811
    pdf["a"] = _drift_text()
    st = parsers.parse_statement(b"STMT:a", allow_ai=False)
    assert st.source == "parser" and not _drift_events()


def test_upload_with_fallback_charges_once_and_is_not_charged_again_on_duplicate(
        client, gemini, pdf, old_parser, no_templates, monkeypatch):  # noqa: F811
    """Okuma hakkı belge başına tek: yedek yapay zekâ okuması ek hak düşürmez; aynı belge tekrar yüklenince de."""
    from app import doc_reads

    charged = []
    monkeypatch.setattr(doc_reads, "charge", lambda key, *a, **k: charged.append(key) or {"daily": 1, "extra": 0})
    text = _drift_text()
    pdf["a"] = text
    gemini["replies"].append(_reply(vakifbank.parse_text(text)))
    files = [("files", ("ekstre.pdf", b"STMT:a", "application/pdf"))]
    r = client.post("/api/statements/upload", files=files).json()[0]
    assert r["status"] == "saved" and r["source"] == "yapay_zeka" and r["txn_count"] == 12 and r["verified"]
    assert len(charged) == 1 and gemini["calls"] == 1
    # okuma önbelleği yoksa da (ör. süresi doldu) yedek çağrı "zaten vardı"da sayılmaz
    from app import doc_cache

    monkeypatch.setattr(doc_cache, "current", lambda: None)
    gemini["replies"].append(_reply(vakifbank.parse_text(text)))
    r = client.post("/api/statements/upload", files=files).json()[0]
    assert r["status"] == "duplicate" and len(charged) == 1


# ---------------------------------------------------------------- yönetici alarmı

def test_alarm_once_per_bank_per_day(owner, nokeys, monkeypatch):  # noqa: F811
    admin = system.user_by_name("admin")["id"]
    sent = []
    real = incidents._send_alert
    monkeypatch.setattr(incidents, "_send_alert", lambda k, *a: sent.append(k["place"]) or real(k, *a))
    ozet = "okuyucu=parser:vakifbank · kontrol=islem_yok · islem=0 · donem_borcu=18"
    for _ in range(3):
        incidents.system_event("parser_drift", "VakıfBank", ozet)
        incidents.flush()
    q = _sistem(admin)
    assert len(q) == 1 and q[0]["title"].startswith("Banka ekstre biçimi değişmiş olabilir")
    assert "VakıfBank" in q[0]["body"] and "kontrol=islem_yok" in q[0]["body"] and "donem_borcu=18" in q[0]["body"]
    # başka banka: ayrı alarm
    incidents.system_event("parser_drift", "Kuveyt Türk", ozet.replace("vakifbank", "kuveytturk"))
    incidents.flush()
    assert len(_sistem(admin)) == 2
    # ertesi gün aynı banka yine bir kez (saatlik sınır değil, gün)
    k = next(k for k in _kinds() if k["place"] == "VakıfBank")
    yday = incidents._utc(incidents.time.time() - 86400)
    with system.connect() as c:
        c.execute("UPDATE incident_kinds SET alerted_at = ? WHERE id = ?", (yday, k["id"]))
    incidents.system_event("parser_drift", "VakıfBank", ozet)
    incidents.flush()
    incidents.system_event("parser_drift", "VakıfBank", ozet)
    incidents.flush()
    assert sent == ["VakıfBank", "Kuveyt Türk", "VakıfBank"]
    assert next(k for k in _kinds() if k["place"] == "VakıfBank")["alerted_at"][:10] == incidents._utc()[:10]


def test_alarm_has_no_personal_data(owner, nokeys, pdf, old_parser, no_templates):  # noqa: F811
    admin = system.user_by_name("admin")["id"]
    text = _drift_text("vakifbank_b.txt")
    pdf["a"] = text
    parsers.parse_statement(b"STMT:a")
    k = _kinds()
    assert len(k) == 1 and k[0]["place"] == "VakıfBank"
    q = _sistem(admin)
    assert len(q) == 1
    blob = f"{k[0]['sample']} {k[0]['place']} {q[0]['title']} {q[0]['body']}"
    holder = re.search(r"Sayın\s+(.+)$", text, re.M).group(1)
    card = re.search(r"Kart No\s*:\s*(\S+)", text).group(1)
    merchants = {m for m in re.findall(r"^\d{2}\.\d{2}\.\d{4}\s+([A-ZÇĞİÖŞÜ]{4,})", text, re.M)}
    assert merchants and holder
    for bad in (holder, *holder.split(), card, card[:4], card[-4:], "ORNEK", *merchants, "Şube", "000000000000"):
        assert bad not in blob, bad
    assert "kontrol=islem_yok" in blob and "islem=0" in blob
    # tutarlar tam TL (kuruş yok): eski okuyucu dönem borcunu 17.8806 okumuştu → 18
    assert "donem_borcu=18 " in blob and not re.search(r"=-?\d+\.\d", blob)


def test_raw_summary_only_for_safe_text():
    """Sayısal özet yalnız sabit biçimdeyse aynen saklanır; tırnak / @ / adres içeren metin süzgeçten geçer."""
    assert incidents._ozet("parser_drift", "kontrol=olcek · islem=3 · donem_borcu=123456.78").endswith("123456.78")
    s = incidents._ozet("parser_drift", 'kontrol=x "AYŞE YILMAZ" 4543 ali@ornek.com')
    assert "AYŞE" not in s and "4543" not in s and "ali@" not in s
    assert "1.234" not in incidents._ozet("ekstre_okunamadi", "tutar 1.234,50")


def test_drift_summary_has_only_numbers_and_fixed_keys():
    st = _st([_t(53.88)], previous_balance=0.0, period_activity=123456.78, total_debt=123456.78)
    st.transactions[0].description = "AYŞE YILMAZ ORNEK MARKET"
    r = gate.check(st)
    s = parsers.drift_summary("parser:Vakıf Bank!<x>", r)
    assert s.startswith("okuyucu=parser:vakfbankx · kontrol=olcek")
    assert "AYŞE" not in s and "ORNEK" not in s and "donem_borcu=123457" in s and "en_buyuk_islem=54" in s
    assert not re.search(r"=-?\d+\.\d", s)  # kuruş yok
    assert [parsers._whole_tl(v) for v in (17.5, 17.49, -0.5, -12.51, 0.0)] == [18, 17, -1, -13, 0]
    assert incidents._ozet("parser_drift", s) == s
