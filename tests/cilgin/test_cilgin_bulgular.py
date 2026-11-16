"""Çılgın testçilerin bulduğu hataların minimal tekrarları (xfail strict). Veriler UYDURMADIR."""
from __future__ import annotations

import json
import threading

import pytest

from tests.cilgin.ortak import Defter
from tests.cilgin.test_cilgin_ozel import D0, ROWS, _acc
from tests.simulasyon import belgeler


@pytest.mark.xfail(strict=True, reason="C1 (ORTA): aynı hesap dökümü AYNI ANDA iki kez yüklenince (çift dokunuş, iki "
                   "sekme, paylaş menüsünden iki kez) satırlar iki kez ekleniyor: POST /api/bank-import "
                   "(app/api/main.py bank_import_run) → db.import_bank_rows'un 'zaten var' (import_hash) denetimi ile "
                   "ekleme aynı işlemde/kilitte değil. Sıralı ikinci yükleme doğru ('tekrar'). Gider/gelir raporu iki "
                   "katı; bakiye çapası etkilenmez. Öneri: içe aktarmayı hesap başı kilitle ya da import_hash'e UNIQUE "
                   "dizin + INSERT OR IGNORE.")
def test_c1_eszamanli_ayni_dokum_tek_kez(tmp_path, client):
    from fastapi.testclient import TestClient

    from app.api.main import app

    d = Defter(tmp_path, D0)
    try:
        aid = _acc(d)
        csv = belgeler.bank_csv("DENEME KISIOGLU", ROWS)
        pv = d.call("post", "/api/bank-import/preview", files={"file": ("d.csv", csv, "text/csv")}).json()
        form = {"mapping": json.dumps(pv["mapping"]), "header_row": str(pv["header_row"]), "account_id": str(aid)}
        res = []
        barrier = threading.Barrier(2)

        def imp():
            c = TestClient(app)
            barrier.wait()
            res.append(c.post("/api/bank-import", files={"file": ("d.csv", csv, "text/csv")}, data=form))

        ts = [threading.Thread(target=imp) for _ in range(2)]
        [t.start() for t in ts]
        [t.join() for t in ts]
        assert all(r.status_code < 500 for r in res)
        with d.db.connect() as c:
            n = c.execute("SELECT COUNT(*) FROM entries WHERE import_hash IS NOT NULL").fetchone()[0]
        assert n == len(ROWS), n
    finally:
        d.close()
