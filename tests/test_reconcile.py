import ast
import sqlite3
from pathlib import Path

import pytest

from crashproof.agent.scripted import ScriptedAgent
from crashproof.clock import Clock, Scheduler
from crashproof.config import FaultProbabilities
from crashproof.ids import IdGen
from crashproof.models import Line, Order, ReturnRequest
from crashproof.rng import Rng
from crashproof.runtime.executor import idempotency_key
from crashproof.runtime.ledger import EffectRow, EffectType, Ledger, LedgerError
from crashproof.runtime.reconcile import make_reconciler, reconcile_workflow
from crashproof.runtime.workflow import HardenedRuntime
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


class KeylessCarrier(CarrierA):
    """A carrier without idempotency keys; a key lookup would be a bug."""

    supports_keys = False

    def get_label_by_key(self, key: str) -> None:
        raise AssertionError("reconciler must not look up by key here")


class World:
    def __init__(
        self,
        seed: int = 0,
        p: FaultProbabilities | None = None,
        *,
        keyless: bool = False,
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
        carrier_type = KeylessCarrier if keyless else CarrierA
        self.carrier = carrier_type(
            self.clock, ids, self.trace, self.bus, faults=self.faults
        )
        self.refunds = RefundService(
            self.clock, ids, self.trace, self.bus, faults=self.faults
        )
        self.ledger = Ledger(sqlite3.connect(":memory:"), self.clock)
        self.reconciler = make_reconciler(
            self.ledger, self.carrier, self.refunds, self.trace, self.clock
        )

    def runtime(self) -> HardenedRuntime:
        return HardenedRuntime(
            REQUEST,
            ScriptedAgent(REQUEST),
            store=self.store,
            carrier=self.carrier,
            refunds=self.refunds,
            bus=self.bus,
            trace=self.trace,
            clock=self.clock,
            ledger=self.ledger,
            max_attempts=5,
            faults=self.faults,
            reconciler=self.reconciler,
        )

    def restart(self) -> HardenedRuntime:
        self.bus.clear_subscribers()
        return self.runtime()

    def drain(self) -> None:
        self.scheduler.run_until_idle(10_000)

    def scan(self) -> None:
        self.bus.publish("return.scanned", WF, {})
        self.drain()

    def names(self) -> list[str]:
        return [e.event for e in self.trace.events]

    def unknown_row(self, effect_type: EffectType) -> EffectRow:
        """Put a row into UNKNOWN the way a timeout does."""
        key = idempotency_key(WF, effect_type)
        request: dict[str, int | str] = {"order_id": "o1", "reference": WF}
        if effect_type == "issue_refund":
            request["amount"] = 3700
        self.ledger.insert_intended(WF, effect_type, key, request)
        self.ledger.mark_executing(key)
        return self.ledger.mark_unknown(key)

    def row(self, effect_type: EffectType) -> EffectRow:
        row = self.ledger.get_effect(idempotency_key(WF, effect_type))
        assert row is not None
        return row


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


def fired(world: World, fault: str, op: str, attempt: int) -> bool:
    return any(
        e.event == f"fault.{fault}"
        and e.data.get("op") == op
        and e.data.get("attempt") == attempt
        for e in world.trace.events
    )


# unit: label


def test_label_found_by_key_is_confirmed_with_standard_shape() -> None:
    world = World()
    key = idempotency_key(WF, "create_label")
    label = world.carrier.create_label("o1", WF, idempotency_key=key)
    row = world.unknown_row("create_label")
    assert world.reconciler(row) == "confirmed"
    done = world.row("create_label")
    assert done.status == "CONFIRMED"
    assert done.response == {"label_id": label.label_id, "status": label.status}
    assert len(world.carrier.labels) == 1


def test_label_not_found_is_failed() -> None:
    world = World()
    row = world.unknown_row("create_label")
    assert world.reconciler(row) == "failed"
    assert world.row("create_label").status == "FAILED"
    assert world.carrier.labels == ()


def test_keyless_carrier_resolves_by_reference() -> None:
    world = World(keyless=True)
    label = world.carrier.create_label("o1", WF)
    row = world.unknown_row("create_label")
    assert world.reconciler(row) == "confirmed"
    done = world.row("create_label")
    assert done.response == {"label_id": label.label_id, "status": label.status}


def test_keyless_carrier_with_no_label_is_failed() -> None:
    world = World(keyless=True)
    row = world.unknown_row("create_label")
    assert world.reconciler(row) == "failed"
    assert world.row("create_label").status == "FAILED"


def test_label_with_other_reference_is_not_a_match_by_reference() -> None:
    world = World(keyless=True)
    world.carrier.create_label("o1", "wf-other")
    row = world.unknown_row("create_label")
    assert world.reconciler(row) == "failed"


# unit: refund


def test_refund_found_by_key_is_confirmed_with_standard_shape() -> None:
    world = World()
    key = idempotency_key(WF, "issue_refund")
    record = world.refunds.issue_refund("o1", 3700, WF, idempotency_key=key)
    row = world.unknown_row("issue_refund")
    assert world.reconciler(row) == "confirmed"
    done = world.row("issue_refund")
    assert done.status == "CONFIRMED"
    assert done.response == {"refund_id": record.refund_id, "amount": 3700}
    assert len(world.refunds.refunds) == 1


def test_refund_not_found_is_failed_and_applies_nothing() -> None:
    world = World()
    row = world.unknown_row("issue_refund")
    assert world.reconciler(row) == "failed"
    assert world.row("issue_refund").status == "FAILED"
    assert world.refunds.refunds == ()


def test_refund_applied_without_key_is_not_found_by_key() -> None:
    world = World()
    world.refunds.issue_refund("o1", 3700, WF)
    row = world.unknown_row("issue_refund")
    assert world.reconciler(row) == "failed"


def test_reconcile_traces_applied_and_not_applied() -> None:
    world = World()
    world.carrier.create_label(
        "o1", WF, idempotency_key=idempotency_key(WF, "create_label")
    )
    world.reconciler(world.unknown_row("create_label"))
    world.reconciler(world.unknown_row("issue_refund"))
    assert "reconcile.applied" in world.names()
    assert "reconcile.not_applied" in world.names()
    event = next(e for e in world.trace.events if e.event == "reconcile.applied")
    assert event.workflow_id == WF
    assert event.actor == "runtime"
    assert event.data == {
        "effect_type": "create_label",
        "key": idempotency_key(WF, "create_label"),
    }


def test_reconciler_rejects_a_row_that_is_not_unknown() -> None:
    world = World()
    key = idempotency_key(WF, "issue_refund")
    row = world.ledger.insert_intended(
        WF, "issue_refund", key, {"order_id": "o1", "reference": WF, "amount": 3700}
    )
    with pytest.raises(LedgerError):
        world.reconciler(row)
    assert world.row("issue_refund").status == "INTENDED"


def test_reconciliation_only_reads_the_world() -> None:
    world = World()
    world.carrier.create_label(
        "o1", WF, idempotency_key=idempotency_key(WF, "create_label")
    )
    world.refunds.issue_refund(
        "o1", 3700, WF, idempotency_key=idempotency_key(WF, "issue_refund")
    )
    before = (world.carrier.labels, world.refunds.refunds)
    world_events = [e for e in world.trace.events if e.actor == "world"]
    world.reconciler(world.unknown_row("create_label"))
    world.reconciler(world.unknown_row("issue_refund"))
    assert (world.carrier.labels, world.refunds.refunds) == before
    assert [e for e in world.trace.events if e.actor == "world"] == world_events


def test_reconciliation_is_never_faulted() -> None:
    world = World(p=probs(f1=1.0, f2=1.0, f5=1.0))
    row = world.unknown_row("issue_refund")
    assert world.reconciler(row) == "failed"
    assert not any(e.event.startswith("fault.") for e in world.trace.events)


# reconcile_workflow


def test_reconcile_workflow_recovers_then_resolves_in_id_order() -> None:
    world = World()
    world.carrier.create_label(
        "o1", WF, idempotency_key=idempotency_key(WF, "create_label")
    )
    label_key = idempotency_key(WF, "create_label")
    refund_key = idempotency_key(WF, "issue_refund")
    world.ledger.insert_intended(
        WF, "create_label", label_key, {"order_id": "o1", "reference": WF}
    )
    world.ledger.mark_executing(label_key)
    world.ledger.insert_intended(
        WF,
        "issue_refund",
        refund_key,
        {"order_id": "o1", "reference": WF, "amount": 3700},
    )
    world.ledger.mark_executing(refund_key)
    assert reconcile_workflow(WF, world.ledger, world.reconciler) == 2
    assert world.row("create_label").status == "CONFIRMED"
    assert world.row("issue_refund").status == "FAILED"
    resolved = [
        e.data["effect_type"]
        for e in world.trace.events
        if e.event.startswith("reconcile.")
    ]
    assert resolved == ["create_label", "issue_refund"]


def test_reconcile_workflow_returns_zero_when_nothing_is_unknown() -> None:
    world = World()
    assert reconcile_workflow(WF, world.ledger, world.reconciler) == 0
    assert world.trace.events == ()


def test_reconcile_workflow_ignores_other_workflows() -> None:
    world = World()
    key = idempotency_key("wf-other", "issue_refund")
    world.ledger.insert_intended(
        "wf-other", "issue_refund", key, {"order_id": "o1", "reference": "wf-other"}
    )
    world.ledger.mark_executing(key)
    assert reconcile_workflow(WF, world.ledger, world.reconciler) == 0
    other = world.ledger.get_effect(key)
    assert other is not None
    assert other.status == "UNKNOWN"


def test_reconcile_workflow_second_call_resolves_nothing() -> None:
    world = World()
    world.unknown_row("issue_refund")
    assert reconcile_workflow(WF, world.ledger, world.reconciler) == 1
    assert reconcile_workflow(WF, world.ledger, world.reconciler) == 0


# end to end through HardenedRuntime


def test_f2_on_label_ends_with_one_label_and_closed() -> None:
    seed = next((s for s in range(300) if _first_label_call_f2(s)), None)
    assert seed is not None, "no seed puts F2 on the first label call"
    world = World(seed, probs(f2=0.5))
    runtime = world.runtime()
    runtime.start()
    world.drain()
    world.scan()
    assert fired(world, "F2", "create_label", 1)
    assert len(world.carrier.labels) == 1
    assert runtime.outcome == "closed"
    assert world.row("create_label").status == "CONFIRMED"
    assert "reconcile.applied" in world.names()


def _first_label_call_f2(seed: int) -> bool:
    world = World(seed, probs(f2=0.5))
    world.runtime().start()
    return fired(world, "F2", "create_label", 1)


def _first_refund_call_f2(seed: int) -> bool:
    world = World(seed, probs(f2=0.5))
    runtime = world.runtime()
    runtime.start()
    world.drain()
    world.scan()
    return fired(world, "F2", "issue_refund", 1) and not fired(
        world, "F2", "create_label", 1
    )


def test_f2_on_refund_ends_with_one_refund_and_closed() -> None:
    seed = next((s for s in range(300) if _first_refund_call_f2(s)), None)
    assert seed is not None, "no seed puts F2 on the first refund call"
    world = World(seed, probs(f2=0.5))
    runtime = world.runtime()
    runtime.start()
    world.drain()
    world.scan()
    assert len(world.refunds.refunds) == 1
    assert runtime.outcome == "closed"
    assert world.row("issue_refund").status == "CONFIRMED"
    assert world.row("issue_refund").attempts == 1
    assert world.names().count("reconcile.applied") >= 1
    issued = [e for e in world.trace.events if e.event == "refund.issued"]
    assert len(issued) == 1
    assert not any(e.event == "refund.replayed" for e in world.trace.events)


def _first_refund_call_f1(seed: int) -> bool:
    world = World(seed, probs(f1=0.5))
    runtime = world.runtime()
    runtime.start()
    world.drain()
    world.scan()
    return fired(world, "F1", "issue_refund", 1) and not fired(
        world, "F1", "create_label", 1
    )


def test_f1_on_refund_is_not_found_then_retried_with_same_key() -> None:
    seed = next((s for s in range(300) if _first_refund_call_f1(s)), None)
    assert seed is not None, "no seed puts F1 on the first refund call"
    world = World(seed, probs(f1=0.5))
    runtime = world.runtime()
    runtime.start()
    world.drain()
    world.scan()
    assert len(world.refunds.refunds) == 1
    assert runtime.outcome == "closed"
    assert "reconcile.not_applied" in world.names()
    key = idempotency_key(WF, "issue_refund")
    assert world.refunds.refunds[0].idempotency_key == key
    assert world.row("issue_refund").attempts >= 2


def _crashes_after_refund(seed: int) -> bool:
    world = World(seed, probs(f5=0.5))
    run_with_restarts(world)
    return any(
        e.event == "fault.F5" and e.data.get("point") == "refund_applied"
        for e in world.trace.events
    )


def test_f5_after_refund_restart_ends_with_one_refund_and_no_stuck_row() -> None:
    seed = next((s for s in range(300) if _crashes_after_refund(s)), None)
    assert seed is not None, "no seed crashes after the refund is applied"
    world = World(seed, probs(f5=0.5))
    runtime = run_with_restarts(world)
    assert len(world.refunds.refunds) == 1
    assert runtime.outcome == "closed"
    assert all(r.status == "CONFIRMED" for r in world.ledger.effects_for(WF))
    assert "reconcile.applied" in world.names()


def test_agent_gives_up_then_reconcile_workflow_resolves_the_refund() -> None:
    world = World(p=probs(f2=1.0))
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
        max_attempts=5,
        faults=world.faults,
    )
    runtime.start()
    assert runtime.outcome == "gave_up"
    (row,) = world.ledger.effects_for(WF)
    assert row.status == "UNKNOWN"
    assert reconcile_workflow(WF, world.ledger, world.reconciler) == 1
    (row,) = world.ledger.effects_for(WF)
    assert row.status == "CONFIRMED"
    assert len(world.carrier.labels) == 1


def test_agent_gives_up_on_a_keyless_label_and_reconcile_resolves_it() -> None:
    world = World(p=probs(f2=1.0), keyless=True)
    HardenedRuntime(
        REQUEST,
        ScriptedAgent(REQUEST),
        store=world.store,
        carrier=world.carrier,
        refunds=world.refunds,
        bus=world.bus,
        trace=world.trace,
        clock=world.clock,
        ledger=world.ledger,
        max_attempts=5,
        faults=world.faults,
    ).start()
    assert reconcile_workflow(WF, world.ledger, world.reconciler) == 1
    assert world.row("create_label").status == "CONFIRMED"
    assert len(world.carrier.labels) == 1


def test_a_row_the_provider_never_saw_becomes_failed() -> None:
    world = World(p=probs(f1=1.0))
    HardenedRuntime(
        REQUEST,
        ScriptedAgent(REQUEST),
        store=world.store,
        carrier=world.carrier,
        refunds=world.refunds,
        bus=world.bus,
        trace=world.trace,
        clock=world.clock,
        ledger=world.ledger,
        max_attempts=5,
        faults=world.faults,
    ).start()
    assert world.carrier.labels == ()
    assert reconcile_workflow(WF, world.ledger, world.reconciler) == 1
    assert world.row("create_label").status == "FAILED"


def test_no_blind_retry_a_reconciled_applied_row_makes_no_second_call() -> None:
    world = World(p=probs(f2=1.0))
    world.runtime().start()
    assert len(world.carrier.labels) == 1
    assert "label.replayed" not in world.names()
    assert world.names().count("label.created_record") == 1


# determinism and static checks


def test_determinism_same_trace_hash() -> None:
    hashes = []
    for _ in range(2):
        world = World(7, probs(f1=0.2, f2=0.2, f3=0.5, f5=0.2))
        run_with_restarts(world)
        reconcile_workflow(WF, world.ledger, world.reconciler)
        hashes.append(trace_hash(world.trace.events))
    assert hashes[0] == hashes[1]


def test_effects_never_duplicate_across_seeds() -> None:
    for seed in range(60):
        world = World(seed, probs(f1=0.1, f2=0.1, f3=0.2, f5=0.2))
        runtime = run_with_restarts(world)
        reconcile_workflow(WF, world.ledger, world.reconciler)
        assert len(world.carrier.labels) <= 1, seed
        assert len(world.refunds.refunds) <= 1, seed
        assert all(r.status != "UNKNOWN" for r in world.ledger.effects_for(WF)), seed
        if runtime.outcome == "closed":
            assert len(world.refunds.refunds) == 1, seed


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


def test_no_broad_except() -> None:
    path = Path(__file__).parent.parent / "src/crashproof/runtime/reconcile.py"
    assert broad_handlers(path) == []


def test_broad_except_scan_detects_a_violation(tmp_path: Path) -> None:
    sample = tmp_path / "bad.py"
    sample.write_text(
        "try:\n    pass\nexcept (ValueError, Exception):\n    pass\n", encoding="utf-8"
    )
    assert broad_handlers(sample) == [3]


def test_reconcile_source_reads_world_only_through_lookups() -> None:
    path = Path(__file__).parent.parent / "src/crashproof/runtime/reconcile.py"
    source = path.read_text(encoding="utf-8")
    for forbidden in ("create_label(", "issue_refund(", "publish(", "faults"):
        assert forbidden not in source, forbidden
