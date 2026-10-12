import sqlite3
from collections.abc import Callable

import pytest

from crashproof.config import load_scenario_config
from crashproof.ids import IdGen
from crashproof.sim import runner
from crashproof.sim.runner import QUIESCENCE_SECONDS, RunResult, RuntimeKind, run_seed
from crashproof.sim.scenario import Scenario, make_scenario
from crashproof.sim.trace import trace_hash

DEFAULT = load_scenario_config()
KINDS: tuple[RuntimeKind, ...] = ("naive", "keyed", "hardened")

# trace_hash values printed from the run BEFORE the C2-4 change (HEAD 65f089b).
BEFORE: list[tuple[int, RuntimeKind, str]] = [
    (1, "naive", "4a84a44c277b79fa0586272467cc065d0966e86285b75ad61c7595910f9b8f16"),
    (1, "keyed", "ebd3bb8a95b7d0e8cdad82016231a9c81f0c866611de24d8a658b4bb1ad471bc"),
    (1, "hardened", "32571347ac4fa35e59a9960eaeb97e3f05ce59bbf7184fe1f1eb4bdbe012595a"),
    (2, "naive", "0cb919851da2a41ff9162d29d38207ddf319eab52e73c83fa5a18bf3a26780d7"),
    (2, "keyed", "678913fe669a372f17fb1dfe1ee69860dc430e8a3149deb5d65e90f84c0b00d1"),
    (2, "hardened", "bc66afb53cfe5085cd984d5208478fd77413232f91ed8c493909fb09134a4166"),
    (3, "naive", "34a18da2a1a76968c095450e56b0b141a5822801c229afd71cc3e8e297461bac"),
    (3, "keyed", "68519efd1451af50aa6e2cc6b77ba7b324a9086b641cb9fab983de0d6b84f292"),
    (3, "hardened", "b4309b94e1d255796b161fd76e6f7cd0c4318a89fe5804fbcb3f2c0f8d1cff9d"),
]


def seeds_where(predicate: Callable[[Scenario], bool]) -> list[int]:
    return [
        seed
        for seed in range(1, 201)
        if predicate(make_scenario(seed, DEFAULT, IdGen(seed)))
    ]


def invariants(result: RunResult) -> set[str]:
    return {v.invariant for v in result.violations}


def test_never_ships_back_is_terminal_in_hardened_and_stuck_elsewhere() -> None:
    seeds = seeds_where(lambda s: s.behavior == "never_ships_back")
    assert seeds
    seed = seeds[0]
    hardened = run_seed(seed, "hardened", DEFAULT)
    assert hardened.outcome == "scan_timeout"
    assert "I7" not in invariants(hardened)
    for kind in ("naive", "keyed"):
        result = run_seed(seed, kind, DEFAULT)
        assert result.outcome == "open"
        assert "I7" in invariants(result)


def test_every_never_ships_back_seed_follows_the_same_rule() -> None:
    for seed in seeds_where(lambda s: s.behavior == "never_ships_back")[:10]:
        assert "I7" not in invariants(run_seed(seed, "hardened", DEFAULT))
        assert "I7" in invariants(run_seed(seed, "naive", DEFAULT))


@pytest.mark.parametrize("kind", KINDS)
def test_the_noop_is_scheduled_exactly_72_hours_after_the_drain(
    kind: RuntimeKind,
) -> None:
    assert QUIESCENCE_SECONDS == 72 * 3600
    h = runner._build_harness(1, DEFAULT)
    runner._add_policy(h, 1, runner.load_policy_config())
    if kind == "hardened":
        runner._add_ledger(h, sqlite3.connect(":memory:"))
    runner._schedule_starts(kind, h, DEFAULT)
    crashes, loop = runner._drain(kind, h, DEFAULT)
    assert h.scheduler.pending == 0
    drained_at, before = h.clock.now, trace_hash(h.trace.events)
    scheduled: list[int] = []
    real = h.scheduler.schedule_at

    def spy(vt: int, action: Callable[[], None]) -> int:
        scheduled.append(vt)
        return real(vt, action)

    h.scheduler.schedule_at = spy  # type: ignore[method-assign]
    runner._quiesce(kind, h, DEFAULT, crashes, loop)
    assert scheduled == [drained_at + 72 * 3600]
    assert h.clock.now == drained_at + 72 * 3600
    assert trace_hash(h.trace.events) == before


@pytest.mark.parametrize(("seed", "kind", "expected"), BEFORE)
def test_the_quiescence_step_does_not_change_traces(
    seed: int, kind: RuntimeKind, expected: str
) -> None:
    assert run_seed(seed, kind, DEFAULT).trace_hash == expected


def test_outside_window_and_fraud_seeds_have_no_hardened_i3() -> None:
    for variant in ("outside_window", "fraud_flagged"):
        seeds = seeds_where(lambda s, v=variant: s.variant == v)  # type: ignore[misc]
        assert seeds
        for seed in seeds[:8]:
            result = run_seed(seed, "hardened", DEFAULT)
            assert "I3" not in invariants(result)
            assert result.outcome.startswith("denied_")


def test_naive_refunds_ineligible_orders_and_fails_i3() -> None:
    seeds = seeds_where(lambda s: s.variant == "outside_window")
    assert any("I3" in invariants(run_seed(s, "naive", DEFAULT)) for s in seeds)


def test_approved_high_value_seed_has_no_hardened_i4() -> None:
    seeds = seeds_where(lambda s: s.variant == "high_value_over_cap")
    closed = [s for s in seeds if run_seed(s, "hardened", DEFAULT).outcome == "closed"]
    assert closed
    for seed in closed:
        result = run_seed(seed, "hardened", DEFAULT)
        assert "I4" not in invariants(result)
        assert b'"approval.decided"' in result.trace_bytes


def test_naive_high_value_refund_fails_i4() -> None:
    seeds = seeds_where(lambda s: s.variant == "high_value_over_cap")
    assert any("I4" in invariants(run_seed(s, "naive", DEFAULT)) for s in seeds)


def test_early_refund_fails_i3_in_naive_but_not_in_hardened() -> None:
    seeds = seeds_where(lambda s: s.early_refund and s.variant == "none")
    assert seeds
    naive_failed = [
        s for s in seeds if "I3" in invariants(run_seed(s, "naive", DEFAULT))
    ]
    assert naive_failed
    for seed in seeds:
        assert "I3" not in invariants(run_seed(seed, "hardened", DEFAULT))
