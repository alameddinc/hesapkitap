"""İsteğe bağlı: kendi gerçek ekstrelerinle mutabakat kontrolü. Repoya PDF koyma!

REAL_PDF_DIR=/yol/ekstreler pytest tests/test_real_pdfs.py
"""
import glob
import os

import pytest

from app.parsers import parse_statement

PDFS = sorted(glob.glob(os.path.join(os.environ.get("REAL_PDF_DIR", "/nonexistent"), "*.pdf")))


@pytest.mark.skipif(not PDFS, reason="REAL_PDF_DIR tanımlı değil")
@pytest.mark.parametrize("path", PDFS, ids=os.path.basename)
def test_real_statement_reconciles(path):
    with open(path, "rb") as f:
        st = parse_statement(f.read(), allow_ai=False)
    assert st.transactions
    assert st.reconciliation_diff() == 0
