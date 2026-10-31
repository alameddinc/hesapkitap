"""Kullanıcıya giden sunucu metinlerinin ortak terimleri (S4 terim sözlüğü; mobil terimler.ts ile aynı).

Adlar kullanıcı onayı bekliyor: değişirse yalnız burası değişir. Para biçimi: app.money.tl (₺1.234,50).
"""
OWN_TRANSFER = "kendi hesapların arası"   # eski adlar: virman, hesaplar arası, kendi hesabına…
LEFT = "Artan"                            # Özet: gelir − gider (totals.left / net)
LEFT_AFTER_SAVINGS = "Elde kalan"         # Sade görünüm: gelir − gider − birikime ayrılan (totals.left_after_savings)
SPENDABLE = "Harcanabilir"
EQ_OK = "Rakamlar tuttu"                  # ekstre denklemi / döküm bakiye zinciri doğrulandı
EQ_BAD = "Rakamlar tutmadı"               # doğrulanamadı: kullanıcı belgeyle karşılaştırmalı
CTA_LOOK = "Bak"                          # Yapılacaklar: ilgili kayda/belgeye git
CONFIRM_OK = "Rakamlar doğru"             # kullanıcı belgeyle karşılaştırdı, onaylıyor (confirm uçları)


def lower_first(s: str) -> str:
    """Cümle içinde kullanılan metnin ilk harfi küçük: 'Rakamlar tutmadı: …' → 'rakamlar tutmadı: …'."""
    return s[:1].lower() + s[1:] if s else s
