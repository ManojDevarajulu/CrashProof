from collections.abc import Callable
from typing import Literal

from pydantic import BaseModel, ConfigDict

from crashproof.clock import Clock, Scheduler
from crashproof.ids import IdGen
from crashproof.models import Json
from crashproof.sim.trace import TraceRecorder
from crashproof.world.faults import FaultInjector


class WorldEvent(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    event_id: str
    type: Literal["label.created", "return.scanned", "refund.confirmed"]
    workflow_id: str
    data: dict[str, Json]
    emitted_vt: int


type Handler = Callable[[WorldEvent], None]


class EventBus:
    """Delivers world events through the harness scheduler.

    Each publish schedules one delivery at now + delay. Same-time deliveries run
    in scheduler insertion order (FIFO). One delivery calls subscribers in
    subscription order. Subscribers are looked up at delivery time, so a
    scheduled delivery still reaches subscribers present when it fires,
    including ones added after a restart (clear_subscribers, then subscribe).
    """

    def __init__(
        self,
        scheduler: Scheduler,
        clock: Clock,
        ids: IdGen,
        trace: TraceRecorder,
        faults: FaultInjector | None = None,
    ) -> None:
        self._scheduler = scheduler
        self._clock = clock
        self._ids = ids
        self._trace = trace
        self._faults = faults
        self._subscribers: list[Handler] = []

    def subscribe(self, handler: Handler) -> None:
        self._subscribers.append(handler)

    def clear_subscribers(self) -> None:
        self._subscribers.clear()

    def publish(
        self,
        type: Literal["label.created", "return.scanned", "refund.confirmed"],
        workflow_id: str,
        data: dict[str, Json],
        *,
        delay: int = 0,
    ) -> WorldEvent:
        now = self._clock.now
        event = WorldEvent(
            event_id=self._ids.new("evt"),
            type=type,
            workflow_id=workflow_id,
            data=data,
            emitted_vt=now,
        )
        self._scheduler.schedule_in(delay, lambda: self._deliver(event))
        self._trace.record(
            now,
            workflow_id,
            "world",
            "event.published",
            {"event_id": event.event_id, "type": type, "deliver_vt": now + delay},
        )
        if self._faults is not None and self._faults.duplicate_delivery(
            workflow_id, type, event.event_id, now + delay
        ):
            self._scheduler.schedule_in(delay, lambda: self._deliver(event))
        return event

    def _deliver(self, event: WorldEvent) -> None:
        self._trace.record(
            self._clock.now,
            event.workflow_id,
            "world",
            "event.delivered",
            {"event_id": event.event_id, "type": event.type},
        )
        for handler in tuple(self._subscribers):
            handler(event)
