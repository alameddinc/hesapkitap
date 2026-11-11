"""Ekstre ve hesap dökümü nasıl indirilir? (GET /api/statement-help, yetenek "statement_help").

Web (Ekstreler → StatementHelp) ve mobil (Ekstre nasıl indirilir? ekranı) AYNI içeriği buradan okur: metin tek yerde.
Biçim: düz metin; **kalın** işaretleri istemcide kalın yazılır. Bir adım/ipucu yalnız bir platformda anlamlıysa
platform "web" | "mobile" (yoksa ikisinde de). Bankaya özel menü yolu uydurulmaz: bankalar arasında ortak yol + ipuçları.
"""
from __future__ import annotations

TITLE = "Ekstre ve hesap dökümü nasıl indirilir?"

SECTIONS = (
    {"key": "kart", "title": "Kredi kartı ekstresi", "icon": "card", "steps": (
        {"text": "Bankanın mobil uygulaması ya da internet şubesi → **Kartlar** → kartını seç."},
        {"text": "**Ekstreler / Hesap Özeti / Dönem Borcu** → dönemi seç → **PDF** olarak indir ya da **Paylaş**."},
        {"text": "Bankalar genelde **son 12 ayı** PDF olarak verir. Daha eski aylar için kart işlemlerini "
                 "**\"Toplu ekstre\" / \"Kart hareketleri\" Excel**'i olarak indir — onu da buraya bırakabilirsin, aylara "
                 "kendisi böler.", "platform": "web"},
        {"text": "Bankalar genelde **son 12 ayı** PDF olarak verir. Daha eski aylar için kart işlemlerini "
                 "**\"Toplu ekstre\" / \"Kart hareketleri\" Excel**'i olarak indir — onu da yükleyebilirsin, aylara "
                 "kendisi böler.", "platform": "mobile"},
        {"text": "E-ekstre e-postayla geliyorsa ekteki PDF'i buraya bırak; şifreliyse genelde TC kimlik no'nun ilk ya da "
                 "son haneleri — bir kez sorulur, hatırlanır.", "platform": "web"},
        {"text": "E-ekstre e-postayla geliyorsa ekteki PDF'i telefona kaydedip yükle; şifreliyse genelde TC kimlik no'nun "
                 "ilk ya da son haneleri — bir kez sorulur, hatırlanır.", "platform": "mobile"},
    )},
    {"key": "hesap", "title": "Banka hesap dökümü (vadesiz)", "icon": "bank", "steps": (
        {"text": "Mobil uygulama / internet şubesi → **Hesaplar** → vadesiz hesabını seç."},
        {"text": "**Hesap Hareketleri** → tarih aralığını seç (ilk seferde 1–2 yıl; banka izin verdiği kadar) → **PDF** "
                 "ya da **Excel** olarak indir. Tek tek dekont değil, **hareket listesi** olmalı."},
        {"text": "Maaşın hangi hesaba yatıyorsa önce onu yükle; sonra kart ödemesi yaptığın ve para aktardığın hesapları."},
        {"text": "Aynı ayı iki kez yüklersen sorun olmaz: aynı hareket iki kez eklenmez."},
    )},
)

TIPS = (
    {"text": "Telefonda en kolayı: banka uygulamasında PDF'i aç → **Paylaş → HesapKitap** (Ayarlar → Telefondan paylaş).",
     "platform": "web"},
    {"text": "Banka uygulamasında PDF'i aç → **Paylaş → Dosyalar'a kaydet**; sonra **+ → Ekstre yükle** ile seç. Birden "
             "fazla dosyayı birlikte seçebilirsin.", "platform": "mobile"},
    {"text": "VakıfBank ve Kuveyt Türk ekstreleri ile tanınan hesap dökümleri yapay zekâsız okunur; diğerleri bir kez "
             "öğrenilir."},
)


def _for(items, platform: str | None) -> list[dict]:
    return [{"text": x["text"]} for x in items if not platform or x.get("platform") in (None, platform)]


def content(platform: str | None = None) -> dict:
    """platform: "web" | "mobile" (o platformun adımları) ya da None (hepsi, platform alanıyla)."""
    if platform is None:
        return {"title": TITLE,
                "sections": [{**{k: v for k, v in s.items() if k != "steps"}, "steps": [dict(x) for x in s["steps"]]}
                             for s in SECTIONS],
                "tips": [dict(x) for x in TIPS]}
    return {"title": TITLE,
            "sections": [{**{k: v for k, v in s.items() if k != "steps"}, "steps": _for(s["steps"], platform)}
                         for s in SECTIONS],
            "tips": _for(TIPS, platform)}
