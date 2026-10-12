import pytest

from crashproof.clock import Clock, Scheduler
from crashproof.config import FaultProbabilities
from crashproof.ids import IdGen
from crashproof.rng import Rng
from crashproof.sim.trace import TraceRecorder, trace_hash
from crashproof.world.carriers import CarrierA
from crashproof.world.events import EventBus, WorldEvent
from crashproof.world.faults import (
    FaultInjector,
    ProviderOp,
    SimulatedCrash,
    Timeout,
)
from crashproof.world.refunds import RefundService


def _probs(
    f1: float = 0.0, f2: float = 0.0, f3: float = 0.0, f5: float = 0.0
) -> FaultProbabilities:
    return FaultProbabilities(F1=f1, F2=f2, F3=f3, F5=f5)


class _World:
    def __init__(self, seed: int, probs: FaultProbabilities | None) -> None:
        self.clock = Clock()
        self.scheduler = Scheduler(self.clock)
        self.trace = TraceRecorder()
        ids = IdGen(seed)
        self.faults = (
            None
            if probs is None
            else FaultInjector(Rng(seed), probs, self.clock, self.trace)
        )
        self.bus = EventBus(
            self.scheduler, self.clock, ids, self.trace, faults=self.faults
        )
        self.carrier = CarrierA(
            self.clock, ids, self.trace, self.bus, faults=self.faults
        )
        self.refunds = RefundService(
            self.clock, ids, self.trace, self.bus, faults=self.faults
        )
        self.seen: list[WorldEvent] = []
        self.bus.subscribe(self.seen.append)

    def names(self) -> list[str]:
        return [e.event for e in self.trace.events]


def _attempt(inj: FaultInjector, op: ProviderOp, ref: str) -> str:
    try:
        n = inj.begin_call(op, ref)
    except Timeout:
        return "F1"
    try:
        inj.end_call(op, ref, n)
    except Timeout:
        return "F2"
    return "ok"


def _crash(inj: FaultInjector, ref: str) -> str:
    try:
        inj.crash_point("refund_applied", ref)
    except SimulatedCrash:
        return "F5"
    return "ok"


def _injector(seed: int, probs: FaultProbabilities) -> FaultInjector:
    return FaultInjector(Rng(seed), probs, Clock(), TraceRecorder())


def _run_standard_ops(world: _World) -> None:
    world.carrier.create_label("ord-1", "wf-1", idempotency_key="k-l")
    world.refunds.issue_refund("ord-1", 500, "wf-1", idempotency_key="k-r")
    world.scheduler.run_until_idle(10)


def test_zero_probabilities_leave_everything_identical() -> None:
    plain = _World(7, None)
    zero = _World(7, _probs())
    _run_standard_ops(plain)
    _run_standard_ops(zero)
    assert trace_hash(plain.trace.events) == trace_hash(zero.trace.events)
    assert plain.carrier.labels == zero.carrier.labels
    assert plain.refunds.refunds == zero.refunds.refunds
    assert plain.seen == zero.seen


def test_f1_label_times_out_and_applies_nothing() -> None:
    world = _World(1, _probs(f1=1.0))
    with pytest.raises(Timeout):
        world.carrier.create_label("ord-1", "wf-1")
    assert world.carrier.labels == ()
    assert world.scheduler.pending == 0
    assert world.names() == ["fault.F1"]


def test_f1_refund_times_out_and_applies_nothing() -> None:
    world = _World(1, _probs(f1=1.0))
    with pytest.raises(Timeout):
        world.refunds.issue_refund("ord-1", 500, "wf-1")
    assert world.refunds.refunds == ()
    assert world.scheduler.pending == 0
    assert world.names() == ["fault.F1"]


def test_f1_fires_before_amount_validation() -> None:
    world = _World(1, _probs(f1=1.0))
    with pytest.raises(Timeout):
        world.refunds.issue_refund("ord-1", -5, "wf-1")


def test_f2_refund_is_applied_but_caller_sees_timeout() -> None:
    world = _World(1, _probs(f2=1.0))
    with pytest.raises(Timeout):
        world.refunds.issue_refund("ord-1", 500, "wf-1")
    assert len(world.refunds.refunds) == 1
    world.scheduler.run_until_idle(10)
    assert [e.type for e in world.seen] == ["refund.confirmed"]
    names = world.names()
    assert names.index("fault.F2") > names.index("event.published")
    assert names[-1] == "event.delivered"


def test_f2_label_is_applied_but_caller_sees_timeout() -> None:
    world = _World(1, _probs(f2=1.0))
    with pytest.raises(Timeout):
        world.carrier.create_label("ord-1", "wf-1")
    assert len(world.carrier.labels) == 1
    world.scheduler.run_until_idle(10)
    assert [e.type for e in world.seen] == ["label.created"]
    names = world.names()
    assert names.index("fault.F2") > names.index("event.published")


def test_f1_wins_over_f2() -> None:
    world = _World(1, _probs(f1=1.0, f2=1.0))
    with pytest.raises(Timeout):
        world.refunds.issue_refund("ord-1", 500, "wf-1")
    assert world.refunds.refunds == ()
    assert world.names() == ["fault.F1"]


def test_f1_and_f2_timeouts_are_indistinguishable() -> None:
    errors: list[Timeout] = []
    for probs in (_probs(f1=1.0), _probs(f2=1.0)):
        world = _World(1, probs)
        with pytest.raises(Timeout) as info:
            world.refunds.issue_refund("ord-1", 500, "wf-1")
        errors.append(info.value)
    assert type(errors[0]) is type(errors[1])
    assert str(errors[0]) == str(errors[1])


def test_f3_delivers_the_same_event_twice() -> None:
    world = _World(1, _probs(f3=1.0))
    world.bus.publish("return.scanned", "wf-1", {"x": 1}, delay=5)
    world.scheduler.run_until_idle(10)
    assert len(world.seen) == 2
    assert world.seen[0] == world.seen[1]
    names = world.names()
    assert names.count("fault.F3") == 1
    assert names.count("event.delivered") == 2
    assert names.index("fault.F3") < names.index("event.delivered")


def test_f3_zero_delivers_once() -> None:
    world = _World(1, _probs(f3=0.0))
    world.bus.publish("return.scanned", "wf-1", {}, delay=5)
    world.scheduler.run_until_idle(10)
    assert len(world.seen) == 1
    assert "fault.F3" not in world.names()


def test_f3_duplicate_is_adjacent_to_the_first() -> None:
    world = _World(1, _probs(f3=1.0))
    first = world.bus.publish("return.scanned", "wf-1", {}, delay=5)
    other = world.bus.publish("label.created", "wf-1", {}, delay=5)
    world.scheduler.run_until_idle(10)
    assert [e.event_id for e in world.seen[:2]] == [first.event_id] * 2
    assert [e.event_id for e in world.seen[2:]] == [other.event_id] * 2


def test_f5_crash_raises_and_is_traced() -> None:
    trace = TraceRecorder()
    inj = FaultInjector(Rng(1), _probs(f5=1.0), Clock(), trace)
    with pytest.raises(SimulatedCrash) as info:
        inj.crash_point("label_applied", "wf-1")
    assert info.value.point == "label_applied"
    assert [e.event for e in trace.events] == ["fault.F5"]


def test_f5_zero_returns_without_raising() -> None:
    trace = TraceRecorder()
    inj = FaultInjector(Rng(1), _probs(f5=0.0), Clock(), trace)
    inj.crash_point("refund_applied", "wf-1")
    assert trace.events == ()


def test_simulated_crash_is_not_caught_by_except_exception() -> None:
    def swallow() -> None:
        try:
            raise SimulatedCrash("refund_applied")
        except Exception:
            return

    with pytest.raises(SimulatedCrash):
        swallow()


def test_timeout_is_caught_by_except_exception() -> None:
    caught: list[Exception] = []
    try:
        raise Timeout("timed out")
    except Exception as exc:
        caught.append(exc)
    assert len(caught) == 1


def test_timeout_is_not_the_builtin_timeout_error() -> None:
    assert not isinstance(Timeout("x"), TimeoutError)
    assert not issubclass(Timeout, TimeoutError)


def _decision_list(seed: int) -> list[str]:
    inj = _injector(seed, _probs(f1=0.3, f2=0.3, f5=0.3))
    out: list[str] = []
    for _ in range(200):
        out.append(_attempt(inj, "issue_refund", "wf-1"))
        out.append(_crash(inj, "wf-1"))
    return out


def test_same_seed_same_decisions_different_seed_differs() -> None:
    assert _decision_list(3) == _decision_list(3)
    assert _decision_list(3) != _decision_list(4)


def test_call_order_does_not_change_decisions() -> None:
    probs = _probs(f1=0.5, f2=0.5, f5=0.5)

    def run(order: list[tuple[str, ProviderOp]]) -> dict[tuple[str, str], list[str]]:
        inj = _injector(11, probs)
        out: dict[tuple[str, str], list[str]] = {}
        for ref, op in order:
            out.setdefault((ref, op), []).append(_attempt(inj, op, ref))
        return out

    ops: list[ProviderOp] = ["create_label", "issue_refund"]
    a = [(r, o) for _ in range(6) for r in ("wf-A", "wf-B") for o in ops]
    b = [(r, o) for _ in range(6) for o in reversed(ops) for r in ("wf-B", "wf-A")]
    assert run(a) == run(b)
    assert set(run(a)) == {(r, o) for r in ("wf-A", "wf-B") for o in ops}


def test_runtimes_with_different_call_counts_share_decisions() -> None:
    probs = _probs(f1=0.5, f2=0.5, f5=0.5)

    one_label = _injector(5, probs)
    label_a = [_attempt(one_label, "create_label", "wf-1")]
    refund_a: list[str] = []
    crash_a: list[str] = []
    for _ in range(3):
        refund_a.append(_attempt(one_label, "issue_refund", "wf-1"))
        crash_a.append(_crash(one_label, "wf-1"))

    four_labels = _injector(5, probs)
    label_b = [_attempt(four_labels, "create_label", "wf-1") for _ in range(4)]
    refund_b = [_attempt(four_labels, "issue_refund", "wf-1")]
    crash_b = [_crash(four_labels, "wf-1")]

    assert label_a[0] == label_b[0]
    assert refund_a[0] == refund_b[0]
    assert crash_a[0] == crash_b[0]


def test_fault_types_are_independent() -> None:
    def f1_f2(f3: float, f5: float) -> list[str]:
        inj = _injector(9, _probs(f1=0.4, f2=0.4, f3=f3, f5=f5))
        out: list[str] = []
        for _ in range(100):
            out.append(_attempt(inj, "issue_refund", "wf-1"))
            inj.duplicate_delivery("wf-1", "return.scanned", "evt-1", 0)
            _crash(inj, "wf-1")
        return out

    assert f1_f2(0.0, 0.0) == f1_f2(1.0, 1.0)


def _seed_with_outcomes(wanted: list[str]) -> int:
    for seed in range(500):
        inj = _injector(seed, _probs(f2=0.5))
        got = [_attempt(inj, "issue_refund", "wf-1") for _ in wanted]
        if got == wanted:
            return seed
    raise AssertionError("no seed found")


def test_replayed_keyed_call_counts_as_second_attempt() -> None:
    seed = _seed_with_outcomes(["ok", "F2"])
    world = _World(seed, _probs(f2=0.5))
    world.refunds.issue_refund("ord-1", 500, "wf-1", idempotency_key="k")
    with pytest.raises(Timeout):
        world.refunds.issue_refund("ord-1", 500, "wf-1", idempotency_key="k")
    assert len(world.refunds.refunds) == 1
    f2 = [e for e in world.trace.events if e.event == "fault.F2"]
    assert len(f2) == 1
    assert f2[0].data["attempt"] == 2
    assert "refund.replayed" in world.names()


def test_counters_survive_a_crash_when_injector_is_reused() -> None:
    trace = TraceRecorder()
    inj = FaultInjector(Rng(1), _probs(f5=1.0), Clock(), trace)
    for _ in range(2):
        with pytest.raises(SimulatedCrash):
            inj.crash_point("refund_applied", "wf-1")
    occurrences = [e.data["occurrence"] for e in trace.events]
    assert occurrences == [1, 2]


def test_attempt_numbers_count_per_reference_and_op() -> None:
    trace = TraceRecorder()
    inj = FaultInjector(Rng(1), _probs(f1=1.0), Clock(), trace)
    kinds: list[ProviderOp] = ["issue_refund", "issue_refund", "create_label"]
    for op in kinds:
        with pytest.raises(Timeout):
            inj.begin_call(op, "wf-1")
    assert [e.data["attempt"] for e in trace.events] == [1, 2, 1]


def test_trace_records_vt_and_actor() -> None:
    clock = Clock()
    trace = TraceRecorder()
    inj = FaultInjector(Rng(1), _probs(f1=1.0), clock, trace)
    clock.advance_to(40)
    with pytest.raises(Timeout):
        inj.begin_call("create_label", "wf-1")
    event = trace.events[0]
    assert (event.vt, event.actor, event.workflow_id) == (40, "fault", "wf-1")
    assert event.data == {"ref": "wf-1", "op": "create_label", "attempt": 1}
