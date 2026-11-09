"""Tek seferlik (tekrar çalıştırılabilir) yönetici komutu: saklı kart ekstrelerinden kart şablonu öğren — yapay zekâsız.

    python -m app.card_reader_backfill [--dry-run] [--user <kullanıcı-adı>]

Yapay zekânın okuduğu (statements.source 'yapay_zeka') ya da kaynağı bilinmeyen (source boş: eski kayıt) PDF kart
ekstreleri için: saklı PDF uygulamanın depolama katmanından okunur (yerel / R2-S3), metne çevrilir, kayıtlı ekstre ve
işlemleri yapay zekâ sonucu yerine geçer ve kart_reader.explain_from_ai çalışır. Şablon YALNIZ aynı metne
uygulandığında kayıtlı sonucu aynen veriyor ve ekstre denklemi tutuyorsa kaydedilir (canlı yüklemeyle aynı kural).

Yan etkiler: yalnız sistem veritabanındaki bank_templates (--dry-run'da hiçbiri). Kullanıcı defterlerine yazılmaz
(salt okunur kopyadan okunur), yapay zekâya gidilmez, okuma hakkı / kota sayılmaz, topluluğa gönderilmez.

Çıktı ekstre başına tek satır: kullanıcı id, banka (bilinen banka adı ya da "?"), sonuç ve neden kodu. Belge metni,
isim, kart numarası ya da tutar yazılmaz. Kodlar: learned / revived / known / would_learn (--dry-run) / already_local
(bankaya özel okuyucu ya da kayıtlı şablon yapay zekâsız zaten okuyor) / no_file / password / unreadable / ve
card_reader.explain_from_ai kodları. Yapay zekâ okuması denklemi tutmuyor ama aynı belgeden şablon tutuyorsa şablon
yine öğrenilir (card_reader._text_fix) ve "ai_fix=N" eklenir: kayıtlı okumada N satır eksik / fazla / işareti ters.
Kayıtlı ekstre değiştirilmez — kullanıcıya düzeltme önerilecek.
"""
import contextlib
import os
import shutil
import sqlite3
import sys
import tempfile

from app import card_reader, db
from app.parsers.base import Statement, Transaction

_TXN_FIELDS = ("txn_date", "description", "amount", "txn_type", "card", "card_holder", "orig_amount", "orig_currency",
               "installment_no", "installments_left", "installment_left_amount", "points", "note")


def books(username: str | None = None) -> list[tuple[dict, str]]:
    """(kullanıcı, canlı defter dosyası); aynı defter bir kez (ortak defter üyeleri sahibininkini paylaşır)."""
    from app import system

    out, seen = [], set()
    for u in system.users():
        if username and u["username"] != username:
            continue
        paths = [system.user_db_path(u) or db.DB_PATH]
        if u.get("member_of"):
            paths.append(system.own_db_path(u))
        for p in paths:
            key = os.path.abspath(p)
            if key not in seen and os.path.isfile(p):
                seen.add(key)
                out.append((u, p))
    return out


def _date(v):
    from datetime import date

    return date.fromisoformat(v) if v else None


def statement_of(row: dict, txns: list[dict]) -> Statement:
    """Kayıtlı ekstre + işlemleri → Statement (yapay zekânın o gün verdiği sonuç; silinen işlemler dahil)."""
    st = Statement(bank=row["bank"], card_no=row["card_no"], period_end=_date(row["period_end"]),
                   due_date=_date(row["due_date"]), total_debt=row["total_debt"], min_payment=row["min_payment"],
                   credit_limit=row["credit_limit"], previous_balance=row["previous_balance"] or 0.0,
                   period_activity=row["period_activity"] or 0.0, interest_fees=row["interest_fees"] or 0.0,
                   payments=row["payments"] or 0.0, next_period_end=_date(row["next_period_end"]),
                   next_due_date=_date(row["next_due_date"]), available_limit=row.get("available_limit"),
                   ai_parsed=True, source="yapay_zeka")
    for t in txns:
        st.transactions.append(Transaction(**{**{k: t.get(k) for k in _TXN_FIELDS},
                                              "txn_date": _date(t["txn_date"]), "card_holder": t.get("card_holder") or ""}))
    return st


def _text(data: bytes, passwords: list[str]) -> tuple[str | None, str | None]:
    from app.parsers import PasswordRequired, _text_with_passwords

    try:
        return _text_with_passwords(data, passwords)[0], None
    except PasswordRequired:
        return None, "password"
    except Exception:  # noqa: BLE001 - bozuk PDF
        return None, "unreadable"


@contextlib.contextmanager
def _no_learn():
    orig = card_reader.learn
    card_reader.learn = lambda text, bank: None
    try:
        yield
    finally:
        card_reader.learn = orig


def _locally_readable(text: str) -> bool:
    """Bankaya özel okuyucu ya da KAYITLI kart şablonu yapay zekâsız okuyor mu (sayaçlara / kayıtlara dokunmadan).
    Kayıtsız deneme (card_reader.learn) sayılmaz: tutan kural burada kaydedilsin (check)."""
    from app import replay_check
    from app.parsers import parse_text

    with replay_check._isolated(no_ai=True), _no_learn():
        try:
            return parse_text(text) is not None
        except Exception:  # noqa: BLE001
            return False


def _fix_count(ai: Statement, fixed: Statement | None) -> int:
    """Kayıtlı (yapay zekâ) okumada şablonun düzelttiği satır sayısı: eksik / fazla / işareti ters (yalnız sayı)."""
    if fixed is None:
        return 0
    from collections import Counter

    from app import money

    def keys(s):
        return Counter((t.txn_date, money.kurus(t.amount)) for t in s.transactions)
    a, b = keys(ai), keys(fixed)
    return max(sum((a - b).values()), sum((b - a).values()))


def check(text: str, st: Statement, dry_run: bool) -> tuple[str, int]:
    """Tek ekstre: (neden kodu, kayıtlı okumada şablonun düzelttiği satır sayısı) — şablon kaydı --dry-run değilse.
    Kayıtlı ekstre DEĞİŞMEZ; düzeltme sayısı yalnız raporlanır (kullanıcıya önerilecek)."""
    from app import templates

    if _locally_readable(text):
        return "already_local", 0
    built, reason = card_reader.explain_from_ai(text, st)
    if not built and (got := card_reader.learn(text, card_reader.bank_of(text))) and got[0].get("bank") \
            and card_reader.is_safe(got[0]):
        built = got  # yapay zekâ okuması öğretmedi ama metin tek başına denklemi tutuyor (canlıda read() de kaydeder)
    if not built:
        return reason, 0
    fix = _fix_count(st, card_reader.fixed_read(text, st, built[0]))
    if dry_run:
        return "would_learn", fix
    spec = built[0]
    if templates.save(spec):
        return "learned", fix
    return ("revived" if templates._revive(spec) else "known"), fix


def _bank(st: Statement, text: str | None) -> str:
    from app.templates import IBAN_BANKS

    b = card_reader._hint_bank(st.bank) or (card_reader.bank_of(text) if text else "")
    return b if b in set(IBAN_BANKS.values()) else "?"


def _book(u: dict, live_path: str, dry_run: bool, emit) -> list[dict]:
    from app import storage

    tmp = tempfile.mkdtemp(prefix="card-backfill-")
    out = []
    try:
        copy = os.path.join(tmp, "kopya.db")
        with contextlib.closing(sqlite3.connect(f"file:{live_path}?mode=ro", uri=True)) as src, \
                contextlib.closing(sqlite3.connect(copy)) as dst:
            src.backup(dst)
        with db.use_space(copy, u):
            db._space.get()["files"] = db._files_scope(live_path)  # dosyalar canlı defterin alanından (R2/yerel)
            with db.connect() as c:
                rows = [dict(r) for r in c.execute(
                    "SELECT * FROM statements WHERE COALESCE(source, '') IN ('', 'yapay_zeka') "
                    "AND lower(COALESCE(filename, '')) NOT LIKE '%.xls%' AND lower(COALESCE(filename, '')) NOT LIKE '%.csv' "
                    "ORDER BY uploaded_at, id")]
                txns = {r["id"]: [dict(t) for t in c.execute(
                    "SELECT * FROM transactions WHERE statement_id = ? ORDER BY id", (r["id"],))] for r in rows}
            passwords = db.pdf_passwords()
            for r in rows:
                st = statement_of(r, txns[r["id"]])
                data = storage.book_get("uploads", f"{r['file_hash']}.pdf")
                text, reason = (None, "no_file") if not data else _text(data, passwords)
                fix = 0
                if text is not None:
                    try:
                        reason, fix = check(text, st, dry_run)
                    except Exception:  # noqa: BLE001 - bir ekstre diğerlerini durdurmasın
                        reason = "error"
                rec = {"user": u["id"], "bank": _bank(st, text), "ok": reason in ("learned", "revived", "known",
                                                                                    "would_learn", "already_local"),
                       "reason": reason, "fix": fix}
                out.append(rec)
                emit(rec)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return out


def run(dry_run: bool = False, username: str | None = None, emit=None) -> list[dict]:
    emit = emit or (lambda rec: print(f"user={rec['user']} bank={rec['bank']} ok={int(rec['ok'])} reason={rec['reason']}"
                                      + (f" ai_fix={rec['fix']}" if rec.get("fix") else ""), flush=True))
    out = []
    for u, path in books(username):
        out += _book(u, path, dry_run, emit)
    return out


def main(argv: list[str]) -> int:
    dry = "--dry-run" in argv
    user = argv[argv.index("--user") + 1] if "--user" in argv and argv.index("--user") + 1 < len(argv) else None
    recs = run(dry, user)
    learned = sum(r["reason"] in ("learned", "would_learn") for r in recs)
    print(f"toplam={len(recs)} {'öğrenilecek' if dry else 'öğrenilen'}={learned}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
