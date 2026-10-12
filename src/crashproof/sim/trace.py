import hashlib
import json
from collections.abc import Sequence

from crashproof.models import Actor, Json, TraceEvent


class TraceRecorder:
    """Append-only trace. Assigns seq itself and keeps vt non-decreasing."""

    def __init__(self) -> None:
        self._events: list[TraceEvent] = []

    @property
    def events(self) -> tuple[TraceEvent, ...]:
        return tuple(self._events)

    def record(
        self,
        vt: int,
        workflow_id: str,
        actor: Actor,
        event: str,
        data: dict[str, Json],
    ) -> TraceEvent:
        if self._events and vt < self._events[-1].vt:
            raise ValueError(f"vt went backwards: {vt} < {self._events[-1].vt}")
        recorded = TraceEvent(
            seq=len(self._events),
            vt=vt,
            workflow_id=workflow_id,
            actor=actor,
            event=event,
            data=data,
        )
        self._events.append(recorded)
        return recorded


def canonical_line(event: TraceEvent) -> str:
    return json.dumps(
        event.model_dump(mode="json"),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    )


def serialize(events: Sequence[TraceEvent]) -> bytes:
    if not events:
        return b""
    return ("\n".join(canonical_line(e) for e in events) + "\n").encode("utf-8")


def trace_hash(events: Sequence[TraceEvent]) -> str:
    return hashlib.sha256(serialize(events)).hexdigest()
