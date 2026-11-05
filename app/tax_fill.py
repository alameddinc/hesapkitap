"""Fişin vergi bilgisini önceki fişlerden tamamla (yapay zekâsız): aynı işyerinin bir fişinde unvan / VKN / vergi dairesi
okunmuş, başka fişinde okunamamışsa eksik olan doldurulur; aynı VKN'nin unvanı ve vergi dairesi tek yazıma getirilir.

Neden (kullanıcı, 05.11.2026): muhasebeci listesinde aynı kafenin bir fişi VKN'li, ötekisi boş; "MUDURLUGU" /
"MÜDÜRLÜĞÜ", "Ankara Kurumlar" / "ANKARA KURUMLAR" gibi iki yazım.

Güvenlik: işyeri adı birden çok farklı VKN'ye çıkıyorsa (aynı adlı iki ayrı işletme, ör. "Cafe") tahmin yapılmaz.
Kayıtlar DEĞİŞMEZ; doldurulan alan çağıranın sözlüğüne yazılır (dışa aktarım ve okuma önizlemesi). Yalnız 10 haneli,
kontrol hanesi tutan VKN kullanılır (app/vat.py).
"""
from collections import Counter, defaultdict

from app import vat

FIELDS = ("legal_name", "tax_no", "tax_office")


def _key(name: str | None) -> str:
    from app.textnorm import merchant_name, normalize

    return normalize(merchant_name(name or "") or name or "").strip()


def _same(s: str) -> str:
    """Yazım farkını yok say: "MUDURLUGU" = "Müdürlüğü", "Ankara Kurumlar" = "ANKARA KURUMLAR"."""
    from app.textnorm import normalize

    return " ".join(normalize(s or "").split())


def _best(variants: Counter) -> str | None:
    """En düzgün yazım: Türkçe harf içeren önce (MÜDÜRLÜĞÜ > MUDURLUGU), sonra en sık, sonra tümü büyük harf olmayan."""
    if not variants:
        return None
    return max(variants, key=lambda v: (any(ch in v for ch in "çğıöşüÇĞİÖŞÜ"), variants[v], not v.isupper(), v))


class Book:
    """Defterdeki fişlerden öğrenilen eşleşmeler: işyeri → VKN'ler, VKN → unvan ve vergi dairesi yazımları."""

    def __init__(self, entries: list[dict]):
        self.by_merchant: dict[str, Counter] = defaultdict(Counter)
        self.names: dict[str, Counter] = defaultdict(Counter)
        self.offices: dict[str, Counter] = defaultdict(Counter)
        for e in entries:
            t = vat.vkn(e.get("tax_no"))
            if not t:
                continue
            for k in {_key(e.get("description")), _key(e.get("legal_name"))} - {""}:
                self.by_merchant[k][t] += 1
            if e.get("legal_name"):
                self.names[t][e["legal_name"].strip()] += 1
            if e.get("tax_office"):
                self.offices[t][e["tax_office"].strip()] += 1

    def complete(self, e: dict) -> dict:
        """e'nin kopyası: eksik vergi alanları dolu, yazımlar tek biçim. filled: doldurulan alan adları."""
        out = dict(e)
        filled = []
        t = vat.vkn(e.get("tax_no"))
        if not t:
            cands = Counter()
            for k in {_key(e.get("description")), _key(e.get("legal_name"))} - {""}:
                cands.update(self.by_merchant.get(k, {}))
            if len(cands) == 1:  # tek VKN: tahmin güvenli; birden çoksa (aynı adlı ayrı işletmeler) dokunma
                t = next(iter(cands))
                out["tax_no"] = t
                filled.append("tax_no")
        if t:
            for field, pool in (("legal_name", self.names), ("tax_office", self.offices)):
                best = _best(pool.get(t, Counter()))
                if best and not (out.get(field) or "").strip():
                    out[field] = best
                    filled.append(field)
                elif best and out.get(field) and _same(out[field]) != _same(best):
                    pass  # farklı bir ad (şube unvanı vb.): olduğu gibi kalır
                elif best:
                    out[field] = best  # aynı ad, başka yazım: tek biçim
        if filled:
            out["tax_filled"] = filled
        return out


def complete_all(entries: list[dict], learn_from: list[dict] | None = None) -> list[dict]:
    b = Book(learn_from if learn_from is not None else entries)
    return [b.complete(e) for e in entries]
