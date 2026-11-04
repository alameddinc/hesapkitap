"""Kart ekstresi şablonu yapay zekâ okumasından öğrenilir (app/card_reader.py learn_from_ai): ikinci ekstre yapay
zekâsız okunur, doğrulama tutmazsa yapay zekâya düşülür ve şablon devre dışı kalır, yapay zekâ aynı kuralı yeniden
doğrularsa açılır. Ekstre düzeni, kişi, kart numarası ve tutarların hepsi uydurmadır."""
import json

import httpx
import pytest

from app import card_reader, receipt, templates
from app import parsers


def _m(x: float) -> str:
    return f"{x:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


# (gün.ay.yy, açıklama, döviz, taksit toplamı, tutar, bonus, tür) — tutar pozitif; ödeme/iade satırda "1.200,00-"
ROWS_A = [
    ("12.09.26", "ORNEK MARKET", None, None, 450.00, 4.50, "harcama"),
    ("14.09.26", "ORNEK ONLINE MAGAZA", ("USD", 20.00), None, 680.00, 6.80, "harcama"),
    ("15.09.26", "ODEME - TESEKKURLER", None, None, 1200.00, None, "odeme"),
    ("18.09.26", "ORNEK GIYIM TAKSIT 2/6", None, 1800.00, 300.00, 3.00, "harcama"),
    ("20.09.26", "ORNEK KITAP IADE", None, None, 120.00, None, "iade"),
    ("25.09.26", "ORNEK AKARYAKIT", None, None, 900.00, 9.00, "harcama"),
    ("01.10.26", "HESAP ISLETIM UCRETI", None, None, 35.60, None, "faiz_ucret"),
    ("05.10.26", "ORNEK ECZANE", None, None, 100.00, 1.00, "harcama"),
]
ROWS_B = [
    ("08.10.26", "ODEME - TESEKKURLER", None, None, 2345.60, None, "odeme"),
    ("10.10.26", "ORNEK KAFE", None, None, 210.00, 2.10, "harcama"),
    ("12.10.26", "ORNEK GIYIM TAKSIT 3/6", None, 1800.00, 300.00, 3.00, "harcama"),
    ("16.10.26", "ORNEK UYGULAMA", ("EUR", 9.99), None, 365.40, 3.65, "harcama"),
    ("20.10.26", "ORNEK SPOR IADE", None, None, 75.00, None, "iade"),
    ("28.10.26", "ORNEK MARKET", None, None, 1040.00, 10.40, "harcama"),
]


def _sign(kind: str) -> int:
    return -1 if kind in ("odeme", "iade") else 1


def statement(rows, cut="05.10.26", due="15.10.26", prev=1200.00, credit="trail", total_delta=0.0):
    """Uydurma bankanın uydurma ekstresi: sütunlu tarih başlığı, sözlükte olmayan borç etiketleri, iki haneli yıl,
    bonus sütunu, taksit toplamı tutardan önce, dövizli satır, ödeme/iade sondaki eksiyle."""
    total = round(prev + sum(_sign(k) * a for *_, a, _b, k in rows), 2) + total_delta
    lines = []
    for d, desc, fx, inst_total, amt, bonus, kind in rows:
        cols = [d, desc]
        if fx:
            cols.append(f"{fx[0]} {_m(fx[1])}")
        if inst_total:
            cols.append(_m(inst_total))
        if kind in ("odeme", "iade"):
            cols.append(_m(amt) + "-" if credit == "trail" else "-" + _m(amt))
        else:
            cols.append(_m(amt))
        if bonus:
            cols.append(_m(bonus))
        lines.append("   ".join(cols))
    body = "\n".join(lines)
    return f"""ODEABANK KREDİ KARTI HESAP ÖZETİ
Sayın DENEME KİŞİ
Deneme Mah. Örnek Sok. No: 3 Test/Ankara
Kart: 4022 33** **** 7788
Hesap Özeti Düzenleme Tarihi    Son Ödeme Tarihi
{cut}    {due}
Bu Dönem Ödenecek Tutar: {_m(total)} TL
Asgari Tutar: {_m(round(total * 0.4, 2))} TL
Geçen Ekstre Bakiyesi {_m(prev)} TL
Kart Limiti 40.000,00 TL
Kullanılabilir Limit {_m(40000 - total)} TL
İşlem Tarihi   Açıklama   Tutar   Bonus
{body}
"""


def ai_reply(rows, cut="2026-10-05", due="2026-10-15", prev=1200.00, **kw) -> dict:
    total = round(prev + sum(_sign(k) * a for *_, a, _b, k in rows), 2)
    paid = sum(a for *_, a, _b, k in rows if k == "odeme")
    activity = round(sum(_sign(k) * a for *_, a, _b, k in rows if k != "odeme"), 2)
    txns = []
    for d, desc, fx, inst_total, amt, _bonus, kind in rows:
        dd, mm, yy = d.split(".")
        n, tot = (None, None)
        if "/" in desc:
            n, tot = (int(x) for x in desc.split()[-1].split("/"))
        txns.append({"date": f"20{yy}-{mm}-{dd}", "description": desc, "amount": amt, "type": kind, "card": None,
                     "installment_no": n, "installments_total": tot,
                     "orig_amount": fx[1] if fx else None, "orig_currency": fx[0] if fx else None})
    return {"bank": "Odeabank", "card_no": "4022********7788", "period_end": cut, "due_date": due,
            "next_period_end": None, "next_due_date": None, "total_debt": total,
            "min_payment": round(total * 0.4, 2), "credit_limit": 40000.0, "available_limit": round(40000 - total, 2),
            "previous_balance": prev, "payments": paid, "period_activity": activity, "transactions": txns, **kw}


@pytest.fixture()
def gemini(monkeypatch):
    """Sahte Gemini: sıradaki yanıtı verir, çağrıları sayar."""
    monkeypatch.setenv("GEMINI_API_KEY", "AIzaTEST0000")
    state = {"calls": 0, "replies": []}

    def post(url, json=None, timeout=None, headers=None):
        state["calls"] += 1
        reply = state["replies"].pop(0)
        return httpx.Response(200, json={
            "candidates": [{"content": {"parts": [{"text": __import__("json").dumps(reply)}]}}],
            "usageMetadata": {"promptTokenCount": 1000, "candidatesTokenCount": 100}})

    monkeypatch.setattr(receipt.httpx, "post", post)
    return state


@pytest.fixture()
def pdf(monkeypatch):
    """PDF metni: b"STMT:<ad>" baytları texts[ad] metnine çevrilir (şifreli için b"ENC:<ad>")."""
    texts: dict[str, str] = {}

    def fake(data: bytes, password: str | None = None) -> str:
        if data.startswith(b"ENC:") and password != "123456":
            raise parsers.PasswordRequired("PDF şifreli")
        return texts[data.split(b":", 1)[1].decode()]

    monkeypatch.setattr(parsers, "extract_text", fake)
    return texts


def _cards() -> list[dict]:
    return [t for t in templates.all_templates() if t["spec"].get("kind") == "card"]


def _ai_calls() -> int:
    from app import system

    with system.connect() as c:
        try:
            return c.execute("SELECT COUNT(*) FROM ai_calls").fetchone()[0]
        except Exception:  # noqa: BLE001 - tablo henüz yok
            return 0


def _learn_first(gemini, pdf):
    pdf["a"] = statement(ROWS_A)
    gemini["replies"].append(ai_reply(ROWS_A))
    return parsers.parse_statement(b"STMT:a")


def test_local_rules_cannot_read_layout_so_ai_reads_and_teaches(tmpdb, gemini, pdf):
    assert card_reader.read(statement(ROWS_A)) is None  # ortak etiketlerle okunamıyor: şablon öğrenmek gerekiyor
    st = _learn_first(gemini, pdf)
    assert gemini["calls"] == 1 and st.ai_parsed and st.source == "yapay_zeka"
    [t] = _cards()
    spec = t["spec"]
    assert spec["bank"] == "Odeabank" and spec["credit"] == "trail" and spec["fx"] is True
    assert spec["date"] == {"order": "dmy", "sep": ".", "year": 2} and spec["amount_idx"] == 0
    assert spec["amount_at"] == {"3": 1}  # taksit satırında toplam tutardan önce
    assert spec["labels"]["total"] == [{"label": "bu donem odenecek tutar", "off": 0, "nth": 0}]
    assert spec["labels"]["due"] == [{"label": "hesap ozeti duzenleme tarihi son odeme tarihi", "off": 1, "nth": 1}]
    assert set(spec["labels"]) == {"period_end", "due", "total", "min", "prev"}  # limitler ortak etiketle okunuyor


def test_second_statement_read_by_template_without_ai(tmpdb, gemini, pdf):
    _learn_first(gemini, pdf)
    costs = _ai_calls()
    pdf["b"] = statement(ROWS_B, cut="05.11.26", due="15.11.26", prev=2345.60)
    st = parsers.parse_statement(b"STMT:b")
    assert gemini["calls"] == 1 and _ai_calls() == costs  # yapay zekâya gidilmedi, maliyet kaydı yok
    assert st.source == "sablon" and not st.ai_parsed
    assert (st.bank, st.card_no, st.period_end.isoformat(), st.due_date.isoformat()) == \
        ("Odeabank", "4022***7788", "2026-11-05", "2026-11-15")
    assert st.total_debt == 1840.40  # 2.345,60 − 2.345,60 + 210 + 300 + 365,40 − 75 + 1.040
    assert (st.previous_balance, st.payments, st.credit_limit) == (2345.60, 2345.60, 40000.0)
    assert st.min_payment == round(st.total_debt * 0.4, 2) and st.reconciliation_diff() == 0
    by = {t.description: t for t in st.transactions}
    assert len(st.transactions) == 6
    assert by["ODEME TESEKKURLER"].txn_type == "odeme" and by["ODEME TESEKKURLER"].amount == -2345.60
    assert by["ORNEK SPOR IADE"].txn_type == "iade" and by["ORNEK SPOR IADE"].amount == -75
    inst = by["ORNEK GIYIM TAKSIT 3/6"]
    assert (inst.amount, inst.installment_no, inst.installments_left) == (300.0, 3, 3)
    fx = by["ORNEK UYGULAMA"]
    assert (fx.amount, fx.orig_amount, fx.orig_currency) == (365.40, 9.99, "EUR")
    assert _cards()[0]["uses"] == 1


def test_upload_response_carries_source(tmpdb, client, gemini, pdf):
    pdf["a"] = statement(ROWS_A)
    gemini["replies"].append(ai_reply(ROWS_A))
    r = client.post("/api/statements/upload", files=[("files", ("a.pdf", b"STMT:a", "application/pdf"))]).json()[0]
    assert r["status"] == "saved" and r["ai"] is True and r["source"] == "yapay_zeka"
    pdf["b"] = statement(ROWS_B, cut="05.11.26", due="15.11.26", prev=2345.60)
    r = client.post("/api/statements/upload", files=[("files", ("b.pdf", b"STMT:b", "application/pdf"))]).json()[0]
    assert r["status"] == "saved" and r["ai"] is False and r["source"] == "sablon" and r["reconciliation_diff"] == 0


def test_failed_validation_falls_back_to_ai_then_disables_and_relearn_revives(tmpdb, gemini, pdf):
    _learn_first(gemini, pdf)
    # dönem borcu satırı işlemlerle tutmuyor (ör. bir satır okunamadı): şablon kullanılmaz, yapay zekâ okur
    for i in (1, 2):
        pdf[f"bad{i}"] = statement(ROWS_B, cut="05.11.26", due="15.11.26", prev=2345.60, total_delta=10.0)
        gemini["replies"].append(ai_reply(ROWS_B, cut="2026-11-05", due="2026-11-15", prev=2345.60))
        st = parsers.parse_statement(f"STMT:bad{i}".encode())
        assert st.source == "yapay_zeka" and gemini["calls"] == 1 + i
        t = _cards()[0]
        assert (t["fails"], t["disabled"]) == (i, int(i == 2))
    # devre dışı: düzgün ekstre de yapay zekâya gider; yapay zekâ aynı kuralı doğrulayınca şablon yeniden açılır
    pdf["good"] = statement(ROWS_B, cut="05.11.26", due="15.11.26", prev=2345.60)
    gemini["replies"].append(ai_reply(ROWS_B, cut="2026-11-05", due="2026-11-15", prev=2345.60))
    assert parsers.parse_statement(b"STMT:good").source == "yapay_zeka" and gemini["calls"] == 4
    [t] = _cards()
    assert (t["fails"], t["disabled"]) == (0, 0)
    assert parsers.parse_statement(b"STMT:good").source == "sablon" and gemini["calls"] == 4


def test_success_resets_fail_count(tmpdb, gemini, pdf):
    _learn_first(gemini, pdf)
    pdf["bad"] = statement(ROWS_B, cut="05.11.26", due="15.11.26", prev=2345.60, total_delta=10.0)
    gemini["replies"].append(ai_reply(ROWS_B, cut="2026-11-05", due="2026-11-15", prev=2345.60))
    parsers.parse_statement(b"STMT:bad")
    assert _cards()[0]["fails"] == 1
    assert parsers.parse_statement(b"STMT:a").source == "sablon"
    assert (_cards()[0]["fails"], _cards()[0]["disabled"]) == (0, 0)  # "art arda" iki hata gerekir


def test_new_layout_from_ai_adds_rule(tmpdb, gemini, pdf):
    _learn_first(gemini, pdf)
    pdf["minus"] = statement(ROWS_B, cut="05.11.26", due="15.11.26", prev=2345.60, credit="minus")
    gemini["replies"].append(ai_reply(ROWS_B, cut="2026-11-05", due="2026-11-15", prev=2345.60))
    assert parsers.parse_statement(b"STMT:minus").source == "yapay_zeka"
    assert sorted(t["spec"]["credit"] for t in _cards()) == ["minus", "trail"]
    assert parsers.parse_statement(b"STMT:minus").source == "sablon" and gemini["calls"] == 2


def test_untrustworthy_ai_result_teaches_nothing(tmpdb, gemini, pdf):
    pdf["a"] = statement(ROWS_A)
    reply = ai_reply(ROWS_A)
    reply["transactions"].pop()  # yapay zekâ bir satırı atladı: denklem tutmaz
    gemini["replies"].append(reply)
    assert parsers.parse_statement(b"STMT:a").source == "yapay_zeka" and _cards() == []
    reply = ai_reply(ROWS_A, due="2026-10-01")  # son ödeme kesimden önce: tutarsız
    gemini["replies"].append(reply)
    parsers.parse_statement(b"STMT:a")
    assert _cards() == []


def test_ai_values_template_cannot_reproduce_are_not_learned(tmpdb, gemini, pdf):
    pdf["a"] = statement(ROWS_A)
    gemini["replies"].append(ai_reply(ROWS_A, card_no="5555********1111"))  # kart no metindekiyle aynı değil
    parsers.parse_statement(b"STMT:a")
    assert _cards() == []


def test_learned_template_has_no_personal_values(tmpdb, gemini, pdf):
    shared = []
    templates.share_async = lambda spec: shared.append(spec)  # conftest ağa çıkmayı engelliyor; burada yakala
    _learn_first(gemini, pdf)
    [t] = _cards()
    spec = t["spec"]
    assert templates.is_safe(spec) and card_reader.is_safe(spec) and shared == [spec]
    raw = json.dumps(spec, ensure_ascii=False)
    for personal in ("DENEME", "deneme", "KİŞİ", "kisi", "4022", "7788", "1.200", "1200", "2.345", "40.000", "Ankara",
                     "Sok", "05.10", "2026", "MARKET", "ornek"):
        assert personal not in raw


def test_is_safe_rejects_document_values():
    ok = {"v": 1, "kind": "card", "bank": "Odeabank", "keywords": ["hesap özeti"],
          "date": {"sep": ".", "order": "dmy", "year": 2}, "num": "tr", "amount_idx": 0, "credit": "trail",
          "amount_at": {"3": 1}, "fx": True,
          "labels": {"total": [{"label": "bu donem odenecek tutar", "off": 0, "nth": 0}]}}
    assert card_reader.is_safe(ok) and templates.is_safe(ok)
    bad_labels = [[{"label": "deneme kisi borcu", "off": 0, "nth": 0}],         # sözlük dışı kelime (ad)
                  [{"label": "borcu 2345", "off": 0, "nth": 0}],                # değer
                  [{"label": "borcu", "off": 0, "nth": 0, "value": 1.0}],       # fazladan alan
                  [{"label": "borcu", "off": 9, "nth": 0}],
                  [{"label": "borcu  ", "off": 0, "nth": 0}]]
    for labels in bad_labels:
        assert not card_reader.is_safe({**ok, "labels": {"total": labels}})
    for bad in ({**ok, "labels": {"isim": [{"label": "borcu", "off": 0, "nth": 0}]}},
                {**ok, "amount_at": {"3": 7}}, {**ok, "amount_at": {"x": 1}}, {**ok, "amount_at": {"3": True}},
                {**ok, "fx": "USD"}, {**ok, "credit": "eval"}, {**ok, "date": {**ok["date"], "year": 3}},
                {**ok, "card": "4022***7788"}, {**ok, "bank": "Deneme Bank"}):
        assert not card_reader.is_safe(bad)


def test_bank_parsers_come_first(tmpdb, gemini, monkeypatch):
    from tests.conftest import fixture_bytes

    def boom(text):
        raise AssertionError("bankaya özel parser varken şablon denenmemeli")

    monkeypatch.setattr(card_reader, "read", boom)
    for name in ("vakifbank_a.txt", "kuveytturk.txt"):
        st = parsers.parse_statement(fixture_bytes(name))
        assert st.source == "parser" and not st.ai_parsed
    assert gemini["calls"] == 0


def test_encrypted_pdf_learns_from_decrypted_text(tmpdb, gemini, pdf, monkeypatch):
    from app.parsers import ai

    pdf["enc"] = statement(ROWS_A)
    sent = []
    real = ai.parse_pdf_with_ai

    def spy(data, mime="application/pdf"):
        sent.append(mime)
        return real(data, mime)

    monkeypatch.setattr(ai, "parse_pdf_with_ai", spy)
    gemini["replies"].append(ai_reply(ROWS_A))
    with pytest.raises(parsers.PasswordRequired):
        parsers.parse_statement(b"ENC:enc")
    st = parsers.parse_statement(b"ENC:enc", passwords=["123456"])
    assert st.source == "yapay_zeka" and sent == ["text/plain"]  # modele şifresi çözülmüş metin gider
    assert len(_cards()) == 1
    st = parsers.parse_statement(b"ENC:enc", passwords=["123456"])
    assert st.source == "sablon" and gemini["calls"] == 1


def test_replay_isolation_does_not_touch_counters(tmpdb, gemini, pdf):
    from app import replay_check

    _learn_first(gemini, pdf)
    pdf["bad"] = statement(ROWS_B, cut="05.11.26", due="15.11.26", prev=2345.60, total_delta=10.0)
    with replay_check._isolated(no_ai=True):
        with pytest.raises(ValueError):
            parsers.parse_statement(b"STMT:bad")
    assert _cards()[0]["fails"] == 0
