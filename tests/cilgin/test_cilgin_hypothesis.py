"""Çılgın testçiler — hypothesis durumlu makinesi (kurallar test_cilgin_durumlu.Kurallar). hypothesis yoksa atlanır
(requirements-dev.txt). Varsayılan koşu derandomize; gece işi `--hypothesis-seed=$HK_SEED` verir."""
from __future__ import annotations

import pytest

from tests.cilgin import ortak
from tests.cilgin.test_cilgin_durumlu import EDGE_AMOUNTS, Kurallar

hyp = pytest.importorskip("hypothesis", reason="hypothesis kurulu değil (requirements-dev.txt)")
from hypothesis import HealthCheck, settings, strategies as st  # noqa: E402
from hypothesis.stateful import RuleBasedStateMachine, initialize, rule  # noqa: E402

_amt = st.one_of(st.sampled_from(EDGE_AMOUNTS), st.floats(min_value=0.01, max_value=50000, allow_nan=False).map(
    lambda x: round(x, 2)))


class Makine(RuleBasedStateMachine, Kurallar):
    tmp_dir = None

    @initialize(cycle=st.sampled_from([1, 15, 17, 28]))
    def baslat(self, cycle):
        self.cycle = cycle
        self.kur(Makine.tmp_dir)

    def teardown(self):
        if getattr(self, "d", None):
            self.kapat()

    @rule(n=st.sampled_from([1, 2, 3, 7, 13, 29, 31]))
    def r_gun(self, n):
        self.gun_ilerle(n)

    @rule(kind=st.sampled_from(["gider", "gelir"]), amount=_amt, acc_k=st.sampled_from([0, 1, 2, None]),
          offset=st.sampled_from([0, -1, -3, -40, -400, 1, 5, 30]), payment=st.sampled_from(["havale", "nakit", "kredi_karti"]))
    def r_kayit(self, kind, amount, acc_k, offset, payment):
        self.kayit(kind, amount, acc_k, offset, payment)

    @rule(amount=_amt, acc_k=st.integers(0, 2), offset=st.sampled_from([0, -1, -10, 3]))
    def r_kayit_geri_al(self, amount, acc_k, offset):
        self.kayit_geri_al(amount, acc_k, offset)

    @rule(k=st.integers(0, 50))
    def r_kotu(self, k):
        self.kotu_girdi(k)

    @rule(amount=_amt, a=st.integers(0, 2), b=st.integers(0, 2), ref=st.booleans(), times=st.integers(1, 3))
    def r_aktarim(self, amount, a, b, ref, times):
        self.aktarim(amount, a, b, ref, times)

    @rule(acc_k=st.integers(0, 2), value=st.sampled_from([0.0, -500.0, 12345.67, 88000.0]))
    def r_bakiye(self, acc_k, value):
        self.bakiye(acc_k, value)

    @rule(times=st.integers(1, 3), total=st.floats(100, 40000).map(lambda x: round(x, 2)), back=st.integers(0, 25))
    def r_ekstre(self, times, total, back):
        self.ekstre(times, total, back)

    @rule(k=st.integers(0, 9), acc_k=st.sampled_from([None, -1, 0, 1]), undo=st.booleans())
    def r_ode(self, k, acc_k, undo):
        self.ode(k, acc_k, undo)

    @rule(n=st.integers(1, 8), reverse=st.booleans(), times=st.integers(1, 3), undo=st.booleans(),
          seed=st.integers(0, 10 ** 6))
    def r_dokum(self, n, reverse, times, undo, seed):
        self.dokum(n, reverse, times, undo, seed)

    @rule(day=st.integers(1, 28))
    def r_ay(self, day):
        self.ay_donumu(day)

    @rule(amount=st.floats(100, 20000).map(lambda x: round(x, 2)), direction=st.sampled_from(["borc", "alacak"]),
          acc_k=st.sampled_from([None, 0, 1]), frac=st.sampled_from([0.25, 0.5, 1.0]), over=st.booleans())
    def r_borc(self, amount, direction, acc_k, frac, over):
        self.borc(amount, direction, acc_k, frac, over)

    @rule(amount=_amt, cek=st.booleans())
    def r_kumbara(self, amount, cek):
        self.kumbara(amount, cek)

    @rule(amount=_amt, offset=st.sampled_from([-20, -1, 0, 3, 40]), pay=st.booleans(), undo=st.booleans())
    def r_fatura(self, amount, offset, pay, undo):
        self.fatura(amount, offset, pay, undo)


def test_cilgin_hypothesis(tmp_path, client, request):
    from hypothesis.stateful import run_state_machine_as_test

    Makine.tmp_dir = tmp_path
    seeded = request.config.getoption("hypothesis_seed", default=None) is not None
    run_state_machine_as_test(Makine, settings=settings(
        max_examples=int(ortak.steps(0) or 12), stateful_step_count=25, deadline=None, database=None,
        derandomize=not seeded, suppress_health_check=list(HealthCheck)))
