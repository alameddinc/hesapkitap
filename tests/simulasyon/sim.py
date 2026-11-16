"""ZAMAN HIZLANDIRMALI SİMÜLASYON: gerçek hayat gün gün oynatılır.

Simülatör iki defter tutar:
- GERÇEK DEFTER (ground truth): her banka/nakit hesabının gerçek bakiyesi, her kartın gerçek borcu (kesilmiş ekstreler),
  her borcun kalanı, kumbara. Kullanıcı uygulamaya söylemese de para hareket eder.
- BEKLENEN UYGULAMA DURUMU: kullanıcının uygulamaya o ana kadar ne söylediği. Hesap bakiyesi = son çapa (elle bakiye
  ya da döküm kapanışı) + çapadan SONRA gerçekleşmiş ve uygulamaya bildirilmiş hareketler.

Uygulamaya veri kullanıcının yapacağı gibi API'den (TestClient) girer: elle kayıt, ekstre PDF'i (sentetik metin,
app/parsers/vakifbank.py okur), hesap dökümü CSV'si (app/bank_import okur), dekont (app/dekont_parse okur), Ödendi,
aktarım, borç, kumbara, fatura. Her gün sonunda finance.net_worth / payables / expected / finance_audit.check çağrılır ve
uygulamanın rakamları BAĞIMSIZ hesapla karşılaştırılır (kontroller: K1 bakiye, K2 free, K3 spendable kalemleri, K4
değişmezler, K5 istisna, K6 denetim). Ufuk (spend_horizon / maaştan önce vadeli) da simülatörün bildiği maaş günlerinden
bağımsız hesaplanır.

Tutarlar, kişiler, kart numaraları, IBAN'lar UYDURMADIR. Rastgelelik tohumlu (persona.seed): aynı tohum aynı günleri
üretir; uyuşmazlık raporu gün, adım (son kullanıcı adımları) ve farkı yazar.
"""
from __future__ import annotations

import calendar
import json
import random
import time
import traceback
from dataclasses import dataclass, field
from datetime import date, timedelta

from app import workdays
from tests.simulasyon import belgeler, saat

EPS = 0.01


# ---------------------------------------------------------------------------- persona tanımı

@dataclass
class AccCfg:
    key: str
    name: str
    kind: str = "banka"          # banka | nakit
    start: float = 0.0
    mode: str = "elle"           # elle: her hareket elle girilir | dokum: hareketler hesap dökümüyle gelir
    iban: str = ""
    bank: str = "Örnek Bankası A.Ş."


@dataclass
class SalaryCfg:
    desc: str
    amount: float
    day: int
    acc: str
    shift: str = "prev"          # prev: tatilse önceki iş günü | none: kaymaz (SSK emekli) | free: düzensiz
    jitter: float = 0.08         # gecikme/erken olasılığı
    var: float = 0.0             # tutar oynaklığı (serbest çalışan)


@dataclass
class CardCfg:
    key: str
    card_no: str
    cut_day: int                 # 0 = ayın son günü
    due_days: int = 10           # son ödeme = kesim + due_days (iş gününe kayar)
    pay_acc: str = ""
    styles: dict = field(default_factory=lambda: {"hesaptan": 5, "hesapsiz": 2, "bakiye_once": 1, "dokum": 0})
    buy_p: float = 0.45          # günlük harcama olasılığı
    buy_mean: float = 900.0
    manual_p: float = 0.4        # harcamanın elle de girilme olasılığı


@dataclass
class BillCfg:
    provider: str
    base: float
    due_day: int
    acc: str
    via: str = "havale"          # havale | otomatik (kartla; card key ile "kart:K1")


@dataclass
class Persona:
    key: str
    title: str
    seed: int
    owner: str
    owner_ascii: str
    cycle_day: int
    accounts: list[AccCfg]
    salaries: list[SalaryCfg] = field(default_factory=list)
    cards: list[CardCfg] = field(default_factory=list)
    rent: tuple | None = None    # (tutar, gün, hesap, "elle"|"dekont")
    bills: list[BillCfg] = field(default_factory=list)
    cash_p: float = 0.5
    cash_mean: float = 250.0
    cash_forget_p: float = 0.1   # nakit harcamayı girmeyi unutma
    jar: tuple | None = None     # (hesap, aylık, gün)
    debts: list[dict] = field(default_factory=list)
    transfer_p: float = 0.06     # kendi hesapları arası aktarım olasılığı (gün)
    atm_p: float = 0.04
    update_every: tuple = (6, 13)
    dokum_every: tuple = (5, 11)
    income_irregular: bool = False


# ---------------------------------------------------------------------------- gerçek defter

@dataclass
class Move:
    seq: int
    day: date
    amount: float
    desc: str                    # döküm satırı açıklaması
    informed: bool               # uygulamaya bakiyeyi etkileyecek şekilde bildirildi mi
    tag: str = ""


@dataclass
class Acc:
    cfg: AccCfg
    id: int = 0
    truth: float = 0.0
    moves: list = field(default_factory=list)
    anchor_bal: float = 0.0
    anchor_day: date | None = None
    anchor_seq: int = 0
    dokum_until: date | None = None
    next_dokum: date | None = None
    next_update: date | None = None

    def truth_at_end_of(self, d: date) -> float:
        return round(self.cfg.start + sum(m.amount for m in self.moves if m.day <= d), 2)

    def expected_app(self) -> float:
        return round(self.anchor_bal + sum(m.amount for m in self.moves if m.informed and m.seq > self.anchor_seq), 2)


@dataclass
class Stmt:
    card: str
    cut: date
    due: date
    total: float
    sid: int | None = None
    uploaded: date | None = None
    paid: float = 0.0            # gerçekte ödenen
    app_paid: float = 0.0        # uygulamanın bildiği ödeme
    pay_day: date | None = None
    pay_plan: date | None = None
    style: str = ""


@dataclass
class Buy:
    day: date
    amount: float
    desc: str
    manual: bool
    entry_id: int | None = None


@dataclass
class Card:
    cfg: CardCfg
    stmts: list = field(default_factory=list)
    buys: list = field(default_factory=list)
    payments: list = field(default_factory=list)  # (gün, tutar) ekstre döngüsüne yazılacak ödemeler
    last_cut: date | None = None
    last_pay_acc: str | None = None


@dataclass
class Bill:
    provider: str
    amount: float
    due: date
    acc: str
    via: str
    bid: int | None = None
    paid: bool = False
    pay_plan: date | None = None


@dataclass
class Debt:
    key: str
    person: str
    direction: str               # borc | alacak
    amount: float
    did: int | None = None
    plan: list = field(default_factory=list)   # [(tarih, tutar)] taksitler; boşsa tek vade
    due: date | None = None
    paid: float = 0.0
    acc: str | None = None


@dataclass
class Mismatch:
    day: str
    check: str
    detail: str
    expected: object = None
    actual: object = None
    steps: list = field(default_factory=list)


# ---------------------------------------------------------------------------- takvim yardımcıları

def last_day(y: int, m: int) -> int:
    return calendar.monthrange(y, m)[1]


def add_month(y: int, m: int, n: int = 1) -> tuple[int, int]:
    k = y * 12 + (m - 1) + n
    return k // 12, k % 12 + 1


def clamp(y: int, m: int, day: int) -> date:
    return date(y, m, min(day, last_day(y, m)) if day else last_day(y, m))


def period_of(d: date, start: int) -> str:
    if start <= 1 or d.day < start:
        return f"{d.year:04d}-{d.month:02d}"
    y, m = add_month(d.year, d.month)
    return f"{y:04d}-{m:02d}"


def period_bounds(p: str, start: int) -> tuple[date, date]:
    y, m = map(int, p.split("-"))
    if start <= 1:
        return date(y, m, 1), date(y, m, last_day(y, m))
    py, pm = add_month(y, m, -1)
    return date(py, pm, start), date(y, m, start) - timedelta(days=1)


def next_period(p: str, n: int = 1) -> str:
    y, m = add_month(*map(int, p.split("-")), n)
    return f"{y:04d}-{m:02d}"


def occurrence(p: str, day: int, start: int) -> date:
    """db.occurrence_date ile aynı tanım (bağımsız yazıldı): dönemin içine düşen ayın `day`'i, ay sonuna kırpılır."""
    y, m = map(int, p.split("-"))
    if start > 1 and day >= start:
        y, m = add_month(y, m, -1)
    return clamp(y, m, day)


# ---------------------------------------------------------------------------- simülatör

class Sim:
    START = date(2026, 11, 1)
    END = date(2027, 8, 31)

    def __init__(self, persona: Persona, client, monkeypatch, tmp_path, start: date | None = None,
                 end: date | None = None, deep_every: int = 1):
        self.p = persona
        self.c = client
        self.mp = monkeypatch
        self.rng = random.Random(persona.seed)
        self.start = start or self.START
        self.end = end or self.END
        self.deep_every = deep_every
        self.clock = saat.SimClock(self.start)
        self.seq = 0
        self.today = self.start
        self.accs: dict[str, Acc] = {}
        self.cards: dict[str, Card] = {}
        self.bills: list[Bill] = []
        self.debts: dict[str, Debt] = {}
        self.jar_id: int | None = None
        self.jar_bal = 0.0
        self.rent_rid: int | None = None
        self.rent_paid: dict[str, date] = {}       # dönem → ödendiği gün (bildirildi)
        self.salary_rids: dict[str, int] = {}
        self.salary_paid: dict[tuple, date] = {}   # (maaş açıklaması, dönem) → yattığı gün (bildirildi)
        self.salary_info: dict[tuple, date] = {}   # uygulamanın bildiği maaşlar (döküm kipinde döküm gelince)
        self.salary_wait: list[tuple] = []
        self.rent_wait: list[tuple] = []
        self.rent_app_pending: set = set()
        self.unknown: list[dict] = []
        self.two_sal_seen = False              # hesapsız Ödendi: {stmt, amount, day, seq, acc, linked}
        self.steps: list[str] = []
        self.mismatches: list[Mismatch] = []
        self.stats = {"gun": 0, "kontrol": 0, "adim": 0, "api": 0, "derin_gun": 0}
        self.gaps: list[float] = []
        self.trace: list[dict] = []
        self.audit_seen: dict[str, int] = {}
        self.tmp = tmp_path
        self._ref = 7700000000

    # ------------------------------------------------------------------ altyapı

    def log(self, msg: str) -> None:
        self.steps.append(f"{self.today} {msg}")
        if len(self.steps) > 40:
            del self.steps[:-40]
        self.stats["adim"] += 1

    def api(self, method: str, url: str, ok=(200,), **kw):
        self.clock.tick()
        self.stats["api"] += 1
        try:
            r = getattr(self.c, method)(url, **kw)
        except Exception as e:  # noqa: BLE001
            self.fail("K5_istisna", f"{method.upper()} {url}: {e!r}", tb=traceback.format_exc(limit=6))
            return None
        if r.status_code not in ok:
            self.fail("K5_http", f"{method.upper()} {url} → {r.status_code}: {r.text[:300]}",
                      expected=ok, actual=r.status_code)
            return None
        return r.json() if r.headers.get("content-type", "").startswith("application/json") else r.text

    def fail(self, check: str, detail: str, expected=None, actual=None, tb: str | None = None) -> None:
        self.mismatches.append(Mismatch(self.today.isoformat(), check, detail + (f"\n{tb}" if tb else ""),
                                        expected, actual, list(self.steps[-8:])))

    def move(self, acc: str, amount: float, desc: str, informed: bool, tag: str = "") -> Move:
        self.seq += 1
        a = self.accs[acc]
        m = Move(self.seq, self.today, round(amount, 2), desc, informed, tag)
        a.moves.append(m)
        a.truth = round(a.truth + amount, 2)
        return m

    def anchor(self, acc: str, bal: float, day: date, seq: int) -> None:
        a = self.accs[acc]
        a.anchor_bal, a.anchor_day, a.anchor_seq = round(bal, 2), day, seq

    def ref(self) -> str:
        self._ref += self.rng.randint(1, 999)
        return str(self._ref)

    # ------------------------------------------------------------------ kurulum

    def setup(self) -> None:
        from app import db

        saat.install(self.mp, self.clock)
        belgeler.install(self.mp)
        self.mp.setattr(db, "DB_PATH", str(self.tmp / f"sim-{self.p.key}.db"))
        self.mp.setattr(db, "UPLOAD_DIR", str(self.tmp / "uploads"))
        self.mp.setattr(db, "BILL_DIR", str(self.tmp / "bills"))
        db.init_db()
        self.day0 = self.start - timedelta(days=1)
        self.today = self.day0
        self.clock.set_day(self.day0)
        saat.freeze(self.mp, self.day0)
        db.set_setting("cycle_mode", "manual")
        db.set_cycle_start_day(self.p.cycle_day)
        db.remember_owner_name(self.p.owner)
        for cfg in self.p.accounts:
            self.api("post", "/api/accounts", json={"name": cfg.name, "balance": cfg.start, "kind": cfg.kind,
                                                    "as_of": self.day0.isoformat()})
            a = Acc(cfg, truth=cfg.start, anchor_bal=cfg.start, anchor_day=self.day0)
            a.dokum_until = self.day0
            a.next_dokum = self.start + timedelta(days=self.rng.randint(*self.p.dokum_every))
            a.next_update = self.start + timedelta(days=self.rng.randint(*self.p.update_every))
            self.accs[cfg.key] = a
        ids = {x["name"]: x["id"] for x in db.list_accounts()}
        for a in self.accs.values():
            a.id = ids[a.cfg.name]
            if a.cfg.iban:
                with db.connect() as c:
                    c.execute("UPDATE accounts SET iban = ? WHERE id = ?", (a.cfg.iban, a.id))
        for s in self.p.salaries:
            r = self.api("post", "/api/recurring", json={"description": s.desc, "amount": s.amount, "kind": "gelir",
                                                        "category": "Maaş", "day_of_month": s.day,
                                                        "start_month": "2026-06", "account_id": self.accs[s.acc].id})
            self.salary_rids[s.desc] = r["id"]
        if self.p.rent:
            amt, day, acc, _ = self.p.rent
            r = self.api("post", "/api/recurring", json={"description": "Ev kirası", "amount": amt, "kind": "gider",
                                                        "category": "Kira & Aidat", "day_of_month": day,
                                                        "start_month": "2026-06", "account_id": self.accs[acc].id})
            self.rent_rid = r["id"]
            # başlangıç dönemi ve öncekinin kirası ödendi (sim öncesi): uygulamaya girilir, bakiyeye etkisi yok
            # (başlangıç bakiyesinin içinde; tarih çapadan önce)
            for back in (2, 1):
                p0 = next_period(period_of(self.start, self.p.cycle_day), -back)
                on = occurrence(p0, day, self.p.cycle_day)
                if on <= self.day0:
                    self.api("post", "/api/entries", json={"entry_date": on.isoformat(), "description": "Ev kirası",
                                                           "amount": amt, "kind": "gider", "category": "Kira & Aidat",
                                                           "payment": "havale", "account_id": self.accs[acc].id,
                                                           "recurring_id": self.rent_rid})
                    self.rent_paid[p0] = on
        # geçmiş maaşlar (sim öncesi) da girilmiş: önceki dönemlerin maaşı yatmış
        for s in self.p.salaries:
            for back in (2, 1, 0):
                p0 = next_period(period_of(self.start, self.p.cycle_day), -back)
                on = self.salary_date(s, p0)
                if on <= self.day0:
                    self.api("post", "/api/entries", json={"entry_date": on.isoformat(), "description": s.desc,
                                                           "amount": s.amount, "kind": "gelir", "category": "Maaş",
                                                           "account_id": self.accs[s.acc].id})
                    self.salary_paid[(s.desc, p0)] = on
                    self.salary_info[(s.desc, p0)] = on
        for cc in self.p.cards:
            card = Card(cc)
            self.cards[cc.key] = card
            # başlangıç: son kesilmiş ekstre (ödenmiş ya da son ödemesi gelmemiş) yüklenir
            cut = self.prev_cut(cc, self.day0)
            prev_cut = self.prev_cut(cc, cut - timedelta(days=1))
            buys = [(prev_cut + timedelta(days=3), "ORNEK MARKET BASLANGIC", round(cc.buy_mean * 12, 2))]
            st = Stmt(cc.key, cut, self.due_of(cc, cut), round(sum(b[2] for b in buys), 2))
            card.stmts.append(st)
            card.last_cut = cut
            self.upload_statement(card, st, prev=0.0, payments=[], buys=buys)
            if st.due < self.start:
                st.paid = st.app_paid = st.total
                self.api("post", f"/api/statements/{st.sid}/pay", json={"amount": st.total,
                                                                         "paid_at": st.due.isoformat(),
                                                                         "account_id": None})
                st.pay_day = st.due
            else:
                self.plan_card_payment(card, st)
        if self.p.jar:
            acc, monthly, _ = self.p.jar
            r = self.api("post", "/api/jars", json={"name": "Tatil", "goal": monthly * 12,
                                                    "account_id": self.accs[acc].id})
            self.jar_id = r["id"]

    # ------------------------------------------------------------------ takvim kuralları (simülatörün dünyası)

    def cut_date(self, cc: CardCfg, y: int, m: int) -> date:
        return clamp(y, m, cc.cut_day)

    def prev_cut(self, cc: CardCfg, d: date) -> date:
        c = self.cut_date(cc, d.year, d.month)
        if c > d:
            y, m = add_month(d.year, d.month, -1)
            c = self.cut_date(cc, y, m)
        return c

    def next_cut(self, cc: CardCfg, cut: date) -> date:
        y, m = add_month(cut.year, cut.month)
        return self.cut_date(cc, y, m)

    def due_of(self, cc: CardCfg, cut: date) -> date:
        d = cut + timedelta(days=cc.due_days)
        return workdays.next_business_day(d) if not workdays.is_business_day(d) else d

    def salary_date(self, s: SalaryCfg, p: str) -> date:
        nominal = occurrence(p, s.day, self.p.cycle_day)
        if s.shift == "prev" and not workdays.is_business_day(nominal):
            return workdays.prev_business_day(nominal)
        return nominal

    # ------------------------------------------------------------------ belgeler

    def upload_statement(self, card: Card, st: Stmt, prev: float, payments: list, buys: list) -> None:
        cc = card.cfg
        nc = self.next_cut(cc, st.cut)
        text = belgeler.card_statement(self.p.owner, cc.card_no, st.cut, st.due, nc, self.due_of(cc, nc), prev,
                                       payments, buys)
        data = belgeler.register(text)
        r = self.api("post", "/api/statements/upload",
                     files=[("files", (f"ekstre-{cc.key}-{st.cut}.pdf", data, "application/pdf"))])
        from app import db

        sid = next((x["id"] for x in db.list_statements()
                    if x["card_no"] == cc.card_no and x["period_end"] == st.cut.isoformat()), None)
        if sid is None:
            self.fail("C_ekstre_okunamadi", f"{cc.key} {st.cut} ekstresi kaydedilmedi: {str(r)[:300]}")
            return
        row = next(x for x in db.list_statements() if x["id"] == sid)
        if abs((row["total_debt"] or 0) - st.total) > EPS:
            self.fail("C_ekstre_tutar", f"{cc.key} {st.cut} toplam", st.total, row["total_debt"])
        if row["due_date"] != st.due.isoformat():
            self.fail("C_ekstre_vade", f"{cc.key} {st.cut} son ödeme", st.due.isoformat(), row["due_date"])
        st.sid, st.uploaded = sid, self.today
        self.log(f"ekstre yüklendi {cc.key} kesim {st.cut} toplam {st.total}")

    def upload_dokum(self, a: Acc, until: date) -> None:
        frm = a.dokum_until + timedelta(days=1)
        rows = []
        bal = a.truth_at_end_of(a.dokum_until)
        for m in sorted((m for m in a.moves if frm <= m.day <= until), key=lambda m: m.seq):
            bal = round(bal + m.amount, 2)
            rows.append((m.day, m.desc, m.amount, bal))
        a.dokum_until = until
        if not rows:
            return
        data = belgeler.bank_csv(self.p.owner_ascii, rows, reverse=self.rng.random() < 0.3)
        name = f"dokum-{a.cfg.key}-{until}.csv"
        pv = self.api("post", "/api/bank-import/preview", files={"file": (name, data, "text/csv")})
        if not pv:
            return
        r = self.api("post", "/api/bank-import", files={"file": (name, data, "text/csv")},
                     data={"mapping": json.dumps(pv["mapping"]), "header_row": str(pv["header_row"]),
                           "account_id": str(a.id)})
        last = max(rows, key=lambda x: x[0])[0]
        last_seq = max(m.seq for m in a.moves if m.day <= until)
        self.anchor(a.cfg.key, a.truth_at_end_of(last), last, last_seq)
        for w in list(self.salary_wait):
            if w[0] == a.cfg.key and w[3] <= until:
                self.salary_info[(w[1], w[2])] = w[3]
                self.salary_wait.remove(w)
        for w in list(self.rent_wait):
            if w[0] == a.cfg.key and w[2] <= until:
                self.rent_app_pending.discard(w[1])
                self.rent_wait.remove(w)
        # dökümdeki kart ödemesi: o kartın "son ödendiği hesap" artık bu (db.last_pay_accounts; belirsiz ödeme adayı)
        for m in a.moves:
            if m.tag == "kart" and frm <= m.day <= until:
                for card in self.cards.values():
                    if m.desc.endswith(card.cfg.card_no[-4:]):
                        card.last_pay_acc = a.cfg.key
        # dökümde görünen hesapsız kart ödemeleri artık bağlanır
        for u in self.unknown:
            if u["acc"] == a.cfg.key and u["day"] <= until:
                u["linked"] = True
        self.log(f"döküm {a.cfg.key} {frm}..{until} {len(rows)} satır → {r}")

    # ------------------------------------------------------------------ olaylar

    def do_salary(self) -> None:
        for s in self.p.salaries:
            p = period_of(self.today, self.p.cycle_day)
            for per in (p, next_period(p)):  # erken yatan maaş sonraki dönemin olabilir
                if (s.desc, per) in self.salary_paid or (s.desc, per, "plan") in self.salary_paid:
                    continue
                on = self.salary_date(s, per)
                if per != p and (on - self.today).days > 5:
                    continue
                if s.shift == "free" or self.rng.random() < s.jitter:
                    k = self.rng.choice([-2, -1, 1, 2, 3]) if s.shift == "free" else self.rng.choice([-1, 1])
                    on = workdays.add_business_days(on, k) if k > 0 else on + timedelta(days=k)
                self.salary_paid[(s.desc, per, "plan")] = on
        for key, on in list(self.salary_paid.items()):
            if len(key) == 3 and on == self.today:
                desc, per, _ = key
                s = next(x for x in self.p.salaries if x.desc == desc)
                amt = s.amount if not s.var else round(s.amount * (1 + self.rng.uniform(-s.var, s.var)), 2)
                del self.salary_paid[key]
                self.pay_salary(s, per, amt)

    def pay_salary(self, s: SalaryCfg, per: str, amt: float) -> None:
        a = self.accs[s.acc]
        elle = a.cfg.mode == "elle"
        self.move(s.acc, amt, f"ORNEK A.S. MAAS ODEMESI {per}" if "Emekli" not in s.desc else "SGK EMEKLI AYLIGI",
                  informed=elle, tag="maas")
        if elle:
            self.api("post", "/api/entries", json={"entry_date": self.today.isoformat(), "description": s.desc,
                                                   "amount": amt, "kind": "gelir", "category": "Maaş",
                                                   "account_id": a.id})
        self.salary_paid[(s.desc, per)] = self.today
        if elle:
            self.salary_info[(s.desc, per)] = self.today
        else:
            self.salary_wait.append((s.acc, s.desc, per, self.today))
        self.log(f"maaş {s.desc} {amt} ({per}) {'elle' if elle else 'dökümde'}")

    def do_cards(self) -> None:
        for card in self.cards.values():
            cc = card.cfg
            # harcama (kesimden önce: kesim günü harcaması o ekstrede)
            if self.rng.random() < cc.buy_p:
                amt = round(self.rng.lognormvariate(0, 0.8) * cc.buy_mean * 0.7, 2)
                amt = max(12.5, min(amt, cc.buy_mean * 8))
                desc = self.rng.choice(["ORNEK MARKET", "ORNEK AKARYAKIT", "ORNEK ECZANE", "ORNEK GIYIM",
                                        "ORNEK RESTORAN", "ORNEK ELEKTRONIK"])
                manual = self.rng.random() < cc.manual_p
                b = Buy(self.today, amt, f"{desc} {self.rng.randint(10, 99)}", manual)
                card.buys.append(b)
                if manual:
                    r = self.api("post", "/api/entries", json={
                        "entry_date": self.today.isoformat(), "description": b.desc.title(), "amount": amt,
                        "kind": "gider", "category": "Market", "payment": "kredi_karti",
                        "card_last4": cc.card_no[-4:]})
                    b.entry_id = r and r.get("id")
                self.log(f"kart {cc.key} harcama {amt} {'elle' if manual else 'ekstrede'}")
            # kesim
            nc = self.next_cut(cc, card.last_cut)
            if self.today == nc:
                buys = [b for b in card.buys if card.last_cut < b.day <= nc]
                pays = [(dd, a) for dd, a in card.payments if card.last_cut < dd <= nc]
                prev = card.stmts[-1].total if card.stmts else 0.0
                total = round(prev + sum(b.amount for b in buys) - sum(a for _, a in pays), 2)
                st = Stmt(cc.key, nc, self.due_of(cc, nc), total)
                st._buys, st._pays, st._prev = buys, pays, prev  # type: ignore[attr-defined]
                st._upload = self.today + timedelta(days=self.rng.randint(1, 4))  # type: ignore[attr-defined]
                card.stmts.append(st)
                card.last_cut = nc
            for st in card.stmts:
                if st.sid is None and getattr(st, "_upload", None) == self.today:
                    self.upload_statement(card, st, st._prev,  # type: ignore[attr-defined]
                                          st._pays, [(b.day, b.desc, b.amount) for b in st._buys])  # type: ignore
                    self.plan_card_payment(card, st)
                if st.pay_plan == self.today and st.paid < st.total:
                    self.pay_card(card, st)

    def plan_card_payment(self, card: Card, st: Stmt) -> None:
        if st.total <= EPS:
            st.paid = st.total
            return
        lo = max(st.uploaded or self.today, st.due - timedelta(days=4))
        st.pay_plan = lo + timedelta(days=self.rng.randint(0, max(0, (st.due - lo).days)))
        st.pay_plan = max(st.pay_plan, self.today + timedelta(days=1)) if st.pay_plan <= self.today else st.pay_plan
        styles = card.cfg.styles
        acc = self.accs[card.cfg.pay_acc]
        pool = [k for k, w in styles.items() for _ in range(w)]
        if acc.cfg.mode == "dokum":
            pool = [k for k in pool if k != "bakiye_once"] or ["dokum"]
        st.style = self.rng.choice(pool)

    def pay_card(self, card: Card, st: Stmt) -> None:
        cc = card.cfg
        a = self.accs[cc.pay_acc]
        amt = round(st.total - st.paid, 2)
        style = st.style
        desc = f"KREDI KARTI ODEMESI {cc.card_no[:4]} **** {cc.card_no[-4:]}"
        st.paid = st.total
        st.pay_day = self.today
        card.payments.append((self.today, amt))
        if style == "hesaptan":
            m = self.move(cc.pay_acc, -amt, desc, informed=True, tag="kart")
            self.api("post", f"/api/statements/{st.sid}/pay", json={"amount": amt, "paid_at": self.today.isoformat(),
                                                                     "account_id": a.id})
            st.app_paid = st.total
            card.last_pay_acc = cc.pay_acc
        elif style == "hesapsiz":
            m = self.move(cc.pay_acc, -amt, desc, informed=False, tag="kart")
            self.api("post", f"/api/statements/{st.sid}/pay", json={"amount": amt, "paid_at": self.today.isoformat()})
            st.app_paid = st.total
            self.unknown.append({"stmt": st, "amount": amt, "day": self.today, "seq": m.seq, "acc": cc.pay_acc,
                                 "linked": False, "card": cc.key})
        elif style == "bakiye_once":
            # önce bankada öder, sonra uygulamada bakiyeyi günceller, sonra Ödendi + "bu ödeme o bakiyede var"
            m = self.move(cc.pay_acc, -amt, desc, informed=True, tag="kart")
            self.update_balance(cc.pay_acc)
            r = self.api("post", f"/api/statements/{st.sid}/pay", json={
                "amount": amt, "paid_at": self.today.isoformat(), "account_id": a.id, "in_balance": True})
            if r is not None and "anchor_conflict" not in r:
                self.fail("K1_capa_sorusu", f"{cc.key}: aynı gün güncellenen hesaptan ödemede çapa sorusu gelmedi",
                          "anchor_conflict", r)
            st.app_paid = st.total
            card.last_pay_acc = cc.pay_acc
        else:  # dokum: uygulamaya yalnız dökümle gelir
            m = self.move(cc.pay_acc, -amt, desc, informed=False, tag="kart")
            st._via_dokum = True  # type: ignore[attr-defined]
        self.log(f"kart ödemesi {cc.key} {amt} ({style}) seq {m.seq}")

    def do_rent(self) -> None:
        if not self.p.rent:
            return
        amt, day, acc, via = self.p.rent
        p = period_of(self.today, self.p.cycle_day)
        if p in self.rent_paid:
            return
        nominal = occurrence(p, day, self.p.cycle_day)
        on = nominal if workdays.is_business_day(nominal) else workdays.prev_business_day(nominal)
        if self.today != max(on, period_bounds(p, self.p.cycle_day)[0]):
            return
        a = self.accs[acc]
        landlord = "ORNEK EV SAHIBI"
        if a.cfg.mode == "dokum":
            self.move(acc, -amt, f"KIRA ODEMESI EFT {landlord}", informed=False, tag="kira")
            self.rent_paid[p] = self.today
            self.rent_app_pending.add(p)
            self.rent_wait.append((acc, p, self.today))
            self.log(f"kira {amt} (dökümde)")
            return
        self.move(acc, -amt, f"KIRA ODEMESI EFT {landlord}", informed=True, tag="kira")
        if via == "dekont":
            text = belgeler.dekont(self.p.owner, a.cfg.iban or "TR000001000000000000000001", landlord,
                                   "TR990006400000000000000077", "Örnek Bankası A.Ş.", self.today, amt, self.ref(), "Kira")
            pdf = belgeler.register(text)
            r = self.api("post", "/api/dekont/parse", files={"file": ("dekont.pdf", pdf, "application/pdf")})
            if r and (abs((r.get("amount") or 0) - amt) > EPS or r.get("date") != self.today.isoformat()):
                self.fail("C_dekont_okuma", "kira dekontu yanlış okundu", {"amount": amt, "date": str(self.today)},
                          {k: r.get(k) for k in ("amount", "date", "direction", "counterparty")})
            body = {"date": self.today.isoformat(), "amount": amt, "direction": "giden",
                    "counterparty": (r or {}).get("counterparty") or landlord, "account_id": a.id,
                    "description": "Kira", "ref": (r or {}).get("ref"), "dekont_file": (r or {}).get("dekont_file")}
            self.api("post", "/api/dekont/save", json=body)
        else:
            self.api("post", "/api/entries", json={"entry_date": self.today.isoformat(), "description": "Ev kirası",
                                                   "amount": amt, "kind": "gider", "category": "Kira & Aidat",
                                                   "payment": "havale", "account_id": a.id})
        self.rent_paid[p] = self.today
        self.log(f"kira {amt} ({via})")

    def do_bills(self) -> None:
        for bc in self.p.bills:
            y, m = self.today.year, self.today.month
            due = clamp(y, m, bc.due_day)
            if due < self.today:
                y, m = add_month(y, m)
                due = clamp(y, m, bc.due_day)
            if (due - self.today).days == 12 and not any(b.provider == bc.provider and b.due == due for b in self.bills):
                amt = round(bc.base * self.rng.uniform(0.7, 1.4), 2)
                b = Bill(bc.provider, amt, due, bc.acc, bc.via)
                r = self.api("post", "/api/bills", json={"provider": bc.provider, "amount": amt,
                                                         "due_date": due.isoformat()})
                b.bid = r and r.get("id")
                b.pay_plan = due - timedelta(days=self.rng.randint(0, 5))
                if b.pay_plan <= self.today:
                    b.pay_plan = self.today + timedelta(days=1)
                self.bills.append(b)
                self.log(f"fatura geldi {bc.provider} {amt} vade {due}")
        for b in self.bills:
            if not b.paid and b.pay_plan == self.today:
                self.pay_bill(b)

    def pay_bill(self, b: Bill) -> None:
        b.paid = True
        if b.via.startswith("kart:"):
            card = self.cards[b.via[5:]]
            card.buys.append(Buy(self.today, b.amount, f"{b.provider.upper()} OTOMATIK ODEME", True))
            self.api("post", f"/api/bills/{b.bid}/pay", json={"amount": b.amount, "paid_at": self.today.isoformat(),
                                                             "payment": "kredi_karti"})
            self.log(f"fatura {b.provider} {b.amount} karttan otomatik")
            return
        a = self.accs[b.acc]
        # faturanın "Ödendi"si hesap sormuyor (PayIn'de account_id yok): bakiye uygulamada düşmez → bildirilmemiş
        self.move(b.acc, -b.amount, f"{b.provider.upper()} FATURA ODEMESI", informed=False, tag="fatura")
        self.api("post", f"/api/bills/{b.bid}/pay", json={"amount": b.amount, "paid_at": self.today.isoformat(),
                                                         "payment": "havale"})
        self.log(f"fatura {b.provider} {b.amount} {a.cfg.key}'dan (hesap sorulmadı)")

    def do_cash(self) -> None:
        nakit = next((a for a in self.accs.values() if a.cfg.kind == "nakit"), None)
        if not nakit:
            return
        if self.rng.random() < self.p.cash_p and nakit.truth > 50:
            amt = round(min(nakit.truth - 10, max(5.0, self.rng.expovariate(1 / self.p.cash_mean))), 2)
            forget = self.rng.random() < self.p.cash_forget_p
            self.move(nakit.cfg.key, -amt, "NAKIT", informed=not forget, tag="nakit")
            if not forget:
                self.api("post", "/api/entries", json={"entry_date": self.today.isoformat(), "description": "Pazar",
                                                       "amount": amt, "kind": "gider", "category": "Market",
                                                       "payment": "nakit"})
            self.log(f"nakit harcama {amt}{' (girilmedi)' if forget else ''}")
        banks = [a for a in self.accs.values() if a.cfg.kind == "banka"]
        if banks and (nakit.truth < 300 and self.rng.random() < 0.5 or self.rng.random() < self.p.atm_p):
            src = max(banks, key=lambda a: a.truth)
            amt = float(self.rng.choice([1000, 2000, 3000, 5000]))
            if src.truth < amt + 1000:
                return
            self.own_transfer(src, nakit, amt, atm=True)

    def own_transfer(self, src: Acc, dst: Acc, amt: float, atm: bool = False) -> None:
        both_elle = src.cfg.mode == "elle" and dst.cfg.mode == "elle"
        if atm:
            ms = self.move(src.cfg.key, -amt, "ATM PARA CEKME ORNEK SUBE", informed=src.cfg.mode == "elle", tag="atm")
        else:
            ms = self.move(src.cfg.key, -amt, f"GIDEN FAST - {self.p.owner_ascii} TARAFINDAN GÖNDERILEN,"
                                              f"{self.p.owner},{dst.cfg.bank},{dst.cfg.iban}",
                           informed=False, tag="aktarim")
        md = self.move(dst.cfg.key, amt, f"FAST Gönderen: {self.p.owner} Alıcı: {self.p.owner} FAST",
                       informed=False, tag="aktarim")
        # bildirim: iki hesap da elle ise (ya da biri nakit) aktarım elle/dekontla girilir
        if src.cfg.mode == "elle" or dst.cfg.mode == "elle" or atm:
            use_dekont = not atm and self.rng.random() < 0.5 and src.cfg.iban and dst.cfg.iban
            body = {"date": self.today.isoformat(), "amount": amt, "from_account_id": src.id,
                    "to_account_id": dst.id, "description": "ATM" if atm else "Aktarım"}
            if use_dekont:
                text = belgeler.dekont(self.p.owner, src.cfg.iban, self.p.owner, dst.cfg.iban, dst.cfg.bank,
                                       self.today, amt, self.ref())
                r = self.api("post", "/api/dekont/parse",
                             files={"file": ("dekont.pdf", belgeler.register(text), "application/pdf")})
                own = (r or {}).get("own")
                if not own:
                    self.fail("C_dekont_kendi", "kendi hesapları arası dekont 'own' olarak tanınmadı",
                              "own", {k: (r or {}).get(k) for k in ("direction", "counterparty", "parties")})
                body.update({"ref": (r or {}).get("ref"), "dekont_file": (r or {}).get("dekont_file"),
                             "dekont_side": (r or {}).get("doc_direction") or "giden"})
            r = self.api("post", "/api/own-transfers", json=body)
            ms.informed = md.informed = True
            if r and r.get("linked"):  # S3 düzeldi: yeni aktarımın dekontu kayıtlı başka aktarıma bağlanmamalı
                self.fail("K1_aktarim_birlesti", "yeni aktarımın dekontu kayıtlı başka bir aktarıma bağlandı",
                          {"linked": False}, {k: r.get(k) for k in ("id", "linked", "created")})
        self.log(f"aktarım {src.cfg.key}→{dst.cfg.key} {amt}{' ATM' if atm else ''} "
                 f"({'elle' if both_elle else 'karışık/döküm'})")

    def do_transfers(self) -> None:
        banks = [a for a in self.accs.values() if a.cfg.kind == "banka"]
        if len(banks) < 2 or self.rng.random() >= self.p.transfer_p:
            return
        src = max(banks, key=lambda a: a.truth)
        dst = self.rng.choice([a for a in banks if a is not src])
        amt = round(min(src.truth * 0.3, self.rng.choice([2500, 5000, 10000, 15000])), 0)
        if amt < 500:
            return
        self.own_transfer(src, dst, float(amt))

    def do_jar(self) -> None:
        if not self.jar_id:
            return
        acc, monthly, day = self.p.jar
        if self.today.day == day:
            self.api("post", f"/api/jars/{self.jar_id}/moves", json={"amount": monthly,
                                                                     "move_date": self.today.isoformat()})
            self.jar_bal = round(self.jar_bal + monthly, 2)
            self.log(f"kumbaraya {monthly}")
        elif self.jar_bal > monthly and self.rng.random() < 0.01:
            amt = round(self.jar_bal / 2, 2)
            self.api("post", f"/api/jars/{self.jar_id}/moves", json={"amount": amt, "direction": "cek",
                                                                     "move_date": self.today.isoformat()})
            self.jar_bal = round(self.jar_bal - amt, 2)
            self.log(f"kumbaradan çek {amt}")

    def create_debt(self, spec: dict) -> None:
        d = Debt(spec["key"], spec["person"], spec["direction"], spec["amount"], acc=spec.get("acc"))
        body = {"person": d.person, "direction": d.direction, "creditor_type": "kisi",
                "debt_date": self.today.isoformat()}
        if spec.get("plan"):
            n, day = spec["plan"]
            inst = round(d.amount / n, 2)
            d.amount = round(inst * n, 2)
            y, m = add_month(self.start.year, self.start.month, 0)
            for k in range(n):
                yy, mm = add_month(y, m, k)
                d.plan.append((clamp(yy, mm, day), inst))
            body.update({"plan_installment": inst, "plan_count": n, "plan_start": f"{y:04d}-{m:02d}", "plan_day": day,
                         "debt_date": self.today.isoformat()})
        else:
            d.due = self.start + timedelta(days=spec.get("due_in", 60))
            body.update({"amount": d.amount, "due_date": d.due.isoformat(), "pay_terms": "tek"})
        if spec.get("with_account"):
            body["account_id"] = self.accs[d.acc].id
            sign = 1 if d.direction == "borc" else -1
            self.move(d.acc, sign * d.amount, f"FAST {d.person}", informed=True, tag="borc_ac")
        r = self.api("post", "/api/debts", json=body)
        d.did = r and r.get("id")
        self.debts[d.key] = d
        self.log(f"borç açıldı {d.key} {d.direction} {d.amount}")

    def do_debts(self) -> None:
        for d in self.debts.values():
            left = round(d.amount - d.paid, 2)
            if left <= EPS:
                continue
            amt = None
            if d.plan:
                for on, inst in d.plan:
                    if on == self.today:
                        amt = min(inst, left)
            elif d.due and self.today == d.due - timedelta(days=self.rng.randint(0, 2)):
                amt = left
            if not amt:
                continue
            from_acc = self.rng.random() < 0.7 and d.acc
            body = {"amount": amt, "paid_at": self.today.isoformat()}
            sign = -1 if d.direction == "borc" else 1
            if from_acc:
                a = self.accs[d.acc]
                body["account_id"] = a.id
                self.move(d.acc, sign * amt, f"FAST {d.person}", informed=True, tag="borc_ode")
            # "hesabımdan değil": eşi nakit verdi vb. → izlenen hesaplarda hareket yok
            self.api("post", f"/api/debts/{d.did}/pay", json=body)
            d.paid = round(d.paid + amt, 2)
            self.log(f"borç ödemesi {d.key} {amt} {'hesaptan' if from_acc else 'hesabımdan değil'}")

    def update_balance(self, key: str) -> None:
        a = self.accs[key]
        self.api("patch", f"/api/accounts/{a.id}", json={"balance": a.truth})
        self.seq += 1
        self.anchor(key, a.truth, self.today, self.seq)
        self.log(f"bakiye güncellendi {key} = {a.truth}")

    def do_updates(self) -> None:
        for a in self.accs.values():
            if a.cfg.mode == "elle" and self.today >= a.next_update:
                self.update_balance(a.cfg.key)
                a.next_update = self.today + timedelta(days=self.rng.randint(*self.p.update_every))

    def do_dokum(self) -> None:
        for a in self.accs.values():
            if a.cfg.mode == "dokum" and self.today >= a.next_dokum:
                self.upload_dokum(a, self.today - timedelta(days=1))
                a.next_dokum = self.today + timedelta(days=self.rng.randint(*self.p.dokum_every))

    # ------------------------------------------------------------------ bağımsız beklenen değerler

    def horizon(self) -> dict:
        cd = self.p.cycle_day
        cur = period_of(self.today, cd)
        end = period_bounds(cur, cd)[1]
        periods = [cur]
        left = (end - self.today).days
        prim = max(self.p.salaries, key=lambda s: s.amount) if self.p.salaries else None
        arrived = bool(prim) and (prim.desc, cur) in self.salary_info and self.salary_info[(prim.desc, cur)] <= self.today
        if left <= 1 or (left <= 7 and arrived):
            periods.append(next_period(cur))
            end = period_bounds(periods[-1], cd)[1]
        later_end = period_bounds(next_period(periods[-1]), cd)[1]
        d_end = end
        if prim:
            nxt = None
            for k in (-1, 0, 1, 2):
                per = next_period(cur, k)
                if (prim.desc, per) in self.salary_info:
                    continue
                on = occurrence(per, prim.day, cd)
                if on < self.today:
                    if workdays.add_business_days(on, 5) < self.today:
                        continue
                    on = self.today
                nxt = on
                break
            if nxt:
                cutoff = workdays.add_business_days(nxt, 1) - timedelta(days=1)
                d_end = max(end, cutoff)
        return {"end": end, "d_end": d_end, "periods": periods, "later_end": later_end}

    def expected_unknown(self) -> float:
        tot = 0.0
        for u in self.unknown:
            if u["linked"] or (self.today - u["day"]).days > 60:
                continue
            cands = [a for a in self.accs.values()]
            card = self.cards[u["card"]]
            if card.last_pay_acc:
                cands = [self.accs[card.last_pay_acc]]
            if any(a.anchor_seq > u["seq"] for a in cands):
                continue  # çapa ödemeden sonra: bakiyenin içinde
            if not any((self.today - a.anchor_day).days <= 14 for a in cands if a.cfg.kind in ("banka", "nakit")):
                continue  # bayat
            tot += u["amount"]
        return round(tot, 2)

    def expected_parts(self, hz: dict) -> dict:
        d_end = hz["d_end"]
        out = {"card_debt": 0.0, "pending_cards": 0.0, "bills": 0.0, "recurring": 0.0, "payable": 0.0}
        later = 0.0
        by: dict[str, float] = {}
        late_ok = False
        alt = 0.0
        for card in self.cards.values():
            up = [s for s in card.stmts if s.sid is not None and s.cut <= self.today]
            if up:
                st = max(up, key=lambda s: s.cut)
                rem = round(st.total - st.app_paid, 2) if st.app_paid else st.total
                if getattr(st, "_via_dokum", False) and st.paid >= st.total and self.dokum_covers(card, st):
                    rem = 0.0
                if rem > EPS and workdays.past_due(st.due.isoformat(), self.today):
                    late_ok = True  # ödeme uygulamaya (döküm) henüz gelmedi: gecikme faizi tahmini doğru
                if rem > EPS:
                    if st.due <= d_end:
                        out["card_debt"] += rem
                    else:
                        later += rem
            # kesilecek ekstrelerin kesin kısmı: elle girilen kart harcamaları (yüklenmemiş döngüler)
            last_up = max((s.cut for s in up), default=None)
            closed = [s for s in card.stmts if s.sid is None and s.cut <= self.today]
            for b in card.buys:
                # bulgu modeli: yüklenmemiş kapanmış döngü varken kesimden sonraki elle harcama o döngüye yazılır
                if closed and b.manual and b.day > closed[-1].cut and b.day <= self.today:
                    if closed[-1].due <= d_end:
                        alt += b.amount
                    continue
                if b.manual and (last_up is None or b.day > last_up) and b.day <= self.today:
                    cut = self.next_cut_on_or_after(card.cfg, b.day)
                    if self.due_of(card.cfg, cut) <= d_end:
                        alt += b.amount
            for b in card.buys:
                if b.manual and (last_up is None or b.day > last_up) and b.day <= self.today:
                    cut = self.next_cut_on_or_after(card.cfg, b.day)
                    due = self.due_of(card.cfg, cut)
                    if due <= d_end:
                        out["pending_cards"] += b.amount
                        by[card.cfg.key] = round(by.get(card.cfg.key, 0.0) + b.amount, 2)
        for b in self.bills:
            if not b.paid and b.due <= d_end:
                out["bills"] += b.amount
        if self.p.rent:
            amt, day, acc, _ = self.p.rent
            pend = self.rent_app_pending
            prev = next_period(hz["periods"][0], -1)
            for per in [prev, *hz["periods"]]:
                known = per in self.rent_paid and per not in pend
                if known:
                    continue
                if per == prev:
                    on = occurrence(per, day, self.p.cycle_day)
                    if not (self.today - timedelta(days=15) <= on <= self.today):
                        continue
                out["recurring"] += amt
        for d in self.debts.values():
            if d.direction != "borc":
                continue
            left = round(d.amount - d.paid, 2)
            if left <= EPS:
                continue
            if d.plan:
                thru = sum(a for on, a in d.plan if on <= d_end)
                out["payable"] += min(left, max(0.0, thru - d.paid))
            elif not d.due or d.due <= d_end:
                out["payable"] += left
        return {k: round(v, 2) for k, v in out.items()} | {"_later_card": round(later, 2), "_pending_by": by,
                                                           "_late_ok": late_ok, "_pending_alt": round(alt, 2)}

    def forecast_until(self, hz: dict, income: float) -> date:
        if income <= EPS:
            return hz["end"]
        nxt = next_period(hz["periods"][-1])
        until = hz["later_end"]
        dates = [occurrence(nxt, s.day, self.p.cycle_day) for s in self.p.salaries if (s.desc, nxt) not in self.salary_info]
        if dates:
            until = min(until, min(dates) - timedelta(days=1))
        return until

    def check_forecast(self, nw: dict, hz: dict, e_inc: float, ep: dict) -> None:
        fb = nw["forecast_balance"]
        until = self.forecast_until(hz, e_inc)
        if fb.get("until") and fb["until"] != until.isoformat():
            self.fail("K7_until", "Ay sonunda kalacak günü (until)", str(until), fb.get("until"))
            return
        cutoff = max(until, hz["d_end"])
        eu = self.expected_parts({**hz, "d_end": cutoff})
        rec_next = 0.0
        if self.p.rent and until > hz["end"]:
            amt, day, _, _ = self.p.rent
            nxt = next_period(hz["periods"][-1])
            on = occurrence(nxt, day, self.p.cycle_day)
            if hz["end"] < on <= until and (nxt not in self.rent_paid or nxt in self.rent_app_pending):
                rec_next = amt
        interest = 0.0
        parts = {p["key"]: p["amount"] for p in nw["spendable_parts"]}
        if ep["_late_ok"]:
            interest = parts.get("card_late_interest", 0.0) + parts.get("card_interest", 0.0)
        e_nns = round(nw["free"] + e_inc - eu["card_debt"] - eu["pending_cards"] - eu["bills"] - eu["payable"]
                      - eu["recurring"] - rec_next - interest, 2)
        got = fb["no_new_spend"]
        over = self.funded_debt_over(hz, until)
        s4 = round(eu["_pending_alt"] - eu["pending_cards"], 2)  # S4 modeli: kesim sonrası harcama kapanmış döngüde
        models = [(o, k) for o in (0.0, over) for k in (0.0, s4) if o or k]
        hit = next(((o, k) for o, k in models if abs(got - (e_nns - o - k)) <= EPS), None)
        if abs(got - e_nns) > EPS and hit:
            name = "BULGU_funded_borc_tamami" if hit[0] else "BULGU_kesim_sonrasi_harcama"
            self.fail(name, f"Ay sonunda kalacak: bilinen hata modeli (taksitli borcun kalanı {hit[0]}, kesim sonrası "
                            f"harcama {hit[1]})", e_nns, got)
        elif abs(fb["no_new_spend"] - e_nns) > EPS:
            fp = {x["key"]: x["amount"] for x in fb["parts"]}
            self.fail("K7_ay_sonu", f"no_new_spend (until {until}; gelir {e_inc}; funded {fp.get('funded')}; "
                                    f"beklenen kalemler ≤until: {({k: v for k, v in eu.items() if not k.startswith('_')})}"
                                    f" + sonraki dönem kirası {rec_next}; uyg. funded_items {fb.get('funded_items')})",
                      e_nns, fb["no_new_spend"])

    def funded_debt_over(self, hz: dict, until: date) -> float:
        """Bulgu modeli (89629f9 _forecast_balance): spendable_later'daki borç kalemi ufuktan sonraki KALANIN tamamı,
        vadesi ilk taksit; funded bu kalemi vadesi until'e kadarsa tamamen düşüyor."""
        over = 0.0
        for d in self.debts.values():
            if d.direction != "borc" or not d.plan:
                continue
            left = round(d.amount - d.paid, 2)
            thru_h = sum(a for on, a in d.plan if on <= hz["d_end"])
            now = min(left, max(0.0, thru_h - d.paid))
            after = round(left - now, 2)
            nxt = next((on for on, _ in d.plan if on > hz["d_end"]), None)
            if after > EPS and nxt and nxt <= until:
                due_u = sum(a for on, a in d.plan if hz["d_end"] < on <= until)
                over += after - min(after, due_u)
        return round(over, 2)

    def expected_income(self, hz: dict) -> float:
        tot = 0.0
        prev = next_period(hz["periods"][0], -1)
        for s in self.p.salaries:
            for per in [prev, *hz["periods"]]:
                if (s.desc, per) in self.salary_info:
                    continue
                # günü SALARY_GRACE (5 iş günü) geçmiş maaş artık beklenmez (finance.salary_awaited; her dönem için)
                nominal = occurrence(per, s.day, self.p.cycle_day)
                if workdays.add_business_days(nominal, 5) < self.today:
                    continue
                tot += s.amount
        return round(tot, 2)

    def two_salary_bug(self, a_inc: float, e_inc: float) -> bool:
        """Bulgu modeli: iki maaşlı defterde dökümdeki maaş satırı ilk "MAAS" kalemine bağlanır ve o dönemin elle
        girilmiş öbür maaşının üstüne yazılır → öbür kalem "bekliyor" kalır (fark = bir maaş tutarı)."""
        if len(self.p.salaries) < 2 or a_inc <= e_inc + EPS:
            return False
        return any(abs(a_inc - e_inc - s.amount) <= EPS for s in self.p.salaries) or self.two_sal_seen

    def two_salary_k1(self, diff: float) -> bool:
        sal = self.p.salaries
        if len(sal) < 2:
            return False
        ds = {round(x.amount - y.amount, 2) for x in sal for y in sal if x is not y}
        return any(abs(diff - k) <= EPS for k in ds) or any(abs(abs(diff) - x.amount) <= EPS for x in sal)

    def dokum_covers(self, card: Card, st: Stmt) -> bool:
        a = self.accs[card.cfg.pay_acc]
        return st.pay_day is not None and a.dokum_until is not None and st.pay_day <= a.dokum_until

    def next_cut_on_or_after(self, cc: CardCfg, d: date) -> date:
        c = self.cut_date(cc, d.year, d.month)
        if c < d:
            y, m = add_month(d.year, d.month)
            c = self.cut_date(cc, y, m)
        return c

    # ------------------------------------------------------------------ kontroller

    def check_day(self, deep: bool) -> None:
        from app import finance, finance_audit

        d = self.today
        try:
            nw = finance.net_worth(d)
            pay = finance.payables(d)
            exp = finance.expected(finance.current_period(d), d)
            audit = finance_audit.check(d) if deep else None
        except Exception as e:  # noqa: BLE001
            self.fail("K5_istisna", f"hesap çöktü: {e!r}", tb=traceback.format_exc(limit=8))
            return
        self.stats["kontrol"] += 1
        n0 = len(self.mismatches)
        # K1 hesap bakiyeleri
        app_bal = {a["id"]: a["current"] for a in nw["accounts"]}
        exp_cash = 0.0
        truth_cash = 0.0
        k1 = False
        for a in self.accs.values():
            e = a.expected_app()
            exp_cash += e
            truth_cash += a.truth
            got = app_bal.get(a.id)
            if got is None or abs(got - e) > EPS:
                k1 = True
                if got is not None and self.two_salary_k1(got - e):
                    self.fail("BULGU_iki_maas_dokum", f"{a.cfg.key}: elle girilmiş maaşın tutarı/tarihi dökümdeki "
                              "öbür maaşla değiştirildi", e, got)
                else:
                    self.fail("K1_bakiye", f"{a.cfg.key} ({a.cfg.mode}) çapa {a.anchor_day}/{a.anchor_bal}", e, got)
        # K2 free (K1 bozuksa sonucudur: yazılmaz)
        unk = self.expected_unknown()
        e_free = round(exp_cash - unk - self.jar_bal, 2)
        if abs(nw["free"] - e_free) > EPS and not k1:
            self.fail("K2_free", f"nakit {round(exp_cash, 2)} − belirsiz {unk} − kumbara {self.jar_bal}"
                                 f" (uyg. belirsiz {nw['unknown_payments']}, kumbara {nw['reserved']})",
                      e_free, nw["free"])
        self.gaps.append(round(truth_cash - self.jar_bal - nw["free"], 2))
        # K3 spendable: ufuk + kalemler
        hz = self.horizon()
        app_hz = nw["spendable_horizon"]
        app_dend = (nw.get("spendable_pre_salary") or {}).get("until") or app_hz
        e_inc = self.expected_income(hz)
        a_inc = (nw["spendable_after_income"] or {}).get("income") or 0.0
        two_sal = self.two_salary_bug(a_inc, e_inc)
        if abs(a_inc - e_inc) > EPS:
            if two_sal:
                self.fail("BULGU_iki_maas_dokum", f"dökümdeki ikinci maaş birincinin elle kaydının üstüne yazıldı; "
                                                  f"beklenen gelir (dönemler {hz['periods']})", e_inc, a_inc)
            else:
                self.fail("K3_gelir", f"beklenen gelir (dönemler {hz['periods']})", e_inc, a_inc)
        if app_hz != hz["end"].isoformat() or app_dend != hz["d_end"].isoformat():
            self.fail("BULGU_iki_maas_dokum" if two_sal else "K3_ufuk", "ufuk / maaştan önce vadeli son gün",
                      (str(hz["end"]), str(hz["d_end"])), (app_hz, app_dend))
            if not two_sal:
                hz = {**hz, "end": date.fromisoformat(app_hz), "d_end": date.fromisoformat(app_dend)}
        if two_sal:  # sonraki kalemler uygulamanın ufkuyla karşılaştırılır (ufuk hatası tek yerde sayılsın)
            hz = {**hz, "end": date.fromisoformat(app_hz), "d_end": date.fromisoformat(app_dend)}
        parts = {p["key"]: p["amount"] for p in nw["spendable_parts"]}
        ep = self.expected_parts(hz)
        self.app_parts = parts
        self.exp_parts = ep
        partbad = False
        for k in ("card_debt", "pending_cards", "bills", "recurring", "payable"):
            if abs(parts.get(k, 0.0) - ep[k]) > EPS:
                partbad = True
                if k == "pending_cards" and abs(parts.get(k, 0.0) - ep["_pending_alt"]) <= EPS:
                    self.fail("BULGU_kesim_sonrasi_harcama", "kesimden SONRA elle girilen kart harcaması, ekstresi "
                              "yüklenmemiş KAPANMIŞ döngünün son ödemesine yazıldı", ep[k], parts.get(k))
                    continue
                extra = ""
                if k == "pending_cards":
                    extra = f" uyg: {[(x['title'][-4:], x['amount'], x['due_date']) for x in nw['pending_cards_items']]}" \
                            f" bekl: {ep['_pending_by']}"
                if k == "recurring":
                    extra = f" uyg: {nw.get('recurring_items')}"
                self.fail(f"K3_{k}", f"spendable kalemi {k} (ufuk {hz['d_end']}){extra}", ep[k], parts.get(k, 0.0))
        interest_ok = ep["_late_ok"]
        for k in ("pending_other", "card_interest", "card_late_interest", "zakat_due"):
            if parts.get(k, 0.0) > EPS and not (k in ("card_late_interest", "card_interest") and interest_ok):
                partbad = True
                self.fail(f"K3_{k}", f"beklenmeyen kalem {k}: {nw.get(k + '_items')}", 0.0, parts.get(k))
        e_sp = round(nw["free"] - ep["card_debt"] - ep["pending_cards"] - ep["bills"] - ep["recurring"] - ep["payable"]
                     - (parts.get("card_late_interest", 0.0) + parts.get("card_interest", 0.0) if interest_ok else 0.0), 2)
        if abs(nw["spendable"] - e_sp) > EPS and not partbad:
            self.fail("K3_spendable", "free − kesin ödemeler (kalemler doğru, toplam tutmuyor)", e_sp, nw["spendable"])
        # K7 Ay sonunda kalacak (fonlama ilkesi, 89629f9): no_new_spend = until günündeki bakiye − until'e kadar kesin
        # ödemeler + ufka giren beklenen gelir (tahmini harcama hariç). until bağımsız: gelir yoksa ufuk sonu; varsa
        # sonraki dönemin ilk beklenen gelirinden bir gün önce (en geç sonraki dönem sonu)
        if not partbad and abs(a_inc - e_inc) <= EPS:
            self.check_forecast(nw, hz, e_inc, ep)
        self.day_bad = len(self.mismatches) > n0
        self.trace.append({"gun": str(d), "free": nw["free"], "spendable": nw["spendable"], "hz": app_hz,
                           "dend": app_dend, "parts": {k: v for k, v in parts.items() if v},
                           "bal": {a.cfg.key: app_bal.get(a.id) for a in self.accs.values()},
                           "truth": {a.cfg.key: a.truth for a in self.accs.values()},
                           "later": nw["spendable_later"]["total"]})
        # K4 değişmezler
        self.invariants(nw, pay, exp)
        # K6 denetim
        if audit is not None:
            self.check_audit(audit, nw)

    def invariants(self, nw: dict, pay: list, exp) -> None:
        fb = nw["forecast_balance"]
        income = (nw["spendable_after_income"] or {}).get("income") or 0.0
        checks = [
            ("K4_forecast_le_nns", fb["amount"] <= fb["no_new_spend"] + 0.005, (fb["amount"], fb["no_new_spend"])),
            ("K4_nns_le_sp_income", fb["no_new_spend"] <= round(nw["spendable"] + income, 2) + 0.005,
             (fb["no_new_spend"], nw["spendable"], income)),
            ("K4_sp_le_free", nw["spendable"] <= nw["free"] + 0.005, (nw["spendable"], nw["free"])),
            ("K4_later_ge0", nw["spendable_later"]["total"] >= 0
             and all(x["amount"] > 0 for x in nw["spendable_later"]["items"]), nw["spendable_later"]["total"]),
        ]
        tot = round(sum(p["amount"] if p["sign"] == "+" else -p["amount"] for p in nw["spendable_parts"]), 2)
        checks.append(("K4_parts_sum", abs(tot - nw["spendable"]) <= EPS, (tot, nw["spendable"])))
        checks.append(("K4_parts_nonneg", all(p["amount"] >= -0.005 for p in nw["spendable_parts"] if p["sign"] == "−"),
                       [p for p in nw["spendable_parts"] if p["amount"] < 0]))
        p = {x["key"]: x["amount"] for x in fb["parts"]}
        checks.append(("K4_forecast_parts", abs(round(p["free"] - p["payments"] + p["income"] - p.get("funded", 0.0)
                                                      - p["variable"], 2) - fb["amount"]) <= EPS, (p, fb["amount"])))
        checks.append(("K4_nns_formul", abs(round(nw["spendable"] + income - p.get("funded", 0.0), 2)
                                            - fb["no_new_spend"]) <= EPS,
                       (fb["no_new_spend"], nw["spendable"], income, p.get("funded"))))
        checks.append(("K4_funded_ge0", p.get("funded", 0.0) >= -0.005 and (income > EPS or p.get("funded", 0.0) <= EPS),
                       (income, p.get("funded"))))
        for name, ok, val in checks:
            if not ok:
                self.fail(name, "değişmez bozuldu", None, val)

    # denetim kodu → simülatörün bildiği durumda o uyarının gerçek olup olmadığı
    def check_audit(self, audit: dict, nw: dict) -> None:
        counts = audit.get("counts") or {}
        for code, n in counts.items():
            self.audit_seen[code] = self.audit_seen.get(code, 0) + 1
            if code == "_hata":
                self.fail("K6_denetim_hata", f"denetim kendi içinde çöktü: {audit.get('refs', {}).get('_hata')}")
                continue
            if self.audit_legit(code, nw):
                continue
            self.fail("K6_yanlis_pozitif", f"denetim '{code}' (n={n}) gerçekte sorun yokken çıktı: "
                                           f"{(audit.get('refs') or {}).get(code)}", None, n)
        # gerçek sorun: hesapsız ödendi açıkken uyarı olmalı
        if any(not u["linked"] and (self.today - u["day"]).days <= 60 and self.unknown_pending(u) for u in self.unknown) \
                and "odendi_hesapsiz" not in counts:
            self.fail("K6_kacirilan", "hesapsız Ödendi ve sonrasında çapa yok, denetim uyarmadı")

    def unknown_pending(self, u: dict) -> bool:
        return not any(a.anchor_seq > u["seq"] for a in self.accs.values()
                       if a.cfg.key == u["acc"] or self.cards[u["card"]].last_pay_acc is None)

    def audit_legit(self, code: str, nw: dict) -> bool:
        if code == "capa_eski":
            return any(a.cfg.kind == "banka" and (self.today - a.anchor_day).days >= 14 for a in self.accs.values())
        if code == "eksi_bakiye":
            return any(a.expected_app() < 0 for a in self.accs.values())
        if code == "odendi_hesapsiz":
            return any(not u["linked"] for u in self.unknown)
        if code == "duzenli_bekliyor":
            # uygulama ödenmiş düzenli ödemeyi bekliyor gösteriyorsa (K3_recurring hatası) denetim DOĞRU yakalıyor
            return getattr(self, "app_parts", {}).get("recurring", 0.0) > self.exp_parts.get("recurring", 0.0) + EPS
        if code == "virman_bekliyor":
            # tek bacaklı aktarım: karşı hesabın dökümü henüz gelmediyse soru yerinde
            return any(a.cfg.mode == "dokum" and any(m.tag == "aktarim" and m.day > a.dokum_until for m in a.moves)
                       for a in self.accs.values())
        if code == "hesapsiz_hareket":
            # faturanın Ödendi'si hesap sormadan havale gideri yazar: gerçek bir hesapsız hareket
            return any(b.paid and not b.via.startswith("kart:") for b in self.bills)
        return False

    # ------------------------------------------------------------------ ana döngü

    def run(self) -> "Sim":
        self.setup()
        self.t0 = time.perf_counter()
        d = self.start
        n = 0
        while d <= self.end:
            self.today = d
            self.clock.set_day(d)
            saat.freeze(self.mp, d)
            try:
                if d == self.start:
                    for b in self.p.debts:
                        self.create_debt(b)
                self.do_dokum()
                self.do_salary()
                self.do_rent()
                self.do_bills()
                self.do_cards()
                self.do_debts()
                self.do_cash()
                self.do_transfers()
                self.do_jar()
                self.do_updates()
            except Exception as e:  # noqa: BLE001
                self.fail("K5_istisna", f"adım çöktü: {e!r}", tb=traceback.format_exc(limit=8))
            busy = d.day in (1, 14, 15, 16, 17, 20, 23, 24, 25, 28) or d.day >= 28
            deep = busy or n % self.deep_every == 0
            self.stats["derin_gun"] += deep
            self.check_day(deep)
            self.stats["gun"] += 1
            n += 1
            d += timedelta(days=1)
        self.stats["sure_sn"] = round(time.perf_counter() - self.t0, 1)
        return self

    # ------------------------------------------------------------------ rapor

    def report(self) -> dict:
        by: dict[str, list] = {}
        for m in self.mismatches:
            by.setdefault(m.check, []).append(m)
        out = {"persona": self.p.key, "baslik": self.p.title, **self.stats,
               "uyusmazlik": len(self.mismatches),
               "sinif": {k: {"n": len(v), "ilk_gun": v[0].day, "ilk": v[0].detail[:400],
                             "beklenen": repr(v[0].expected)[:200], "gercek": repr(v[0].actual)[:200],
                             "adimlar": v[0].steps}
                         for k, v in sorted(by.items())},
               "free_gercek_farki": {"max": max(self.gaps, default=0), "min": min(self.gaps, default=0)},
               "denetim_kodlari": self.audit_seen, "iz": self.trace}
        return out
