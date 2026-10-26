"""Genel kart ekstresi okuyucu (sahte ekstreler): öğrenme, ekstre denklemi, şablonun kaydı ve tekrar kullanımı."""
from app import card_reader, templates

GARANTI = """GARANTI BBVA BONUS KREDİ KARTI HESAP ÖZETİ
Sayın AYŞE YILMAZ
Kart Numarası 5400 12** **** 3456
Hesap Kesim Tarihi 14/10/2026
Son Ödeme Tarihi 24/10/2026
Dönem Borcu 3.865,40 TL
Asgari Ödeme Tutarı 1.546,16 TL
Önceki Dönem Borcu 2.000,00 TL
Kart Limiti 50.000,00 TL
Sonraki Hesap Kesim Tarihi 14/11/2026
Sonraki Son Ödeme Tarihi 24/11/2026
İşlem Tarihi Açıklama Tutar Bonus
16/09/2026 ÖDEMENİZ İÇİN TEŞEKKÜR EDERİZ -2.000,00 0,00
18/09/2026 ORNEK MARKET ISTANBUL 1.250,40 12,50
20/09/2026 ORNEK GIYIM 2/3 TAKSIT 800,00 8,00
22/09/2026 ORNEK RESTORAN 615,00 6,15
25/09/2026 ORNEK SPOR IADE -100,00 0,00
01/10/2026 ORNEK ELEKTRONIK 1.250,00 12,50
14/10/2026 KKDF 25,00 0,00
14/10/2026 BSMV 25,00 0,00
"""

YKB = """YAPI KREDİ WORLDCARD EKSTRESİ
Kart No: 4111 11** **** 2222
Hesap Kesim Tarihi: 05.10.2026  Son Ödeme Tarihi: 15.10.2026
Dönem Borcu: 1,500.00 TL   Asgari Ödeme: 600.00 TL
05.09.2026 ONCEKI DONEM BORCU 900.00
10.09.2026 ODEME - TESEKKURLER +900.00
12.09.2026 ORNEK KAFE 300.00
15.09.2026 ORNEK ECZANE 450.00
30.09.2026 ORNEK AKARYAKIT 750.00
"""


def test_learns_garanti_like_statement_and_reuses_template(tmpdb):
    shared = []
    templates.share_async = lambda spec: shared.append(spec)  # conftest zaten ağa çıkmayı engelliyor; burada yakala
    st = card_reader.read(GARANTI)
    assert st and st.bank == "Garanti" and st.card_no == "5400***3456"
    assert (st.period_end.isoformat(), st.due_date.isoformat(), st.total_debt, st.min_payment) == ("2026-10-14", "2026-10-24", 3865.40, 1546.16)
    assert st.next_period_end.isoformat() == "2026-11-14" and st.previous_balance == 2000 and st.payments == 2000
    kinds = {t.description[:12]: t.txn_type for t in st.transactions}
    assert kinds["ÖDEMENİZ İÇİ"] == "odeme" and kinds["ORNEK SPOR I"] == "iade" and kinds["KKDF"] == "faiz_ucret"
    inst = next(t for t in st.transactions if "GIYIM" in t.description)
    assert (inst.installment_no, inst.installments_left) == (2, 1)
    assert st.reconciliation_diff() == 0
    spec = next(t["spec"] for t in templates.all_templates() if t["spec"]["kind"] == "card")
    assert spec["bank"] == "Garanti" and spec["num"] == "tr" and spec["credit"] == "minus" and shared == [spec]
    assert "AYŞE" not in str(spec) and "3456" not in str(spec)  # şablon kişisel veri taşımaz
    # ikinci ekstre: kayıtlı şablonla okunur (yeniden öğrenilmez), kullanım sayısı artar
    card_reader.read(GARANTI.replace("ORNEK RESTORAN 615,00", "ORNEK KAFE 615,00"))
    assert next(t for t in templates.all_templates() if t["spec"]["kind"] == "card")["uses"] == 1


def test_us_numbers_plus_payments_and_previous_balance_line(tmpdb):
    st = card_reader.read(YKB)
    assert st and st.bank == "Yapı Kredi" and st.total_debt == 1500 and len(st.transactions) == 4
    assert st.previous_balance == 900 and st.payments == 900


def test_rejects_when_equation_does_not_hold(tmpdb):
    broken = GARANTI.replace("Dönem Borcu 3.865,40", "Dönem Borcu 9.999,99")
    assert card_reader.read(broken) is None
    assert not [t for t in templates.all_templates() if t["spec"]["kind"] == "card"]


def test_is_safe():
    ok = {"v": 1, "kind": "card", "bank": "Garanti", "keywords": ["dönem borcu"], "date": {"sep": "/", "order": "dmy"},
          "num": "tr", "amount_idx": -1, "credit": "minus"}
    assert templates.is_safe(ok)
    assert not templates.is_safe({**ok, "keywords": ["AYŞE YILMAZ"]})
    assert not templates.is_safe({**ok, "bank": "Sahte Bank"})
    assert not templates.is_safe({**ok, "amount_idx": 99})
    assert not templates.is_safe({**ok, "extra": "x"})


def test_parse_statement_uses_generic_reader(tmpdb, monkeypatch):
    import app.parsers as parsers

    monkeypatch.setattr(parsers, "extract_text", lambda data, password=None: GARANTI)
    st = parsers.parse_statement(b"%PDF", allow_ai=False)
    assert st.bank == "Garanti" and not st.ai_parsed and st.reconciliation_diff() == 0
