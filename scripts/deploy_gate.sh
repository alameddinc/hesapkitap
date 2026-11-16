#!/usr/bin/env bash
# Deploy kapısı: canlıya çıkmadan önce uzun testler. Başarısızsa çıkış kodu ≠ 0 (deploy betiği durur).
#   - 10 aylık zaman hızlandırmalı simülasyon (6 persona; tests/simulasyon, işaret: uzun)
#   - çılgın testçilerin uzun turu (tests/cilgin, işaret: uzun) + hızlı tur ve hypothesis makinesi
#   - bilinen hataların minimal tekrarları (xfail strict: düzeltilen hata XPASS → kırmızı, işaret kaldırılmalı)
# Ortam: PY (python; starlette==1.10.0 kurulu venv), HK_SEED (yoksa günün tarihi), N (paralel işçi, varsayılan 6).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
PY="${PY:-python}"
N="${N:-6}"
export HK_SEED="${HK_SEED:-$(date +%Y%m%d)}"
SV="$("$PY" -c 'import starlette; print(starlette.__version__)')"
want="$(grep -E '^starlette==' requirements.txt | cut -d= -f3)"
if [ "$SV" != "$want" ]; then
  echo "deploy_gate: starlette $SV ≠ canlı $want (requirements.txt) — canlı sürümle koş" >&2
  exit 3
fi
xd=()
"$PY" -c "import xdist" 2>/dev/null && xd=(-n "$N")
echo "deploy_gate: HK_SEED=$HK_SEED starlette=$SV"
t0=$(date +%s)
"$PY" -m pytest -q -p no:cacheprovider "${xd[@]}" -m uzun tests/simulasyon tests/cilgin
"$PY" -m pytest -q -p no:cacheprovider "${xd[@]}" -m "not uzun" tests/simulasyon tests/cilgin
echo "deploy_gate: YEŞİL ($(( $(date +%s) - t0 )) sn) — tekrar için HK_SEED=$HK_SEED"
