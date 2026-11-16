"""Çılgın testçilerin bulduğu hataların minimal tekrarları (düzeltildi; artık normal test). Veriler UYDURMADIR."""
from __future__ import annotations

import json
import threading

from tests.cilgin.ortak import Defter
from tests.cilgin.test_cilgin_ozel import D0, ROWS, _acc
from tests.simulasyon import belgeler


def test_c1_eszamanli_ayni_dokum_tek_kez(tmp_path, client):
    """C1: aynı döküm AYNI ANDA iki kez yüklenince satırlar ikilenmez. Deterministik: birinci yükleme ilk satırını
    yazmadan (create_entry) durur, ikinci yüklemeyi başlatır ve onun bitmesini en çok 1 sn bekler. Kilit yoksa ikinci
    yükleme bu arada "zaten var" denetimini boş geçip bütün satırları yazar (kopya); kilit varsa ikinci yükleme
    birincinin bitmesini bekler ve satırlarını "tekrar" görür."""
    from fastapi.testclient import TestClient

    from app import db
    from app.api.main import app

    d = Defter(tmp_path, D0)
    try:
        aid = _acc(d)
        csv = belgeler.bank_csv("DENEME KISIOGLU", ROWS)
        pv = d.call("post", "/api/bank-import/preview", files={"file": ("d.csv", csv, "text/csv")}).json()
        form = {"mapping": json.dumps(pv["mapping"]), "header_row": str(pv["header_row"]), "account_id": str(aid)}
        res: dict[str, object] = {}
        second_done = threading.Event()
        paused = threading.Event()
        real_create = db.create_entry

        def imp(name: str) -> None:
            res[name] = TestClient(app).post("/api/bank-import", files={"file": ("d.csv", csv, "text/csv")}, data=form)
            if name == "ikinci":
                second_done.set()

        def create_entry(data):
            if data.get("import_hash") and not paused.is_set():  # birinci yüklemenin ilk satırı: ikinciyi yarıştır
                paused.set()
                t2 = threading.Thread(target=imp, args=("ikinci",))
                t2.start()
                second_done.wait(timeout=1.0)
            return real_create(data)

        d.mp.setattr(db, "create_entry", create_entry)
        t1 = threading.Thread(target=imp, args=("birinci",))
        t1.start()
        t1.join(timeout=30)
        assert second_done.wait(timeout=30)
        assert paused.is_set()
        assert all(r.status_code == 200 for r in res.values()), [r.text for r in res.values()]
        with d.db.connect() as c:
            n = c.execute("SELECT COUNT(*) FROM entries WHERE import_hash IS NOT NULL").fetchone()[0]
        assert n == len(ROWS), n
        assert res["ikinci"].json()["tekrar"] == len(ROWS)
    finally:
        d.close()
