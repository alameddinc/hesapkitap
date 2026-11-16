#!/usr/bin/env bash
# Mutasyon testi (yalnız RAPOR): app/finance.py ve app/db.py'nin finans fonksiyonlarında küçük bozulmalar (mutant)
# üretir, hızlı finans testleriyle koşar; testlerin YAKALAMADIĞI (hayatta kalan) mutantları listeler. Repo kirlenmez:
# kopya geçici dizinde koşar.
#
#   scripts/mutasyon.sh                 # örneklem: FONK listesindeki fonksiyonlar
#   FONK="spend_horizon net_worth" scripts/mutasyon.sh
#   PY=/yol/python scripts/mutasyon.sh  # mutmut 3.x kurulu python (requirements-dev.txt)
#
# Çıktı: $OUT (varsayılan ./mutasyon-raporu.txt): hayatta kalan mutantlar ve diff'leri.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PY="${PY:-python}"
OUT="${OUT:-$ROOT/mutasyon-raporu.txt}"
FONK="${FONK:-spend_horizon _deduct_end _debt_due_split salary_awaited next_salary_date salary_arrived _spendable_after_income unknown_statement_payments _account_balance debt_after_anchor statement_remaining}"
TESTS="${TESTS:-tests/test_finans_cekismeli.py tests/test_finance_invariants.py tests/test_gercek_gun_tekrari.py tests/test_p0_cekismeli.py tests/test_p0_maas_gun.py tests/simulasyon/test_belge_akisi.py}"
WORK="$(mktemp -d "${TMPDIR:-/tmp}/hk-mutasyon.XXXXXX")"
trap 'rm -rf "$WORK"' EXIT

rsync -a --exclude .git --exclude node_modules --exclude web --exclude mutants --exclude data "$ROOT/" "$WORK/"
cd "$WORK"
sel=""
for t in $TESTS; do [ -e "$t" ] && sel="$sel\"$t\", "; done
cat > pyproject.toml <<CFG
[tool.mutmut]
source_paths = ["app/finance.py", "app/db.py"]
pytest_add_cli_args_test_selection = [${sel%, }]
pytest_add_cli_args = ["-p", "no:cacheprovider"]
also_copy = ["tests/", "templates/", "parity/"]
CFG
# mutmut 3: fonksiyon adı deseniyle örneklem (x_<fonksiyon>__mutmut_*)
pats=()
for f in $FONK; do pats+=("app.finance.x_${f}__mutmut_*" "app.db.x_${f}__mutmut_*"); done
"$PY" -m mutmut run "${pats[@]}" || true
"$PY" -m mutmut results > "$WORK/sonuc.txt" 2>&1 || true
{
  echo "# Mutasyon raporu ($(date -u +%F)) — fonksiyonlar: $FONK"
  echo "# testler: $TESTS"
  grep -E "survived|no tests|timeout|suspicious" "$WORK/sonuc.txt" || echo "(hayatta kalan yok)"
  echo
  for m in $(grep -E ": survived" "$WORK/sonuc.txt" | cut -d: -f1 | head -60); do
    echo "## $m"; "$PY" -m mutmut show "$m" 2>/dev/null | head -20; echo
  done
} > "$OUT"
echo "rapor: $OUT"
grep -c ": survived" "$WORK/sonuc.txt" || true
