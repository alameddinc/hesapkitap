# Katkı rehberi

Teşekkürler! En çok ihtiyaç duyulan katkı **yeni banka parser'ları**.

## Yeni banka ekleme

Yapay zekâ yedeği her bankayı okuyabilir, ama bankaya özel parser **ücretsiz, anında ve kesindir**.

1. **Ekstre metnini çıkar** (PDF'in kendisini asla paylaşma):
   ```bash
   python -c "import pdfplumber,sys; print('\n'.join(p.extract_text() for p in pdfplumber.open(sys.argv[1]).pages))" ekstre.pdf
   ```
2. **Sahte bir fixture yaz:** `tests/fixtures/` altına, aynı formatta ama **uydurma** isim, kart numarası, işyeri ve
   tutarlarla bir `.txt`. Toplamlar tutmalı (bkz. `tests/fixtures/make_fixtures.py`).
3. **Parser'ı yaz:** `app/parsers/<banka>.py`
   - `is_<banka>(text) -> bool` — bankayı tanı
   - `parse_text(text) -> Statement` — ekstre özeti + işlemler
   - Taksitli işlemde `amount` = bu ayki taksit; `installment_no`, `installments_left`, `installment_left_amount` doldur
   - Ödeme `odeme`, iade `iade`, faiz/vergi/ücret `faiz_ucret`; iade ve ödeme negatif tutar
4. **Kaydet:** `app/parsers/__init__.py` → `DETECTORS` listesine ekle, `SUPPORTED`'a adını yaz.
5. **Test:** `Statement.reconciliation_diff()` **0** dönmeli. `tests/test_<banka>.py` ekle.
   Kendi gerçek ekstrelerinle yerelde: `REAL_PDF_DIR=/yol pytest tests/test_real_pdfs.py`

## Kişisel veri kuralı

PR'larda **gerçek** ekstre, isim, kart numarası, işyeri adı veya tutar olmamalı. Fixture'lar uydurma olmalı.
Kategori kurallarına (`app/categorize.py`) sadece ulusal zincirler/markalar eklenir; mahalle işletmesi eklenmez
(kullanıcılar onları uygulamadan kendi kuralı olarak ekler).

## Geliştirme

```bash
docker compose -f docker-compose.dev.yml up --build   # http://localhost:5173
docker compose -f docker-compose.dev.yml run --rm api pytest -q
cd web && npm ci && npx tsc --noEmit
```

Commit mesajları: [Conventional Commits](https://www.conventionalcommits.org/) (`feat:`, `fix:`, `docs:` …).
