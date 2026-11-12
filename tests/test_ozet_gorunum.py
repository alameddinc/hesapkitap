"""Özet görünümü (Klasik / Sade): düzen listesi görünen sırayı, "-ad" işaretlerini ve diğer görünümün saklı sırasını
("=ad") birlikte taşır; sunucu olduğu gibi saklar (80 öğeye kadar), mobil ve web düzenleri ayrı."""


def test_layout_keeps_other_view_order_and_long_lists(client):
    sade = ["sade", "baslarken", "gelenler", "ayrinti", "kartlar", "donem-sonu", "uyarilar", "odemeler", "net", "zekat", "sadaka",
            "kumbaralar", "borc-plani", "butceler", "kategoriler", "isyerleri", "istatistik-link", "-faiz-ucret"]
    klasik = ["kartlar", "faiz-ucret", "donem-sonu", "gelenler", "baslarken", "uyarilar", "net", "zekat", "sadaka", "butceler",
              "kategoriler", "isyerleri", "istatistik-link", "odemeler", "trend", "karsilastirma", "yillik", "faiz", "kumbaralar",
              "altin", "son-islemler", "borc-plani"]
    lay = sade + ["=" + x for x in klasik] + ["=-trend"]
    assert len(lay) > 40
    r = client.put("/api/me/ui", json={"layout": lay, "layout_mobile": ["donem", "faiz", "=sade", "=ayrinti", "=donem"]})
    assert r.status_code == 200
    ui = client.get("/api/meta").json()["ui"]
    assert ui["layout"] == lay
    assert ui["layout_mobile"] == ["donem", "faiz", "=sade", "=ayrinti", "=donem"]
    # varsayılana dön: yalnız web düzeni silinir
    client.put("/api/me/ui", json={"reset_layout": True})
    ui = client.get("/api/meta").json()["ui"]
    assert ui["layout"] is None and ui["layout_mobile"][0] == "donem"
