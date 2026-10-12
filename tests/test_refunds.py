import pytest

from crashproof.clock import Clock, Scheduler
from crashproof.ids import IdGen
from crashproof.models import TraceEvent
from crashproof.sim.trace import TraceRecorder
from crashproof.world.carriers import IdempotencyConflict
from crashproof.world.events import EventBus, WorldEvent
from crashproof.world.refunds import InvalidRefundAmount, RefundService


class _World:
    def __init__(self) -> None:
        self.clock = Clock()
        self.scheduler = Scheduler(self.clock)
        self.ids = IdGen(1)
        self.trace = TraceRecorder()
        self.bus = EventBus(self.scheduler, self.clock, self.ids, self.trace)
        self.refunds = RefundService(self.clock, self.ids, self.trace, self.bus)
        self.delivered: list[WorldEvent] = []
        self.bus.subscribe(self.delivered.append)

    def drain(self) -> None:
        self.scheduler.run_until_idle(100)

    def names(self) -> list[str]:
        return [e.event for e in self.trace.events]

    def first(self, name: str) -> TraceEvent:
        return next(e for e in self.trace.events if e.event == name)


def test_keyless_twice_gives_two_refunds() -> None:
    w = _World()
    a = w.refunds.issue_refund("ord-1", 300, "wf-1")
    b = w.refunds.issue_refund("ord-1", 300, "wf-1")
    assert a.refund_id != b.refund_id
    assert w.refunds.refunds == (a, b)
    assert w.refunds.total_refunded("ord-1") == 600


def test_same_key_same_request_replays() -> None:
    w = _World()
    w.clock.advance_to(3)
    first = w.refunds.issue_refund("ord-1", 300, "wf-1", idempotency_key="k")
    w.drain()
    w.clock.advance_to(8)
    second = w.refunds.issue_refund("ord-1", 300, "wf-1", idempotency_key="k")
    w.drain()
    assert second == first
    assert second.created_vt == 3
    assert len(w.refunds.refunds) == 1
    assert w.refunds.total_refunded("ord-1") == 300
    assert [e.type for e in w.delivered] == ["refund.confirmed"]
    assert w.names().count("refund.replayed") == 1
    assert w.first("refund.replayed").vt == 8


@pytest.mark.parametrize(
    ("order_id", "amount", "reference"),
    [("ord-2", 300, "wf-1"), ("ord-1", 400, "wf-1"), ("ord-1", 300, "wf-2")],
)
def test_same_key_different_request_conflicts(
    order_id: str, amount: int, reference: str
) -> None:
    w = _World()
    w.refunds.issue_refund("ord-1", 300, "wf-1", idempotency_key="k")
    w.drain()
    before = len(w.trace.events)
    with pytest.raises(IdempotencyConflict):
        w.refunds.issue_refund(order_id, amount, reference, idempotency_key="k")
    w.drain()
    assert len(w.refunds.refunds) == 1
    assert [e.type for e in w.delivered] == ["refund.confirmed"]
    assert [e.event for e in w.trace.events[before:]] == ["refund.key_conflict"]


@pytest.mark.parametrize("amount", [0, -1, True, 1.5])
def test_invalid_amount_applies_nothing(amount: object) -> None:
    w = _World()
    with pytest.raises(InvalidRefundAmount):
        w.refunds.issue_refund("ord-1", amount, "wf-1")  # type: ignore[arg-type]
    w.drain()
    assert w.refunds.refunds == ()
    assert w.delivered == []
    assert w.trace.events == ()


def test_amount_over_paid_is_not_checked() -> None:
    w = _World()
    w.refunds.issue_refund("unknown-order", 10**9, "wf-1")
    assert w.refunds.total_refunded("unknown-order") == 10**9


def test_record_trace_and_event_for_new_refund() -> None:
    w = _World()
    w.clock.advance_to(6)
    record = w.refunds.issue_refund("ord-1", 300, "wf-1", idempotency_key="k")
    w.drain()
    assert record.refund_id.startswith("rfd-")
    assert (record.order_id, record.amount, record.reference) == ("ord-1", 300, "wf-1")
    assert (record.idempotency_key, record.created_vt) == ("k", 6)
    issued = w.first("refund.issued")
    assert (issued.vt, issued.actor, issued.workflow_id) == (6, "world", "wf-1")
    assert issued.data == {
        "refund_id": record.refund_id,
        "order_id": "ord-1",
        "amount": 300,
        "reference": "wf-1",
        "key": "k",
    }
    assert w.names() == ["refund.issued", "event.published", "event.delivered"]
    (event,) = w.delivered
    assert (event.type, event.workflow_id, event.emitted_vt) == (
        "refund.confirmed",
        "wf-1",
        6,
    )


def test_lookups() -> None:
    w = _World()
    a = w.refunds.issue_refund("ord-1", 100, "wf-1", idempotency_key="k")
    b = w.refunds.issue_refund("ord-1", 200, "wf-1")
    w.refunds.issue_refund("ord-2", 50, "wf-2")
    assert w.refunds.get_by_key("k") == a
    assert w.refunds.get_by_key("absent") is None
    assert w.refunds.get_by_reference("wf-1") == (a, b)
    assert w.refunds.get_by_reference("wf-9") == ()
    assert [r.amount for r in w.refunds.refunds] == [100, 200, 50]


def test_total_refunded_unknown_order_is_zero() -> None:
    assert _World().refunds.total_refunded("nope") == 0


def test_reads_are_not_traced() -> None:
    w = _World()
    w.refunds.issue_refund("ord-1", 100, "wf-1", idempotency_key="k")
    before = len(w.trace.events)
    w.refunds.get_by_key("k")
    w.refunds.get_by_reference("wf-1")
    w.refunds.total_refunded("ord-1")
    assert len(w.trace.events) == before
