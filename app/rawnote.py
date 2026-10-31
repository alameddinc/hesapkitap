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
    """Ham banka metni yalnız notun ilk satırından: 'Hesap hareketi · <ham>' → ham. Açıklamadan ASLA türetilmez
    (kullanıcı düzeltince değişir): 'Hesap hareketi' tek başına (eski kayıt) → None; onarım (bank_repair) eski
    kayıtlarda açıklama değişmemişse ham satırı geri yazar. description bağımsız değişken yalnız geriye uyum için."""
    first, _ = _head(note)
    if first is None:
        return None
    rest = first[len(PREFIX):]
    if rest.startswith(" · "):
        rest = rest[3:]
        while m := _EXTRA.match(rest):
            rest = rest[m.end():]
        return rest or None
    return None


def legacy_raw(note: str | None, description: str | None) -> str | None:
    """Onarım ve iç eşleştirme için: eski kayıtta notu yalnız 'Hesap hareketi' ise içe aktarma açıklamayı hamın
    kendisi yazmıştı (kullanıcı değiştirmediyse). Kullanıcıya gösterilmez."""
    raw = raw_of(note)
    if raw is not None:
        return raw
    first, _ = _head(note)
    return (description or None) if first is not None and first.strip() == PREFIX else None


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
