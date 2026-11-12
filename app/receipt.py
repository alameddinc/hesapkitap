"""Fiş fotoğrafından gider bilgisi çıkarma (Gemini API, REST)."""
import base64
import json
import logging
import os
import re
from datetime import date

import httpx

from app import config, vat

log = logging.getLogger("hesapkitap.receipt")

GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
DEFAULT_MODEL = "gemini-3.1-flash-lite"
MAX_BYTES = 15 * 1024 * 1024

ITEM_RULES = """items (sadece fiş, en fazla 40 satır, fişteki sırayla):
- name: satırın fişteki yazısı. product: aynı ürünü farklı marketlerde eşleştirmek için STANDART ad: marka + ürün + gramaj/hacim,
  Türkçe karakterli, kısaltmalar açık (ör. "ULKER GOFRET CIK 36GR" → "Ülker Çikolatalı Gofret 36 g"; "SUT 1L PINAR" → "Pınar Süt 1 L").
- brand: ürünün markası (ör. Ülker, Pınar, Marlboro); kafe/restoranın kendi ürünüyse (latte, tost) işletmenin markası
  (ör. Kahve Dünyası); bilinmiyorsa null.
- "2Adt x 230,00 Latte" → qty=2, unit="adet", unit_price=230, amount=460 (satır toplamı). unit_price yalnız fişte birim
  fiyat yazıyorsa (miktar satırı), yoksa null.
- Tartılı ürün "0,750 KG x 89,90" → qty=0.75, unit="kg", amount=67.43; litre ise unit="lt".
- Türk yazar kasa fişinde MİKTAR SATIRI ("2 ad X 2,50", "2 AD X 40.00", "0,750 KG X 89,90") ÜRÜN SATIRINDAN ÖNCE, kendi
  satırında gelir ve ALTINDAKİ ürüne aittir; o ürünün satırında sağda toplam yazar ("*5,00" = 2 × 2,50). Miktar satırını
  AYRI ÜRÜN olarak YAZMA ve adını ürün adına katma; qty ve unit'i altındaki ürüne ver. Her ürünün amount'u KENDİ satırının
  sağındaki tutardır (bir alttaki ya da üstteki ürünün tutarı değil). Örnek:
    2 ad X 2,50
    POSET ORTA BOY        *5,00
    MADEN SUYU 6X200      *60,00
  → [{{"name": "POSET ORTA BOY", "qty": 2, "amount": 5.00}}, {{"name": "MADEN SUYU 6X200", "qty": 1, "amount": 60.00}}]
- Bir ürünün hemen altındaki İNDİRİM/KAMPANYA satırı o ürünün amount'undan düşülür. Hangi ürüne ait olduğu belli olmayan genel
  indirimi ("İNDİRİMLER", "Satış İndirimi", "TOPLAM İNDİRİM", "İskonto", kupon/puan kullanımı) fişin sonunda TEK kalem olarak
  yaz: name fişteki adı (ör. "Satış İndirimi"), amount EKSİ (ör. -73.00), qty 1. "TOPLAM İNDİRİM" ile aynı indirimi iki kez yazma.
  Böylece kalemlerin toplamı GENEL TOPLAM'ı tutar (ör. ara toplam 730,00 − indirim 73,00 = toplam 657,00).
- Fiyatı okunamayan ürünü amount null ile yine yaz.
- Birden fazla görüntü varsa hepsi AYNI fişin sırayla parçalarıdır: üst üste binen (iki fotoğrafta da görünen) satırları BİR kez
  yaz; total son parçadaki GENEL TOPLAM'dır.
"""


PROMPT = """Bu bir Türkiye'de alınmış alışveriş fişi fotoğrafı. Bilgileri çıkar ve SADECE şu JSON'u döndür:
{{
  "merchant": "MARKA / tabela adı, kısa (ör. Espressolab, Migros)",
  "legal_name": "fişteki resmi şirket unvanı (ör. NEW TASTE GIDA SAN.VE DIŞ.TİC.LTD.ŞTİ.) veya null",
  "date": "YYYY-MM-DD (fişteki tarih; okunamazsa null)",
  "total": 0.00,
  "vat": 0.00,
  "payment": "nakit | kart | bilinmiyor",
  "category": "şu listeden en uygun olan: {categories}",
  "items": [{{"name": "fişteki satır yazısı", "product": "standart ürün adı", "qty": 1, "unit": "adet", "unit_price": null, "amount": 0.00}}],
  "confidence": "yüksek | orta | düşük"
}}
Kurallar: total = fişteki GENEL TOPLAM / TOPLAM (KDV dahil, ödenen tutar). Türk sayı formatında 1.234,56 = 1234.56.
Fiş değilse veya okunamıyorsa total null ve confidence "düşük" döndür.
""" + ITEM_RULES




def _gemini_reason(status: int, msg: str) -> str:
    """Gemini'nin (İngilizce) hata yanıtından kullanıcıya Türkçe neden."""
    m = (msg or "").lower()
    if status in (401, 403) or "api key" in m or "permission" in m:
        return "Gemini anahtarı geçersiz ya da yetkisiz; Ayarlar → Anahtarlar'dan kontrol et"
    if status == 429 or "quota" in m or "exhausted" in m:
        return "Yapay zekâ servisinin kullanım sınırı doldu ya da şu an yoğun; birkaç dakika sonra tekrar dene"
    if status == 400 and ("size" in m or "too large" in m or "token" in m):
        return "Belge yapay zekâ için çok büyük; daha küçük parçalara bölüp tekrar dene"
    if status == 404 or "model" in m and "not found" in m:
        return "Ayarlardaki yapay zekâ modeli bulunamadı; Ayarlar → Anahtarlar'dan model adını kontrol et"
    if status >= 500:
        return "Yapay zekâ servisi şu an yanıt vermiyor; biraz sonra tekrar dene"
    return "Yapay zekâ belgeyi okuyamadı; tekrar dene ya da elle gir"


class ReceiptError(Exception):
    pass


def _shared_key_user() -> dict | None:
    """Kendi sunucusu: ortak (yöneticinin) anahtarını kullanan yönetici-olmayan kullanıcı; kota ona, yapay zekâ çağrısı
    başına uygulanır. Hazır sürümde None: orada hak BELGE başınadır (app/doc_reads.py) — bir belgenin içindeki birden çok
    yapay zekâ çağrısı ayrıca sayılmaz, çift sayım olmaz."""
    from app import db, plans

    if plans.hosted():
        return None
    u = db.current_user()
    if not u or u.get("role") == "admin" or config.own_ai_key():
        return None
    return u


def _check_quota() -> None:
    from app import system

    u = _shared_key_user()
    if not u:
        return
    quota, used = system.ai_quota(u), system.ai_used_pool(u)  # ortak defterde ailenin ortak havuzu
    if quota is not None and used >= quota:
        raise ReceiptError(f"Bu ayki yapay zekâ okuma hakkın doldu ({used}/{quota}). Ay başında yenilenir; beklemek "
                           "istemezsen Ayarlar'dan kendi Gemini anahtarını girebilirsin.")


class DailyLimitError(ReceiptError):
    """Kullanıcı başı günlük yapay zekâ çağrısı sınırı (S18): istemci elle girişe yönlendirir."""

    def fields(self) -> dict:
        return {"manual": True, "daily_limit": True}


def daily_cap() -> int:
    """HESAPKITAP_AI_DAILY (0 = sınırsız). Varsayılan: hazır sürümde 150 çağrı/gün, kendi sunucusunda sınırsız."""
    from app import plans

    v = os.environ.get("HESAPKITAP_AI_DAILY")
    return int(v) if v and v.strip().isdigit() else (150 if plans.hosted() else 0)


def _daily_user() -> dict | None:
    """Sınır kime uygulanır: ortak anahtarı kullanan yönetici-olmayan kullanıcı (kendi anahtarı olan sayılmaz)."""
    from app import db

    u = db.current_user()
    if not u or u.get("role") == "admin" or not u.get("id") or config.own_ai_key():
        return None
    return u


def _daily_gate() -> None:
    """Çağrıdan önce: bugünkü çağrı sayısı sınırdaysa DailyLimitError; değilse sayar (başarısız çağrı da maliyettir)."""
    from datetime import date as _date

    from app import system

    cap, u = daily_cap(), _daily_user()
    if not cap or not u:
        return
    day = _date.today().isoformat()
    with system.connect() as c:
        c.execute("CREATE TABLE IF NOT EXISTS ai_daily (user_id INTEGER NOT NULL, day TEXT NOT NULL, "
                  "n INTEGER NOT NULL DEFAULT 0, PRIMARY KEY (user_id, day))")
        r = c.execute("SELECT n FROM ai_daily WHERE user_id = ? AND day = ?", (u["id"], day)).fetchone()
        if r and r[0] >= cap:
            raise DailyLimitError("Bugünkü yapay zekâ okuma sınırına ulaştın; yarın yeniden açılır. Bu arada kaydı "
                                  "elle ekleyebilirsin.")
        c.execute("INSERT INTO ai_daily (user_id, day, n) VALUES (?, ?, 1) ON CONFLICT(user_id, day) DO UPDATE SET "
                  "n = n + 1", (u["id"], day))
        c.execute("DELETE FROM ai_daily WHERE day < date(?, '-7 days')", (day,))


def _count_usage() -> None:
    from app import system

    u = _shared_key_user()
    if u:
        system.ai_add(u["id"])


def enabled() -> bool:
    return bool(config.get("GEMINI_API_KEY"))


def _extract_json(text: str) -> dict:
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        raise ReceiptError("Yapay zekâ okunabilir bir yanıt vermedi; tekrar dene ya da elle gir")
    return json.loads(m.group(0))


def _num(v, qty: bool = False) -> float | None:
    """Fiş/fatura sayısı. Tutar (varsayılan) Türk biçimleriyle: "1.234,50", "12,90"; virgül yoksa tek/çok nokta ve
    ardından tam 3 hane binliktir ("1.234" → 1234, "1.234.567"), 2 hane ondalıktır ("2.90" → 2,9). İkisi birden varsa
    sondaki ayraç ondalıktır ("1,234.56"). Tutar yarım yukarı kuruşa yuvarlanır (app/money.py).
    qty=True (adet/kg): nokta da virgül de ondalıktır ("1.250" kg → 1,25), 3 haneye yuvarlanır ("0,915" kg)."""
    import re

    from app import money

    if v is None or v == "" or isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return round(float(v), 3) if qty else money.money(v)
    s = re.sub(r"\s|₺|TL", "", str(v))
    neg = s.startswith("-")
    s = s.lstrip("+-")
    if not re.fullmatch(r"[\d.,]*\d[\d.,]*", s):
        return None
    if qty:
        s = s.replace(",", ".")
    elif "," in s and "." in s:
        s = s.replace(".", "").replace(",", ".") if s.rfind(",") > s.rfind(".") else s.replace(",", "")
    elif "," in s:
        s = s.replace(".", "").replace(",", ".")
    elif re.fullmatch(r"\d{1,3}(\.\d{3})+", s):  # binlik nokta
        s = s.replace(".", "")
    try:
        x = float(s)
    except ValueError:
        return None
    x = -x if neg else x
    return round(x, 3) if qty else money.money(x)  # adet/kg 3 hane: tartılı ürün 0,915 kg


BILL_PROMPT = """Bu bir Türkiye'de kesilmiş fatura (elektrik, doğalgaz, su, internet, telefon, aidat vb.). PDF veya fotoğraf olabilir.
SADECE şu JSON'u döndür:
{
  "provider": "kurum adı, kısa (ör. Enerjisa, İGDAŞ, Türk Telekom, Turkcell, İSKİ)",
  "type": "elektrik | dogalgaz | su | internet | telefon | aidat | diger",
  "amount": 0.00,
  "due_date": "YYYY-MM-DD (SON ÖDEME TARİHİ; okunamazsa null)",
  "issue_date": "YYYY-MM-DD veya null",
  "period": "fatura dönemi, ör. 'Ekim 2026' veya null",
  "subscriber_no": "abone/tesisat/hizmet no veya null",
  "paid": "belge bu faturanın ÖDENDİĞİNİ gösteriyorsa true (banka ödeme dekontu, tahsilat makbuzu, 'ödendi', 'tahsil edildi', 'işlem başarılı', 'fatura ödeme'); ödenecek fatura ise false",
  "paid_at": "YYYY-MM-DD ödeme/işlem tarihi (sadece paid true ise) veya null",
  "pay_method": "havale | kredi_karti | nakit | bilinmiyor (dekontta hesaptan/IBAN'dan ödeme veya otomatik ödeme → havale; kartla ödeme → kredi_karti)",
  "doc_kind": "fatura | transfer (kişiye/hesaba havale-EFT-FAST para transferi dekontu: GÖNDEREN, ALICI, IBAN var; kurum/abone yok)",
  "confidence": "yüksek | orta | düşük"
}
amount = ÖDENECEK TUTAR (KDV dahil toplam); dekontta ÖDENEN TUTAR. Türk sayı formatında 1.234,56 = 1234.56."""

BILL_CATEGORY = {"elektrik": "Elektrik", "dogalgaz": "Doğalgaz", "su": "Su", "internet": "İnternet & TV",
                 "telefon": "Telefon", "aidat": "Kira & Aidat"}


def _pdf_pages(data: bytes, mime: str) -> int | None:
    """Yapay zekâya gidecek belgenin sayfa sayısı: PDF'te sayfa sayısı, PDF'ten çıkarılmış metinde (banka dökümü, şifreli
    ekstre) bilinen sayfaları; bilinmiyorsa None (sınır uygulanmaz)."""
    if mime == "application/pdf":
        try:
            import io

            import pdfplumber

            with pdfplumber.open(io.BytesIO(data)) as pdf:
                return len(pdf.pages)
        except Exception:  # noqa: BLE001 - açılamayan (şifreli) PDF: model kendi hatasını verir
            return None
    if mime == "text/plain":
        from app import parsers

        try:
            pages = parsers.pages_of(data.decode())
        except UnicodeDecodeError:
            return None
        return len(pages) if pages else None
    return None


def _check_ai_pages(docs: list[tuple[bytes, str]]) -> None:
    """Maliyet güvenliği: yapay zekâya en çok limits.ai_max_pages() sayfa (HESAPKITAP_AI_MAX_PAGES, varsayılan 50)."""
    from app import limits

    most = limits.ai_max_pages()
    for d, m in docs:
        n = _pdf_pages(d, m)
        if n and n > most:
            raise ReceiptError(f"Bu belge {n} sayfa; yapay zekâ en çok {most} sayfalık belge okur. Bankadan daha kısa "
                               "dönemli bir döküm indir ya da Excel/CSV dosyasını yükle; harcamaları elle de girebilirsin.")


# kind="otomatik…": belge türü yanıttan anlaşılır; fatura → belge, fiş → bu tür (app/ai_costs.py KINDS)
AUTO_KINDS = {"otomatik": "fis", "otomatik_metin": "fis_metin", "otomatik_foto": "fis_foto"}


def _gemini_json(prompt: str, data: bytes | None, mime: str, max_output_tokens: int | None = None,
                 more: list[tuple[bytes, str]] | None = None, model: str | None = None,
                 kind: str = "belge", media_resolution: str | None = None,
                 thinking: str | None = None) -> tuple[dict, dict]:
    """more: aynı belgenin devam görüntüleri (uzun fiş birden fazla fotoğraf). kind: maliyet ölçümünde tür
    (fis | fis_metin | fis_foto | belge | dokum | kategori; AUTO_KINDS'takiler yanıttaki belge türünden — fatura → belge,
    diğeri → fiş türü). media_resolution: generationConfig.mediaResolution (ör. MEDIA_RESOLUTION_MEDIUM); None → modelin varsayılanı.
    thinking: düşünme düzeyi (minimal | low | medium | high; receipt_thinking) — model destekliyorsa
    generationConfig.thinkingConfig; model reddederse (400, düşünme ayarı) bir kez ayarsız yeniden denenir."""
    key = config.get("GEMINI_API_KEY")
    if not key:
        raise ReceiptError("Gemini API anahtarı tanımlı değil (Ayarlar → Anahtarlar)")
    from app import ai_restrictions

    ai_restrictions.check_ai()  # yönetici kısıtlaması (no_ai / günlük sınır dolu): yapay zekâya gidilmez
    _check_quota()
    docs =([(data, mime)] if data is not None else []) + list(more or [])
    if sum(len(d) for d, _ in docs) > MAX_BYTES:
        raise ReceiptError("Dosya çok büyük (en fazla 15 MB)")
    _check_ai_pages(docs)
    _daily_gate()
    model = model or config.get("GEMINI_MODEL") or DEFAULT_MODEL
    think = _thinking_config(model, thinking)
    body = {
        "contents": [{"parts": [
            {"text": prompt},
            *({"inline_data": {"mime_type": m, "data": base64.b64encode(d).decode()}} for d, m in docs),
        ]}],
        "generationConfig": {"responseMimeType": "application/json", "temperature": 0,
                             **({"maxOutputTokens": max_output_tokens} if max_output_tokens else {}),
                             **({"mediaResolution": media_resolution} if media_resolution else {}),
                             **({"thinkingConfig": think} if think else {})},
    }
    try:
        r = httpx.post(GEMINI_URL.format(model=model), json=body, timeout=180,
                       headers={"x-goog-api-key": key})
        if think and r.status_code == 400 and "think" in r.text.lower():  # model düşünme ayarını tanımıyor
            log.warning("Gemini düşünme ayarını reddetti (%s, %s); ayarsız yeniden deneniyor", model, think)
            body["generationConfig"].pop("thinkingConfig")
            r = httpx.post(GEMINI_URL.format(model=model), json=body, timeout=180,
                           headers={"x-goog-api-key": key})
    except httpx.HTTPError as e:
        log.warning("Gemini'ye ulaşılamadı: %s", e)
        raise ReceiptError("Yapay zekâ servisine ulaşılamadı; biraz sonra tekrar dene") from e
    if r.status_code != 200:
        msg = r.json().get("error", {}).get("message", r.text[:200]) if r.headers.get("content-type", "").startswith("application/json") else r.text[:200]
        log.warning("Gemini hatası (%s): %s", r.status_code, msg)  # İngilizce ayrıntı günlükte, kullanıcıya Türkçe neden
        raise ReceiptError(_gemini_reason(r.status_code, msg))

    resp = r.json()
    try:
        text = "".join(p.get("text", "") for p in resp["candidates"][0]["content"]["parts"])
    except (KeyError, IndexError) as e:
        raise ReceiptError("Yapay zekâ boş yanıt verdi; tekrar dene ya da elle gir") from e
    _count_usage()
    usage = resp.get("usageMetadata", {})
    try:
        out = _extract_json(text)
    except (ReceiptError, ValueError):  # bozuk / yarım yanıt da ücretlidir: maliyet tavanları (ai_costs) saysın
        _record_cost(AUTO_KINDS.get(kind, kind), model, usage)
        raise
    if kind in AUTO_KINDS:
        kind = "belge" if out.get("doc_type") == "fatura" or out.get("due_date") else AUTO_KINDS[kind]
    _record_cost(kind, model, usage)
    return out, {"model": model, "input": usage.get("promptTokenCount"), "output": usage.get("candidatesTokenCount"),
                 "thoughts": usage.get("thoughtsTokenCount"), "cached": usage.get("cachedContentTokenCount")}


def _record_cost(kind: str, model: str, usage: dict) -> None:
    """Maliyet ölçümü (app/ai_costs.py): token sayıları; içerik/metin kaydedilmez. Kayıt hatası okumayı bozmaz."""
    try:
        from app import ai_costs, db

        u = db.current_user()
        ai_costs.record((u or {}).get("id"), kind, model, usage)
    except Exception:  # noqa: BLE001
        log.warning("yapay zekâ maliyet kaydı yazılamadı", exc_info=True)


def gemini_text_json(prompt: str, max_output_tokens: int | None = 4096, kind: str = "kategori",
                     model: str | None = None) -> tuple[dict, dict]:
    """Belgesiz, yalnız metin istemi (ör. işyeri adlarına kategori önerisi, ürün adlarına grup: kind "grup"). Kota/günlük
    sınır _gemini_json'daki gibi. model: None → yapılandırılan / varsayılan model."""
    return _gemini_json(prompt, None, "text/plain", max_output_tokens=max_output_tokens, kind=kind, model=model)


def card_last4(v) -> str | None:
    """Kartın son 4 hanesi: "1234", "****1234", 1234 → "1234"; okunamadıysa None."""
    m = re.search(r"(\d{4})\D*$", str(v or ""))
    return m.group(1) if m else None


def _installments(v) -> int:
    try:
        n = int(float(str(v).strip()))
    except (TypeError, ValueError):
        return 1
    return n if 2 <= n <= 36 else 1


# miktar satırı: "2 ad X 2.50", "2 AD x 40,00", "0,750 KG X 89,90" (ardından ürün adı gelebilir: model birleştirmiş)
QTY_LINE = re.compile(r"^\s*(\d+(?:[.,]\d+)?)\s*(AD|ADT|ADET|KG|GR|LT|L)\.?\s*[xX×*]\s*(\d+(?:[.,]\d{1,3})?)\s*", re.I)


def _qty_of(m: re.Match) -> tuple[float, str, float]:
    n, unit, price = float(m.group(1).replace(",", ".")), m.group(2).upper(), float(m.group(3).replace(",", "."))
    return n, ("kg" if unit in ("KG", "GR") else "lt" if unit in ("LT", "L") else "adet"), price


def fix_qty_lines(items: list[dict]) -> list[dict]:
    """Model miktar satırını ("2 ad X 2.50") ayrı ürün yazdıysa ya da ürün adına kattıysa düzelt.

    Türk fişinde miktar satırı ürün satırının ÜSTÜNDEDİR. Model onu ayrı ürün yazıp ürünün tutarını ona verince sonraki
    adlar bir aşağı kayar ("2× 2 ad X 2.50 5,00", "POSET ORTA BOY 60,00"…): tutarların sırası doğru, adlar kaymış.
    O noktadan sonra adlar (miktar satırları çıkarılarak) tutarlarla yeniden eşlenir; ad kalmayan son tutar 'Okunamayan
    ürün' olur (toplam korunur). Miktar (adet × birim) toplamı tutan ilk satıra verilir: '2 ad X 40.00' → 80,00'lik ürün."""
    toks = []
    for it in items:
        m = QTY_LINE.match(it.get("name") or "")
        q = _qty_of(m) if m else None
        rest = (it["name"][m.end():] if m else it.get("name") or "").strip(" -*")
        toks.append({**it, "_q": q, "_rest": rest})
    if not any(t["_q"] for t in toks):
        return items

    def total(q):
        return round(q[0] * q[2], 2)

    def near(a, b):
        return a is not None and abs(a - b) <= 0.02

    # tutar yuvası kaplayan (kaydıran) ayrı miktar satırı: tutarı adet × birim ve bir sonraki satırın tutarı değil
    shift_at = next((k for k, t in enumerate(toks) if t["_q"] and not t["_rest"] and near(t.get("amount"), total(t["_q"]))
                     and not (k + 1 < len(toks) and near(toks[k + 1].get("amount"), total(t["_q"])))), None)
    head = [t for t in toks[: shift_at if shift_at is not None else len(toks)] if t["_rest"]]
    out = [{**t, "name": t["_rest"]} for t in head]
    if shift_at is not None:
        tail = toks[shift_at:]
        names = [t for t in tail if t["_rest"]]
        amounts = [t.get("amount") for t in tail if t["_rest"] or near(t.get("amount"), total(t["_q"]))]
        for i in range(max(len(names), len(amounts))):
            if i < len(names):
                out.append({**names[i], "name": names[i]["_rest"], "amount": amounts[i] if i < len(amounts) else None,
                            "qty": 1, "unit": "adet"})
            elif amounts[i] is not None:
                out.append({"name": "Okunamayan ürün", "product": None, "brand": None, "qty": 1, "unit": "adet",
                            "amount": amounts[i], "split": None})
    # miktarlar: miktar satırından sonraki (birleşikse kendisi) ilk üç ürün içinde tutarı adet × birim olana
    taken: set[int] = set()
    seen_names = 0
    for t in toks:
        if t["_q"]:
            n, unit, _ = t["_q"]
            for j in range(min(seen_names, len(out) - 1), min(seen_names + 3, len(out))):
                if j not in taken and near(out[j].get("amount"), total(t["_q"])):
                    out[j] = {**out[j], "qty": int(n) if unit == "adet" and float(n).is_integer() else n, "unit": unit,
                              "unit_price": t["_q"][2]}
                    taken.add(j)
                    break
        if t["_rest"]:
            seen_names += 1
    for o in out:
        o.pop("_q", None)
        o.pop("_rest", None)
    return out


def _near(a, b) -> bool:
    return isinstance(a, (int, float)) and isinstance(b, (int, float)) and abs(a - b) <= 0.02


def fix_misattached_qty(items: list[dict]) -> list[dict]:
    """Model miktarı ("2 ADET X 194,95") yanlış ürüne bağladıysa komşusuna taşı.

    Kalemin adet × birimi kendi tutarını değil de hemen altındaki (ya da üstündeki) ürünün tutarını veriyorsa ve o komşunun
    kendi adet × birimi yoksa ya da tutmuyorsa: adet, birim ve birim fiyat komşuya geçer, kalem tek adet kalır. Örn.
    "2× EKER YOGURT 124,95 (2 × 194,95)" + "PINAR SUZME 389,90" → "EKER YOGURT 124,95" + "2× PINAR SUZME 389,90"."""
    out = [dict(it) for it in items]

    def exp(it):
        up, q = it.get("unit_price"), it.get("qty") or 1
        return round(q * up, 2) if isinstance(up, (int, float)) and up > 0 else None

    for i, it in enumerate(out):
        e = exp(it)
        if e is None or _near(it.get("amount"), e):
            continue
        for j in (i + 1, i - 1):
            if not 0 <= j < len(out):
                continue
            nb = out[j]
            if not _near(nb.get("amount"), e):
                continue
            ne = exp(nb)
            if ne is not None and _near(nb.get("amount"), ne):
                continue  # komşunun kendi miktarı doğru: dokunma
            out[j] = {**nb, "qty": it.get("qty"), "unit": it.get("unit") or "adet", "unit_price": it.get("unit_price")}
            out[i] = {**it, "qty": 1, "unit": "adet", "unit_price": None}
            break
    return out


def _items(raw: dict) -> list[dict]:
    """Ürün satırları; "2Adt x 230,00" gibi adetler qty olarak gelir."""
    out = []
    items = [_long_item(i) for i in (raw.get("items") or [])[:40]] if isinstance(raw.get("items"), list) else []
    raw = {**raw, "items": fix_qty_lines([i for i in items if isinstance(i, dict) and i.get("name")])}
    for i in (raw.get("items") or [])[:40]:
        if not isinstance(i, dict) or not i.get("name"):
            continue
        unit = i.get("unit") if i.get("unit") in ("adet", "kg", "lt") else "adet"
        q = _num(i.get("qty"), qty=True)
        q = q if q and q > 0 else 1
        if unit == "adet":
            q = int(q) if float(q).is_integer() and q >= 1 else 1
        split = i.get("split") if isinstance(i.get("split"), str) and i.get("split") else None
        product = str(i.get("product") or "").strip()[:80] or None
        brand = str(i.get("brand") or "").strip()[:40] or None
        out.append({"name": str(i["name"])[:60], "product": product, "brand": brand, "qty": q, "unit": unit,
                    "amount": _num(i.get("amount")), "split": split, "unit_price": _num(i.get("unit_price"))})
    return fix_misattached_qty(out)


def _iso(d) -> str | None:
    try:
        return date.fromisoformat(d).isoformat() if d else None
    except (ValueError, TypeError):
        return None


DOC_PROMPT = """Bu belge Türkiye'den; ya bir ALIŞVERİŞ FİŞİ (ödeme yapılmış, POS/yazar kasa fişi) ya da
ödenecek bir FATURA (elektrik, doğalgaz, su, internet, telefon, aidat; "son ödeme tarihi" içerir). Bir faturanın banka ödeme
dekontu ya da tahsilat makbuzu da "fatura" sayılır (paid true). PDF veya fotoğraf olabilir.
SADECE şu JSON'u döndür:
{{
  "doc_type": "fis | fatura",
  "merchant": "MARKA / tabela / kurum adı, kısa (ör. Espressolab, Enerjisa)",
  "legal_name": "belgedeki resmi şirket unvanı (ör. NEW TASTE GIDA SAN.VE DIŞ.TİC.LTD.ŞTİ.) veya null",
  "address": "fişte yazan işyeri adresi (mahalle/cadde, ilçe/il) kısa, veya null",
  "branch": "şube / semt adı, kısa (ör. Fıstıkağacı, Kadıköy Moda); fiş başlığında ya da adreste geçen; yoksa null",
  "date": "YYYY-MM-DD fiş tarihi / fatura düzenleme tarihi veya null",
  "total": 0.00,
  "due_date": "YYYY-MM-DD SON ÖDEME TARİHİ (sadece fatura) veya null",
  "bill_type": "elektrik | dogalgaz | su | internet | telefon | aidat | diger | null",
  "period": "fatura dönemi veya null",
  "subscriber_no": "abone/tesisat no veya null",
  "paid": "sadece fatura: belge faturanın ÖDENDİĞİNİ gösteriyorsa (banka fatura ödeme dekontu, tahsilat makbuzu, 'ödendi', 'tahsil edildi') true, ödenecek fatura ise false",
  "paid_at": "YYYY-MM-DD faturanın ödendiği gün (sadece paid true ise) veya null",
  "pay_method": "sadece ödenmiş fatura: havale | kredi_karti | nakit | bilinmiyor",
  "payment": "nakit | kart | yemek_karti | bilinmiyor  (YEMEK ÇEKİ/KARTI, Multinet, Sodexo/Pluxee, Edenred, Setcard, Metropol → yemek_karti)",
  "payment_card": "yemek/hediye kartı markası (ör. Multinet) veya null",
  "card_last4": "kartla ödendiyse slipte/fişte maskeli yazan kart numarasının SON 4 hanesi (ör. 4000 **** **** 1234 → \"1234\"); yoksa null",
  "installments": "kartla TAKSİTLİ ödendiyse taksit sayısı (fişte/slipte 'TAKSİT SAYISI: 3', '3 TAKSİT', '3 AY' gibi), tek çekimse 1",
  "category": "fiş ise şu listeden en uygunu: {categories}",
  "vat_total": "sadece fiş: TOPKDV / TOPLAM KDV tutarı veya null",
  "vat_lines": [{{"rate": 1, "amount": 0.00}}],
  "tax_no": "sadece fiş: satıcının 10 haneli vergi no (V.NO, VKN, V.D. … NO); 11 haneli TCKN ise null",
  "tax_office": "sadece fiş: vergi dairesi adı (ör. Kadıköy) veya null",
  "items": [{{"name": "fişteki satır yazısı", "product": "standart ürün adı", "brand": "marka veya null", "qty": 1, "unit": "adet", "unit_price": null, "amount": 0.00, "split": null}}],
  "confidence": "yüksek | orta | düşük"
}}
{split_rule}total = ödenen/ödenecek GENEL TOPLAM (KDV dahil). Türk sayı formatında 1.234,56 = 1234.56.
vat_lines: fişin oran oran KDV dökümü ("%1 *18,25", "KDV %20 3,33"); rate 1 | 10 | 20, her oran bir kez; döküm yoksa [].
""" + ITEM_RULES


SPLIT_RULE = """items[].split: ürün şu kategorilerden birine AÇIKÇA giriyorsa o kategorinin adı, değilse null: {cats}.
Fiş kısaltmalarını çöz (ör. "MRLB TCH BLU" = Marlboro sigara). Emin değilsen null.
"""

# ---------------------------------------------------------------- sıkı çıktı (maliyet diyeti, 04.11.2026)
# Fotoğraf okumasının maliyetinin yarısı çıktıydı (~485 token): girintili JSON, her alanın "null"u, her kalemde uzun
# anahtarlar ve varsayılanlar (qty 1, unit adet, unit_price null, split null). Sıkı istem aynı alanları ve kuralları ister
# ama tek satır, null'suz JSON ve kısa kalem anahtarları (n/p/b/q/u/up/a/s; _long_item uzun adlara çevirir) ister.
# Okunamayan (bulanık, fiş olmayan) fotoğrafta tutar uydurmasın diye bir kural eklendi. Ölçüm (40 gerçek fiş,
# app/receipt_bench.py; ayrıntı rapor): çıktı 485 → 294 token, ₺0,0755 → ₺0,0624/fiş; toplam 39/39, kalem 7 → 8/12,
# KDV 25/25, VKN 20/20 (değişmedi). Kurallar kısaltılınca (girdi −420 token) bulanık fişlerde toplam 39 → 36–38/39'a
# düştü; görüntü çözünürlüğü MEDIUM'da (−524 token) 34/39: ikisi de varsayılan değil.
# HESAPKITAP_RECEIPT_COMPACT=0: eski istem (DOC_PROMPT, uzun anahtarlar, girintili JSON).
DOC_PROMPT_COMPACT = """Bu belge Türkiye'den; ya bir ALIŞVERİŞ FİŞİ (ödeme yapılmış, POS/yazar kasa fişi) ya da
ödenecek bir FATURA (elektrik, doğalgaz, su, internet, telefon, aidat; "son ödeme tarihi" içerir). Bir faturanın banka ödeme
dekontu ya da tahsilat makbuzu da "fatura" sayılır (paid true). PDF veya fotoğraf olabilir.
SADECE şu JSON'u TEK SATIR, girintisiz döndür (null olan alanı ve kalemde q 1, u "adet", up null değerini YAZMA):
{{
  "doc_type": "fis | fatura",
  "merchant": "MARKA / tabela / kurum adı, kısa (ör. Espressolab, Enerjisa)",
  "legal_name": "belgedeki resmi şirket unvanı (ör. NEW TASTE GIDA SAN.VE DIŞ.TİC.LTD.ŞTİ.) veya null",
  "address": "fişte yazan işyeri adresi (mahalle/cadde, ilçe/il) kısa, veya null",
  "branch": "şube / semt adı, kısa (ör. Fıstıkağacı, Kadıköy Moda); fiş başlığında ya da adreste geçen; yoksa null",
  "date": "YYYY-MM-DD fiş tarihi / fatura düzenleme tarihi veya null",
  "total": 0.00,
  "due_date": "YYYY-MM-DD SON ÖDEME TARİHİ (sadece fatura) veya null",
  "bill_type": "elektrik | dogalgaz | su | internet | telefon | aidat | diger | null",
  "period": "fatura dönemi veya null",
  "subscriber_no": "abone/tesisat no veya null",
  "paid": "sadece fatura: belge faturanın ÖDENDİĞİNİ gösteriyorsa (banka fatura ödeme dekontu, tahsilat makbuzu, 'ödendi', 'tahsil edildi') true, ödenecek fatura ise false",
  "paid_at": "YYYY-MM-DD faturanın ödendiği gün (sadece paid true ise) veya null",
  "pay_method": "sadece ödenmiş fatura: havale | kredi_karti | nakit | bilinmiyor",
  "payment": "nakit | kart | yemek_karti | bilinmiyor  (YEMEK ÇEKİ/KARTI, Multinet, Sodexo/Pluxee, Edenred, Setcard, Metropol → yemek_karti)",
  "payment_card": "yemek/hediye kartı markası (ör. Multinet) veya null",
  "card_last4": "kartla ödendiyse slipte/fişte maskeli yazan kart numarasının SON 4 hanesi (ör. 4000 **** **** 1234 → \"1234\"); yoksa null",
  "installments": "kartla TAKSİTLİ ödendiyse taksit sayısı (fişte/slipte 'TAKSİT SAYISI: 3', '3 TAKSİT', '3 AY' gibi), tek çekimse 1",
  "category": "fiş ise şu listeden en uygunu: {categories}",
  "vat_total": "sadece fiş: TOPKDV / TOPLAM KDV tutarı veya null",
  "vat_lines": [{{"rate": 1, "amount": 0.00}}],
  "tax_no": "sadece fiş: satıcının 10 haneli vergi no (V.NO, VKN, V.D. … NO); 11 haneli TCKN ise null",
  "tax_office": "sadece fiş: vergi dairesi adı (ör. Kadıköy) veya null",
  "items": [{{"n": "fişteki satır yazısı (name)", "p": "standart ürün adı (product)", "b": "marka (brand) veya null", "q": 1, "u": "adet", "up": null, "a": 0.00}}],
  "confidence": "yüksek | orta | düşük"
}}
{split_rule}total = ödenen/ödenecek GENEL TOPLAM (KDV dahil). Türk sayı formatında 1.234,56 = 1234.56.
Belge okunamıyorsa (bulanık, fiş/fatura değil) tutar UYDURMA: total null, confidence "düşük".
vat_lines: fişin oran oran KDV dökümü ("%1 *18,25", "KDV %20 3,33"); rate 1 | 10 | 20, her oran bir kez; döküm yoksa [].
items (kısa anahtarlar: n=name, p=product, b=brand, q=qty, u=unit, up=unit_price, a=amount; sadece fiş, en fazla 40 satır, fişteki sırayla):
- name: satırın fişteki yazısı. product: aynı ürünü farklı marketlerde eşleştirmek için STANDART ad: marka + ürün + gramaj/hacim,
  Türkçe karakterli, kısaltmalar açık (ör. "ULKER GOFRET CIK 36GR" → "Ülker Çikolatalı Gofret 36 g"; "SUT 1L PINAR" → "Pınar Süt 1 L").
- brand: ürünün markası (ör. Ülker, Pınar, Marlboro); kafe/restoranın kendi ürünüyse (latte, tost) işletmenin markası
  (ör. Kahve Dünyası); bilinmiyorsa null.
- "2Adt x 230,00 Latte" → qty=2, unit="adet", unit_price=230, amount=460 (satır toplamı). unit_price yalnız fişte birim
  fiyat yazıyorsa (miktar satırı), yoksa null.
- Tartılı ürün "0,750 KG x 89,90" → qty=0.75, unit="kg", amount=67.43; litre ise unit="lt".
- Türk yazar kasa fişinde MİKTAR SATIRI ("2 ad X 2,50", "2 AD X 40.00", "0,750 KG X 89,90") ÜRÜN SATIRINDAN ÖNCE, kendi
  satırında gelir ve ALTINDAKİ ürüne aittir; o ürünün satırında sağda toplam yazar ("*5,00" = 2 × 2,50). Miktar satırını
  AYRI ÜRÜN olarak YAZMA ve adını ürün adına katma; qty ve unit'i altındaki ürüne ver. Her ürünün amount'u KENDİ satırının
  sağındaki tutardır (bir alttaki ya da üstteki ürünün tutarı değil). Örnek:
    2 ad X 2,50
    POSET ORTA BOY        *5,00
    MADEN SUYU 6X200      *60,00
  → [{{"n": "POSET ORTA BOY", "q": 2, "a": 5.00}}, {{"n": "MADEN SUYU 6X200", "q": 1, "a": 60.00}}]
- Bir ürünün hemen altındaki İNDİRİM/KAMPANYA satırı o ürünün amount'undan düşülür. Hangi ürüne ait olduğu belli olmayan genel
  indirimi ("İNDİRİMLER", "Satış İndirimi", "TOPLAM İNDİRİM", "İskonto", kupon/puan kullanımı) fişin sonunda TEK kalem olarak
  yaz: name fişteki adı (ör. "Satış İndirimi"), amount EKSİ (ör. -73.00), qty 1. "TOPLAM İNDİRİM" ile aynı indirimi iki kez yazma.
  Böylece kalemlerin toplamı GENEL TOPLAM'ı tutar (ör. ara toplam 730,00 − indirim 73,00 = toplam 657,00).
- Fiyatı okunamayan ürünü amount null ile yine yaz.
- Birden fazla görüntü varsa hepsi AYNI fişin sırayla parçalarıdır: üst üste binen (iki fotoğrafta da görünen) satırları BİR kez
  yaz; total son parçadaki GENEL TOPLAM'dır.
"""

SPLIT_RULE_COMPACT = """items[].s (split): ürün şu kategorilerden birine AÇIKÇA giriyorsa o kategorinin adı, değilse yazma: {cats}.
Fiş kısaltmalarını çöz (ör. "MRLB TCH BLU" = Marlboro sigara). Emin değilsen yazma.
"""

# kısa kalem anahtarı → uzun (_long_item); eski uzun anahtarlı yanıt da okunur
ITEM_KEYS = {"n": "name", "p": "product", "b": "brand", "q": "qty", "u": "unit", "up": "unit_price", "a": "amount",
             "s": "split"}


def _flag(name: str, default: bool = True) -> bool:
    v = (os.environ.get(name) or "").strip().lower()
    return default if not v else v not in ("0", "false", "no", "off", "hayir")


def compact_output() -> bool:
    """HESAPKITAP_RECEIPT_COMPACT (varsayılan açık): sıkı çıktı istemi (DOC_PROMPT_COMPACT); 0 → eski DOC_PROMPT."""
    return _flag("HESAPKITAP_RECEIPT_COMPACT")


MEDIA_RES = {"low": "MEDIA_RESOLUTION_LOW", "medium": "MEDIA_RESOLUTION_MEDIUM", "high": "MEDIA_RESOLUTION_HIGH"}


def media_resolution() -> str | None:
    """HESAPKITAP_RECEIPT_MEDIA_RES: fiş fotoğrafının modeldeki çözünürlüğü (low | medium | high). Boş/bilinmeyen →
    alan gönderilmez (modelin varsayılanı: gemini-3.1-flash-lite'ta HIGH, görüntü başı ~1.064 token; MEDIUM 540, LOW 266).
    Varsayılan kapalı: MEDIUM'da 40 fişte toplam 39 → 34/39'a düştü. Gemini 3'te görüntü token'ı çözünürlük ayarına
    bağlıdır, piksel boyutuna değil (1600 → 512 px yine 1.064 token): istemci tarafı küçültme token kazandırmaz."""
    return MEDIA_RES.get((os.environ.get("HESAPKITAP_RECEIPT_MEDIA_RES") or "").strip().lower())


THINKING_LEVELS = ("minimal", "low", "medium", "high")
THINKING_DEFAULT = "low"
THINKING_OFF = ("off", "0", "none", "kapali", "kapalı", "default", "varsayilan", "varsayılan")
THINKING_SETTING = "receipt_thinking"  # system ayarı (sunucu geneli); ortam değişkeni önce gelir
# Gemini 2.5 düşünme bütçesi (token) — Gemini 3 thinkingLevel alır; 2.5 Pro 128'in altına inmez
_THINKING_BUDGET = {"minimal": 0, "low": 512, "medium": 2048, "high": 8192}


def receipt_thinking() -> str | None:
    """Fiş/fatura okumasında modelin düşünme düzeyi: HESAPKITAP_RECEIPT_THINKING, yoksa system ayarı receipt_thinking,
    yoksa THINKING_DEFAULT ("low"). minimal | low | medium | high; off (kapali/varsayilan) → ayar gönderilmez (modelin
    kendi varsayılanı: Gemini 3'te dinamik "high"). Fiş çıkarımı akıl yürütme istemez: düşünme token'ı çıktı fiyatından
    ödenir; düşük düzey maliyeti ve süreyi kısar. Doğruluk düşerse (app/receipt_bench.py --thinking ile ölç) off yapılır."""
    v = (os.environ.get("HESAPKITAP_RECEIPT_THINKING") or "").strip().lower()
    if not v:
        try:
            from app import system

            v = (system.get(THINKING_SETTING) or "").strip().lower()
        except Exception:  # noqa: BLE001 - ayar okunamazsa varsayılan
            v = ""
    if v in THINKING_OFF:
        return None
    return v if v in THINKING_LEVELS else THINKING_DEFAULT


def _thinking_config(model: str | None, level: str | None) -> dict | None:
    """Modelin anladığı biçim: Gemini 3 → {"thinkingLevel": düzey}; Gemini 2.5 → {"thinkingBudget": token}; başka
    model (ya da düzey yok) → None (alan gönderilmez)."""
    if not level or level not in THINKING_LEVELS or not model:
        return None
    m = model.lower().removeprefix("models/")
    if m.startswith("gemini-3"):
        return {"thinkingLevel": level}
    if m.startswith("gemini-2.5"):
        b = _THINKING_BUDGET[level]
        return {"thinkingBudget": max(b, 128) if "pro" in m else b}
    return None


def doc_prompt(categories: list[str], split_categories: list[str] | None = None) -> str:
    """Fiş/fatura istemi (fotoğraf/PDF yolu): sıkı çıktı açıksa DOC_PROMPT_COMPACT, değilse DOC_PROMPT."""
    cats = ", ".join(split_categories or [])
    if compact_output():
        return DOC_PROMPT_COMPACT.format(categories=", ".join(categories),
                                         split_rule=SPLIT_RULE_COMPACT.format(cats=cats) if split_categories else "")
    return DOC_PROMPT.format(categories=", ".join(categories),
                             split_rule=SPLIT_RULE.format(cats=cats) if split_categories else "")


def _long_item(i: dict) -> dict:
    """Kısa anahtarlı kalemi uzun adlara çevir ({"n": "SUT", "a": 25} → {"name": "SUT", "amount": 25}). Aynı alanın
    uzun adı da varsa uzun ad kazanır (eski yanıtlar, yeniden okuma, metin yolu uzun anahtarla gelir)."""
    if not isinstance(i, dict):
        return i
    out = {ITEM_KEYS.get(k, k): v for k, v in i.items() if k not in ITEM_KEYS.values()}
    out.update({k: v for k, v in i.items() if k in ITEM_KEYS.values()})
    return out


def text_first() -> bool:
    """HESAPKITAP_RECEIPT_TEXT_FIRST=1: eski davranış — fotoğrafla birlikte gelen telefon metni yapay zekâya fotoğraf
    yerine gider (fis_metin; tutarsızsa fotoğrafla yedek, fis_foto). Varsayılan (0): yapay zekâ fotoğrafı okur, metin
    yalnız yardımcıdır (fatura şablonu, KDV/VKN yedeği, yerel okuma). Ölçüm (04.11.2026, 17 fiş, app/receipt_bench.py):
    fotoğraf ₺0,06/fiş ve kalem yapısı %71 tutarlı; metin ₺0,11/fiş, %60 — metin daha uzun çıktı üretiyor."""
    return os.environ.get("HESAPKITAP_RECEIPT_TEXT_FIRST", "0").strip() == "1"


def rule_read(text: str | None, qr: list[str] | None, categories: list[str]) -> dict | None:
    """Yapay zekâsız kural okuması (app/karekod.py): karekodlu kurum faturası, karekodlu akaryakıt fişi (litre ×
    birim fiyat metinle doğrulanır), kalemsiz POS slibi. Tutmazsa None. Okuma hakkı çağıranda aynen düşer."""
    from app import karekod

    code = karekod.best(qr)
    doc = None
    if code and code["kind"] == "gib":
        doc = karekod.bill_doc(code, text)
    elif code and code["kind"] == "pompa":
        doc = karekod.fuel_doc(code, text, categories)
    if doc is None and text:
        doc = karekod.pos_slip(text, categories)
    return doc  # sayım: app/api/main.py _count_receipt / _count_bill (doc["rule"])


def parse_document(data: bytes, mime: str, categories: list[str], split_categories: list[str] | None = None,
                   more: list[tuple[bytes, str]] | None = None, ocr_text: str | None = None,
                   qr: list[str] | None = None) -> dict:
    """Fiş mi fatura mı kendisi anlar. Gemini yoksa, kota dolduysa ya da ulaşılamazsa yerel okuma (Tesseract).
    ocr_text: fotoğrafla birlikte gelen telefon metni (yardımcı): yapay zekâya gitmez; önce kurumun fatura şablonu bu
    metinle denenir, model KDV/VKN'yi boş bırakırsa metinden doldurulur, yerel okuma Tesseract yerine bu metinle yapılır.
    qr: telefonun fotoğrafta çözdüğü karekod içerikleri (app/karekod.py): kural okuması (fatura, akaryakıt), karekod
    VKN'si fiş şablonu imzası olur, yapay zekâ okumasında tutar/tarih/VKN karekodla düzeltilir. Boş/None: eski davranış."""
    from app import bill_templates, karekod, ocr, receipt_templates

    text = (ocr_text or "").replace("\r\n", "\n").strip()[:MAX_OCR_CHARS] or None
    code = karekod.best(qr) if qr else None
    if text and (hit := bill_templates.read(text)):
        return bill_templates.as_doc(hit)  # telefon metni kurumun fatura şablonunu tutuyor: yapay zekâsız
    if (text or code) and (hit := rule_read(text, qr, categories)):
        return hit  # karekod (fatura / akaryakıt) ya da POS slibi: yapay zekâsız (app/karekod.py)
    qr_vkn = code.get("vkn") if code and not karekod.is_bill(code, text) else None
    if text and (hit := receipt_templates.read(text, categories, vkn=qr_vkn)):
        return karekod.apply_hints(hit, code)  # zincir / akaryakıt fişi şablonu: yapay zekâsız (app/receipt_templates.py)
    if not more and (hit := bill_templates.read(bill_templates.text_of(data, mime))):
        return bill_templates.as_doc(hit)  # kurumun fatura şablonu: yapay zekâsız (app/bill_templates.py)
    if not enabled():
        return karekod.apply_hints(
            _local(data, mime, categories, "Yapay zekâ anahtarı yok; yerel okuma kullanıldı", more, text), code)
    try:
        return karekod.apply_hints(
            _parse_document_ai(data, mime, categories, split_categories, more, ocr_text=text, qr_vkn=qr_vkn), code)
    except ReceiptError as e:
        if not (text or ocr.available() or mime == "application/pdf"):
            raise
        return karekod.apply_hints(_local(data, mime, categories, str(e), more, text), code)


def _local(data: bytes, mime: str, categories: list[str], reason: str, more: list[tuple[bytes, str]] | None = None,
           text: str | None = None) -> dict:
    """Yerel (kural tabanlı) okuma: telefon metni varsa o metinden, yoksa Tesseract / PDF metin katmanı."""
    from app import ocr

    try:
        doc = ocr.parse_text(text, categories) if text else ocr.parse(data, mime, categories, more)
    except ValueError as e:
        raise ReceiptError(f"{reason}. {e}") from e
    doc["note"] = reason
    return doc


def _parse_document_ai(data: bytes, mime: str, categories: list[str], split_categories: list[str] | None = None,
                       more: list[tuple[bytes, str]] | None = None, kind: str = "otomatik",
                       ocr_text: str | None = None, qr_vkn: str | None = None) -> dict:
    """Tek Gemini çağrısı. split_categories: kalem kurallarının kategorileri; model her fiş satırını bunlardan
    birine etiketler (kelime kuralının yakalayamadığı kısaltmalar için). kind: maliyet türü (metin yolunun fotoğraf
    yedeği "otomatik_foto"). ocr_text: telefon metni (yapay zekâya gitmez): faturada şablon bundan öğrenilir, fişte
    KDV/VKN boşsa bundan doldurulur. qr_vkn: karekodun satıcı VKN'si (fiş şablonu imzası; metindeki VKN'den önce)."""
    from app import bill_templates

    images_only = all((m or "").startswith("image/") for m in [mime, *(m for _, m in (more or []))])
    raw, _ = _gemini_json(doc_prompt(categories, split_categories), data, mime, max_output_tokens=8000, more=more,
                          kind=kind, media_resolution=media_resolution() if images_only else None,
                          thinking=receipt_thinking())
    doc = _doc_of(raw, categories)
    # şablon metni: PDF metin katmanı; fotoğrafta (metin katmanı yok) telefon metni — metin yolu da ondan öğreniyordu
    own = bill_templates.text_of(data, mime)
    if doc["type"] == "bill" and (learn_from := (own if not more else None) or ocr_text):
        bill_templates.learn(learn_from, raw)
    if doc["type"] == "receipt" and (own or ocr_text):  # e-arşiv PDF'i / telefon metni: KDV/VKN kural yedeği
        vat.fill(doc, own or ocr_text)
    if doc["type"] == "receipt" and ocr_text:  # fişin düzeni öğrenilir: aynı işyerinin sonraki fişi yapay zekâsız
        from app import receipt_templates

        receipt_templates.learn(ocr_text, doc, vkn=qr_vkn)
    return doc


MAX_OCR_CHARS = 20000  # telefonda çıkarılan metin üst sınırı (4 parçalı uzun fiş rahat sığar)

TEXT_INTRO = """Aşağıdaki METİN, bir belgenin fotoğrafından telefonda çıkarılmış yazıdır (OCR). Satır sırası ve satır
sonları korunmuştur: fişin her satırı metinde ayrı bir satırdır ve aynı satırdaki yazılar (ör. solda ürün adı, sağda
fiyatı) aynı satırda, aralarında boşlukla durur. OCR harf hataları olabilir (O/0, I/1, S/5, B/8, Türkçe harfin
düşmesi: Ş→S, İ→I, Ğ→G); bağlamdan düzelt ama metinde olmayan satır UYDURMA. "--- PARÇA n ---" satırı aynı belgenin
sırayla çekilmiş fotoğraflarını ayırır. Aşağıdaki kurallarda geçen "fotoğraf", "görüntü" ve "satır" bu metni anlatır.
"""


def parse_document_text(text: str, categories: list[str], split_categories: list[str] | None = None) -> dict:
    """Telefonda (Apple Vision / ML Kit) çıkarılmış metinden fiş ya da fatura: fotoğraf yerine metin gider, istem ve
    çıktı biçimi parse_document ile aynı. Maliyet türü fiş için fis_metin."""
    text = (text or "").replace("\r\n", "\n").strip()[:MAX_OCR_CHARS]
    if not text:
        raise ReceiptError("Metin boş")
    from app import bill_templates

    if hit := bill_templates.read(text):
        return bill_templates.as_doc(hit)
    split_rule = SPLIT_RULE.format(cats=", ".join(split_categories)) if split_categories else ""
    prompt = (TEXT_INTRO + DOC_PROMPT.format(categories=", ".join(categories), split_rule=split_rule)
              + "\nMETİN (satır sonları fişteki gibi):\n<<<\n" + text + "\n>>>\n")
    raw, _ = _gemini_json(prompt, None, "text/plain", max_output_tokens=8000, kind="otomatik_metin",
                          thinking=receipt_thinking())
    doc = _doc_of(raw, categories)
    if doc["type"] == "bill":
        bill_templates.learn(text, raw)
    return vat.fill(doc, text)  # model KDV/VKN'yi boş bıraktıysa metindeki TOPKDV / V.NO satırından


def _doc_of(raw: dict, categories: list[str]) -> dict:
    """Modelin ham JSON'undan fiş/fatura sonucu (fotoğraf ve metin yolu aynı)."""
    if raw.get("doc_type") == "fatura" or raw.get("due_date"):
        paid = _bill_paid(raw)
        return {
            "type": "bill",
            "provider": (raw.get("merchant") or "").strip()[:60] or "Fatura",
            "legal_name": (raw.get("legal_name") or "").strip()[:120] or None,
            "category": BILL_CATEGORY.get(raw.get("bill_type"), "Faturalar"),
            "amount": _num(raw.get("total")),
            "due_date": _iso(raw.get("due_date")),
            "description": " · ".join(x for x in (raw.get("period"), raw.get("subscriber_no") and f"Abone {raw['subscriber_no']}") if x),
            **paid,
            "confidence": raw.get("confidence") or "orta",
            "source": "yapay_zeka",
        }
    category = raw.get("category") if raw.get("category") in categories else "Diğer"
    return {
        "type": "receipt",
        "merchant": (raw.get("merchant") or "").strip()[:80] or "Fiş",
        "date": _iso(raw.get("date")),
        "total": _num(raw.get("total")),
        "payment": raw.get("payment") or "bilinmiyor",
        "payment_card": (raw.get("payment_card") or "").strip()[:40] or None,
        "installments": _installments(raw.get("installments")),
        "card_last4": card_last4(raw.get("card_last4")),
        "category": category,
        "items": _items(raw),
        "legal_name": (raw.get("legal_name") or "").strip()[:120] or None,
        "address": (raw.get("address") or "").strip()[:160] or None,
        "branch": (raw.get("branch") or "").strip()[:60] or None,
        **vat.of_raw(raw, _num(raw.get("total"))),  # KDV, KDV dökümü, VKN, vergi dairesi (app/vat.py)
        "confidence": raw.get("confidence") or "orta",
        "source": "yapay_zeka",
    }


def parse_bill(data: bytes, mime: str) -> dict:
    """Fatura: önce kurumun şablonu (PDF metni / e-posta metni, yapay zekâsız), tutmazsa yapay zekâ — okuması şablon
    öğretir (app/bill_templates.py). source: "sablon" | "yapay_zeka"."""
    from app import bill_templates

    from app import dekont_parse

    text = bill_templates.text_of(data, mime)
    if hit := bill_templates.read(text):
        return {**bill_templates.as_bill(hit), "looks_like_transfer": False}
    raw, meta = _gemini_json(BILL_PROMPT, data, mime, max_output_tokens=8000, kind="belge")
    # yanlış seçim koruması (ek çağrı yok): model "transfer" dedi ya da PDF metni havale/EFT dekontu; kurum/abone yoksa
    transfer = (str(raw.get("doc_kind") or "").strip().lower() == "transfer" or dekont_parse.is_transfer(text)) \
        and not raw.get("subscriber_no")
    if not transfer:
        bill_templates.learn(text, raw)
    return {
        "looks_like_transfer": transfer,
        "provider": (raw.get("provider") or "").strip()[:60] or "Fatura",
        "category": BILL_CATEGORY.get(raw.get("type"), "Faturalar"),
        "amount": _num(raw.get("amount")),
        "due_date": _iso(raw.get("due_date")),
        "issue_date": _iso(raw.get("issue_date")),
        "description": " · ".join(x for x in (raw.get("period"), raw.get("subscriber_no") and f"Abone {raw['subscriber_no']}") if x),
        **_bill_paid(raw),
        "confidence": raw.get("confidence") or "orta",
        "model": meta["model"],
        "source": "yapay_zeka",
    }


_PAY_METHODS = ("havale", "kredi_karti", "nakit")


def _bill_paid(raw: dict) -> dict:
    """Fatura belgesi ödeme dekontu/makbuzu mu: {paid, paid_at, payment}. Ödenmişse tarih yoksa None kalır (istemci sorar)."""
    paid = raw.get("paid") is True or str(raw.get("paid")).strip().lower() == "true"
    if not paid:
        return {"paid": False, "paid_at": None, "payment": None}
    method = raw.get("pay_method")
    return {"paid": True, "paid_at": _iso(raw.get("paid_at")), "payment": method if method in _PAY_METHODS else None}


def parse_receipt(image: bytes, mime: str, categories: list[str]) -> dict:
    raw, meta = _gemini_json(PROMPT.format(categories=", ".join(categories)), image, mime, max_output_tokens=8000,
                             kind="fis", thinking=receipt_thinking())

    d = _iso(raw.get("date"))
    category = raw.get("category") if raw.get("category") in categories else "Diğer"
    return {
        "merchant": (raw.get("merchant") or "").strip()[:80] or "Fiş",
        "date": d,
        "total": _num(raw.get("total")),
        "vat": _num(raw.get("vat")),
        "payment": raw.get("payment") or "bilinmiyor",
        "category": category,
        "items": _items(raw),
        "legal_name": (raw.get("legal_name") or "").strip()[:120] or None,
        "confidence": raw.get("confidence") or "orta",
        "model": meta["model"],
        "tokens": {"input": meta["input"], "output": meta["output"]},
    }


REREAD_PROMPT = """Bu, daha önce okunmuş bir Türkiye alışveriş fişinin fotoğrafı; ilk okumada ürün kalemleri hatalı çıktı
(ad ile fiyat kaymış ya da miktar satırı ayrı ürün sanılmış olabilir). Satır satır dikkatle yeniden oku.
{total_hint}SADECE şu JSON'u döndür:
{{"total": 0.00, "items": [{{"name": "fişteki satır yazısı", "product": "standart ürün adı", "brand": "marka veya null",
"qty": 1, "unit": "adet", "unit_price": null, "amount": 0.00}}], "confidence": "yüksek | orta | düşük"}}
Her ürünün amount'u KENDİ satırının sağındaki tutardır. Türk sayı biçimi 1.234,56 = 1234.56.
""" + ITEM_RULES


REREAD_MODEL = "gemini-3.5-flash"


def reread_model() -> str:
    """Yeniden okuma modeli (ilk okumadan güçlü): GEMINI_REREAD_MODEL, yoksa REREAD_MODEL."""
    return os.environ.get("GEMINI_REREAD_MODEL") or REREAD_MODEL


def reread_items(image: bytes, mime: str, total: float | None, more: list[tuple[bytes, str]] | None = None) -> dict:
    """Saklı fiş fotoğrafını daha güçlü modelle, fiş toplamını bildirerek yeniden oku. {items, total, model, confidence}."""
    hint = (f"Fişin GENEL TOPLAMI {total:.2f} TL: kalemlerin amount toplamı (indirimler düşülmüş) bunu TUTMALI; tutmuyorsa "
            "kaçan ya da kayan satırı bul.\n") if total else ""
    model = reread_model()
    raw, meta = _gemini_json(REREAD_PROMPT.format(total_hint=hint), image, mime, max_output_tokens=8000, model=model,
                             more=more, kind="fis")
    return {"items": _items(raw), "total": _num(raw.get("total")) or total, "model": meta["model"],
            "confidence": raw.get("confidence") or "orta"}
