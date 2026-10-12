import ast
import sqlite3
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
from crashproof.runtime.executor import idempotency_key
from crashproof.runtime.ledger import Ledger
from crashproof.runtime.tools import ToolCall
from crashproof.runtime.workflow import MAIN, HardenedRuntime, Workflow, WorkflowError
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
        self.ledger = Ledger(sqlite3.connect(":memory:"), self.clock)

    def runtime(self, agent: object | None = None) -> HardenedRuntime:
        return HardenedRuntime(
            REQUEST,
            agent or ScriptedAgent(REQUEST),  # type: ignore[arg-type]
            store=self.store,
            carrier=self.carrier,
            refunds=self.refunds,
            bus=self.bus,
            trace=self.trace,
            clock=self.clock,
            ledger=self.ledger,
            max_attempts=5,
            faults=self.faults,
        )

    def drain(self) -> None:
        self.scheduler.run_until_idle(10_000)

    def scan(self) -> None:
        self.bus.publish("return.scanned", WF, {})
        self.drain()

    def names(self) -> list[str]:
        return [e.event for e in self.trace.events]

    def state(self) -> str:
        row = self.ledger.get_workflow(WF)
        assert row is not None
        return row.state

    def restart(self, agent: object | None = None) -> HardenedRuntime:
        self.bus.clear_subscribers()
        return self.runtime(agent)


class FixedAgent:
    def __init__(self, actions: Sequence[Action]) -> None:
        self._actions = list(actions)
        self.observations: list[Observation] = []

    def next_action(self, obs: Observation) -> Action:
        self.observations.append(obs)
        return self._actions.pop(0) if self._actions else Done()


def call(tool: str, **args: object) -> ToolCall:
    return ToolCall.model_validate({"tool": tool, "args": args})


def machine(world: World) -> Workflow:
    return Workflow(world.ledger, WF, world.trace, world.clock)


# state machine


def test_new_workflow_is_received_and_persisted() -> None:
    world = World()
    wf = machine(world)
    assert (wf.state, wf.outcome, wf.is_terminal) == ("RECEIVED", None, False)
    assert world.state() == "RECEIVED"


def test_legal_sequence_ends_closed_with_outcome() -> None:
    world = World()
    wf = machine(world)
    for target in MAIN[1:]:
        wf.advance_to(target)
        assert wf.state == target
    assert wf.is_terminal
    assert wf.outcome == "closed"
    advances = [e for e in world.trace.events if e.event == "workflow.advance"]
    assert len(advances) == len(MAIN) - 1
    assert advances[0].data == {"from": "RECEIVED", "to": "ELIGIBILITY_CHECKED"}


def test_skip_forward_raises_and_changes_nothing() -> None:
    world = World()
    wf = machine(world)
    with pytest.raises(WorkflowError):
        wf.advance_to("LABEL_CREATED")
    assert wf.state == "RECEIVED"


def test_replay_is_a_noop() -> None:
    world = World()
    wf = machine(world)
    wf.advance_to("ELIGIBILITY_CHECKED")
    wf.advance_to("LABEL_PENDING")
    wf.advance_to("ELIGIBILITY_CHECKED")
    wf.advance_to("LABEL_PENDING")
    assert wf.state == "LABEL_PENDING"
    assert world.names().count("workflow.replay") == 2
    assert world.names().count("workflow.advance") == 2


def test_state_persists_across_a_rebuild() -> None:
    world = World()
    machine(world).advance_to("ELIGIBILITY_CHECKED")
    rebuilt = machine(world)
    assert rebuilt.state == "ELIGIBILITY_CHECKED"
    rebuilt.advance_to("LABEL_PENDING")
    assert machine(world).state == "LABEL_PENDING"


def test_fail_from_any_non_terminal_state_persists() -> None:
    for steps in range(len(MAIN) - 1):
        world = World()
        wf = machine(world)
        for target in MAIN[1 : steps + 1]:
            wf.advance_to(target)
        wf.fail("needs_review")
        assert (wf.state, wf.outcome) == ("FAILED_NEEDS_REVIEW", "needs_review")
        assert machine(world).is_terminal


def test_terminal_workflow_cannot_move() -> None:
    world = World()
    wf = machine(world)
    wf.fail("gave_up")
    with pytest.raises(WorkflowError):
        wf.fail("again")
    with pytest.raises(WorkflowError):
        wf.advance_to("ELIGIBILITY_CHECKED")


def test_advance_to_non_main_state_raises() -> None:
    world = World()
    with pytest.raises(WorkflowError):
        machine(world).advance_to("FAILED_NEEDS_REVIEW")


# runtime flows


def test_happy_path_ends_closed() -> None:
    world = World()
    runtime = world.runtime()
    runtime.start()
    world.drain()
    assert not runtime.is_terminal
    assert world.state() == "AWAITING_RETURN_SCAN"
    world.scan()
    assert runtime.is_terminal
    assert runtime.outcome == "closed"
    assert world.state() == "CLOSED"
    (label,) = world.carrier.labels
    (refund,) = world.refunds.refunds
    assert label.idempotency_key == idempotency_key(WF, "create_label")
    assert refund.idempotency_key == idempotency_key(WF, "issue_refund")
    assert refund.amount == 3700
    assert [r.status for r in world.ledger.effects_for(WF)] == ["CONFIRMED"] * 2
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


def test_check_policy_failure_does_not_advance() -> None:
    world = World()
    runtime = world.runtime(FixedAgent([call("check_policy", order_id="nope")]))
    runtime.start()
    assert world.state() == "FAILED_NEEDS_REVIEW"
    assert "workflow.advance" in world.names()
    advances = [
        e.data["to"] for e in world.trace.events if e.event == "workflow.advance"
    ]
    assert "ELIGIBILITY_CHECKED" not in advances
    assert runtime.outcome == "gave_up"


def test_label_dedup_after_rebuild_at_awaiting() -> None:
    world = World()
    first = world.runtime()
    first.start()
    world.drain()
    assert world.state() == "AWAITING_RETURN_SCAN"
    second = world.restart()
    second.start()
    world.drain()
    assert len(world.carrier.labels) == 1
    assert "executor.dedup" in world.names()
    assert not second.is_terminal
    world.scan()
    assert second.outcome == "closed"
    assert len(world.carrier.labels) == 1
    assert len(world.refunds.refunds) == 1
    assert [r.attempts for r in world.ledger.effects_for(WF)] == [1, 1]


def test_f2_on_label_agent_gives_up_with_one_label() -> None:
    world = World(p=probs(f2=1.0))
    runtime = world.runtime()
    runtime.start()
    assert len(world.carrier.labels) == 1
    assert runtime.outcome == "gave_up"
    assert world.state() == "FAILED_NEEDS_REVIEW"
    (row,) = world.ledger.effects_for(WF)
    assert row.status == "UNKNOWN"
    assert world.names().count("runtime.tool_timeout") == 4
    assert world.names().count("executor.unknown_pending") == 3


def test_forced_f5_then_rebuild_makes_no_provider_call() -> None:
    world = World(p=probs(f5=1.0))
    with pytest.raises(SimulatedCrash):
        world.runtime().start()
    (row,) = world.ledger.effects_for(WF)
    assert row.status == "EXECUTING"
    assert len(world.carrier.labels) == 1
    rebuilt = world.restart()
    rebuilt.start()
    (row,) = world.ledger.effects_for(WF)
    assert row.status == "UNKNOWN"
    assert len(world.carrier.labels) == 1
    assert rebuilt.outcome == "gave_up"
    assert world.state() == "FAILED_NEEDS_REVIEW"


def test_persisted_scan_received_lets_rebuilt_runtime_finish() -> None:
    world = World()
    world.runtime().start()
    world.drain()
    world.ledger.set_scan_received(WF)
    rebuilt = world.restart()
    rebuilt.start()
    assert rebuilt.outcome == "closed"
    assert len(world.refunds.refunds) == 1
    assert len(world.carrier.labels) == 1


def test_scan_event_is_persisted() -> None:
    world = World()
    world.runtime().start()
    world.drain()
    row = world.ledger.get_workflow(WF)
    assert row is not None
    assert row.scan_received is False
    world.scan()
    row = world.ledger.get_workflow(WF)
    assert row is not None
    assert row.scan_received is True


def test_duplicate_scan_is_harmless() -> None:
    world = World(p=probs(f3=1.0))
    runtime = world.runtime()
    runtime.start()
    world.drain()
    world.scan()
    world.scan()
    assert len(world.refunds.refunds) == 1
    assert runtime.outcome == "closed"
    assert world.names().count("event.delivered") >= 4


def test_scan_for_another_workflow_is_ignored() -> None:
    world = World()
    runtime = world.runtime()
    runtime.start()
    world.drain()
    world.bus.publish("return.scanned", "wf-other", {})
    world.drain()
    row = world.ledger.get_workflow(WF)
    assert row is not None
    assert row.scan_received is False
    assert not runtime.is_terminal


def test_terminal_row_makes_start_a_noop() -> None:
    world = World()
    world.runtime().start()
    world.drain()
    world.scan()
    agent = FixedAgent([])
    rebuilt = world.restart(agent)
    before = len(world.trace.events)
    rebuilt.start()
    world.scan()
    assert agent.observations == []
    assert rebuilt.outcome == "closed"
    assert rebuilt.is_terminal
    added = [e.event for e in world.trace.events[before:]]
    assert not any(n.startswith(("agent.", "runtime.", "workflow.")) for n in added)


def test_exhausted_effect_goes_to_needs_review() -> None:
    world = World()
    key = idempotency_key(WF, "create_label")
    world.carrier.create_label("other-order", WF, idempotency_key=key)
    runtime = HardenedRuntime(
        REQUEST,
        ScriptedAgent(REQUEST),
        store=world.store,
        carrier=world.carrier,
        refunds=world.refunds,
        bus=world.bus,
        trace=world.trace,
        clock=world.clock,
        ledger=world.ledger,
        max_attempts=1,
    )
    runtime.start()
    assert runtime.outcome == "gave_up"
    assert world.state() == "FAILED_NEEDS_REVIEW"
    (row,) = world.ledger.effects_for(WF)
    assert row.status == "FAILED"


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
    assert world.state() == "FAILED_NEEDS_REVIEW"


def test_agent_that_skips_a_step_fails_loudly() -> None:
    world = World()
    runtime = world.runtime(FixedAgent([call("create_return_label", order_id="o1")]))
    with pytest.raises(WorkflowError):
        runtime.start()
    assert world.carrier.labels == ()


def test_determinism_same_trace_hash() -> None:
    hashes = []
    for _ in range(2):
        world = World(7, probs(f1=0.2, f2=0.2, f3=0.5))
        world.runtime().start()
        world.drain()
        world.scan()
        hashes.append(trace_hash(world.trace.events))
    assert hashes[0] == hashes[1]


def run_with_restarts(world: World) -> HardenedRuntime:
    runtime = world.runtime()
    needs_start = True
    scan_sent = False
    for _ in range(100):
        try:
            if needs_start:
                needs_start = False
                runtime.start()
            world.drain()
            if not runtime.is_terminal and not scan_sent:
                scan_sent = True
                world.scan()
            return runtime
        except SimulatedCrash:
            runtime = world.restart()
            needs_start = True
    raise AssertionError("restart loop did not settle")


def test_crashes_never_duplicate_effects_across_seeds() -> None:
    for seed in range(60):
        world = World(seed, probs(f1=0.1, f2=0.1, f3=0.2, f5=0.2))
        runtime = run_with_restarts(world)
        assert len(world.carrier.labels) <= 1, seed
        assert len(world.refunds.refunds) <= 1, seed
        if runtime.outcome == "closed":
            assert len(world.refunds.refunds) == 1, seed


# no broad excepts


def broad_handlers(path: Path) -> list[int]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    bad: list[int] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.ExceptHandler):
            continue
        if node.type is None:
            bad.append(node.lineno)
            continue
        caught = node.type.elts if isinstance(node.type, ast.Tuple) else [node.type]
        names = {c.id for c in caught if isinstance(c, ast.Name)}
        if names & {"Exception", "BaseException"}:
            bad.append(node.lineno)
    return bad


@pytest.mark.parametrize("module", ["ledger", "executor", "workflow"])
def test_no_broad_except(module: str) -> None:
    path = Path(__file__).parent.parent / "src/crashproof/runtime" / f"{module}.py"
    assert broad_handlers(path) == []


def test_broad_except_scan_detects_a_violation(tmp_path: Path) -> None:
    sample = tmp_path / "bad.py"
    sample.write_text(
        "try:\n    pass\nexcept (ValueError, Exception):\n    pass\n", encoding="utf-8"
    )
    assert broad_handlers(sample) == [3]
