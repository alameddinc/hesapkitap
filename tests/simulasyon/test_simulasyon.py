"""Zaman hızlandırmalı simülasyon testi (A). Ayrıntı: tests/simulasyon/sim.py.

İşaret `uzun`: varsayılan koşuda da çalışır (6 persona × 304 gün; -n 6 ile ~persona başı süre). Kısa koşu için
SIM_GUN=60 (ilk 60 gün) ya da -m "not uzun". Rapor: SIM_RAPOR=<dizin> verilirse persona başına JSON yazılır.
"""
from __future__ import annotations

import json
import os
from datetime import timedelta

import pytest

from tests.simulasyon.personalar import ALL
from tests.simulasyon.sim import Sim


@pytest.mark.uzun
@pytest.mark.parametrize("persona", ALL, ids=[p.key for p in ALL])
def test_simulasyon(persona, client, monkeypatch, tmp_path):
    days = int(os.environ.get("SIM_GUN", "0") or 0)
    end = Sim.START + timedelta(days=days - 1) if days else None
    sim = Sim(persona, client, monkeypatch, tmp_path, end=end).run()
    rep = sim.report()
    if out := os.environ.get("SIM_RAPOR"):
        os.makedirs(out, exist_ok=True)
        with open(os.path.join(out, f"{persona.key}.json"), "w", encoding="utf-8") as f:
            json.dump(rep, f, ensure_ascii=False, indent=1, default=str)
    assert rep["gun"] == (days or 304) and rep["kontrol"] == rep["gun"]
    unknown = {k: v for k, v in rep["sinif"].items() if k not in KNOWN}
    assert not unknown, json.dumps({k: {x: y for x, y in v.items()} for k, v in unknown.items()},
                                   ensure_ascii=False, indent=1, default=str)[:6000]


# Bilinen (raporlanmış) hatalar: simülatör uygulamanın değerini o hatanın MODELİYLE birebir açıklayabildiğinde
# uyuşmazlığı K* yerine BULGU_* sınıfına yazar (sim.py check_day). Minimal tekrarları test_bulgular.py'de xfail(strict).
# Hata düzeltilince: xfail XPASS olur → işareti kaldır, buradan ve sim.py'deki modelden sil.
KNOWN: dict[str, str] = {
    # 16.11 entegrasyon: S1–S9 hepsi düzeldi (test_bulgular.py'de xfail yok); bilinen hata kalmadı
}
