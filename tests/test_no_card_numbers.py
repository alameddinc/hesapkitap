"""Açık repoda gerçek kart numarası olmasın: izlenen her metin dosyasında Luhn'u tutan 16 haneli numara yalnız genel
test numaralarından biri olabilir. 06.11.2026: bir döküm satırındaki kart numarası teste girmişti (geçmiş temizlendi)."""
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ALLOWED = {"4111111111111111", "4242424242424242", "5555555555554444", "4000000000000002"}  # genel test kartları
SUFFIXES = (".py", ".ts", ".tsx", ".js", ".md", ".json", ".txt", ".csv", ".html", ".yml", ".yaml", ".toml")
NUM = re.compile(r"(?<![\d*])\d{16}(?!\d)")


def _luhn(n: str) -> bool:
    s = 0
    for i, d in enumerate(reversed(n)):
        x = int(d) * (2 if i % 2 else 1)
        s += x - 9 if x > 9 else x
    return s % 10 == 0


def test_no_real_card_numbers_in_repo():
    files = subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True, text=True, check=True).stdout.split()
    found = []
    for f in files:
        if not f.endswith(SUFFIXES) or "node_modules" in f:
            continue
        try:
            text = (ROOT / f).read_text(errors="ignore")
        except OSError:
            continue
        for n in set(NUM.findall(text)):
            if n not in ALLOWED and _luhn(n) and not re.fullmatch(r"(\d)\1{15}", n):
                found.append(f"{f}: …{n[-4:]}")  # numaranın kendisi test çıktısına yazılmaz
    assert not found, "kart numarasına benzeyen değer (4111111111111111 gibi genel test numarası kullan): " + ", ".join(found)
