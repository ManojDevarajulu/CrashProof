from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict

from crashproof.clock import Clock
from crashproof.ids import IdGen
from crashproof.sim.trace import TraceRecorder
from crashproof.world.events import EventBus
from crashproof.world.faults import FaultInjector


class IdempotencyConflict(ValueError):
    """Raised when an idempotency key is reused with a different request."""


class KeysNotSupported(RuntimeError):
    """Raised when a key lookup is made on a carrier that has no idempotency keys."""


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)


class LabelResult(_Frozen):
    label_id: str
    status: Literal["created", "pending"]


class LabelRecord(_Frozen):
    label_id: str
    order_id: str
    reference: str
    idempotency_key: str | None
    status: Literal["created", "pending"]
    created_vt: int


class LabelProvider(Protocol):
    supports_keys: bool

    @property
    def labels(self) -> tuple[LabelRecord, ...]: ...

    def create_label(
        self,
        order_id: str,
        reference: str,
        *,
        idempotency_key: str | None = None,
    ) -> LabelResult: ...

    def get_label_by_reference(self, reference: str) -> tuple[LabelRecord, ...]: ...

    def get_label_by_key(self, key: str) -> LabelRecord | None: ...


class CarrierA:
    """FastShip: synchronous labels, supports idempotency keys.

    The same key with the same request returns the original label and creates
    nothing new. The same key with a different request raises
    IdempotencyConflict and applies nothing. Without a key every call creates a
    new label.
    """

    supports_keys = True

    def __init__(
        self,
        clock: Clock,
        ids: IdGen,
        trace: TraceRecorder,
        bus: EventBus,
        faults: FaultInjector | None = None,
    ) -> None:
        self._clock = clock
        self._ids = ids
        self._trace = trace
        self._bus = bus
        self._faults = faults
        self._labels: list[LabelRecord] = []
        self._by_key: dict[str, tuple[tuple[str, str], LabelRecord]] = {}

    @property
    def labels(self) -> tuple[LabelRecord, ...]:
        return tuple(self._labels)

    def create_label(
        self,
        order_id: str,
        reference: str,
        *,
        idempotency_key: str | None = None,
    ) -> LabelResult:
        if self._faults is None:
            return self._create_label(
                order_id, reference, idempotency_key=idempotency_key
            )
        attempt = self._faults.begin_call("create_label", reference)
        result = self._create_label(
            order_id, reference, idempotency_key=idempotency_key
        )
        self._faults.end_call("create_label", reference, attempt)
        return result

    def _create_label(
        self,
        order_id: str,
        reference: str,
        *,
        idempotency_key: str | None = None,
    ) -> LabelResult:
        request = (order_id, reference)
        if idempotency_key is not None and idempotency_key in self._by_key:
            return self._replay(idempotency_key, request, reference)
        record = LabelRecord(
            label_id=self._ids.new("lbl"),
            order_id=order_id,
            reference=reference,
            idempotency_key=idempotency_key,
            status="created",
            created_vt=self._clock.now,
        )
        self._labels.append(record)
        if idempotency_key is not None:
            self._by_key[idempotency_key] = (request, record)
        self._trace.record(
            self._clock.now,
            reference,
            "world",
            "label.created_record",
            {
                "label_id": record.label_id,
                "order_id": order_id,
                "reference": reference,
                "key": idempotency_key,
            },
        )
        self._bus.publish(
            "label.created",
            reference,
            {"label_id": record.label_id, "order_id": order_id},
            delay=0,
        )
        return LabelResult(label_id=record.label_id, status=record.status)

    def _replay(
        self, key: str, request: tuple[str, str], reference: str
    ) -> LabelResult:
        stored_request, record = self._by_key[key]
        if stored_request != request:
            self._trace.record(
                self._clock.now,
                reference,
                "world",
                "label.key_conflict",
                {"key": key, "label_id": record.label_id},
            )
            raise IdempotencyConflict(f"key {key!r} reused with a different request")
        self._trace.record(
            self._clock.now,
            reference,
            "world",
            "label.replayed",
            {"key": key, "label_id": record.label_id},
        )
        return LabelResult(label_id=record.label_id, status=record.status)

    def get_label_by_reference(self, reference: str) -> tuple[LabelRecord, ...]:
        """Labels for a reference, in creation order. Empty means none exist."""
        return tuple(r for r in self._labels if r.reference == reference)

    def get_label_by_key(self, key: str) -> LabelRecord | None:
        """The label created under a key, or None. Not found is a valid answer."""
        entry = self._by_key.get(key)
        return None if entry is None else entry[1]


class CarrierB(CarrierA):
    """GlobeLine: asynchronous labels, no idempotency keys.

    The key argument is accepted and ignored, so every call creates a new label.
    A label is recorded as pending and label.created arrives after label_delay.
    """

    supports_keys = False

    def __init__(
        self,
        clock: Clock,
        ids: IdGen,
        trace: TraceRecorder,
        bus: EventBus,
        faults: FaultInjector | None = None,
        *,
        label_delay: int,
    ) -> None:
        super().__init__(clock, ids, trace, bus, faults)
        self._label_delay = label_delay

    def _create_label(
        self,
        order_id: str,
        reference: str,
        *,
        idempotency_key: str | None = None,
    ) -> LabelResult:
        record = LabelRecord(
            label_id=self._ids.new("lbl"),
            order_id=order_id,
            reference=reference,
            idempotency_key=None,
            status="pending",
            created_vt=self._clock.now,
        )
        self._labels.append(record)
        self._trace.record(
            self._clock.now,
            reference,
            "world",
            "label.created_record",
            {
                "label_id": record.label_id,
                "order_id": order_id,
                "reference": reference,
                "key": None,
            },
        )
        self._bus.publish(
            "label.created",
            reference,
            {"label_id": record.label_id, "order_id": order_id},
            delay=self._label_delay,
        )
        return LabelResult(label_id=record.label_id, status=record.status)

    def get_label_by_key(self, key: str) -> LabelRecord | None:
        raise KeysNotSupported("GlobeLine does not support idempotency keys")


class CarrierC(CarrierA):
    """NordPost: synchronous labels with idempotency keys, same as FastShip."""
