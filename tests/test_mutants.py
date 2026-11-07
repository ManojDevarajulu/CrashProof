"""Mutation tests: break the hardened runtime and require the checkers to notice.

Each mutant patches one behaviour for one test. src/ is never edited. A mutant is
killed when the invariants it targets report violations on the fixed seed range.
A control run with no patch must report none, otherwise a kill proves nothing.
"""

from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass

import pytest

from crashproof.config import load_scenario_config
from crashproof.models import Json
from crashproof.runtime import reconcile as reconcile_module
from crashproof.runtime.executor import Executor
from crashproof.runtime.ledger import EffectRow, Ledger, LedgerError
from crashproof.runtime.policy import PolicyEngine, VerdictKind
from crashproof.runtime.workflow import HardenedRuntime
from crashproof.sim import runner
from crashproof.sim.runner import run_seed
from crashproof.world.refunds import RefundRecord, RefundService

SEEDS = range(1, 301)
CONFIG = load_scenario_config()


@dataclass(frozen=True)
class Mutant:
    id: str
    description: str
    apply: Callable[[pytest.MonkeyPatch], None]
    must_fire: frozenset[str]
    # Some bugs are stopped by a guard inside the runtime before any invariant can
    # fire; they are killed by the guard's exception, not by a checker.
    must_raise: type[Exception] | None = None


def _violation_counts() -> Counter[str]:
    """Seeds violating each invariant when the hardened runtime runs SEEDS."""
    counts: Counter[str] = Counter()
    for seed in SEEDS:
        result = run_seed(seed, "hardened", CONFIG)
        counts.update({v.invariant for v in result.violations})
    return counts


def _refund_without_key(mp: pytest.MonkeyPatch) -> None:
    original = RefundService.issue_refund

    def issue_refund(
        self: RefundService,
        order_id: str,
        amount: int,
        reference: str,
        *,
        idempotency_key: str | None = None,
    ) -> RefundRecord:
        return original(self, order_id, amount, reference)

    mp.setattr(RefundService, "issue_refund", issue_refund)


def _attempt_dependent_key(mp: pytest.MonkeyPatch) -> None:
    """The ledger keeps a stable key; the provider gets a new one on every attempt."""
    original = Executor._provider_call
    attempts: Counter[str] = Counter()

    def provider_call(
        self: Executor, row: EffectRow, order_id: str, amount: int | None
    ) -> dict[str, Json]:
        attempts[row.idempotency_key] += 1
        suffix = f"|attempt{attempts[row.idempotency_key]}"
        shifted = row.model_copy(
            update={"idempotency_key": row.idempotency_key + suffix}
        )
        return original(self, shifted, order_id, amount)

    mp.setattr(Executor, "_provider_call", provider_call)


def _always_allow(rule: str) -> Callable[[pytest.MonkeyPatch], None]:
    def apply(mp: pytest.MonkeyPatch) -> None:
        def allow(self: PolicyEngine, *args: Json) -> tuple[VerdictKind, str]:
            return "allow", f"mutant: {rule} always allows"

        mp.setattr(PolicyEngine, f"_{rule.lower()}", allow)

    return apply


def _no_scan_timeout(mp: pytest.MonkeyPatch) -> None:
    def never(self: HardenedRuntime) -> None:
        return None

    mp.setattr(HardenedRuntime, "_schedule_scan_timeout", never)


def _reconcile_finds_nothing(mp: pytest.MonkeyPatch) -> None:
    mp.setattr(reconcile_module, "_lookup", lambda *args: None)


def _no_recovery_after_restart(mp: pytest.MonkeyPatch) -> None:
    mp.setattr(Ledger, "recover", lambda self: [])


def _window_one_day_too_long(mp: pytest.MonkeyPatch) -> None:
    def p4(self: PolicyEngine) -> tuple[VerdictKind, str]:
        order = self._store.get_order(self._request.order_id)
        if order.delivered_at is None:
            return "deny", "order not delivered"
        age = self._request.requested_at - order.delivered_at
        if age <= (self._cfg.window_days + 1) * 86400:
            return "allow", "mutant: window one day too long"
        return "deny", "outside window"

    mp.setattr(PolicyEngine, "_p4", p4)


def _skip_reconcile(mp: pytest.MonkeyPatch) -> None:
    mp.setattr(runner, "reconcile_workflow", lambda *args: 0)


MUTANTS = [
    Mutant(
        "M1",
        "refund idempotency key not sent to the refund service",
        _refund_without_key,
        frozenset({"I1", "I2", "I8"}),
    ),
    Mutant(
        "M2",
        "key sent to the provider changes on every attempt",
        _attempt_dependent_key,
        frozenset({"I1", "I2", "I5", "I8"}),
    ),
    Mutant(
        "M3",
        "P2 never escalates an over-cap refund",
        _always_allow("P2"),
        frozenset({"I4"}),
    ),
    Mutant(
        "M4",
        "P6 allows fraud-flagged orders",
        _always_allow("P6"),
        frozenset({"I3"}),
    ),
    Mutant(
        "M5",
        "no scan timeout",
        _no_scan_timeout,
        frozenset({"I7"}),
    ),
    Mutant(
        "M7",
        "reconcile lookup always reports the effect as not applied",
        _reconcile_finds_nothing,
        # Refunds are protected by the provider-side key, so only a label on the
        # keyless carrier B is duplicated.
        frozenset({"I5"}),
    ),
    Mutant(
        "M8",
        "EXECUTING effects are not treated as UNKNOWN after a restart",
        _no_recovery_after_restart,
        frozenset(),
        LedgerError,
    ),
    Mutant(
        "M9",
        "P3 allows a refund before the return scan",
        _always_allow("P3"),
        frozenset({"I3"}),
    ),
    Mutant(
        "M10",
        "P4 window one day too long",
        _window_one_day_too_long,
        frozenset({"I3"}),
    ),
]

# Mutants that are allowed to survive, each with the reason. A survivor not listed
# here fails the build. A listed mutant that is killed also fails, so the entry
# is removed once it stops being true.
SURVIVORS = {
    "M11": (
        "P5 allows a second label: the ledger key already prevents a second label, "
        "so P5 is never the deciding check",
        _always_allow("P5"),
    ),
    "M12": (
        "P1 allows any amount: the agent never over-asks on a normal return",
        _always_allow("P1"),
    ),
    "M6": (
        "end-of-run reconcile: never changes an outcome at the frozen fault "
        "probabilities (DECISIONS.md, pre-demo sabotage S6)",
        _skip_reconcile,
    ),
}


def test_control_has_no_violations() -> None:
    assert _violation_counts() == Counter()


@pytest.mark.parametrize("mutant", MUTANTS, ids=lambda m: m.id)
def test_mutant_is_killed(mutant: Mutant, monkeypatch: pytest.MonkeyPatch) -> None:
    mutant.apply(monkeypatch)
    if mutant.must_raise is not None:
        with pytest.raises(mutant.must_raise):
            _violation_counts()
        name = mutant.must_raise.__name__
        print(f"\n{mutant.id} {mutant.description}: killed by {name}")
        return
    counts = _violation_counts()
    print(f"\n{mutant.id} {mutant.description}: {dict(sorted(counts.items()))}")
    missing = mutant.must_fire - counts.keys()
    assert not missing, f"{mutant.id} survived; {sorted(missing)} never fired"


@pytest.mark.parametrize("mutant_id", sorted(SURVIVORS))
def test_documented_survivor_still_survives(
    mutant_id: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    reason, apply = SURVIVORS[mutant_id]
    apply(monkeypatch)
    counts = _violation_counts()
    print(f"\n{mutant_id} survives ({reason}): {dict(sorted(counts.items()))}")
    assert counts == Counter(), f"{mutant_id} is now killed; remove it from SURVIVORS"
