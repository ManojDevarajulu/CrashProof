from collections.abc import Sequence
from pathlib import Path

import pytest

from crashproof.agent.base import Action, Done, Observation
from crashproof.agent.scripted import ScriptedAgent
from crashproof.clock import Clock, Scheduler
from crashproof.config import FaultProbabilities
from crashproof.ids import IdGen
from crashproof.models import Line, Order, ReturnRequest
from crashproof.rng import Rng
from crashproof.runtime.naive import NaiveRuntime
from crashproof.runtime.tools import ToolCall
from crashproof.sim.trace import TraceRecorder, trace_hash
from crashproof.world.carriers import CarrierA
from crashproof.world.events import EventBus
from crashproof.world.faults import FaultInjector, SimulatedCrash
from crashproof.world.refunds import RefundService
from crashproof.world.store import Store

WF = "wf-1"
ORDER = Order(
    order_id="o1",
    customer_id="c1",
    lines=[
        Line(sku="a", qty=2, unit_price=1500),
        Line(sku="b", qty=1, unit_price=700),
    ],
    paid_amount=3700,
    delivered_at=0,
    carrier="A",
    fraud_risk=False,
)
REQUEST = ReturnRequest(
    workflow_id=WF, order_id="o1", line_skus=["a", "b"], reason="r", requested_at=10
)


def probs(
    f1: float = 0.0, f2: float = 0.0, f3: float = 0.0, f5: float = 0.0
) -> FaultProbabilities:
    return FaultProbabilities(F1=f1, F2=f2, F3=f3, F5=f5)


class World:
    def __init__(self, seed: int = 0, p: FaultProbabilities | None = None) -> None:
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
        self.carrier = CarrierA(
            self.clock, ids, self.trace, self.bus, faults=self.faults
        )
        self.refunds = RefundService(
            self.clock, ids, self.trace, self.bus, faults=self.faults
        )

    def runtime(self, agent: object) -> NaiveRuntime:
        return NaiveRuntime(
            REQUEST,
            agent,  # type: ignore[arg-type]
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

    def scan(self, workflow_id: str = WF) -> None:
        self.bus.publish("return.scanned", workflow_id, {})
        self.drain()

    def names(self) -> list[str]:
        return [e.event for e in self.trace.events]


class FixedAgent:
    def __init__(self, actions: Sequence[Action]) -> None:
        self._actions = list(actions)
        self.observations: list[Observation] = []

    def next_action(self, obs: Observation) -> Action:
        self.observations.append(obs)
        return self._actions.pop(0) if self._actions else Done()


class RecordingAgent:
    def __init__(self, inner: ScriptedAgent) -> None:
        self._inner = inner
        self.observations: list[Observation] = []

    def next_action(self, obs: Observation) -> Action:
        self.observations.append(obs)
        return self._inner.next_action(obs)


def scripted() -> ScriptedAgent:
    return ScriptedAgent(REQUEST)


def call(tool: str, **args: object) -> ToolCall:
    return ToolCall.model_validate({"tool": tool, "args": args})


def run_to_end(world: World) -> NaiveRuntime:
    runtime = world.runtime(scripted())
    runtime.start()
    world.drain()
    world.scan()
    return runtime


def test_happy_path() -> None:
    world = World()
    runtime = world.runtime(scripted())
    runtime.start()
    world.drain()
    assert not runtime.is_terminal
    assert len(world.carrier.labels) == 1
    assert world.refunds.refunds == ()
    world.scan()
    assert [r.amount for r in world.refunds.refunds] == [3700]
    assert len(world.carrier.labels) == 1
    assert runtime.is_terminal
    assert runtime.outcome == "closed"
    executed = [
        e.data["tool"] for e in world.trace.events if e.event == "runtime.tool_executed"
    ]
    assert executed == [
        "get_order",
        "check_policy",
        "create_return_label",
        "issue_refund",
        "close_case",
    ]
    assert world.names().count("runtime.terminal") == 1


def test_trace_actors_and_values() -> None:
    world = World()
    run_to_end(world)
    mine = [e for e in world.trace.events if e.event.startswith(("agent.", "runtime."))]
    assert {e.workflow_id for e in mine} == {WF}
    for e in mine:
        assert e.actor == ("agent" if e.event.startswith("agent.") else "runtime")


def test_no_idempotency_keys_sent() -> None:
    world = World()
    run_to_end(world)
    assert all(r.idempotency_key is None for r in world.carrier.labels)
    assert all(r.idempotency_key is None for r in world.refunds.refunds)
    assert all(r.reference == WF for r in world.refunds.refunds)
    assert all(r.reference == WF for r in world.carrier.labels)


def test_no_dedup_two_refunds() -> None:
    world = World()
    refund = call("issue_refund", order_id="o1", amount=100)
    runtime = world.runtime(FixedAgent([refund, refund, call("close_case")]))
    runtime.start()
    assert len(world.refunds.refunds) == 2
    assert runtime.outcome == "closed"


def test_check_policy_gives_facts_only() -> None:
    world = World()
    agent = FixedAgent([call("check_policy", order_id="o1")])
    world.runtime(agent).start()
    result = agent.observations[1].last_result
    assert result is not None
    assert result.data == {"delivered_at": 0, "fraud_risk": False, "paid_amount": 3700}


def test_unknown_order_gives_not_ok_and_gave_up() -> None:
    world = World()
    agent = RecordingAgent(
        ScriptedAgent(
            ReturnRequest(
                workflow_id=WF,
                order_id="nope",
                line_skus=["a"],
                reason="r",
                requested_at=1,
            )
        )
    )
    runtime = world.runtime(agent)
    runtime.start()
    assert runtime.outcome == "gave_up"
    result = agent.observations[1].last_result
    assert result is not None
    assert result.ok is False
    assert result.error == "order_not_found"


def test_escalate_is_not_supported() -> None:
    world = World()
    runtime = world.runtime(FixedAgent([call("escalate_to_human", reason="x")]))
    runtime.start()
    assert runtime.outcome == "gave_up"


def test_timeout_observation_and_reexecution_duplicates_label() -> None:
    world = World(p=probs(f2=1.0))
    agent = RecordingAgent(scripted())
    runtime = world.runtime(agent)
    runtime.start()
    label_call = call("create_return_label", order_id="o1")
    timed_out = [o for o in agent.observations if o.timed_out]
    assert len(timed_out) == 4
    for obs in timed_out:
        assert obs.last_result is None
        assert obs.last_call == label_call
    assert len(world.carrier.labels) == 4
    assert runtime.outcome == "gave_up"
    assert world.names().count("runtime.tool_timeout") == 4


def test_f1_timeout_applies_nothing() -> None:
    world = World(p=probs(f1=1.0))
    runtime = world.runtime(scripted())
    runtime.start()
    assert world.carrier.labels == ()
    assert runtime.outcome == "gave_up"


def run_with_f2(seed: int) -> World:
    world = World(seed, probs(f2=0.3))
    world.runtime(scripted()).start()
    world.drain()
    world.scan()
    return world


def test_f2_on_refund_gives_duplicate_refunds_for_some_seed() -> None:
    hits = [
        seed
        for seed in range(301)
        if len(run_with_f2(seed).refunds.get_by_reference(WF)) >= 2
    ]
    assert hits


def test_crash_propagates_and_restart_creates_second_label() -> None:
    world = World(p=probs(f5=1.0))
    with pytest.raises(SimulatedCrash):
        world.runtime(scripted()).start()
    assert len(world.carrier.labels) == 1
    world.bus.clear_subscribers()
    with pytest.raises(SimulatedCrash):
        world.runtime(scripted()).start()
    assert len(world.carrier.labels) == 2


def test_duplicate_scan_webhook_via_f3_gives_one_refund() -> None:
    world = World(p=probs(f3=1.0))
    runtime = world.runtime(scripted())
    runtime.start()
    world.drain()
    world.scan()
    assert len(world.refunds.refunds) == 1
    assert world.names().count("event.delivered") >= 4
    assert runtime.outcome == "closed"


def test_two_manual_scans_give_one_refund() -> None:
    world = World()
    runtime = world.runtime(scripted())
    runtime.start()
    world.drain()
    world.scan()
    world.scan()
    assert len(world.refunds.refunds) == 1
    assert runtime.outcome == "closed"


def test_scan_for_other_workflow_is_ignored() -> None:
    world = World()
    runtime = world.runtime(scripted())
    runtime.start()
    world.drain()
    world.scan("wf-other")
    assert world.refunds.refunds == ()
    assert not runtime.is_terminal


def test_events_ignored_after_terminal() -> None:
    world = World()
    runtime = run_to_end(world)
    before = len(world.trace.events)
    world.scan()
    added = [e.event for e in world.trace.events[before:]]
    assert not any(n.startswith(("agent.", "runtime.")) for n in added)
    assert len(world.refunds.refunds) == 1
    assert runtime.outcome == "closed"


@pytest.mark.parametrize(
    "bad",
    [
        call("issue_refund", order_id="o1", amount=0),
        call("issue_refund", order_id="o1"),
        call("get_order", order_id="o1", reference="x"),
    ],
)
def test_invalid_call_is_terminal_and_traced(bad: ToolCall) -> None:
    world = World()
    runtime = world.runtime(FixedAgent([bad]))
    runtime.start()
    assert runtime.outcome == "invalid_call"
    assert "runtime.invalid_call" in world.names()
    assert world.refunds.refunds == ()


def test_determinism_same_trace_hash() -> None:
    hashes = []
    for _ in range(2):
        world = World(7, probs(f1=0.2, f2=0.2, f3=0.5))
        world.runtime(scripted()).start()
        world.drain()
        world.scan()
        hashes.append(trace_hash(world.trace.events))
    assert hashes[0] == hashes[1]


def test_source_has_no_broad_except() -> None:
    source = Path(__file__).parent.parent / "src/crashproof/runtime/naive.py"
    text = source.read_text(encoding="utf-8")
    assert "except Exception" not in text
    assert "except BaseException" not in text
    assert "except:" not in text
