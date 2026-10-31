"""Kayıt notunda saklanan ham banka metni (yeni kolon yok).

Dökümden gelen kaydın notu: ilk satır "Hesap hareketi[ · <ek bilgi>…][ · <ham banka metni>]" (fatura: "Hesap hareketi ·
Telefon … · Fatura no … · <ham>"); ham metin yoksa açıklamanın kendisi hamdır. Kullanıcının kendi notu bu satırın
ALTINDA (yeni satırda) durur. Not düzenlenince (PATCH note) ilk satır korunur, kullanıcı notu yalnız alt kısmı değiştirir:
açıklama ya da not değişse de banka açıklaması kaybolmaz.
"""
import re

PREFIX = "Hesap hareketi"
_EXTRA = re.compile(r"(Telefon|Abone no|Sözleşme no|Tesisat no|Mukavele no|Fatura no|Son ödeme) [^·]{1,40} · ")


def _head(note: str | None) -> tuple[str | None, str | None]:
    """(ham satırı, kullanıcı notu). Not "Hesap hareketi" ile başlamıyorsa ham satır yok, notun tamamı kullanıcınındır."""
    if not note:
        return None, None
    if not note.startswith(PREFIX):
        return None, note
    first, _, rest = note.partition("\n")
    return first, (rest.strip() or None)


def raw_of(note: str | None, description: str | None = None) -> str | None:
    """Ham banka metni: 'Hesap hareketi · <ham>' → ham; 'Hesap hareketi' (ek yok) → açıklama; değilse None."""
    first, _ = _head(note)
    if first is None:
        return None
    rest = first[len(PREFIX):]
    if rest.startswith(" · "):
        rest = rest[3:]
        while m := _EXTRA.match(rest):
            rest = rest[m.end():]
        return rest or None
    return (description or None) if rest.strip() == "" else None


def user_note(note: str | None) -> str | None:
    """Kullanıcının kendi notu (ham satır çıkarılmış)."""
    return _head(note)[1]


def with_user_note(old_note: str | None, new_user_note: str | None) -> str | None:
    """PATCH note: kayıtta ham satır varsa korunur, kullanıcı notu altına yazılır. İstemci notun tamamını geri
    gönderiyorsa (eski istemci: 'Hesap hareketi · …' ile başlıyor) olduğu gibi yazılır."""
    first, _ = _head(old_note)
    if first is None or (new_user_note or "").startswith(PREFIX):
        return new_user_note
    u = (new_user_note or "").strip()
    return first + (f"\n{u}" if u else "")
