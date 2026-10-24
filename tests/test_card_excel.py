import io

from openpyxl import Workbook


def _xlsx(rows):
    wb = Workbook()
    ws = wb.active
    for r in rows:
        ws.append(r)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


ROWS = [
    ["Kart Türü", "Örnek Kart"], ["Kart Numarası", "979267******9106"], ["Şube", "X"], [], ["Toplu Ekstre"],
    ["İşlem Tarihi", "Açıklama", "Tutar", "Sektör", "Mil/ Altın Puan"],
    ["20.06.2026", "ORNEK MARKET ISTANBUL TR", "250.00 TL", "Bakkallar ve Süpermarketler", "0"],
    ["17.06.2026", "Ödeme", "1000.00 TL", "", "0"],
    ["10.06.2026", "ORNEK KAFE TR", "120.50 TL", "Yemek Yerleri ve Restoranlar", "0"],
    ["10.01.2026", "ORNEK ELEKTRONIK TR 6/12 Taksit", "500.00 TL", "ÇOK KATLI MAĞAZALAR", "0"],  # taksit: komşunun dönemi
    ["20.05.2026", "ORNEK PETROL TR", "900.00 TL", "Servis İstasyonları", "0"],
]


def test_card_excel_becomes_statements(client, tmpdb):
    data = _xlsx(ROWS)
    r = client.post("/api/statements/upload", files={"files": ("kart.xlsx", data, "application/vnd.ms-excel")}).json()[0]
    assert r["status"] == "saved" and r["card_excel"]["statements"] == 2 and r["card_excel"]["transactions"] == 5
    with tmpdb.connect() as c:
        sts = [tuple(x) for x in c.execute("SELECT period_end, total_debt, payments FROM statements ORDER BY period_end")]
        cats = dict(c.execute("SELECT description, category FROM transactions").fetchall())
        types = dict(c.execute("SELECT description, txn_type FROM transactions").fetchall())
    # 20.05 → Haziran kesimi (15.06); 10.06 ve taksit satırı → 15.06; 17.06 ve 20.06 → 15.07
    assert sts == [("2026-06-15", 1520.5, 0.0), ("2026-07-15", 250.0, 1000.0)]
    assert types["Ödeme"] == "odeme" and cats["ORNEK KAFE TR"] == "Kafe & Restoran"
    # tekrar yükleme bir şey eklemez; hesap hareketi akışı reddeder (harcama gelir olmasın)
    r2 = client.post("/api/statements/upload", files={"files": ("kart.xlsx", data, "application/vnd.ms-excel")}).json()[0]
    assert r2["card_excel"]["statements"] == 0
    p = client.post("/api/bank-import/preview", files={"file": ("kart.xlsx", data, "application/vnd.ms-excel")})
    assert p.status_code == 422 and "kredi kartı" in p.json()["detail"]


def test_account_preview_warns_when_all_positive(client):
    rows = [["Tarih", "Açıklama", "Tutar"]] + [[f"0{i}.06.2026", f"MAĞAZA {i}", f"{100 + i},00"] for i in range(1, 8)]
    data = _xlsx(rows)
    p = client.post("/api/bank-import/preview", files={"file": ("hareket.xlsx", data, "application/vnd.ms-excel")}).json()
    assert p["warning"] and "kredi kartı" in p["warning"]
