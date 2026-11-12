"""Ödeme aracısı ön eki (app/textnorm.py PAYMENT_GATEWAYS): asıl satıcı ayrılır."""
from app import finance
from app.textnorm import normalize, strip_gateway


def test_izico_spelling_is_gateway():
    """Bazı bankalar iyzico'yu "IZICO" yazar: farklı abonelikler tek "IZICO" altında toplanmasın (12.11 cihaz testi)."""
    assert strip_gateway(normalize("IZICO/AmazonPrimeTR")) == "AMAZONPRIMETR"
    assert strip_gateway(normalize("IZICO /SPOTIFY.COM ISTANBUL TR")).startswith("SPOTIFY")
    a, b = finance.merchant_name("IZICO/AmazonPrimeTR"), finance.merchant_name("IZICO /SPOTIFY.COM ISTANBUL TR")
    assert a != b and "IZICO" not in a.upper() and "IZICO" not in b.upper()
