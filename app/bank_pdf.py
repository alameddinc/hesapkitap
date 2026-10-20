"""Banka hesap dökümü PDF'i (vadesiz hesap hareketleri) → satırlar.

PDF düzenleri bankadan bankaya çok farklı (çok satırlı açıklama, boşluksuz metin, ayrı satıra düşen eksi işareti).
Metin yapay zekâyla satırlara ayrılır, sonra her satır BAKİYE ZİNCİRİYLE doğrulanır:
önceki bakiye + tutar = bakiye. İşaret kaybolsa bile bakiye farkı doğru yönü verir.
"""
import re

from app.textnorm import normalize
from app import receipt
from app.parsers import extract_text

PROMPT = """Bu bir Türkiye bankası VADESİZ HESAP HAREKETLERİ dökümünün düz metni (PDF'ten çıkarıldı; satırlar bölünmüş,
kelimeler bitişik, sütunlar karışık olabilir: tutar ile bakiye yer değiştirmiş ya da alt satıra düşmüş olabilir).
Tüm hareketleri sırayla çıkar ve SADECE şu JSON'u döndür:
{
  "owner_name": "hesap sahibinin adı soyadı",
  "bank": "banka adı",
  "opening_balance": 0.00,
  "closing_balance": 0.00,
  "rows": [
    {"date": "YYYY-MM-DD",
     "description": "işlem adı + o harekete ait TÜM açıklama satırları, kelimesi kelimesine, tek satırda",
     "numbers": [0.00, 0.00],
     "amount": 0.00,
     "balance": 0.00}
  ]
}
Kurallar:
- numbers: o harekete ait bloktaki TÜM para tutarları (tutar ve bakiye dahil), göründükleri sırayla; eksi işareti
  ayrı satırda "-" olarak duruyorsa tutara ekle. Referans/işlem/kart/hesap numaralarını, saatleri ve tarihleri KOYMA.
- amount: girene pozitif, çıkana negatif (tahminin). balance: işlemden sonraki bakiye (tahminin).
- description KISALTILMAYACAK ve ÖZETLENMEYECEK: işlem adından sonraki devam satırlarının tamamı (gönderen, alıcı,
  "X hesabından Y hesabına", banka adı, kira/fatura notu, kart no) aynen yazılır. Bu bilgi kendi hesapları
  arasındaki aktarımı ayırt etmek için şart. Sadece referans/sorgu/işlem numaralarını çıkarabilirsin.
- "Devir bakiyesi" satırını rows'a yazma; opening_balance'a yaz. Türk sayı biçimi 1.234,56 = 1234.56.
- Hiçbir hareketi atlama, dökümdeki sırayı koru."""


class BankPdfError(ValueError):
    pass


def parse_account_pdf(data: bytes, passwords: list[str] | None = None) -> dict:
    if not receipt.enabled():
        raise BankPdfError("PDF dökümleri yapay zekâ ile okunur; Ayarlar → Anahtarlar'dan Gemini anahtarı gir "
                           "ya da bankadan Excel/CSV indir.")
    text = None
    for pw in [None, *(passwords or [])]:
        try:
            text = extract_text(data, pw)
            break
        except Exception:  # noqa: BLE001 - şifreli ise sıradaki şifre
            continue
    if text is None:
        raise BankPdfError("PDF açılamadı (şifreli olabilir)")
    best = None
    for _attempt in range(3):  # model bazen bir satır atlar: bakiye zinciri tutana kadar tekrar dene
        res = _parse_text(text)
        if res["verified"]:
            return res
        score = (res["closing_ok"], -len(res["chain_bad"]), len(res["rows"]))
        if best is None or score > best[0]:
            best = (score, res)
    return best[1]


def _parse_text(text: str) -> dict:
    raw, _ = receipt._gemini_json(PROMPT, text.encode(), "text/plain", max_output_tokens=60000)
    rows = []
    for r in raw.get("rows") or []:
        amt, bal = receipt._num(r.get("amount")), receipt._num(r.get("balance"))
        nums = [n for n in (receipt._num(x) for x in (r.get("numbers") or [])) if n is not None]
        if r.get("date") and (amt is not None or nums):
            rows.append({"date": str(r["date"])[:10], "description": str(r.get("description") or "")[:500],
                         "amount": amt, "balance": bal, "numbers": nums})
    opening, closing = receipt._num(raw.get("opening_balance")), receipt._num(raw.get("closing_balance"))
    # Bazı bankalar (ör. VakıfBank) açılış bakiyesini yazmaz; model tahmin eder. Tahmin zincire uymazsa
    # açılışı ilk satırdan geriye hesapla (ilk bakiye ∓ ilk tutar) ve kapanışa kadar tutan adayı seç.
    import copy

    candidates = [opening] if opening is not None else []
    first = rows[0] if rows else {}
    if first.get("amount") is not None and first.get("balance") is not None:
        # metindeki işaret ("-100.000,00") çoğunlukla doğru: önce ona uyan açılışı dene. İlk satırda zincir
        # iki yönü de kabul eder (açılış bilinmediği için), işareti yalnızca bu ayırır.
        candidates.append(round(first["balance"] - first["amount"], 2))
    if rows and len(rows[0].get("numbers") or []) >= 2:
        nums = [abs(n) for n in rows[0]["numbers"]]
        for bi, b in enumerate(nums):
            for ai, a in enumerate(nums):
                if ai != bi:
                    candidates += [round(b - a, 2), round(b + a, 2)]
    chosen = None
    for op in dict.fromkeys(candidates):  # sırayı koru, tekrarları at
        trial = copy.deepcopy(rows)
        solve_chain(trial, op, closing)
        chk = verify_chain(trial, op, closing)
        if chosen is None or (chk["verified"] and not chosen[2]["verified"]):
            chosen = (trial, op, chk)
        if chk["verified"]:
            break
    if chosen is None:
        for r in rows:
            r.pop("numbers", None)
        chosen = (rows, opening, verify_chain(rows, opening, closing))
    rows, opening, check = chosen
    for r in rows:
        r.pop("numbers", None)
    restore_descriptions(rows, text)
    return {"owner_name": raw.get("owner_name") or "", "bank": raw.get("bank") or "", "rows": rows, **check}


def solve_chain(rows: list[dict], opening: float | None, closing: float | None = None) -> None:
    """Tutar/bakiye sütunları karışık olsa da doğru eşleşmeyi bakiye zincirinden bul.

    Bir satırda sayılardan biri yeni bakiye (b), diğeri tutar (a) ve |b − önceki bakiye| = |a| olmalı. Bu tek satırda
    simetrik olabilir (66.157,88 − 53.800,36 = 12.357,52 ve tersi), o yüzden olası tüm yollar izlenir; sonraki
    satırlarla ve kapanış bakiyesiyle tutarlı olan yol seçilir."""
    if opening is None:
        return
    # paths: önceki bakiye → [seçimler]; her satırda yalnızca zincire uyan dallar yaşar
    paths: dict[float, list] = {round(opening, 2): []}
    for r in rows:
        nums = [abs(n) for n in r.get("numbers") or []]
        nxt: dict[float, list] = {}
        for prev, choice in paths.items():
            opts = []
            for bi, bal in enumerate(nums):
                if any(ai != bi and abs(abs(bal - prev) - a) <= 0.01 for ai, a in enumerate(nums)):
                    opts.append(round(bal, 2))
            if not opts and r.get("amount") is not None:  # sayılar eksikse modelin tahmini
                opts.append(round(prev + r["amount"], 2))
            for bal in opts:
                nxt.setdefault(bal, choice + [bal])
        if not nxt:  # zincir koptu: modelin değerleriyle devam et
            return
        paths = nxt
    if closing is not None and round(closing, 2) in paths:
        chosen = paths[round(closing, 2)]
    else:
        # kapanış bilinmiyorsa: her satırda sondaki sayıyı bakiye sayan yola en yakın olanı seç
        chosen = min(paths.values(), key=lambda ch: sum(
            1 for r, bal in zip(rows, ch) if r.get("numbers") and abs(abs(r["numbers"][-1]) - bal) > 0.01))
    prev = round(opening, 2)
    for r, bal in zip(rows, chosen):
        r["balance"], r["amount"] = bal, round(bal - prev, 2)
        prev = bal
    for r in rows:
        r.pop("numbers", None)


def verify_chain(rows: list[dict], opening: float | None, closing: float | None) -> dict:
    """Bakiye zinciri: her satırda önceki bakiye + tutar = bakiye. İşaret hatasını bakiye farkıyla düzelt."""
    prev, ok, fixed, bad = opening, 0, 0, []
    for i, r in enumerate(rows):
        bal = r.get("balance")
        if r.get("amount") is None:
            bad.append(i)
            continue
        if prev is not None and bal is not None:
            diff = round(bal - prev, 2)
            if abs(diff - r["amount"]) <= 0.01:
                ok += 1
            elif abs(abs(diff) - abs(r["amount"])) <= 0.01:
                r["amount"] = diff  # işaret yanlış okunmuş: bakiye farkı esas
                fixed += 1
                ok += 1
            else:
                bad.append(i)
        prev = bal if bal is not None else prev
    closing_ok = closing is None or prev is None or abs(prev - closing) <= 0.01
    return {"chain_ok": ok, "chain_fixed": fixed, "chain_bad": bad, "closing_ok": closing_ok,
            "verified": not bad and closing_ok and ok == len(rows)}


_DATE_LINE = re.compile(r"^\s*(\d{2})[./](\d{2})[./](\d{4})\b")
_NUM_TOKEN = re.compile(r"^-?[\d.,]+$|^\d{1,2}:\d{2}(:\d{2})?$")


def _fmt_amounts(v: float) -> set[str]:
    us = f"{abs(v):,.2f}"
    return {us, us.replace(",", "_").replace(".", ",").replace("_", ".")}


def restore_descriptions(rows: list[dict], text: str) -> None:
    """Model uzun açıklamaları kısaltabiliyor (alıcı adı sonda kalıyor). Satırı ham metindeki bloğuyla
    (aynı tarih + tutar, tarih satırıyla başlayıp sonraki tarih satırına kadar) eşleştir; blok modelin
    açıklamasıyla aynı başlıyorsa tam metni kullan. Düzeni farklı bankalarda (açıklama tarihten önce) dokunmaz."""
    blocks: list[tuple[str, str]] = []  # (iso tarih, blok metni)
    for line in text.splitlines():
        m = _DATE_LINE.match(line)
        if m:
            blocks.append((f"{m.group(3)}-{m.group(2)}-{m.group(1)}", line.strip()))
        elif blocks:
            blocks[-1] = (blocks[-1][0], blocks[-1][1] + " " + line.strip())
    used: set[int] = set()
    squash = lambda t: re.sub(r"[^A-Z0-9]", "", normalize(t))  # noqa: E731
    for r in rows:
        if r.get("amount") is None or not r.get("description"):
            continue
        amts = _fmt_amounts(r["amount"])
        for i, (d, block) in enumerate(blocks):
            if i in used or d != r["date"]:
                continue
            first, *rest = block.split(" ", 1)
            tokens = (rest[0] if rest else "").split()
            if not any(t.lstrip("-+") in amts for t in tokens):
                continue
            # baştaki saat, referans no, tutar, bakiye
            while tokens and (_NUM_TOKEN.match(tokens[0]) or re.fullmatch(r"\d{6,}", tokens[0])):
                tokens.pop(0)
            full = " ".join(tokens)
            # aynı gün aynı tutarda iki havale olabilir (iki farklı kişiden 1.000 TL): başlangıç uzunca uyuşmalı
            n = min(len(squash(r["description"])), 40)
            if n >= 8 and squash(full)[:n] == squash(r["description"])[:n] and len(full) > len(r["description"]):
                r["description"] = full[:500]
                used.add(i)
                break
