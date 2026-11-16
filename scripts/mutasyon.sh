#!/usr/bin/env bash
# Mutasyon testi (yalnız rapor) — ayrıntı: scripts/mutasyon.py. Örnek:
#   PY=/yol/venv/bin/python MUT_K=6 scripts/mutasyon.sh
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
exec "${PY:-python}" "$ROOT/scripts/mutasyon.py" "$@"
