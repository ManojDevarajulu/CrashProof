from crashproof.clock import Clock, Scheduler
from crashproof.ids import IdGen
from crashproof.sim.trace import TraceRecorder, trace_hash
from crashproof.world.events import EventBus, WorldEvent


def _world() -> tuple[Clock, Scheduler, TraceRecorder, EventBus]:
    clock = Clock()
    scheduler = Scheduler(clock)
    trace = TraceRecorder()
    return clock, scheduler, trace, EventBus(scheduler, clock, IdGen(1), trace)


def test_delivery_happens_at_now_plus_delay() -> None:
    clock, scheduler, _, bus = _world()
    seen: list[tuple[int, str]] = []
    bus.subscribe(lambda e: seen.append((clock.now, e.event_id)))
    event = bus.publish("return.scanned", "wf-1", {}, delay=50)
    assert seen == []
    scheduler.run_next()
    assert seen == [(50, event.event_id)]


def test_zero_delay_not_delivered_until_scheduler_runs() -> None:
    _, scheduler, _, bus = _world()
    seen: list[WorldEvent] = []
    bus.subscribe(seen.append)
    bus.publish("label.created", "wf-1", {})
    assert seen == []
    scheduler.run_until_idle(10)
    assert len(seen) == 1


def test_same_vt_events_arrive_fifo() -> None:
    _, scheduler, _, bus = _world()
    seen: list[str] = []
    bus.subscribe(lambda e: seen.append(e.type))
    bus.publish("refund.confirmed", "wf-1", {})
    bus.publish("label.created", "wf-1", {})
    bus.publish("return.scanned", "wf-1", {})
    scheduler.run_until_idle(10)
    assert seen == ["refund.confirmed", "label.created", "return.scanned"]


def test_later_delay_arrives_after_earlier_published() -> None:
    _, scheduler, _, bus = _world()
    seen: list[str] = []
    bus.subscribe(lambda e: seen.append(e.type))
    bus.publish("return.scanned", "wf-1", {}, delay=10)
    bus.publish("label.created", "wf-1", {}, delay=5)
    scheduler.run_until_idle(10)
    assert seen == ["label.created", "return.scanned"]


def test_subscribers_called_in_subscription_order() -> None:
    _, scheduler, _, bus = _world()
    seen: list[str] = []
    bus.subscribe(lambda e: seen.append("first"))
    bus.subscribe(lambda e: seen.append("second"))
    bus.publish("label.created", "wf-1", {})
    scheduler.run_until_idle(10)
    assert seen == ["first", "second"]


def test_clear_subscribers_stops_delivery() -> None:
    _, scheduler, _, bus = _world()
    seen: list[WorldEvent] = []
    bus.subscribe(seen.append)
    bus.clear_subscribers()
    bus.publish("label.created", "wf-1", {})
    scheduler.run_until_idle(10)
    assert seen == []


def test_scheduled_delivery_reaches_subscriber_added_later() -> None:
    _, scheduler, _, bus = _world()
    old: list[WorldEvent] = []
    new: list[WorldEvent] = []
    bus.subscribe(old.append)
    bus.publish("return.scanned", "wf-1", {}, delay=10)
    bus.clear_subscribers()
    bus.subscribe(new.append)
    scheduler.run_until_idle(10)
    assert old == []
    assert len(new) == 1


def test_publish_and_delivery_are_traced() -> None:
    clock, scheduler, trace, bus = _world()
    clock.advance_to(5)
    event = bus.publish("return.scanned", "wf-1", {}, delay=20)
    scheduler.run_until_idle(10)
    published, delivered = trace.events
    assert (published.vt, published.actor, published.event) == (
        5,
        "world",
        "event.published",
    )
    assert published.workflow_id == "wf-1"
    assert published.data == {
        "event_id": event.event_id,
        "type": "return.scanned",
        "deliver_vt": 25,
    }
    assert (delivered.vt, delivered.event) == (25, "event.delivered")
    assert delivered.data == {"event_id": event.event_id, "type": "return.scanned"}


def _run_once() -> str:
    _, scheduler, trace, bus = _world()
    bus.publish("label.created", "wf-1", {"a": 1}, delay=3)
    bus.publish("refund.confirmed", "wf-1", {}, delay=3)
    scheduler.run_until_idle(10)
    return trace_hash(trace.events)


def test_identical_runs_give_identical_trace_hash() -> None:
    assert _run_once() == _run_once()
