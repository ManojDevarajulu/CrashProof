import pytest

from crashproof.clock import Clock, Scheduler
from crashproof.ids import IdGen
from crashproof.models import TraceEvent
from crashproof.sim.trace import TraceRecorder
from crashproof.world.carriers import CarrierA, IdempotencyConflict
from crashproof.world.events import EventBus, WorldEvent


class _World:
    def __init__(self) -> None:
        self.clock = Clock()
        self.scheduler = Scheduler(self.clock)
        self.ids = IdGen(1)
        self.trace = TraceRecorder()
        self.bus = EventBus(self.scheduler, self.clock, self.ids, self.trace)
        self.carrier = CarrierA(self.clock, self.ids, self.trace, self.bus)
        self.delivered: list[WorldEvent] = []
        self.bus.subscribe(self.delivered.append)

    def drain(self) -> None:
        self.scheduler.run_until_idle(100)

    def names(self) -> list[str]:
        return [e.event for e in self.trace.events]

    def first(self, name: str) -> TraceEvent:
        return next(e for e in self.trace.events if e.event == name)


def test_supports_keys() -> None:
    assert CarrierA.supports_keys is True
    assert _World().carrier.supports_keys is True


def test_keyless_calls_make_two_labels() -> None:
    w = _World()
    a = w.carrier.create_label("ord-1", "wf-1")
    b = w.carrier.create_label("ord-1", "wf-1")
    assert a.label_id != b.label_id
    assert [r.label_id for r in w.carrier.labels] == [a.label_id, b.label_id]
    refs = w.carrier.get_label_by_reference("wf-1")
    assert [r.label_id for r in refs] == [a.label_id, b.label_id]


def test_result_status_created() -> None:
    assert _World().carrier.create_label("ord-1", "wf-1").status == "created"


def test_same_key_same_request_replays() -> None:
    w = _World()
    w.clock.advance_to(7)
    first = w.carrier.create_label("ord-1", "wf-1", idempotency_key="k")
    w.drain()
    w.clock.advance_to(9)
    second = w.carrier.create_label("ord-1", "wf-1", idempotency_key="k")
    w.drain()
    assert second == first
    assert len(w.carrier.labels) == 1
    assert w.carrier.labels[0].created_vt == 7
    assert [e.type for e in w.delivered] == ["label.created"]
    assert w.names().count("label.replayed") == 1
    assert w.first("label.replayed").vt == 9


@pytest.mark.parametrize(
    ("order_id", "reference"), [("ord-2", "wf-1"), ("ord-1", "wf-2")]
)
def test_same_key_different_request_conflicts(order_id: str, reference: str) -> None:
    w = _World()
    w.carrier.create_label("ord-1", "wf-1", idempotency_key="k")
    w.drain()
    before = len(w.trace.events)
    with pytest.raises(IdempotencyConflict):
        w.carrier.create_label(order_id, reference, idempotency_key="k")
    w.drain()
    assert len(w.carrier.labels) == 1
    assert [e.type for e in w.delivered] == ["label.created"]
    assert [e.event for e in w.trace.events[before:]] == ["label.key_conflict"]
    assert w.first("label.key_conflict").workflow_id == reference


def test_key_lookup_present_and_absent() -> None:
    w = _World()
    result = w.carrier.create_label("ord-1", "wf-1", idempotency_key="k")
    found = w.carrier.get_label_by_key("k")
    assert found is not None
    assert found.label_id == result.label_id
    assert w.carrier.get_label_by_key("other") is None


def test_reference_lookup_empty_when_none() -> None:
    assert _World().carrier.get_label_by_reference("wf-9") == ()


def test_record_contents() -> None:
    w = _World()
    w.clock.advance_to(4)
    w.carrier.create_label("ord-1", "wf-1", idempotency_key="k")
    record = w.carrier.labels[0]
    assert record.label_id.startswith("lbl-")
    assert (record.order_id, record.reference) == ("ord-1", "wf-1")
    assert (record.idempotency_key, record.status, record.created_vt) == (
        "k",
        "created",
        4,
    )


def test_trace_and_event_for_new_label() -> None:
    w = _World()
    w.clock.advance_to(4)
    result = w.carrier.create_label("ord-1", "wf-1", idempotency_key="k")
    w.drain()
    created = w.first("label.created_record")
    assert (created.vt, created.actor, created.workflow_id) == (4, "world", "wf-1")
    assert created.data == {
        "label_id": result.label_id,
        "order_id": "ord-1",
        "reference": "wf-1",
        "key": "k",
    }
    assert w.names() == [
        "label.created_record",
        "event.published",
        "event.delivered",
    ]
    (event,) = w.delivered
    assert (event.type, event.workflow_id, event.emitted_vt) == (
        "label.created",
        "wf-1",
        4,
    )
    assert w.first("event.published").data["deliver_vt"] == 4


def test_reads_are_not_traced() -> None:
    w = _World()
    w.carrier.create_label("ord-1", "wf-1", idempotency_key="k")
    before = len(w.trace.events)
    w.carrier.get_label_by_key("k")
    w.carrier.get_label_by_reference("wf-1")
    assert len(w.trace.events) == before
