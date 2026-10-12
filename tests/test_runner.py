import json
from typing import Any

import pytest

from crashproof.clock import SchedulingError
from crashproof.config import (
    FaultProbabilities,
    RunLimits,
    ScenarioConfig,
    VariantWeights,
    load_scenario_config,
)
from crashproof.ids import IdGen
from crashproof.sim import runner
from crashproof.sim.runner import RunResult, RuntimeKind, evaluate_seed, run_seed
from crashproof.sim.scenario import make_scenario
from crashproof.world.faults import SimulatedCrash

DEFAULT = load_scenario_config()


def config(
    f1: float = 0.0,
    f2: float = 0.0,
    f3: float = 0.0,
    f5: float = 0.0,
    max_restarts: int = 5,
    max_events: int = 10000,
) -> ScenarioConfig:
    return DEFAULT.model_copy(
        update={
            "faults": FaultProbabilities(F1=f1, F2=f2, F3=f3, F5=f5),
            "run": RunLimits(max_restarts=max_restarts, max_events=max_events),
        }
    )


def quiet(config: ScenarioConfig) -> ScenarioConfig:
    cut2 = config.cut2.model_copy(
        update={
            "variant_weights": VariantWeights(
                none=1,
                outside_window=0,
                high_value_over_cap=0,
                fraud_flagged=0,
                partial_return=0,
            ),
            "early_refund_percent": 0,
        }
    )
    return config.model_copy(update={"cut2": cut2})


def seed_with(behavior: str) -> int:
    for seed in range(1, 201):
        if make_scenario(seed, DEFAULT, IdGen(seed)).behavior == behavior:
            return seed
    raise AssertionError(f"no seed with behavior {behavior}")


def events(result: RunResult) -> list[dict[str, Any]]:
    return [json.loads(line) for line in result.trace_bytes.decode().splitlines()]


def names(result: RunResult) -> list[str]:
    return [e["event"] for e in events(result)]


def count(result: RunResult, event: str) -> int:
    return names(result).count(event)


@pytest.mark.parametrize("kind", ["naive", "hardened"])
def test_two_runs_give_identical_bytes(kind: RuntimeKind) -> None:
    for seed in (1, 2, 3, 4, 5):
        a, b = run_seed(seed, kind, DEFAULT), run_seed(seed, kind, DEFAULT)
        assert a.trace_bytes == b.trace_bytes
        assert a.trace_hash == b.trace_hash


def test_trace_hash_is_hash_of_the_bytes() -> None:
    import hashlib

    result = run_seed(1, "hardened", DEFAULT)
    assert result.trace_hash == hashlib.sha256(result.trace_bytes).hexdigest()


def test_evaluate_seed_matches_run_seed_when_deterministic() -> None:
    assert evaluate_seed(3, "naive", DEFAULT) == run_seed(3, "naive", DEFAULT)


def test_evaluate_seed_adds_i9_when_runs_differ(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    real = runner.run_seed
    calls: list[int] = []

    def flaky(seed: int, kind: RuntimeKind, cfg: ScenarioConfig) -> RunResult:
        calls.append(1)
        result = real(seed, kind, cfg)
        if len(calls) == 2:
            return result.model_copy(update={"trace_hash": "different"})
        return result

    monkeypatch.setattr(runner, "run_seed", flaky)
    result = evaluate_seed(seed_with("normal"), "naive", config())
    assert [v.invariant for v in result.violations] == ["I9"]


def test_kinds_share_workflow_scenario_and_fault_events() -> None:
    cfg = config(f1=0.3, f2=0.3, f3=0.3)
    for seed in (1, 2, 3):
        naive, hardened = run_seed(seed, "naive", cfg), run_seed(seed, "hardened", cfg)
        assert events(naive)[0] == events(hardened)[0]
        assert events(naive)[0]["event"] == "scenario.created"
        assert events(naive)[0]["workflow_id"] == "wf-000001"
        first_faults = [e for e in events(naive) if e["event"].startswith("fault.F")]
        assert first_faults, "expected some faults at these probabilities"


def test_fault_free_runs_have_no_fault_events() -> None:
    for kind in ("naive", "hardened"):
        result = run_seed(seed_with("normal"), kind, config())  # type: ignore[arg-type]
        assert not [n for n in names(result) if n.startswith("fault.")]
        assert result.violations == []
        assert result.outcome == "closed"
        assert result.restarts == 0


def test_f5_always_with_one_restart_naive_two_labels_hardened_one() -> None:
    cfg = config(f5=1.0, max_restarts=1)
    seed = seed_with("normal")
    naive = run_seed(seed, "naive", cfg)
    hardened = run_seed(seed, "hardened", cfg)
    assert [v.invariant for v in naive.violations] == ["I5", "I7"]
    assert not [v for v in hardened.violations if v.invariant == "I5"]
    assert count(naive, "label.created_record") == 2
    assert count(hardened, "label.created_record") == 1


def test_naive_crash_loop_counts_every_crash() -> None:
    result = run_seed(seed_with("normal"), "naive", config(f5=1.0, max_restarts=5))
    assert result.outcome == "crash_loop"
    assert result.restarts == 6
    assert count(result, "runner.restart") == 5
    assert count(result, "runner.crash_loop") == 1


def test_hardened_crash_loop_has_no_i8_violation_after_end_reconcile() -> None:
    for seed in range(1, 30):
        result = run_seed(seed, "hardened", quiet(config(f5=1.0, max_restarts=3)))
        assert result.outcome in {
            "crash_loop",
            "closed",
            "open",
            "gave_up",
            "needs_review",
            "scan_timeout",
        }
        assert not [v for v in result.violations if v.invariant == "I8"]


def test_duplicate_request_naive_two_refunds_hardened_one() -> None:
    seed = seed_with("duplicate_request")
    naive = run_seed(seed, "naive", config())
    hardened = run_seed(seed, "hardened", config())
    assert count(naive, "refund.issued") == 2
    assert count(naive, "label.created_record") == 2
    assert {v.invariant for v in naive.violations} == {"I1", "I2", "I5"}
    assert count(hardened, "refund.issued") == 1
    assert count(hardened, "label.created_record") == 1
    assert hardened.violations == []


@pytest.mark.parametrize("kind", ["naive", "hardened"])
def test_never_ships_back_has_no_refund_and_stays_open(kind: RuntimeKind) -> None:
    result = run_seed(seed_with("never_ships_back"), kind, config())
    assert count(result, "refund.issued") == 0
    assert count(result, "customer.ships") == 0
    assert result.outcome == ("scan_timeout" if kind == "hardened" else "open")
    if kind == "hardened":
        assert result.violations == []
    else:
        assert [v.invariant for v in result.violations] == ["I7"]


def test_customer_ships_once_even_with_duplicate_label_events() -> None:
    result = run_seed(seed_with("normal"), "hardened", config(f3=1.0))
    assert count(result, "customer.ships") == 1


def test_naive_runtime_closed_before_a_later_crash_is_not_rebuilt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    real = runner._build_harness

    def crash_late(seed: int, cfg: ScenarioConfig) -> Any:
        h = real(seed, cfg)

        def die() -> None:
            raise SimulatedCrash("refund_applied")

        h.scheduler.schedule_at(1_000_000, die)
        return h

    monkeypatch.setattr(runner, "_build_harness", crash_late)
    result = run_seed(seed_with("normal"), "naive", config())
    assert result.restarts == 1
    assert result.outcome == "closed"
    assert count(result, "refund.issued") == 1
    assert count(result, "label.created_record") == 1
    assert result.violations == []


def test_event_cap_propagates_as_scheduling_error() -> None:
    with pytest.raises(SchedulingError):
        run_seed(1, "naive", config(max_events=1))


def test_trace_values_have_no_floats() -> None:
    for e in events(run_seed(1, "hardened", DEFAULT)):
        for value in e["data"].values():
            assert not isinstance(value, float)


@pytest.mark.parametrize("kind", ["naive", "keyed", "hardened"])
def test_runner_passes_every_invariant_input_to_check_world(
    monkeypatch: pytest.MonkeyPatch, kind: RuntimeKind
) -> None:
    seen: dict[str, Any] = {}
    real = runner.check_world

    def spy(*args: Any, **kwargs: Any) -> Any:
        seen.update(kwargs)
        seen["effects"] = args[3]
        return real(*args, **kwargs)

    monkeypatch.setattr(runner, "check_world", spy)
    run_seed(1, kind, DEFAULT)
    for name in (
        "trace_events",
        "oracle_eligible",
        "approvals",
        "refund_cap",
        "workflow_terminal",
    ):
        assert seen[name] is not None
    applicable = kind == "hardened"
    assert (seen["effects"] is not None) == applicable
    assert (seen["decisions_by_workflow"] is not None) == applicable
