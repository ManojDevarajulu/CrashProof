import pytest

from crashproof.clock import Clock, Scheduler
from crashproof.config import FaultProbabilities
from crashproof.ids import IdGen
from crashproof.rng import Rng
from crashproof.sim.trace import TraceRecorder
from crashproof.world.carriers import (
    CarrierA,
    CarrierB,
    CarrierC,
    IdempotencyConflict,
    KeysNotSupported,
    LabelProvider,
)
from crashproof.world.events import EventBus, WorldEvent
from crashproof.world.faults import FaultInjector, Timeout

DELAY = 120


class _World:
    def __init__(self, f1: float = 0.0, f2: float = 0.0) -> None:
        self.clock = Clock()
        self.scheduler = Scheduler(self.clock)
        self.ids = IdGen(1)
        self.trace = TraceRecorder()
        faults = FaultInjector(
            Rng(1),
            FaultProbabilities(F1=f1, F2=f2, F3=0.0, F5=0.0),
            self.clock,
            self.trace,
        )
        self.bus = EventBus(self.scheduler, self.clock, self.ids, self.trace)
        args = (self.clock, self.ids, self.trace, self.bus, faults)
        self.a = CarrierA(*args)
        self.b = CarrierB(*args, label_delay=DELAY)
        self.c = CarrierC(*args)
        self.delivered: list[tuple[int, WorldEvent]] = []
        self.bus.subscribe(lambda e: self.delivered.append((self.clock.now, e)))

    def drain(self) -> None:
        self.scheduler.run_until_idle(100)


def count_labels(provider: LabelProvider) -> int:
    return len(provider.labels)


def test_supports_keys_flags() -> None:
    w = _World()
    assert (w.a.supports_keys, w.b.supports_keys, w.c.supports_keys) == (
        True,
        False,
        True,
    )


def test_b_ignores_keys_and_creates_a_label_per_call() -> None:
    w = _World()
    first = w.b.create_label("ord-1", "wf-1", idempotency_key="k")
    second = w.b.create_label("ord-1", "wf-1", idempotency_key="k")
    assert first.label_id != second.label_id
    assert [r.idempotency_key for r in w.b.labels] == [None, None]


def test_b_returns_pending() -> None:
    w = _World()
    assert w.b.create_label("ord-1", "wf-1").status == "pending"
    assert w.b.labels[0].status == "pending"


def test_b_label_created_event_is_delayed() -> None:
    w = _World()
    w.clock.advance_to(10)
    result = w.b.create_label("ord-1", "wf-1")
    published = next(e for e in w.trace.events if e.event == "event.published")
    assert published.data["deliver_vt"] == 10 + DELAY
    assert w.delivered == []
    w.drain()
    assert [(vt, e.type) for vt, e in w.delivered] == [(10 + DELAY, "label.created")]
    assert w.delivered[0][1].data["label_id"] == result.label_id


def test_b_lookup_by_reference_returns_all_labels() -> None:
    w = _World()
    a = w.b.create_label("ord-1", "wf-1")
    b = w.b.create_label("ord-1", "wf-1")
    w.b.create_label("ord-2", "wf-2")
    refs = w.b.get_label_by_reference("wf-1")
    assert [r.label_id for r in refs] == [a.label_id, b.label_id]


def test_b_key_lookup_raises() -> None:
    with pytest.raises(KeysNotSupported):
        _World().b.get_label_by_key("k")


def test_b_traces_the_record_like_a() -> None:
    w = _World()
    w.b.create_label("ord-1", "wf-1", idempotency_key="k")
    record = next(e for e in w.trace.events if e.event == "label.created_record")
    assert set(record.data) == {"label_id", "order_id", "reference", "key"}
    assert record.data["key"] is None


def test_c_honors_keys_like_a() -> None:
    w = _World()
    first = w.c.create_label("ord-1", "wf-1", idempotency_key="k")
    again = w.c.create_label("ord-1", "wf-1", idempotency_key="k")
    assert again == first
    assert count_labels(w.c) == 1
    assert first.status == "created"
    assert w.c.get_label_by_key("k") == w.c.labels[0]
    with pytest.raises(IdempotencyConflict):
        w.c.create_label("ord-2", "wf-1", idempotency_key="k")
    assert count_labels(w.c) == 1


def test_all_carriers_satisfy_the_protocol() -> None:
    w = _World()
    for provider in (w.a, w.b, w.c):
        assert count_labels(provider) == 0


@pytest.mark.parametrize("name", ["a", "b", "c"])
def test_f1_applies_nothing(name: str) -> None:
    w = _World(f1=1.0)
    provider: LabelProvider = getattr(w, name)
    with pytest.raises(Timeout):
        provider.create_label("ord-1", "wf-1")
    assert count_labels(provider) == 0


@pytest.mark.parametrize("name", ["a", "b", "c"])
def test_f2_applies_then_raises(name: str) -> None:
    w = _World(f2=1.0)
    provider: LabelProvider = getattr(w, name)
    with pytest.raises(Timeout):
        provider.create_label("ord-1", "wf-1")
    assert count_labels(provider) == 1
