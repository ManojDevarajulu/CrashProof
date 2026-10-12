import inspect
import json
from pathlib import Path
from typing import Any

import pytest

from crashproof import cli
from crashproof.agent.scripted import ScriptedAgent
from crashproof.clock import Clock, Scheduler
from crashproof.config import (
    FaultProbabilities,
    ScenarioConfig,
    load_scenario_config,
)
from crashproof.ids import IdGen
from crashproof.models import Line, Order, ReturnRequest
from crashproof.rng import Rng
from crashproof.runtime import keyed
from crashproof.runtime.executor import idempotency_key
from crashproof.runtime.keyed import KeyedRuntime
from crashproof.sim.runner import RunResult, run_seed
from crashproof.sim.scenario import make_scenario
from crashproof.sim.trace import TraceRecorder, trace_hash
from crashproof.world.carriers import (
    CarrierA,
    CarrierB,
    CarrierC,
    LabelProvider,
    LabelResult,
)
from crashproof.world.events import EventBus
from crashproof.world.faults import FaultInjector, SimulatedCrash
from crashproof.world.refunds import RefundService
from crashproof.world.store import Store

WF = "wf-1"
ORDER = Order(
    order_id="o1",
    customer_id="c1",
    lines=[Line(sku="a", qty=2, unit_price=1500), Line(sku="b", qty=1, unit_price=700)],
    paid_amount=3700,
    delivered_at=0,
    carrier="A",
    fraud_risk=False,
)
REQUEST = ReturnRequest(
    workflow_id=WF, order_id="o1", line_skus=["a", "b"], reason="r", requested_at=10
)
LABEL_KEY = idempotency_key(WF, "create_label")
REFUND_KEY = idempotency_key(WF, "issue_refund")
DEFAULT = load_scenario_config()


class RecordingB(CarrierB):
    """GlobeLine that remembers the key argument each call received."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.received: list[str | None] = []

    def create_label(
        self,
        order_id: str,
        reference: str,
        *,
        idempotency_key: str | None = None,
    ) -> LabelResult:
        self.received.append(idempotency_key)
        return super().create_label(
            order_id, reference, idempotency_key=idempotency_key
        )


class World:
    def __init__(
        self,
        carrier: str = "A",
        p: FaultProbabilities | None = None,
        seed: int = 0,
    ) -> None:
        self.clock = Clock()
        self.scheduler = Scheduler(self.clock)
        self.trace = TraceRecorder()
        ids = IdGen(seed)
        self.faults = (
            None if p is None else FaultInjector(Rng(seed), p, self.clock, self.trace)
        )
        self.bus = EventBus(
            self.scheduler, self.clock, ids, self.trace, faults=self.faults
        )
        self.store = Store([ORDER])
        args = (self.clock, ids, self.trace, self.bus, self.faults)
        self.carrier: LabelProvider
        if carrier == "A":
            self.carrier = CarrierA(*args)
        elif carrier == "B":
            self.carrier = RecordingB(*args, label_delay=120)
        else:
            self.carrier = CarrierC(*args)
        self.refunds = RefundService(
            self.clock, ids, self.trace, self.bus, faults=self.faults
        )

    def runtime(self) -> KeyedRuntime:
        return KeyedRuntime(
            REQUEST,
            ScriptedAgent(REQUEST),
            store=self.store,
            carrier=self.carrier,
            refunds=self.refunds,
            bus=self.bus,
            trace=self.trace,
            clock=self.clock,
            faults=self.faults,
        )

    def drain(self) -> None:
        self.scheduler.run_until_idle(10_000)

    def scan(self) -> None:
        self.bus.publish("return.scanned", WF, {})
        self.drain()

    def run(self, runtime: KeyedRuntime) -> None:
        runtime.start()
        self.drain()
        self.scan()

    def restart(self) -> KeyedRuntime:
        self.bus.clear_subscribers()
        return self.runtime()


def probs(f1: float = 0.0, f2: float = 0.0, f5: float = 0.0) -> FaultProbabilities:
    return FaultProbabilities(F1=f1, F2=f2, F3=0.0, F5=f5)


def force_first(monkeypatch: pytest.MonkeyPatch, fault: str, kind: str) -> None:
    original = FaultInjector._fires

    def fires(
        self: FaultInjector, name: str, p: float, ref: str, k: str, n: int
    ) -> bool:
        if name == fault:
            return k == kind and n == 1
        return original(self, name, p, ref, k, n)

    monkeypatch.setattr(FaultInjector, "_fires", fires)


def seed_with(carrier: str, behavior: str = "normal") -> int:
    for seed in range(1, 401):
        s = make_scenario(seed, DEFAULT, IdGen(seed))
        if s.carrier == carrier and s.behavior == behavior:
            return seed
    raise AssertionError(f"no seed with carrier {carrier} and behavior {behavior}")


def quiet() -> ScenarioConfig:
    zero = FaultProbabilities(F1=0.0, F2=0.0, F3=0.0, F5=0.0)
    return DEFAULT.model_copy(update={"faults": zero})


def events(result: RunResult) -> list[dict[str, Any]]:
    return [json.loads(line) for line in result.trace_bytes.decode().splitlines()]


@pytest.mark.parametrize("carrier", ["A", "C"])
def test_keys_sent_on_key_carriers(carrier: str) -> None:
    world = World(carrier)
    world.run(world.runtime())
    assert [r.idempotency_key for r in world.carrier.labels] == [LABEL_KEY]
    assert [r.idempotency_key for r in world.refunds.refunds] == [REFUND_KEY]


def test_carrier_b_receives_no_key_but_refund_still_does() -> None:
    world = World("B")
    world.run(world.runtime())
    assert isinstance(world.carrier, RecordingB)
    assert world.carrier.received == [None]
    assert [r.idempotency_key for r in world.refunds.refunds] == [REFUND_KEY]


def test_keyed_has_no_ledger_or_state_machine() -> None:
    params = inspect.signature(KeyedRuntime).parameters
    for name in ("ledger", "reconciler", "max_attempts"):
        assert name not in params


@pytest.mark.parametrize(("carrier", "labels"), [("A", 1), ("B", 2)])
def test_duplicate_request_fault_free(carrier: str, labels: int) -> None:
    result = run_seed(seed_with(carrier, "duplicate_request"), "keyed", quiet())
    names = [e["event"] for e in events(result)]
    assert names.count("label.created_record") == labels
    assert names.count("refund.issued") == 1


@pytest.mark.parametrize(("carrier", "labels"), [("A", 1), ("B", 2)])
def test_f2_on_the_label(
    monkeypatch: pytest.MonkeyPatch, carrier: str, labels: int
) -> None:
    force_first(monkeypatch, "F2", "create_label")
    world = World(carrier, probs(f2=0.5))
    world.run(world.runtime())
    assert len(world.carrier.labels) == labels
    assert "runtime.tool_timeout" in [e.event for e in world.trace.events]


def test_f2_on_the_refund_is_deduplicated_by_the_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    force_first(monkeypatch, "F2", "issue_refund")
    world = World("A", probs(f2=0.5))
    world.run(world.runtime())
    assert len(world.refunds.refunds) == 1
    assert "runtime.tool_timeout" in [e.event for e in world.trace.events]


@pytest.mark.parametrize(("carrier", "labels"), [("A", 1), ("B", 2)])
def test_f5_after_a_label_then_restart(
    monkeypatch: pytest.MonkeyPatch, carrier: str, labels: int
) -> None:
    force_first(monkeypatch, "F5", "label_applied")
    world = World(carrier, probs(f5=0.5))
    with pytest.raises(SimulatedCrash):
        world.runtime().start()
    assert len(world.carrier.labels) == 1
    world.run(world.restart())
    assert len(world.carrier.labels) == labels


def test_f5_after_a_refund_then_restart(monkeypatch: pytest.MonkeyPatch) -> None:
    force_first(monkeypatch, "F5", "refund_applied")
    world = World("A", probs(f5=0.5))
    world.runtime().start()
    world.drain()
    with pytest.raises(SimulatedCrash):
        world.scan()
    assert len(world.refunds.refunds) == 1
    restarted = world.restart()
    restarted.start()
    world.drain()
    world.scan()
    assert len(world.refunds.refunds) == 1


def test_fault_events_match_naive_up_to_first_divergence() -> None:
    compared = 0
    for seed in range(1, 61):
        naive = events(run_seed(seed, "naive", DEFAULT))
        other = events(run_seed(seed, "keyed", DEFAULT))
        split = 0
        for a, b in zip(naive, other, strict=False):
            if a != b:
                break
            split += 1
        naive_faults = [e for e in naive[:split] if e["event"].startswith("fault.")]
        other_faults = [e for e in other[:split] if e["event"].startswith("fault.")]
        assert naive_faults == other_faults
        compared += len(naive_faults)
        if split < min(len(naive), len(other)):
            assert other[split]["event"] == naive[split]["event"]
            assert other[split]["data"]["key"] != naive[split]["data"]["key"]
    assert compared > 0


@pytest.mark.parametrize("carrier", ["A", "B", "C"])
def test_determinism_of_world_runs(carrier: str) -> None:
    hashes = []
    for _ in range(2):
        world = World(carrier, probs(f1=0.2, f2=0.2), seed=7)
        world.run(world.runtime())
        hashes.append(trace_hash(world.trace.events))
    assert hashes[0] == hashes[1]


def test_source_has_no_broad_except() -> None:
    text = Path(keyed.__file__).read_text(encoding="utf-8")
    assert "except Exception" not in text
    assert "except BaseException" not in text
    assert "except:" not in text


@pytest.mark.parametrize("carrier", ["A", "B", "C"])
def test_run_seed_works_and_is_deterministic(carrier: str) -> None:
    seed = seed_with(carrier)
    first = run_seed(seed, "keyed", DEFAULT)
    second = run_seed(seed, "keyed", DEFAULT)
    assert first.runtime == "keyed"
    assert first.trace_hash == second.trace_hash


@pytest.fixture
def in_tmp(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.chdir(tmp_path)
    return tmp_path


def test_eval_with_three_runtimes_nulls_i8_for_keyed(in_tmp: Path) -> None:
    cli.main(["eval", "--seeds", "20", "--runtimes", "hardened,keyed,naive"])
    summary = json.loads(Path("results/summary.json").read_text(encoding="utf-8"))
    runtimes = summary["runtimes"]
    assert list(runtimes) == ["hardened", "keyed", "naive"]
    assert runtimes["keyed"]["I8"] is None
    assert runtimes["naive"]["I8"] is None
    assert isinstance(runtimes["hardened"]["I8"], int)
    assert sorted(runtimes["keyed"]) == sorted(runtimes["hardened"])


def test_two_three_runtime_evals_are_byte_identical(in_tmp: Path) -> None:
    args = ["eval", "--seeds", "15", "--runtimes", "naive,keyed,hardened"]
    cli.main(args)
    first = [Path(f"results/{n}.json").read_bytes() for n in ("summary", "failures")]
    cli.main(args)
    second = [Path(f"results/{n}.json").read_bytes() for n in ("summary", "failures")]
    assert first == second


def test_replay_works_for_keyed(in_tmp: Path) -> None:
    assert cli.main(["replay", "--seed", "1", "--runtime", "keyed"]) == 0
    assert Path("results/replay/seed-1-keyed.jsonl").exists()
