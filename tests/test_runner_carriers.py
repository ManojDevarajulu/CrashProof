import json
from typing import Any

import pytest

from crashproof.config import (
    CarrierWeights,
    FaultProbabilities,
    ScenarioConfig,
    load_scenario_config,
)
from crashproof.ids import IdGen
from crashproof.sim import runner
from crashproof.sim.runner import RunResult, RuntimeKind, run_seed
from crashproof.sim.scenario import make_scenario
from crashproof.world.carriers import CarrierA, CarrierB, CarrierC
from crashproof.world.faults import FaultInjector

DEFAULT = load_scenario_config()
KINDS: tuple[RuntimeKind, ...] = ("naive", "hardened")


def quiet(**faults: float) -> ScenarioConfig:
    probs = {"F1": 0.0, "F2": 0.0, "F3": 0.0, "F5": 0.0} | faults
    return DEFAULT.model_copy(update={"faults": FaultProbabilities(**probs)})


def seed_with(carrier: str, behavior: str = "normal") -> int:
    for seed in range(1, 401):
        s = make_scenario(seed, DEFAULT, IdGen(seed))
        if s.carrier == carrier and s.behavior == behavior:
            return seed
    raise AssertionError(f"no seed with carrier {carrier} and behavior {behavior}")


def events(result: RunResult) -> list[dict[str, Any]]:
    return [json.loads(line) for line in result.trace_bytes.decode().splitlines()]


def count(result: RunResult, event: str) -> int:
    return [e["event"] for e in events(result)].count(event)


def force_first_label_attempt(monkeypatch: pytest.MonkeyPatch, fault: str) -> None:
    original = FaultInjector._fires

    def fires(
        self: FaultInjector, name: str, p: float, ref: str, kind: str, n: int
    ) -> bool:
        if name == fault and kind == "create_label":
            return n == 1
        return original(self, name, p, ref, kind, n)

    monkeypatch.setattr(FaultInjector, "_fires", fires)


@pytest.mark.parametrize(
    ("carrier", "cls"), [("A", CarrierA), ("B", CarrierB), ("C", CarrierC)]
)
def test_harness_builds_the_right_class(carrier: str, cls: type) -> None:
    seed = seed_with(carrier)
    harness = runner._build_harness(seed, DEFAULT)
    assert type(harness.carrier) is cls
    assert harness.scenario.carrier == carrier


def test_carrier_a_trace_has_no_scenario_carrier_event() -> None:
    for seed in range(1, 41):
        if make_scenario(seed, DEFAULT, IdGen(seed)).carrier != "A":
            continue
        for kind in KINDS:
            assert count(run_seed(seed, kind, DEFAULT), "scenario.carrier") == 0


@pytest.mark.parametrize("carrier", ["B", "C"])
def test_other_carriers_record_the_carrier_after_scenario_created(carrier: str) -> None:
    result = run_seed(seed_with(carrier), "hardened", DEFAULT)
    trace = events(result)
    assert trace[0]["event"] == "scenario.created"
    assert trace[1]["event"] == "scenario.carrier"
    assert trace[1]["actor"] == "world"
    assert trace[1]["data"] == {"carrier": carrier}


def test_b_hardened_f2_on_the_label_ends_with_one_label(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    force_first_label_attempt(monkeypatch, "F2")
    result = run_seed(seed_with("B"), "hardened", quiet())
    assert count(result, "fault.F2") == 1
    assert count(result, "label.created_record") == 1
    assert result.violations == []


def test_b_hardened_f1_on_the_label_retries_and_ends_with_one_label(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    force_first_label_attempt(monkeypatch, "F1")
    result = run_seed(seed_with("B"), "hardened", quiet())
    assert count(result, "fault.F1") == 1
    assert count(result, "label.created_record") == 1
    assert result.violations == []


def test_b_duplicate_request_naive_two_labels_hardened_one() -> None:
    seed = seed_with("B", "duplicate_request")
    naive = run_seed(seed, "naive", quiet())
    hardened = run_seed(seed, "hardened", quiet())
    assert count(naive, "label.created_record") == 2
    assert count(hardened, "label.created_record") == 1


def test_c_customer_publishes_two_delayed_scans_and_one_refund() -> None:
    seed = seed_with("C")
    scenario = make_scenario(seed, DEFAULT, IdGen(seed))
    expected = scenario.scan_delay + DEFAULT.carriers.c_scan_extra_delay
    for kind in KINDS:
        result = run_seed(seed, kind, quiet())
        scans = [
            e
            for e in events(result)
            if e["event"] == "event.published" and e["data"]["type"] == "return.scanned"
        ]
        assert len(scans) == 2
        assert scans[0]["data"]["event_id"] != scans[1]["data"]["event_id"]
        for scan in scans:
            assert scan["data"]["deliver_vt"] - scan["vt"] == expected
        assert count(result, "refund.issued") == 1
        assert result.violations == []


@pytest.mark.parametrize("carrier", ["B", "C"])
@pytest.mark.parametrize("kind", KINDS)
def test_two_runs_give_identical_trace_hash(carrier: str, kind: RuntimeKind) -> None:
    seed = seed_with(carrier)
    assert (
        run_seed(seed, kind, DEFAULT).trace_hash
        == run_seed(seed, kind, DEFAULT).trace_hash
    )


def test_zero_weights_keep_every_seed_on_one_carrier() -> None:
    only_b = DEFAULT.model_copy(
        update={
            "carriers": DEFAULT.carriers.model_copy(
                update={"weights": CarrierWeights(A=0, B=1, C=0)}
            )
        }
    )
    for seed in range(1, 11):
        assert type(runner._build_harness(seed, only_b).carrier) is CarrierB
