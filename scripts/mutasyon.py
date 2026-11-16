"""Mutasyon testi (yalnız RAPOR): finans fonksiyonlarına tek tek küçük bozulma (mutant) uygular, hızlı finans
testlerini koşar; testlerin YAKALAMADIĞI (hayatta kalan) mutantları yazar. mutmut 3 bu boyuttaki dosyalarda (db.py
~7.000, finance.py ~4.000 satır) bütün dosyayı mutasyonladığı için saatler sürüyor; bu betik yalnız hedef fonksiyonları
ve tohumlu bir ÖRNEKLEMİ dener. Repo kirlenmez: geçici kopyada koşar.

  python scripts/mutasyon.py                       # varsayılan hedefler, fonksiyon başı 4 mutant
  MUT_K=10 MUT_SEED=7 python scripts/mutasyon.py    # daha çok mutant / başka örneklem
  MUT_FONK="finance.spend_horizon db._account_balance" python scripts/mutasyon.py

Mutasyonlar: karşılaştırma (< ↔ <=, > ↔ >=, == ↔ !=), + ↔ −, and ↔ or, `not` kaldırma, min ↔ max, tamsayı sabit ±1.
Çıktı: MUT_OUT (varsayılan mutasyon-raporu.md).
"""
from __future__ import annotations

import ast
import copy
import difflib
import os
import random
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT = ("finance.net_worth finance.spend_horizon finance._deduct_end finance._debt_due_split finance.salary_realized "
           "finance.salary_arrived finance.next_salary_date finance._spendable_after_income finance._forecast_balance "
           "finance._due_this_period finance.salary_awaited db.unknown_statement_payments db._account_balance "
           "db.debt_after_anchor db.statement_remaining")
TESTS = os.environ.get("MUT_TESTS", "tests/test_finans_cekismeli.py tests/test_finance_invariants.py "
                       "tests/test_gercek_gun_tekrari.py tests/test_p0_cekismeli.py tests/test_p0_maas_gun.py "
                       "tests/simulasyon/test_belge_akisi.py").split()
SWAP_CMP = {ast.Lt: ast.LtE, ast.LtE: ast.Lt, ast.Gt: ast.GtE, ast.GtE: ast.Gt, ast.Eq: ast.NotEq, ast.NotEq: ast.Eq}


def _func(tree: ast.Module, name: str) -> ast.FunctionDef:
    for n in tree.body:
        if isinstance(n, ast.FunctionDef) and n.name == name:
            return n
    raise SystemExit(f"fonksiyon yok: {name}")


def _points(fn: ast.FunctionDef) -> list[tuple[int, str]]:
    """(düğüm sırası, tür) — mutasyon noktaları, fonksiyon gövdesinde ast.walk sırasıyla."""
    out = []
    for i, n in enumerate(ast.walk(fn)):
        if isinstance(n, ast.Compare) and type(n.ops[0]) in SWAP_CMP:
            out.append((i, "cmp"))
        elif isinstance(n, ast.BinOp) and isinstance(n.op, (ast.Add, ast.Sub)):
            out.append((i, "addsub"))
        elif isinstance(n, ast.BoolOp):
            out.append((i, "andor"))
        elif isinstance(n, ast.UnaryOp) and isinstance(n.op, ast.Not):
            out.append((i, "not"))
        elif isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id in ("min", "max"):
            out.append((i, "minmax"))
        elif isinstance(n, ast.Constant) and type(n.value) is int and 0 <= n.value <= 60:
            out.append((i, "int"))
    return out


def _apply(fn: ast.FunctionDef, idx: int, kind: str) -> tuple[ast.FunctionDef, str]:
    fn2 = copy.deepcopy(fn)
    node = list(ast.walk(fn2))[idx]
    if kind == "cmp":
        node.ops[0] = SWAP_CMP[type(node.ops[0])]()
        what = "karşılaştırma"
    elif kind == "addsub":
        node.op = ast.Sub() if isinstance(node.op, ast.Add) else ast.Add()
        what = "+/−"
    elif kind == "andor":
        node.op = ast.Or() if isinstance(node.op, ast.And) else ast.And()
        what = "and/or"
    elif kind == "not":
        # not x → x: ebeveyn yerine operand (UnaryOp'u Constant ile değil, kendi operandıyla değiştir)
        for parent in ast.walk(fn2):
            for f, v in ast.iter_fields(parent):
                if v is node:
                    setattr(parent, f, node.operand)
                elif isinstance(v, list) and node in v:
                    v[v.index(node)] = node.operand
        what = "not kaldır"
    elif kind == "minmax":
        node.func.id = "max" if node.func.id == "min" else "min"
        what = "min/max"
    else:
        node.value = node.value + 1
        what = "sabit+1"
    return fn2, what


def main() -> int:
    k = int(os.environ.get("MUT_K", "4"))
    seed = int(os.environ.get("MUT_SEED", "1"))
    targets = os.environ.get("MUT_FONK", DEFAULT).split()
    out_path = Path(os.environ.get("MUT_OUT", ROOT / "mutasyon-raporu.md"))
    py = os.environ.get("PY", sys.executable)
    rng = random.Random(seed)
    work = Path(tempfile.mkdtemp(prefix="hk-mut-"))
    try:
        subprocess.run(["rsync", "-a", "--exclude", ".git", "--exclude", "node_modules", "--exclude", "web",
                        "--exclude", "data", f"{ROOT}/", f"{work}/"], check=True)
        xd = ["-n", os.environ.get("MUT_N", "4")] if subprocess.run([py, "-c", "import xdist"],
                                                                    capture_output=True).returncode == 0 else []
        base = [py, "-m", "pytest", "-q", "-x", "-p", "no:cacheprovider", *xd, *TESTS]
        t0 = time.time()
        r = subprocess.run(base, cwd=work, capture_output=True, text=True)
        if r.returncode != 0:
            print(r.stdout[-2000:])
            raise SystemExit("temel test koşusu kırmızı: mutasyon anlamsız")
        rows, survivors = [], []
        for t in targets:
            mod, name = t.split(".", 1)
            src_path = work / "app" / f"{mod}.py"
            original = src_path.read_text(encoding="utf-8")
            tree = ast.parse(original)
            fn = _func(tree, name)
            pts = _points(fn)
            pick = rng.sample(pts, min(k, len(pts)))
            lines = original.splitlines(keepends=True)
            for idx, kind in pick:
                fn2, what = _apply(fn, idx, kind)
                fn2.decorator_list = []
                new_src = ast.unparse(fn2) + "\n"
                mutated = "".join(lines[:fn.lineno - 1]) + new_src + "".join(lines[fn.end_lineno:])
                src_path.write_text(mutated, encoding="utf-8")
                try:
                    res = subprocess.run(base, cwd=work, capture_output=True, text=True, timeout=600)
                    killed = res.returncode != 0
                except subprocess.TimeoutExpired:
                    killed = True
                finally:
                    src_path.write_text(original, encoding="utf-8")
                d = "".join(x for x in difflib.unified_diff((ast.unparse(_strip(fn)) + "\n").splitlines(True),
                                                            new_src.splitlines(True), n=0)
                            if x.startswith(("+", "-")) and not x.startswith(("+++", "---")))
                rows.append((t, what, killed))
                if not killed:
                    survivors.append((t, what, d))
                print(f"{'ÖLDÜ   ' if killed else 'YAŞIYOR'} {t} {what}", flush=True)
        total = len(rows)
        dead = sum(1 for r_ in rows if r_[2])
        with open(out_path, "w", encoding="utf-8") as f:
            f.write(f"# Mutasyon raporu (örneklem)\n\ntohum {seed}, fonksiyon başı {k} mutant, testler: {' '.join(TESTS)}\n\n")
            f.write(f"Toplam {total}, öldürülen {dead}, **hayatta kalan {total - dead}** "
                    f"(skor %{round(100 * dead / max(1, total))}), süre {round(time.time() - t0)} sn\n\n")
            f.write("| fonksiyon | mutasyon | sonuç |\n|---|---|---|\n")
            for t, what, killed in rows:
                f.write(f"| {t} | {what} | {'öldü' if killed else '**yaşıyor**'} |\n")
            for t, what, d in survivors:
                f.write(f"\n## Yaşayan: {t} ({what})\n\n```diff\n{d}```\n")
        print(f"rapor: {out_path} — {dead}/{total} öldü")
        return 0
    finally:
        shutil.rmtree(work, ignore_errors=True)


def _strip(fn: ast.FunctionDef) -> ast.FunctionDef:
    f = copy.deepcopy(fn)
    f.decorator_list = []
    return f


if __name__ == "__main__":
    sys.exit(main())
